// Tests THROUGH the pre-existing, stable public API
// `turborepo_fs::recursive_copy(src, dst, use_gitignore)` — the copy helper
// that `turbo prune` uses to populate the pruned `out/` tree, whose contract
// ("copy a tree, honoring only the `.gitignore` files *inside* that tree") the
// bug violates. Independent of how the fix is implemented: it only observes the
// copied filesystem state, so any valid fix passes, while a fix that disables
// gitignore handling fails the second test.
//
// turbopath (for AbsoluteSystemPathBuf) is added to turborepo-fs's
// [dev-dependencies] by tests/test-setup.sh; tempfile is already a
// dev-dependency of the crate.

use std::fs;

use tempfile::tempdir;
use turbopath::AbsoluteSystemPathBuf;
use turborepo_fs::recursive_copy;

/// Build the customer's reproduction scenario in a fresh tempdir and return
/// (repo_root_tempdir, dst_tempdir) after copying `apps/web` -> dst with
/// gitignore handling enabled.
///
/// Layout created:
///   <root>/.git/                              (git boundary, activates
///                                              git-aware ignore traversal)
///   <root>/.gitignore                         -> "/apps/web/coverage"
///                                              (root-anchored entry meant to
///                                              ignore ONE coverage dir)
///   <root>/apps/web/src/api/coverage/test.js  (real source, shares basename
///                                              "coverage" but a DIFFERENT path)
///   <root>/apps/web/src/api/.gitignore        -> "ignored.js"
///                                              (workspace-local ignore)
///   <root>/apps/web/src/api/ignored.js        (genuinely ignored file)
fn run_copy_scenario() -> (tempfile::TempDir, tempfile::TempDir) {
    let root_tmp = tempdir().unwrap();
    let root = root_tmp.path();

    // Git boundary so the `ignore` crate activates git-aware traversal.
    fs::create_dir_all(root.join(".git")).unwrap();

    // Root-anchored ignore entry targeting one specific coverage dir.
    fs::write(root.join(".gitignore"), "/apps/web/coverage\n").unwrap();

    // A real, committed source dir that merely shares the basename
    // "coverage" but lives at a different path than the entry anchors to.
    let nested = root.join("apps/web/src/api/coverage");
    fs::create_dir_all(&nested).unwrap();
    fs::write(nested.join("test.js"), "console.log('covered');\n").unwrap();

    // A workspace-local .gitignore inside the copied tree, plus a file it
    // genuinely ignores. This MUST still be respected after the fix.
    fs::write(
        root.join("apps/web/src/api/.gitignore"),
        "ignored.js\n",
    )
    .unwrap();
    fs::write(root.join("apps/web/src/api/ignored.js"), "ignored\n").unwrap();

    let dst_tmp = tempdir().unwrap();
    let src = AbsoluteSystemPathBuf::try_from(root.join("apps/web").as_path()).unwrap();
    let dst = AbsoluteSystemPathBuf::try_from(dst_tmp.path()).unwrap();

    // recursive_copy strips the `src` prefix, so the copied tree under
    // `dst` is rooted at apps/web's contents (e.g. dst/src/api/...).
    recursive_copy(&src, &dst, true).unwrap();

    (root_tmp, dst_tmp)
}

/// fail_to_pass: a nested directory whose basename matches a ROOT-ANCHORED
/// `.gitignore` entry in a parent directory must still be copied. Pre-fix
/// the copy walk reads the parent `.gitignore` and drops it; post-fix it is
/// copied.
#[test]
fn test_nested_dir_sharing_root_gitignore_basename_is_copied() {
    let (_root_tmp, dst_tmp) = run_copy_scenario();

    let copied = dst_tmp.path().join("src/api/coverage/test.js");
    assert!(
        copied.exists(),
        "nested source dir apps/web/src/api/coverage/test.js was dropped from \
         the pruned copy: a root-anchored '/apps/web/coverage' ignore entry \
         must not over-match the unrelated nested 'coverage' directory \
         (expected at {copied:?})"
    );
}

/// pass_to_pass: `.gitignore` files located WITHIN the copied subtree must
/// still be respected. Guards the over-correction of "fixing" the bug by
/// disabling gitignore handling entirely (e.g. passing use_gitignore=false
/// or .git_ignore(false)), which would start copying genuinely-ignored
/// files into the pruned output.
#[test]
fn test_workspace_local_gitignore_still_respected() {
    let (_root_tmp, dst_tmp) = run_copy_scenario();

    let ignored = dst_tmp.path().join("src/api/ignored.js");
    assert!(
        !ignored.exists(),
        "workspace-local .gitignore (apps/web/src/api/.gitignore ignoring \
         'ignored.js') must still be respected during the copy; the fix must \
         not disable gitignore handling (found unexpected file at {ignored:?})"
    );
}

/// Build a fresh reproduction: a git boundary at `<root>`, a parent
/// `.gitignore` at `gi_relpath` (relative to `<root>`) containing `gi_body`,
/// and a real nested source file at `apps/web/<nested>`. Copy `apps/web` ->
/// dst with gitignore handling enabled, and return whether `<nested>` survived
/// under dst. The parent `.gitignore` lives at or above the copied workspace,
/// so it must NOT influence the copy once the walk no longer ascends past its
/// root.
fn copy_workspace_with_parent_ignore(gi_relpath: &str, gi_body: &str, nested: &str) -> bool {
    let root_tmp = tempdir().unwrap();
    let root = root_tmp.path();
    fs::create_dir_all(root.join(".git")).unwrap();

    let gi = root.join(gi_relpath);
    fs::create_dir_all(gi.parent().unwrap()).unwrap();
    fs::write(&gi, gi_body).unwrap();

    let nested_file = root.join("apps/web").join(nested);
    fs::create_dir_all(nested_file.parent().unwrap()).unwrap();
    fs::write(&nested_file, "// real committed source\n").unwrap();

    let dst_tmp = tempdir().unwrap();
    let src = AbsoluteSystemPathBuf::try_from(root.join("apps/web").as_path()).unwrap();
    let dst = AbsoluteSystemPathBuf::try_from(dst_tmp.path()).unwrap();
    recursive_copy(&src, &dst, true).unwrap();

    dst_tmp.path().join(nested).exists()
}

/// fail_to_pass: NO parent `.gitignore` entry — whatever its anchoring, its
/// location above the workspace, or the basename it targets — may drop a real
/// nested source directory from the copied workspace. The headline scenario
/// above is one specific shape (a root-anchored entry sharing the `coverage`
/// basename); these geometries guard against a fix that only neutralizes that
/// one shape — e.g. allow-listing the literal `coverage` basename, or
/// suppressing only the repo-root `.gitignore` while still reading an
/// intermediate one. A correct fix (stop the copy walk from reading any
/// `.gitignore` above the copied root) covers every case here; pre-fix every
/// case drops the file.
#[test]
fn test_parent_gitignore_never_overmatches_nested_source() {
    // (label, gitignore path relative to root, gitignore body, nested source file)
    let cases: &[(&str, &str, &str, &str)] = &[
        (
            "root-anchored entry, different basename",
            ".gitignore",
            "/apps/web/dist\n",
            "src/api/dist/bundle.js",
        ),
        (
            "root-level, non-anchored basename",
            ".gitignore",
            "logs\n",
            "src/api/logs/out.txt",
        ),
        (
            "intermediate-parent .gitignore above the workspace",
            "apps/.gitignore",
            "/web/dist\n",
            "src/api/dist/main.js",
        ),
    ];

    for (label, gi_relpath, gi_body, nested) in cases {
        assert!(
            copy_workspace_with_parent_ignore(gi_relpath, gi_body, nested),
            "parent .gitignore over-matched and dropped a real nested source file \
             [case: {label}]: a parent ignore entry ({gi_body:?} in {gi_relpath}) \
             must not remove apps/web/{nested} from the pruned copy"
        );
    }
}
