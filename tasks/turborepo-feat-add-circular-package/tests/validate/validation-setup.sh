#!/usr/bin/env bash
# Validation setup: runs after the agent applies its edits. Two jobs:
#   1. Rebuild the `turbo` CLI binary (incremental — the image baked a pre-fix
#      target/debug/turbo, so only touched crates recompile + relink).
#   2. Stage fixture monorepos under /tmp/cycle_fixtures/. Each lockfile holds
#      only workspace-link entries, so `npm install` is fully offline.
# Idempotent: re-running nukes and rebuilds the fixtures; the cargo cache survives.
set -euo pipefail

REPO=/repo/turborepo
TURBO_BIN="$REPO/target/debug/turbo"
FIXTURES=/tmp/cycle_fixtures

export RUSTUP_HOME="${RUSTUP_HOME:-/usr/local/rustup}"
export CARGO_HOME="${CARGO_HOME:-/usr/local/cargo}"
export PATH="$CARGO_HOME/bin:$PATH"

echo "validation-setup: rustc=$(rustc --version 2>&1 | head -1)"
echo "validation-setup: cargo=$(cargo --version 2>&1 | head -1)"

# Step 1: Rebuild turbo with the agent's edits applied (incremental).
echo "validation-setup: rebuilding turbo (incremental)..."
( cd "$REPO" && cargo build -p turbo 2>&1 | tail -5 )
test -x "$TURBO_BIN" || {
    echo "ERROR: turbo binary missing at $TURBO_BIN after cargo build" >&2
    exit 1
}
echo "validation-setup: turbo built at $TURBO_BIN ($("$TURBO_BIN" --version 2>&1 | head -1))"

# Step 2: Stage fixture monorepos under /tmp/cycle_fixtures/.
mkdir -p "$FIXTURES"
rm -rf "$FIXTURES"/three_cycle "$FIXTURES"/two_cycle \
       "$FIXTURES"/multi_cycle "$FIXTURES"/acyclic \
       "$FIXTURES"/acyclic_minimal

# --- Fixture: acyclic ------------------------------------------------------
# Copy the repo's existing basic_monorepo fixture verbatim. It already has
# package.json, package-lock.json, and per-package package.json files for
# my-app + util + another. No npm install needed.
cp -r "$REPO/turborepo-tests/integration/fixtures/basic_monorepo" "$FIXTURES/acyclic"

# --- Fixture: acyclic_minimal (pkg-p → pkg-q, strictly linear, no cycle) ---
mkdir -p "$FIXTURES/acyclic_minimal/packages/pkg-p"
mkdir -p "$FIXTURES/acyclic_minimal/packages/pkg-q"
cat > "$FIXTURES/acyclic_minimal/package.json" <<'EOF'
{
  "name": "monorepo",
  "packageManager": "npm@10.5.0",
  "workspaces": ["packages/*"]
}
EOF
cat > "$FIXTURES/acyclic_minimal/turbo.json" <<'EOF'
{
  "$schema": "https://turborepo.dev/schema.v2.json"
}
EOF
cat > "$FIXTURES/acyclic_minimal/.gitignore" <<'EOF'
node_modules/
.turbo
.npmrc
EOF
cat > "$FIXTURES/acyclic_minimal/package-lock.json" <<'EOF'
{
  "name": "monorepo",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "":            { "name": "monorepo", "workspaces": ["packages/*"] },
    "node_modules/@repo/pkg-p": { "resolved": "packages/pkg-p", "link": true },
    "node_modules/@repo/pkg-q": { "resolved": "packages/pkg-q", "link": true },
    "packages/pkg-p": { "name": "@repo/pkg-p", "dependencies": { "@repo/pkg-q": "*" } },
    "packages/pkg-q": { "name": "@repo/pkg-q" }
  }
}
EOF
cat > "$FIXTURES/acyclic_minimal/packages/pkg-p/package.json" <<'EOF'
{
  "name": "@repo/pkg-p",
  "dependencies": { "@repo/pkg-q": "*" }
}
EOF
cat > "$FIXTURES/acyclic_minimal/packages/pkg-q/package.json" <<'EOF'
{
  "name": "@repo/pkg-q"
}
EOF

# --- Fixture: three_cycle (a → b → c → a, plus pkg-d as a non-participant) -
mkdir -p "$FIXTURES/three_cycle/packages/pkg-a"
mkdir -p "$FIXTURES/three_cycle/packages/pkg-b"
mkdir -p "$FIXTURES/three_cycle/packages/pkg-c"
mkdir -p "$FIXTURES/three_cycle/packages/pkg-d"
cat > "$FIXTURES/three_cycle/package.json" <<'EOF'
{
  "name": "monorepo",
  "packageManager": "npm@10.5.0",
  "workspaces": ["packages/*"]
}
EOF
cat > "$FIXTURES/three_cycle/turbo.json" <<'EOF'
{
  "$schema": "https://turborepo.dev/schema.v2.json"
}
EOF
cat > "$FIXTURES/three_cycle/.gitignore" <<'EOF'
node_modules/
.turbo
.npmrc
EOF
cat > "$FIXTURES/three_cycle/package-lock.json" <<'EOF'
{
  "name": "monorepo",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "":            { "name": "monorepo", "workspaces": ["packages/*"] },
    "node_modules/@repo/pkg-a": { "resolved": "packages/pkg-a", "link": true },
    "node_modules/@repo/pkg-b": { "resolved": "packages/pkg-b", "link": true },
    "node_modules/@repo/pkg-c": { "resolved": "packages/pkg-c", "link": true },
    "node_modules/@repo/pkg-d": { "resolved": "packages/pkg-d", "link": true },
    "packages/pkg-a": { "name": "@repo/pkg-a", "dependencies": { "@repo/pkg-b": "*" } },
    "packages/pkg-b": { "name": "@repo/pkg-b", "dependencies": { "@repo/pkg-c": "*" } },
    "packages/pkg-c": { "name": "@repo/pkg-c", "dependencies": { "@repo/pkg-a": "*" } },
    "packages/pkg-d": { "name": "@repo/pkg-d", "dependencies": { "@repo/pkg-a": "*" } }
  }
}
EOF
cat > "$FIXTURES/three_cycle/packages/pkg-a/package.json" <<'EOF'
{
  "name": "@repo/pkg-a",
  "dependencies": { "@repo/pkg-b": "*" }
}
EOF
cat > "$FIXTURES/three_cycle/packages/pkg-b/package.json" <<'EOF'
{
  "name": "@repo/pkg-b",
  "dependencies": { "@repo/pkg-c": "*" }
}
EOF
cat > "$FIXTURES/three_cycle/packages/pkg-c/package.json" <<'EOF'
{
  "name": "@repo/pkg-c",
  "dependencies": { "@repo/pkg-a": "*" }
}
EOF
cat > "$FIXTURES/three_cycle/packages/pkg-d/package.json" <<'EOF'
{
  "name": "@repo/pkg-d",
  "dependencies": { "@repo/pkg-a": "*" }
}
EOF

# --- Fixture: two_cycle (a → b → a) ----------------------------------------
mkdir -p "$FIXTURES/two_cycle/packages/pkg-a"
mkdir -p "$FIXTURES/two_cycle/packages/pkg-b"
cat > "$FIXTURES/two_cycle/package.json" <<'EOF'
{
  "name": "monorepo",
  "packageManager": "npm@10.5.0",
  "workspaces": ["packages/*"]
}
EOF
cat > "$FIXTURES/two_cycle/turbo.json" <<'EOF'
{
  "$schema": "https://turborepo.dev/schema.v2.json"
}
EOF
cat > "$FIXTURES/two_cycle/.gitignore" <<'EOF'
node_modules/
.turbo
.npmrc
EOF
cat > "$FIXTURES/two_cycle/package-lock.json" <<'EOF'
{
  "name": "monorepo",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "":            { "name": "monorepo", "workspaces": ["packages/*"] },
    "node_modules/@repo/pkg-a": { "resolved": "packages/pkg-a", "link": true },
    "node_modules/@repo/pkg-b": { "resolved": "packages/pkg-b", "link": true },
    "packages/pkg-a": { "name": "@repo/pkg-a", "dependencies": { "@repo/pkg-b": "*" } },
    "packages/pkg-b": { "name": "@repo/pkg-b", "dependencies": { "@repo/pkg-a": "*" } }
  }
}
EOF
cat > "$FIXTURES/two_cycle/packages/pkg-a/package.json" <<'EOF'
{
  "name": "@repo/pkg-a",
  "dependencies": { "@repo/pkg-b": "*" }
}
EOF
cat > "$FIXTURES/two_cycle/packages/pkg-b/package.json" <<'EOF'
{
  "name": "@repo/pkg-b",
  "dependencies": { "@repo/pkg-a": "*" }
}
EOF

# --- Fixture: multi_cycle (two disjoint 2-cycles: a↔b, x↔y) ----------------
mkdir -p "$FIXTURES/multi_cycle/packages/pkg-a"
mkdir -p "$FIXTURES/multi_cycle/packages/pkg-b"
mkdir -p "$FIXTURES/multi_cycle/packages/pkg-x"
mkdir -p "$FIXTURES/multi_cycle/packages/pkg-y"
cat > "$FIXTURES/multi_cycle/package.json" <<'EOF'
{
  "name": "monorepo",
  "packageManager": "npm@10.5.0",
  "workspaces": ["packages/*"]
}
EOF
cat > "$FIXTURES/multi_cycle/turbo.json" <<'EOF'
{
  "$schema": "https://turborepo.dev/schema.v2.json"
}
EOF
cat > "$FIXTURES/multi_cycle/.gitignore" <<'EOF'
node_modules/
.turbo
.npmrc
EOF
cat > "$FIXTURES/multi_cycle/package-lock.json" <<'EOF'
{
  "name": "monorepo",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "":            { "name": "monorepo", "workspaces": ["packages/*"] },
    "node_modules/@repo/pkg-a": { "resolved": "packages/pkg-a", "link": true },
    "node_modules/@repo/pkg-b": { "resolved": "packages/pkg-b", "link": true },
    "node_modules/@repo/pkg-x": { "resolved": "packages/pkg-x", "link": true },
    "node_modules/@repo/pkg-y": { "resolved": "packages/pkg-y", "link": true },
    "packages/pkg-a": { "name": "@repo/pkg-a", "dependencies": { "@repo/pkg-b": "*" } },
    "packages/pkg-b": { "name": "@repo/pkg-b", "dependencies": { "@repo/pkg-a": "*" } },
    "packages/pkg-x": { "name": "@repo/pkg-x", "dependencies": { "@repo/pkg-y": "*" } },
    "packages/pkg-y": { "name": "@repo/pkg-y", "dependencies": { "@repo/pkg-x": "*" } }
  }
}
EOF
cat > "$FIXTURES/multi_cycle/packages/pkg-a/package.json" <<'EOF'
{
  "name": "@repo/pkg-a",
  "dependencies": { "@repo/pkg-b": "*" }
}
EOF
cat > "$FIXTURES/multi_cycle/packages/pkg-b/package.json" <<'EOF'
{
  "name": "@repo/pkg-b",
  "dependencies": { "@repo/pkg-a": "*" }
}
EOF
cat > "$FIXTURES/multi_cycle/packages/pkg-x/package.json" <<'EOF'
{
  "name": "@repo/pkg-x",
  "dependencies": { "@repo/pkg-y": "*" }
}
EOF
cat > "$FIXTURES/multi_cycle/packages/pkg-y/package.json" <<'EOF'
{
  "name": "@repo/pkg-y",
  "dependencies": { "@repo/pkg-x": "*" }
}
EOF

# Step 3: Initialise each fixture as a git repo + run npm install offline.
# Turbo's package-graph builder requires both a git history and an installed
# node_modules tree to recognise the workspace. The lockfile only contains
# workspace-link entries, so npm install is fully offline.
for d in "$FIXTURES"/three_cycle "$FIXTURES"/two_cycle \
         "$FIXTURES"/multi_cycle "$FIXTURES"/acyclic \
         "$FIXTURES"/acyclic_minimal; do
    test -d "$d" || continue
    (
        cd "$d"
        # If a stale .git from a previous run survives, blow it away —
        # idempotent.
        rm -rf .git
        git init -q --initial-branch=main
        git config user.email "validate@senior-swe-bench"
        git config user.name "validate"
        git add .
        git commit -q -m "initial fixture" --no-gpg-sign
        # offline install: lockfile is workspace-link-only, no fetches
        # happen. We still tolerate failures because the basic_monorepo
        # copy may already have node_modules baked in from prior runs.
        npm install --silent --offline 2>&1 | tail -3 || true
    )
done

# Step 4: Sanity-check that turbo runs at all in a fixture. If `turbo
# --version` itself fails, every story would fail with the same opaque error;
# better to surface it here.
( cd "$FIXTURES/acyclic" && "$TURBO_BIN" --version >/dev/null )

echo "validation-setup: done. Fixtures staged under $FIXTURES/{three_cycle,two_cycle,multi_cycle,acyclic,acyclic_minimal}"
ls "$FIXTURES"
