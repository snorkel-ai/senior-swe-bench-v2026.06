#!/usr/bin/env bash
# validation-setup.sh — bring up the validation runtime for
# plausible-feat-shared-dashboard-deeplink.
#
# Sourced (not exec'd) by the validation runner so background
# services and env vars persist for the validation agent.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/plausible}"
cd "$REPO_DIR"

# ---------------------------------------------------------------- #
# 1. PostgreSQL                                                     #
# ---------------------------------------------------------------- #
PG_VERSION="$(pg_lsclusters -h 2>/dev/null | awk '{print $1}' | head -1 || true)"
PG_VERSION="${PG_VERSION:-16}"

if ! pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
    echo "[validation-setup] starting PostgreSQL ${PG_VERSION}"
    pg_ctlcluster "$PG_VERSION" main start 2>&1 | tail -3 || \
        service postgresql start 2>&1 | tail -3 || true

    for _ in $(seq 1 30); do
        pg_isready -h 127.0.0.1 -p 5432 -q && break
        sleep 1
    done
fi

# Trust local + 127.0.0.1 so Plausible's DATABASE_URL connects
# without prompting for credentials.
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
if [ -f "$PG_HBA" ] && ! grep -q "host all all 127.0.0.1/32 trust" "$PG_HBA"; then
    echo "local all all trust"             >> "$PG_HBA"
    echo "host  all all 127.0.0.1/32 trust" >> "$PG_HBA"
    echo "host  all all ::1/128       trust" >> "$PG_HBA"
    pg_ctlcluster "$PG_VERSION" main reload 2>&1 | tail -1 || \
        service postgresql reload 2>&1 | tail -1 || true
fi

# Ensure the `postgres` superuser has the password the .env files
# expect (idempotent — ALTER USER is a no-op if it already matches).
su - postgres -c "psql -tAc \"ALTER USER postgres WITH PASSWORD 'postgres'\"" \
    >/dev/null 2>&1 || true

# ---------------------------------------------------------------- #
# 2. ClickHouse                                                     #
# ---------------------------------------------------------------- #
if ! curl -fsS "http://127.0.0.1:8123/ping" >/dev/null 2>&1; then
    echo "[validation-setup] starting ClickHouse"
    mkdir -p /var/lib/clickhouse /var/log/clickhouse-server
    # ClickHouse refuses to run as root if /var/lib/clickhouse is owned
    # by the `clickhouse` user (default after apt-get install). We're
    # running everything as root in the container, so reassign data
    # + log ownership before launching the daemon.
    chown -R root:root /var/lib/clickhouse /var/log/clickhouse-server
    # Use nohup + background instead of --daemon: daemon mode can silently
    # fail to fork in some container runtimes (Modal), leaving ClickHouse
    # unreachable. The foreground+nohup approach is more portable.
    nohup clickhouse-server \
        --config-file=/etc/clickhouse-server/config.xml \
        > /var/log/clickhouse-server/startup.log 2>&1 &

    for i in $(seq 1 60); do
        curl -fsS "http://127.0.0.1:8123/ping" >/dev/null 2>&1 && \
            echo "[validation-setup] ClickHouse ready after ${i}s" && break
        sleep 1
    done
fi

if ! curl -fsS "http://127.0.0.1:8123/ping" >/dev/null 2>&1; then
    echo "[validation-setup] WARNING: ClickHouse did not become ready within 60s"
    echo "[validation-setup] last 10 lines of startup log:"
    tail -10 /var/log/clickhouse-server/startup.log 2>/dev/null || echo "(no log)"
fi

# ---------------------------------------------------------------- #
# 3. Plausible :e2e_test repos                                      #
# ---------------------------------------------------------------- #
# config/.env.e2e_test points at plausible_e2e on both Postgres and
# ClickHouse. We unset DATABASE_URL/CLICKHOUSE_DATABASE_URL exported
# by the Dockerfile so the e2e_test config takes effect.
unset DATABASE_URL CLICKHOUSE_DATABASE_URL ENVIRONMENT

# `mix ecto.create` with --quiet swallows the "database already exists"
# notice; chain ecto.migrate after to bring schemas current.
echo "[validation-setup] migrating :e2e_test repos"
MIX_ENV=e2e_test mix ecto.create --quiet 2>&1 | tail -3 || true
MIX_ENV=e2e_test mix ecto.migrate          2>&1 | tail -3 || true

MIX_ENV=e2e_test mix ecto.create --quiet -r Plausible.IngestRepo \
    2>&1 | tail -3 || true
MIX_ENV=e2e_test mix ecto.migrate          -r Plausible.IngestRepo \
    2>&1 | tail -3 || true

# ---------------------------------------------------------------- #
# 4. Baseline seed                                                  #
# ---------------------------------------------------------------- #
# Drop the seed script next to the repo root so `mix run` resolves
# the path consistently from the repo cwd.
SEED_SCRIPT="${REPO_DIR}/senior_swe_bench_seed.exs"
if [ ! -f "$SEED_SCRIPT" ]; then
    cp "/tests/validate/seed_shared_link.exs" "$SEED_SCRIPT"
fi

echo "[validation-setup] seeding baseline shared link"
MIX_ENV=e2e_test mix run --no-start "$SEED_SCRIPT" 2>&1 | tail -3 || true

# ---------------------------------------------------------------- #
# 5. Phoenix server                                                 #
# ---------------------------------------------------------------- #
PHX_LOG="${PHX_LOG:-/var/log/plausible_phx.log}"
mkdir -p "$(dirname "$PHX_LOG")"

if ! curl -fsS "http://127.0.0.1:8000/api/system/health/ready" \
        >/dev/null 2>&1; then
    echo "[validation-setup] launching mix phx.server (e2e_test) on :8000"
    nohup env MIX_ENV=e2e_test PORT=8000 \
        mix phx.server > "$PHX_LOG" 2>&1 &
    echo $! > /tmp/plausible_phx.pid
fi

# ---------------------------------------------------------------- #
# 6. Wait for readiness                                             #
# ---------------------------------------------------------------- #
echo "[validation-setup] waiting for Phoenix readiness probe"
for attempt in $(seq 1 90); do
    if curl -fsS "http://127.0.0.1:8000/api/system/health/ready" \
            >/dev/null 2>&1; then
        echo "[validation-setup] Phoenix is up after ${attempt}s"
        break
    fi
    sleep 1
done

if ! curl -fsS "http://127.0.0.1:8000/api/system/health/ready" \
        >/dev/null 2>&1; then
    echo "[validation-setup] WARNING: Phoenix did not become ready within 90s"
    echo "[validation-setup] last 40 lines of $PHX_LOG:"
    tail -40 "$PHX_LOG" 2>/dev/null || echo "(no log)"
fi

echo "[validation-setup] done."
