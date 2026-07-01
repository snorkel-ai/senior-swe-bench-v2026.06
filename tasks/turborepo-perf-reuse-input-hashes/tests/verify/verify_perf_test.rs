//! Tests through the pre-existing public `SCM::get_package_file_hashes` API on
//! the `turborepo-scm` crate. The signature, inputs, and return type are
//! unchanged; only the *internal behavior* of the explicit-input branch
//! changes (it consults `RepoGitIndex` instead of unconditionally hashing
//! every matched file). Independent of the internal implementation choice
//! (partition helper, inline check, hash_objects skip-list, etc.), it asserts
//! only the externally-observable consequences:
//!
//!   1. Discriminating fail-to-pass: when called with a populated
//!      `RepoGitIndex` and an explicit-input glob over a package of
//!      clean tracked files, the call must NOT re-read the file
//!      contents from disk. Observed via `/proc/self/io` `rchar`,
//!      which counts every byte the process pulls in via `read()`-
//!      family syscalls. Pre-fix the explicit-input branch streams
//!      every matched file through the blob-hash path and `rchar`
//!      grows by ~total file content size; post-fix the same call
//!      reuses OIDs already known to the index and `rchar` only ticks
//!      up by the few hundred bytes of metadata the walker reads.
//!
//!   2. Pass-to-pass correctness: with-index and without-index paths
//!      produce identical `GitHashes` for explicit inputs that mix
//!      tracked, untracked, and parent-directory matches. Mirrors the
//!      regression test the upstream PR adds.
//!
//! Test-helper boilerplate (`tmp_dir`, `init_git`, `commit_all`, `rchar`) is
//! inlined here because `turborepo-scm`'s own `test_utils.rs` is `pub(crate)`
//! and unreachable from an integration test under `tests/`.

use std::process::Command;

use tempfile::TempDir;
use turbopath::{
    AbsoluteSystemPath, AbsoluteSystemPathBuf, AnchoredSystemPathBuf, RelativeUnixPathBuf,
};
use turborepo_scm::SCM;

// ---------------------------------------------------------------------------
// Inlined test helpers (see module-level note above).
// ---------------------------------------------------------------------------

fn tmp_dir() -> (TempDir, AbsoluteSystemPathBuf) {
    let tmp = tempfile::tempdir().expect("failed to create tempdir");
    let dir = AbsoluteSystemPathBuf::try_from(tmp.path())
        .expect("convert tempdir to absolute path")
        .to_realpath()
        .expect("realpath tempdir");
    (tmp, dir)
}

fn run_git(repo: &AbsoluteSystemPath, args: &[&str]) {
    let out = Command::new("git")
        .args(args)
        .current_dir(repo)
        .output()
        .expect("spawn git");
    assert!(
        out.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&out.stderr),
    );
}

fn init_git(root: &AbsoluteSystemPath) {
    run_git(root, &["init", "--quiet", "."]);
    run_git(root, &["config", "--local", "user.name", "test"]);
    run_git(root, &["config", "--local", "user.email", "test@example.com"]);
}

fn commit_all(root: &AbsoluteSystemPath) {
    run_git(root, &["add", "."]);
    run_git(root, &["commit", "--quiet", "-m", "init"]);
}

/// Read the cumulative `rchar` byte count for this process. `/proc/self/io`
/// is the kernel's per-process I/O accounting view: `rchar` is the total
/// number of bytes the process has caused to be read at the syscall
/// layer (it counts both cache hits and disk reads). Comparing rchar
/// before and after a single function call gives a direct measure of
/// "did this call read file contents."
fn rchar() -> u64 {
    let s = std::fs::read_to_string("/proc/self/io").unwrap_or_default();
    s.lines()
        .find_map(|l| {
            l.strip_prefix("rchar: ")
                .and_then(|v| v.parse::<u64>().ok())
        })
        .unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Test 1 — discriminating fail-to-pass: with-index + explicit input glob
// must not re-read clean tracked file contents.
// ---------------------------------------------------------------------------

#[test]
fn explicit_input_path_skips_file_reads_with_index() {
    // Workload sized to make the discrimination unambiguous:
    //   N=200 tracked files × FILE_BYTES=4096 = ~800KB total content.
    // Pre-fix `rchar_delta` ≈ 832118 bytes (file content + git index
    // metadata). Post-fix ≈ 116 bytes (just the index/walker metadata).
    // Threshold = 80KB leaves >10x margin from pre-fix and >700x from
    // post-fix — robust to filesystem and CI noise.
    const N: usize = 200;
    const FILE_BYTES: usize = 4096;

    let (_tmp, root) = tmp_dir();
    init_git(&root);

    let pkg = root.join_component("my-pkg");
    pkg.create_dir_all().unwrap();
    pkg.join_component("package.json")
        .create_with_contents("{}")
        .unwrap();

    let chunk = "x".repeat(64);
    for i in 0..N {
        let f = pkg.join_component(&format!("file-{:04}.ts", i));
        let mut s = String::with_capacity(FILE_BYTES + 80);
        while s.len() < FILE_BYTES {
            s.push_str(&chunk);
            s.push('\n');
        }
        f.create_with_contents(&s).unwrap();
    }
    commit_all(&root);

    let scm = SCM::new(&root);
    let index = scm
        .build_repo_index_eager()
        .expect("build_repo_index_eager returned None — expected git SCM");
    let pkg_anchor = AnchoredSystemPathBuf::from_raw("my-pkg").unwrap();

    // Measure the FIRST (cold) with-index call. No warmup and no min-of-N:
    // `rchar` counts bytes read at the syscall layer regardless of page-cache
    // warmth (and allocator spikes don't issue read()s), so a warmup buys
    // nothing for this measurement — and it would actively defeat the test. A
    // fix that merely memoizes file hashes across calls (instead of consulting
    // the index) reads ~0 on a warmed/repeated call yet still re-reads every
    // matched file on this cold call; measuring the cold call keeps that
    // memo-only shortcut failing. The index-reuse fix reads only walker/index
    // metadata even cold.
    let r0 = rchar();
    let h = scm
        .get_package_file_hashes(&root, &pkg_anchor, &["**/*.ts"], false, None, Some(&index))
        .unwrap();
    let bytes_read = rchar().saturating_sub(r0);
    // The `**/*.ts` glob matches the N .ts files. The implicit config-file
    // inclusion (package.json) adds 1.
    assert_eq!(
        h.len(),
        N + 1,
        "expected N=200 .ts files + 1 package.json (config), got {}",
        h.len()
    );

    let total_content_bytes = (N * FILE_BYTES) as u64;
    let threshold = total_content_bytes / 10; // 80KB

    assert!(
        bytes_read < threshold,
        "the with-index explicit-input path should not re-read clean tracked \
         file contents from disk; expected rchar_delta < {threshold} bytes, \
         got {bytes_read} bytes (total file-content size = {total_content_bytes} bytes). \
         Pre-fix this path streams every matched file through the blob-hash \
         routine and rchar grows proportionally to the total content size; \
         post-fix the same call reuses OIDs already known to the repo index \
         and rchar only ticks up by the few hundred bytes of walker metadata. \
         A bare `cargo test` build that doesn't touch the input-hashing path \
         (or one that only patches the mixed default+inputs branch), and a fix \
         that merely memoizes across calls (this is a cold call), leave this \
         assertion failing.",
    );
}

// ---------------------------------------------------------------------------
// Test 2 — pass-to-pass correctness: with-index hashes must equal
// without-index hashes for explicit inputs that span tracked,
// untracked, and parent-directory files.
// ---------------------------------------------------------------------------

#[test]
fn with_index_matches_without_index_for_explicit_inputs() {
    // Setup mirrors the upstream PR's added regression test
    // (`test_inputs_without_defaults_match_no_index_for_tracked_and_parent_files`):
    //   - my-pkg/committed-file        (tracked, clean)
    //   - my-pkg/package.json          (config, included automatically)
    //   - new-root-file                (tracked, clean, parent of my-pkg)
    //   - my-pkg/uncommitted-file      (untracked — needs real hashing)
    //
    // Input glob `../**/*-file` exercises the parent-path branch and
    // hits all three "*-file" entries.
    let (_tmp, root) = tmp_dir();
    init_git(&root);

    let pkg = root.join_component("my-pkg");
    pkg.create_dir_all().unwrap();
    pkg.join_component("committed-file")
        .create_with_contents("committed")
        .unwrap();
    pkg.join_component("package.json")
        .create_with_contents("{}")
        .unwrap();
    root.join_component("new-root-file")
        .create_with_contents("root")
        .unwrap();
    // Tracked-then-modified: committed clean, then changed in the working
    // tree below. The index optimization must NOT reuse the committed
    // ls-tree OID for it — a modified working-tree file has to be routed
    // through content hashing, so with-index must still equal without-index.
    pkg.join_component("modified-file")
        .create_with_contents("original-committed-content")
        .unwrap();
    commit_all(&root);

    pkg.join_component("uncommitted-file")
        .create_with_contents("new")
        .unwrap();
    // Modify the tracked file in the working tree (no re-commit), so its
    // working-tree content differs from the committed blob.
    pkg.join_component("modified-file")
        .create_with_contents("CHANGED-content-differs-from-committed-blob")
        .unwrap();

    let scm = SCM::new(&root);
    let index = scm
        .build_repo_index_eager()
        .expect("build_repo_index_eager returned None — expected git SCM");
    let pkg_anchor = AnchoredSystemPathBuf::from_raw("my-pkg").unwrap();

    let with_idx = scm
        .get_package_file_hashes(
            &root,
            &pkg_anchor,
            &["../**/*-file"],
            false,
            None,
            Some(&index),
        )
        .unwrap();
    let without_idx = scm
        .get_package_file_hashes(&root, &pkg_anchor, &["../**/*-file"], false, None, None)
        .unwrap();

    assert_eq!(
        with_idx, without_idx,
        "the optimized (with-index) path must produce hashes identical to the \
         unoptimized (without-index) path. A diff here means the optimization \
         introduced a correctness bug — most likely reusing a stale ls-tree \
         OID for a file that should have been routed through the file-content \
         hashing path because of a status entry."
    );

    let p = |s: &str| RelativeUnixPathBuf::new(s).unwrap();
    assert!(
        with_idx.contains_key(&p("committed-file")),
        "expected 'committed-file' in result"
    );
    assert!(
        with_idx.contains_key(&p("uncommitted-file")),
        "expected 'uncommitted-file' (untracked) in result"
    );
    assert!(
        with_idx.contains_key(&p("modified-file")),
        "expected 'modified-file' (tracked-then-modified) in result"
    );
    assert!(
        with_idx.contains_key(&p("../new-root-file")),
        "expected '../new-root-file' (parent-dir match) in result"
    );
}
