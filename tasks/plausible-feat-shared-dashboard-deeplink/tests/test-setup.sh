#!/usr/bin/env bash
# test-setup.sh — environment bootstrap for the plausible verifier.
#
# This script is `source`d (not exec'd) by the harbor verifier runner so
# that env vars and background services stay alive for `mix test`.
#
# Plausible boots both PostgreSQL (Plausible.Repo, application metadata)
# and ClickHouse (Plausible.IngestRepo / Plausible.ClickhouseRepo,
# event analytics) at application start. The default `mix test` alias
# also runs `clean_clickhouse` after the suite, so both services must
# be reachable before we can run the verifier.

set -e

REPO_DIR="${REPO_DIR:-/repo/plausible}"
cd "$REPO_DIR"

# ---------------------------------------------------------------- #
# 1. PostgreSQL                                                     #
# ---------------------------------------------------------------- #
PG_VERSION="$(pg_lsclusters -h 2>/dev/null | awk '{print $1}' | head -1)"
PG_VERSION="${PG_VERSION:-16}"

if ! pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
    pg_ctlcluster "$PG_VERSION" main start 2>&1 || \
        service postgresql start || true

    for _ in $(seq 1 30); do
        pg_isready -h 127.0.0.1 -p 5432 -q && break
        sleep 1
    done
fi

# Trust local + 127.0.0.1 so DATABASE_URL connects without prompting.
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
if [ -f "$PG_HBA" ] && ! grep -q "host all all 127.0.0.1/32 trust" "$PG_HBA"; then
    echo "local all all trust"             >> "$PG_HBA"
    echo "host  all all 127.0.0.1/32 trust" >> "$PG_HBA"
    echo "host  all all ::1/128       trust" >> "$PG_HBA"
    pg_ctlcluster "$PG_VERSION" main reload 2>&1 || \
        service postgresql reload || true
fi

# Match plausible's expected role: postgres / postgres.
su - postgres -c "psql -tAc \"ALTER USER postgres WITH PASSWORD 'postgres'\"" \
    >/dev/null 2>&1 || true

# ---------------------------------------------------------------- #
# 2. ClickHouse                                                     #
# ---------------------------------------------------------------- #
if ! curl -fsS "http://127.0.0.1:8123/ping" >/dev/null 2>&1; then
    mkdir -p /var/lib/clickhouse /var/log/clickhouse-server
    # ClickHouse refuses to run as root if /var/lib/clickhouse is owned
    # by the `clickhouse` user (default after apt-get install). We're
    # running everything as root in the container, so reassign data +
    # log ownership before launching the daemon.
    chown -R root:root /var/lib/clickhouse /var/log/clickhouse-server
    # Use nohup + background instead of --daemon: daemon mode can silently
    # fail to fork in some container runtimes (Modal), leaving ClickHouse
    # unreachable. The foreground+nohup approach is more portable.
    nohup clickhouse-server \
        --config-file=/etc/clickhouse-server/config.xml \
        > /var/log/clickhouse-server/startup.log 2>&1 &

    for i in $(seq 1 60); do
        curl -fsS "http://127.0.0.1:8123/ping" >/dev/null 2>&1 && \
            echo "test-setup: ClickHouse ready after ${i}s" && break
        sleep 1
    done
fi

# ---------------------------------------------------------------- #
# 3. Plausible env + migrations                                     #
# ---------------------------------------------------------------- #
# The Dockerfile already exports DATABASE_URL / CLICKHOUSE_DATABASE_URL
# / SECRET_KEY_BASE / etc.; only re-export here defensively.
export MIX_ENV="${MIX_ENV:-test}"

# Postgres metadata DB.
mix ecto.create --quiet 2>&1 | tail -3 || true
mix ecto.migrate          2>&1 | tail -3 || true

# ClickHouse event DB. Plausible.IngestRepo owns the schema.
mix ecto.create --quiet -r Plausible.IngestRepo 2>&1 | tail -3 || true
mix ecto.migrate           -r Plausible.IngestRepo 2>&1 | tail -3 || true

# Sanity check: Plausible.Application starts cleanly.
mix run --no-start -e ':ok' >/dev/null 2>&1 \
    && echo "test-setup: services up, repos migrated" \
    || echo "test-setup: WARNING — application sanity check did not return clean"
