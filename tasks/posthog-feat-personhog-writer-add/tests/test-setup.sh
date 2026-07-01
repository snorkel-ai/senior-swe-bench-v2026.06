#!/usr/bin/env bash
# Sourced (not exec'd) by the verifier framework; env exports must persist.
# Idempotent. The DB is the only stateful dependency this bootstrap starts;
# a Kafka broker for end-to-end validation stories is provisioned by the
# validation stage (the base image ships no broker).
set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/posthog}"
RUST_DIR="${REPO_DIR}/rust"

# ── Postgres (idempotent across re-sourcing) ─────────────────────────────
service postgresql start >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
    if pg_isready -h localhost >/dev/null 2>&1; then break; fi
    sleep 0.5
done

# ── Env contract (matches bin/start-rust-service personhog-writer) ───────
export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog_persons"
export PERSONS_DATABASE_URL="$DATABASE_URL"
export KAFKA_HOSTS="${KAFKA_HOSTS:-localhost:9092}"
export KAFKA_TOPIC="${KAFKA_TOPIC:-personhog_updates}"
export KAFKA_CONSUMER_GROUP="${KAFKA_CONSUMER_GROUP:-personhog-writer}"
export PG_TARGET_TABLE="${PG_TARGET_TABLE:-personhog_person_tmp}"
export METRICS_PORT="${METRICS_PORT:-9103}"
# Keep cargo's toolchain on PATH for non-login shells.
export PATH="/usr/local/cargo/bin:${PATH}"

# ── Land in the Rust workspace root ──────────────────────────────────────
cd "$RUST_DIR"

# ── Sanity checks — surface env issues at setup time, not mid-test ───────
command -v cargo >/dev/null 2>&1 || { echo "[test-setup] cargo not on PATH"; }
test -f "${RUST_DIR}/Cargo.toml" || { echo "[test-setup] rust workspace Cargo.toml missing"; }
psql "$DATABASE_URL" -tAc \
    "SELECT 1 FROM information_schema.tables WHERE table_name='personhog_person_tmp'" \
    2>/dev/null | grep -q 1 \
    || echo "[test-setup] WARNING: personhog_person_tmp not found — image migration went wrong"

echo "[test-setup] ready: PG up, cwd=$(pwd), cargo=$(cargo --version 2>/dev/null)"
