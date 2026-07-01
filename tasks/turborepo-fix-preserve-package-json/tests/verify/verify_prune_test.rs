//! Behavioral verifier for `turborepo-fix-preserve-package-json`.
//!
//! Tests through the pre-existing `turbo prune <scope>` CLI — the
//! highest-level public interface where the bug is observable. The
//! tests are independent of the agent's internal implementation choice
//! (recursive value merge, in-place struct mutation, hand-rolled text
//! rewrite, etc.). They only assert externally-visible behaviour:
//!
//!   1. The pruned `package.json` retains the source file's key
//!      ordering. Pre-fix `serde_json::to_string_pretty(&PackageJson)`
//!      writes keys in the `PackageJson` struct's declaration order
//!      and shoves any unrecognized top-level keys (kept in the
//!      flattened `other` `BTreeMap`) to the end. Different bytes →
//!      different global hash → cache miss inside the pruned output.
//!
//!   2. When `pnpm-workspace.yaml` declares `patchedDependencies`, the
//!      pruned `out/package.json`'s `pnpm.patchedDependencies` does
//!      NOT gain entries copied from the workspace yaml. Pre-fix the
//!      `prune_patches` helper migrates workspace-yaml patches into
//!      `package.json`'s `pnpm.patchedDependencies`, adding bytes the
//!      original file never had. Post-fix the migration is gone.
//!
//!   3. The pruned `out/pnpm-workspace.yaml`'s `patchedDependencies`
//!      mapping is filtered to retain only entries whose patch path is
//!      used by the pruned subgraph. Pre-fix the workspace yaml is
//!      copied verbatim — entries that reference patches dropped by
//!      pruning still appear in the output. Post-fix the workspace
//!      yaml is pruned in place.
//!
//! Test infrastructure mirrors the in-repo `crates/turborepo/tests/`
//! patterns (e.g. `setup_lockfile_test` in `tests/common/mod.rs` for
//! the lockfile-tests fixture, and `prune_test.rs` for prune CLI
//! invocations). The harness helpers (`copy_dir_all`, `run_turbo`)
//! are pre-existing and reused here. We deliberately bypass
//! `setup_integration_test` / `setup_package_manager` because those
//! rewrite the tempdir's `package.json` via `serde_json::to_string_pretty`,
//! which itself reorders keys pre-fix and would mask the bug. Instead
//! we copy the fixture verbatim and `git init` directly, leaving the
//! `package.json` byte-for-byte identical to the source file.
//!
//! Tests 2 and 3 inject `patchedDependencies` into the fixture's
//! `pnpm-workspace.yaml` AFTER setup. The injected entries point at a
//! real lockfile patch (`patches/is-odd@3.0.1.patch`, which the
//! `dependency` workspace's transitive deps bring into the pruned
//! subgraph) and a fake one (`patches/no-such-patch.patch`), giving
//! the verifier two distinct discriminants:
//!  - The pre-fix migration loop only migrates entries whose value is
//!    in the `pruned_patches` set, so the lockfile-aligned
//!    `ghost-in-use` entry is what migrates pre-fix and stays out of
//!    `package.json` post-fix (Test 2).
//!  - The post-fix in-place workspace-yaml prune drops entries whose
//!    value is NOT in `pruned_patches`, so the unaligned
//!    `ghost-not-used` entry is what survives pre-fix (verbatim copy)
//!    and gets dropped post-fix (Test 3).

mod common;

use std::{
    fs,
    path::{Path, PathBuf},
};

use common::{combined_output, git, run_turbo, setup};

/// Path within the workspace to the fixture this verifier exercises.
/// `lockfile-tests/fixtures/pnpm-patch` is a pre-existing pnpm@7.33.0
/// monorepo whose lockfile and `package.json` both declare patches
/// (`is-odd@3.0.1`, `@babel/core@7.20.12`, `moleculer@0.14.28`). The
/// `package.json` includes `private: true` — an unrecognized field
/// from the perspective of `turborepo-repository::package_json::PackageJson`,
/// so it lands in the struct's flattened `other` `BTreeMap` and pre-fix
/// re-serialization shoves it to the end of the output.
const FIXTURE_RELATIVE_PATH: &str = "lockfile-tests/fixtures/pnpm-patch";

/// The workspace under the fixture that `turbo prune` targets. The
/// fixture has a single workspace `packages/dependency`. Pruning to
/// it keeps the patches reachable through that workspace's deps and
/// drops anything the pruned subgraph doesn't reference.
const PRUNE_SCOPE: &str = "dependency";

/// A patch path that IS in the lockfile (and therefore in the pruned
/// subgraph's `patches()` set). We use it as the "in use" injection
/// target for Tests 2 and 3.
const IN_USE_PATCH_PATH: &str = "patches/is-odd@3.0.1.patch";

/// A patch path that is NOT in the lockfile (and therefore NOT in any
/// subgraph's `patches()` set). Used as the "not used" injection
/// target for Test 3. The file does not exist on disk; that's fine —
/// the workspace-yaml prune code reads the path string only to look
/// it up in the patches set, never to open the file.
const NOT_USED_PATCH_PATH: &str = "patches/no-such-patch.patch";

/// Synthetic `pnpm.patchedDependencies` entry injected into root
/// `package.json` for Test 1. Its patch path is intentionally NOT
/// declared in the lockfile, so the pruned subgraph's `patches()` set
/// does not contain it. The pre-fix `prune_patches()` helper filters
/// `existing_patches` by `patches_set.contains(...)`, dropping the
/// entry; any senior-defensible fix that re-applies that filter (or
/// merges a filtered struct against the original Value) likewise drops
/// it. A verbatim-copy workaround that wraps the rewrite in
/// `if pruned_patches == original_patches { copy_original() }`
/// retains the entry, since pruned_patches is lockfile-driven and
/// adding to package.json doesn't perturb that equality.
const GHOST_PATCH_KEY: &str = "ghost-not-in-lockfile@9.9.9";
const GHOST_PATCH_PATH: &str = "patches/ghost-not-in-lockfile.patch";

// ---------------------------------------------------------------------------
// Helpers: workspace root + fixture setup.
// ---------------------------------------------------------------------------

/// Path to the cargo workspace root, derived from `CARGO_MANIFEST_DIR`
/// of `crates/turborepo`. Mirrors the helper used by the in-tree
/// `setup_lockfile_test` in `tests/common/mod.rs`.
fn workspace_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .join("../..")
        .canonicalize()
        .expect("failed to resolve workspace root from CARGO_MANIFEST_DIR")
}

/// Copy the `pnpm-patch` fixture into `dir` byte-for-byte and `git
/// init` it. Bypasses `setup_integration_test` / `setup_package_manager`
/// — those re-serialize `package.json` via `serde_json::to_string_pretty`,
/// which itself reorders keys pre-fix and would mask the very bug
/// these tests are designed to catch. `turbo prune` reads files from
/// disk and does not require `pnpm install` to have run, so skipping
/// corepack/install setup is safe.
fn copy_pnpm_patch_fixture(dir: &Path) {
    let src = workspace_root().join(FIXTURE_RELATIVE_PATH);
    setup::copy_dir_all(&src, dir).expect("copy_dir_all failed");

    git(dir, &["init", "--quiet", "--initial-branch=main"]);
    git(dir, &["config", "user.email", "turbo-test@example.com"]);
    git(dir, &["config", "user.name", "Turbo Test"]);
}

/// Append a `patchedDependencies:` block to `pnpm-workspace.yaml` in
/// `dir`. The fixture's source workspace yaml has only a `packages:`
/// list; we add the `patchedDependencies:` mapping so the pre-fix
/// migration / post-fix in-place prune codepaths have something to
/// react to.
///
/// `entries` is `(key, patch_path)` pairs. The patch path is expected
/// to be relative to the repo root.
fn append_workspace_yaml_patched_deps(dir: &Path, entries: &[(&str, &str)]) {
    let ws_path = dir.join("pnpm-workspace.yaml");
    let mut contents = fs::read_to_string(&ws_path).expect("workspace yaml missing");
    if !contents.ends_with('\n') {
        contents.push('\n');
    }
    contents.push_str("\npatchedDependencies:\n");
    for (key, value) in entries {
        contents.push_str(&format!("  {key}: {value}\n"));
    }
    fs::write(&ws_path, contents).expect("failed to write workspace yaml");
}

/// Append a `pnpm.patchedDependencies` entry to the root `package.json`
/// in `dir`. The fixture's source file declares its
/// `patchedDependencies` block as a flat 6-space-indented mapping
/// ending with the `moleculer@0.14.28` line; we splice the new entry in
/// after that line, preserving the surrounding formatting. Used by
/// Test 1 to introduce a discriminator entry whose patch path is not
/// in the lockfile (see `GHOST_PATCH_KEY` for the rationale).
fn inject_root_patched_dependency(dir: &Path, key: &str, patch_path: &str) {
    let pkg_path = dir.join("package.json");
    let original = fs::read_to_string(&pkg_path).expect("root package.json missing");
    let needle = "\"moleculer@0.14.28\": \"patches/moleculer@0.14.28.patch\"";
    let replacement = format!(
        "{needle},\n      \"{key}\": \"{patch_path}\""
    );
    let updated = original.replace(needle, &replacement);
    assert_ne!(
        updated, original,
        "fixture invariant: expected to find moleculer entry in pnpm.patchedDependencies \
         to splice the synthetic entry after"
    );
    fs::write(&pkg_path, updated).expect("failed to write injected package.json");
}

/// Stage everything in the fixture and create a single commit. Run
/// after any test-time mutations (workspace-yaml injection) so the
/// repo is in a clean state when `turbo prune` reads it.
fn commit_fixture_state(dir: &Path) {
    git(dir, &["add", "."]);
    git(dir, &["commit", "-m", "initial", "--quiet"]);
}

// ---------------------------------------------------------------------------
// Helpers: text-level key extraction. We CANNOT use
// `serde_json::Value::as_object().keys()` here because the
// `serde_json` `preserve_order` feature flag flips the underlying map
// type at WORKSPACE scope. With the agent's fix applied that flag is
// on, and `Value::keys()` returns insertion order; without the fix
// the same call returns alphabetical order, so a Value-based
// comparison would compare alphabetical-to-alphabetical pre-fix and
// erroneously pass. Reading the file as text and parsing key
// positions directly bypasses serde_json's map-type indirection.
// ---------------------------------------------------------------------------

/// Extract the top-level object keys from a JSON document in document
/// (file) order. Assumes the document was produced by
/// `serde_json::to_string_pretty` (or a hand-formatted file matching
/// that style): top-level keys live on lines that begin with exactly
/// two spaces of indentation followed by a quoted key.
fn extract_top_level_keys(json_text: &str) -> Vec<String> {
    let mut keys = Vec::new();
    for line in json_text.lines() {
        // Top-level keys are at indent depth 1 (2 spaces). Lines
        // indented further (nested object members) start with at least
        // 4 spaces; reject anything with a third leading space.
        if line.starts_with("  \"") && !line.starts_with("   ") {
            let after_indent = &line[3..];
            if let Some(end) = after_indent.find("\":") {
                keys.push(after_indent[..end].to_string());
            }
        }
    }
    keys
}

/// Extract literal sub-keys from the `patchedDependencies:` mapping
/// of a `pnpm-workspace.yaml`. Crude but sufficient for the fixture
/// we're testing — the mapping is always flat and indented by exactly
/// two spaces. We avoid pulling in a yaml dep (none is in the test
/// crate's dev-dependencies) by string-scanning instead.
fn workspace_yaml_patched_dep_keys(yaml_text: &str) -> Vec<String> {
    let mut keys = Vec::new();
    let mut in_section = false;
    for line in yaml_text.lines() {
        // The section header is at column 0 (`patchedDependencies:`).
        if !line.starts_with(' ') && line.trim_start().starts_with("patchedDependencies:") {
            in_section = true;
            continue;
        }
        if !in_section {
            continue;
        }
        // Section ends at the next non-indented non-blank line.
        if !line.is_empty() && !line.starts_with(' ') {
            in_section = false;
            continue;
        }
        // Sub-keys are at exactly two spaces of indent. Reject deeper
        // indentation; the patchedDependencies mapping in pnpm-workspace.yaml
        // is always flat (key: value) so this is sufficient.
        if line.starts_with("  ") && !line.starts_with("   ") {
            let trimmed = line.trim_start();
            if let Some(colon) = trimmed.find(':') {
                let key = trimmed[..colon].trim().trim_matches('"').trim_matches('\'');
                if !key.is_empty() {
                    keys.push(key.to_string());
                }
            }
        }
    }
    keys
}

// ---------------------------------------------------------------------------
// Test 1: pruned package.json preserves source key order.
// ---------------------------------------------------------------------------

/// PRE-FIX: `prune` deserializes the root `package.json` into a
/// `PackageJson` Rust struct, drops dependency entries the pruned
/// subgraph doesn't need, and re-serializes the struct via
/// `serde_json::to_string_pretty`. The struct has fixed field order
/// (name, version, packageManager, dependencies, ...) and shoves
/// unrecognized fields (`private` in this fixture) into a flattened
/// `other` BTreeMap that gets emitted alphabetically AFTER the struct
/// fields. The fixture's source file has `private` between `version`
/// and `dependencies`, so the pre-fix output reorders it to the end.
/// Different bytes → different global hash → cache miss.
///
/// POST-FIX: the pruned output retains the source file's key order.
///
/// Discriminator: in addition to the key-order assertion, this test
/// injects a synthetic `pnpm.patchedDependencies` entry whose patch
/// path is NOT declared in the lockfile (see `GHOST_PATCH_KEY`). The
/// pre-fix `prune_patches()` helper filters via
/// `patches_set.contains(...)` and drops it; any senior-defensible
/// fix likewise drops it. A verbatim-copy workaround that wraps the
/// rewrite in `if pruned_patches == original_patches { copy_original() }`
/// — exploiting that this fixture's prune scope happens to retain
/// every lockfile patch — would leave the entry in place. Asserting
/// the entry is absent forces the rewrite to actually run.
#[test]
fn test_prune_preserves_package_json_key_order_with_pnpm_patches() {
    let tempdir = tempfile::tempdir().expect("failed to create tempdir");
    copy_pnpm_patch_fixture(tempdir.path());

    // Inject a synthetic patchedDependencies entry whose patch path is not
    // in the lockfile. See module-level docstring on `GHOST_PATCH_KEY` for
    // the rationale.
    inject_root_patched_dependency(tempdir.path(), GHOST_PATCH_KEY, GHOST_PATCH_PATH);
    commit_fixture_state(tempdir.path());

    // Read the source from the tempdir copy after injection — that's the
    // state on disk when turbo prune runs, and what the pruned output's
    // top-level key ordering must match.
    let source_path = tempdir.path().join("package.json");
    let source_text =
        fs::read_to_string(&source_path).expect("failed to read source package.json");
    let source_keys = extract_top_level_keys(&source_text);
    assert!(
        source_keys.contains(&"private".to_string()),
        "fixture invariant: source package.json declares the `private` field. \
         Found keys: {source_keys:?}"
    );

    let output = run_turbo(tempdir.path(), &["prune", PRUNE_SCOPE]);
    assert!(
        output.status.success(),
        "turbo prune failed: {}",
        combined_output(&output)
    );

    let pruned_path = tempdir.path().join("out/package.json");
    let pruned_text =
        fs::read_to_string(&pruned_path).expect("failed to read pruned out/package.json");
    let pruned_keys = extract_top_level_keys(&pruned_text);

    assert_eq!(
        pruned_keys, source_keys,
        "pruned package.json should preserve the source file's key ordering. \
         Pre-fix `serde_json::to_string_pretty` writes the keys in struct \
         declaration order and shoves any field not recognized by the \
         `PackageJson` struct (e.g. `private`) into a flattened `other` \
         BTreeMap, emitted alphabetically AFTER the struct fields. The \
         resulting byte-different file invalidates the global hash even \
         when the package's deps are unchanged.\n\
         Source keys: {source_keys:?}\n\
         Pruned keys: {pruned_keys:?}\n\
         --- pruned out/package.json ---\n{pruned_text}"
    );

    // Verbatim-copy discriminator: the pruned package.json must drop
    // `pnpm.patchedDependencies` entries whose patch path is not in the
    // lockfile's pruned patches set. The synthetic ghost entry was injected
    // only into the source file; the lockfile never declared it, so the
    // pre-fix and any senior-defensible fix filter it out. A workaround
    // that copies the original verbatim leaves it in place.
    let pruned: serde_json::Value =
        serde_json::from_str(&pruned_text).expect("pruned out/package.json is not valid JSON");
    let patched = pruned
        .get("pnpm")
        .and_then(|v| v.get("patchedDependencies"))
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    assert!(
        !patched.contains_key(GHOST_PATCH_KEY),
        "pruned out/package.json's `pnpm.patchedDependencies` must drop \
         entries whose patch path is not in the pruned subgraph's patches \
         set. Pre-fix `prune_patches()` filters via \
         `patches_set.contains(...)`; a verbatim-copy workaround that \
         skips the rewrite (e.g. wrapping it in \
         `if pruned_patches == original_patches {{ copy_original() }}`) \
         leaves the synthetic entry in place. \
         Got entries: {entries:?}\n\
         --- pruned out/package.json ---\n{pruned_text}",
        entries = patched.keys().collect::<Vec<_>>(),
    );
}

// ---------------------------------------------------------------------------
// Test 2: prune does NOT migrate pnpm-workspace.yaml's
//   `patchedDependencies` into `package.json`'s
//   `pnpm.patchedDependencies`.
// ---------------------------------------------------------------------------

/// PRE-FIX: when `pnpm-workspace.yaml` declares `patchedDependencies`,
/// the `prune_patches` helper iterates the workspace yaml's entries
/// and inserts each one whose value (a patch path) is in the pruned
/// subgraph's patches set into `package.json`'s
/// `pnpm.patchedDependencies`. The pruned `out/package.json`
/// therefore gains keys that were never in the source `package.json`.
///
/// POST-FIX: the migration loop is removed. `out/package.json`'s
/// `pnpm.patchedDependencies` is still filtered to the pruned
/// subgraph, but it is NOT augmented with keys from the workspace
/// yaml.
///
/// Discriminator: we inject a `ghost-in-use` entry into the fixture's
/// `pnpm-workspace.yaml` whose value is `patches/is-odd@3.0.1.patch`
/// (which IS in `pruned_patches` because the lockfile lists it and
/// the `dependency` workspace's transitive deps reach it). Pre-fix the
/// migration moves `ghost-in-use` into the pruned `package.json`'s
/// `pnpm.patchedDependencies`. Post-fix it does not.
#[test]
fn test_prune_does_not_migrate_workspace_yaml_patches_into_package_json() {
    let tempdir = tempfile::tempdir().expect("failed to create tempdir");
    copy_pnpm_patch_fixture(tempdir.path());

    // Inject `patchedDependencies` into the workspace yaml. The
    // `ghost-in-use` key is NEW (the source `package.json`'s
    // `pnpm.patchedDependencies` does not contain it), and its value
    // matches a patch already declared in the lockfile so the
    // pre-fix migration's `patches_set.contains(...)` filter accepts
    // it.
    append_workspace_yaml_patched_deps(tempdir.path(), &[("ghost-in-use", IN_USE_PATCH_PATH)]);
    commit_fixture_state(tempdir.path());

    let output = run_turbo(tempdir.path(), &["prune", PRUNE_SCOPE]);
    assert!(
        output.status.success(),
        "turbo prune failed: {}",
        combined_output(&output)
    );

    let pruned_path = tempdir.path().join("out/package.json");
    let pruned_text =
        fs::read_to_string(&pruned_path).expect("failed to read pruned out/package.json");
    let pruned: serde_json::Value =
        serde_json::from_str(&pruned_text).expect("pruned out/package.json is not valid JSON");

    let patched = pruned
        .get("pnpm")
        .and_then(|v| v.get("patchedDependencies"))
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();

    assert!(
        !patched.contains_key("ghost-in-use"),
        "pruned out/package.json should not gain `ghost-in-use` from the \
         workspace yaml's patchedDependencies. Pre-fix the prune helper \
         migrates entries from `pnpm-workspace.yaml` into \
         `package.json`'s `pnpm.patchedDependencies`, adding bytes the \
         source file never had. Post-fix the migration must be removed.\n\
         pnpm.patchedDependencies on the pruned package.json: {keys:?}\n\
         --- pruned out/package.json ---\n{pruned_text}",
        keys = patched.keys().collect::<Vec<_>>(),
    );
}

// ---------------------------------------------------------------------------
// Test 3: pruned `pnpm-workspace.yaml`'s `patchedDependencies` is
//   filtered in place.
// ---------------------------------------------------------------------------

/// PRE-FIX: `pnpm-workspace.yaml` is copied to the pruned output
/// verbatim by `prune.copy_file(workspace_config_path, ...)`. Entries
/// that reference patches no longer in the pruned subgraph still
/// appear in `out/pnpm-workspace.yaml`, leaving the pruned monorepo
/// declaring patches that aren't present.
///
/// POST-FIX: after copying the workspace yaml into the pruned output,
/// prune filters its `patchedDependencies` mapping in place to retain
/// only entries whose patch path is in the pruned subgraph's patches
/// set.
///
/// Discriminator: we inject two entries — `ghost-in-use` whose patch
/// path IS in `pruned_patches`, and `ghost-not-used` whose patch path
/// is NOT (it points at a fake patch file no lockfile entry
/// references). Pre-fix both survive in the pruned workspace yaml.
/// Post-fix only `ghost-in-use` survives.
#[test]
fn test_prune_workspace_yaml_patched_dependencies_pruned_in_place() {
    let tempdir = tempfile::tempdir().expect("failed to create tempdir");
    copy_pnpm_patch_fixture(tempdir.path());

    append_workspace_yaml_patched_deps(
        tempdir.path(),
        &[
            ("ghost-in-use", IN_USE_PATCH_PATH),
            ("ghost-not-used", NOT_USED_PATCH_PATH),
        ],
    );
    commit_fixture_state(tempdir.path());

    let output = run_turbo(tempdir.path(), &["prune", PRUNE_SCOPE]);
    assert!(
        output.status.success(),
        "turbo prune failed: {}",
        combined_output(&output)
    );

    let pruned_ws = tempdir.path().join("out/pnpm-workspace.yaml");
    let pruned_yaml = fs::read_to_string(&pruned_ws).expect("out/pnpm-workspace.yaml missing");
    let keys = workspace_yaml_patched_dep_keys(&pruned_yaml);

    assert!(
        keys.iter().any(|k| k == "ghost-in-use"),
        "pruned out/pnpm-workspace.yaml should retain `ghost-in-use` \
         (its patch path is in the pruned subgraph). Got keys: {keys:?}\n\
         --- pruned out/pnpm-workspace.yaml ---\n{pruned_yaml}"
    );

    assert!(
        !keys.iter().any(|k| k == "ghost-not-used"),
        "pruned out/pnpm-workspace.yaml should drop `ghost-not-used` \
         (its patch path is NOT in the pruned subgraph). Pre-fix the \
         workspace yaml is copied verbatim, leaving the pruned output \
         declaring patches that don't exist for this scope. Post-fix \
         the workspace yaml's patchedDependencies must be filtered in \
         place. Got keys: {keys:?}\n\
         --- pruned out/pnpm-workspace.yaml ---\n{pruned_yaml}"
    );
}
