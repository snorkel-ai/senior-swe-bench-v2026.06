#!/usr/bin/env bash
# Boot Postgres + ClickHouse and export Plausible test env vars before the
# verifier runs `mix test`. Must be idempotent across re-runs.

set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/plausible}"
PG_BIN="/usr/lib/postgresql/18/bin"
PG_VERSION=18
PG_CLUSTER=main
PG_DATA="/var/lib/postgresql/${PG_VERSION}/${PG_CLUSTER}"
PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/${PG_CLUSTER}"
PG_LOG="/var/log/postgresql/postgresql-${PG_VERSION}-${PG_CLUSTER}.log"
CH_LOG="/var/log/clickhouse-server/clickhouse-server.log"

cd "$REPO_DIR"

export PATH="${PG_BIN}:${PATH}"

# PostgreSQL
start_postgres() {
    if pg_isready -h 127.0.0.1 -p 5432 -q; then
        echo "[test-setup] postgres already accepting connections"
        return 0
    fi

    install -d -m 755 -o postgres -g postgres "$(dirname "${PG_LOG}")"

    # Trust local md5 connections so DATABASE_URL (password 'postgres' over
    # 127.0.0.1) works; the cluster is initialised at apt-install time.
    if ! grep -q '^host.*127.0.0.1/32.*md5' "${PG_CONF_DIR}/pg_hba.conf"; then
        echo "host all all 127.0.0.1/32 md5" \
            | sudo tee -a "${PG_CONF_DIR}/pg_hba.conf" >/dev/null
    fi

    echo "[test-setup] starting postgres (cluster ${PG_VERSION}/${PG_CLUSTER})"
    # Debian splits the data dir from the config dir, so pass the config
    # file locations explicitly via -o.
    sudo -u postgres "${PG_BIN}/pg_ctl" \
        -D "${PG_DATA}" \
        -l "${PG_LOG}" \
        -o "-c config_file=${PG_CONF_DIR}/postgresql.conf -c hba_file=${PG_CONF_DIR}/pg_hba.conf -c ident_file=${PG_CONF_DIR}/pg_ident.conf -c listen_addresses=127.0.0.1 -c port=5432 -c unix_socket_directories=/tmp" \
        start

    for i in $(seq 1 30); do
        if pg_isready -h 127.0.0.1 -p 5432 -q; then
            break
        fi
        sleep 1
        if [ "$i" = "30" ]; then
            echo "[test-setup] postgres failed to start; tail of log:" >&2
            tail -50 "${PG_LOG}" >&2 || true
            return 1
        fi
    done

    # Match the password in DATABASE_URL (config/.env.test).
    sudo -u postgres "${PG_BIN}/psql" -h /tmp -p 5432 -c \
        "ALTER USER postgres WITH PASSWORD 'postgres';" >/dev/null
}

start_postgres

# ClickHouse
start_clickhouse() {
    if clickhouse-client --query "SELECT 1" >/dev/null 2>&1; then
        echo "[test-setup] clickhouse already accepting connections"
        return 0
    fi

    install -d -m 755 -o clickhouse -g clickhouse \
        /var/log/clickhouse-server /var/lib/clickhouse \
        /var/run/clickhouse-server

    echo "[test-setup] starting clickhouse-server (background)"
    sudo -u clickhouse clickhouse-server --daemon \
        --config-file=/etc/clickhouse-server/config.xml \
        --pid-file=/var/run/clickhouse-server/clickhouse-server.pid

    for i in $(seq 1 30); do
        if clickhouse-client --query "SELECT 1" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        if [ "$i" = "30" ]; then
            echo "[test-setup] clickhouse failed to start; tail of log:" >&2
            tail -50 "${CH_LOG}" >&2 || true
            return 1
        fi
    done
}

start_clickhouse

# Plausible test environment.
# Mirrors config/.env.test; the secret-looking values are dummy strings
# copied verbatim from the upstream test env file.

export DATABASE_URL="${DATABASE_URL:-postgres://postgres:postgres@127.0.0.1:5432/plausible_test}"
export CLICKHOUSE_DATABASE_URL="${CLICKHOUSE_DATABASE_URL:-http://127.0.0.1:8123/plausible_test}"
export SECRET_KEY_BASE="${SECRET_KEY_BASE:-/njrhntbycvastyvtk1zycwfm981vpo/0xrvwjjvemdakc/vsvbrevlwsc6u8rcg}"
export TOTP_VAULT_KEY="${TOTP_VAULT_KEY:-1Jah1HEOnCEnmBE+4/OgbJRraJIppPmYCNbZoFJboZs=}"
export BASE_URL="${BASE_URL:-http://localhost:8000}"
export MAILER_ADAPTER="${MAILER_ADAPTER:-Bamboo.TestAdapter}"
export ENABLE_EMAIL_VERIFICATION="${ENABLE_EMAIL_VERIFICATION:-true}"
export SELFHOST="${SELFHOST:-false}"
export ENVIRONMENT="${ENVIRONMENT:-test}"
export CRON_ENABLED="${CRON_ENABLED:-false}"
export LOG_LEVEL="${LOG_LEVEL:-warning}"
export HCAPTCHA_SITEKEY="${HCAPTCHA_SITEKEY:-test}"
export HCAPTCHA_SECRET="${HCAPTCHA_SECRET:-scottiger}"
export IP_GEOLOCATION_DB="${IP_GEOLOCATION_DB:-test/priv/GeoLite2-City-Test.mmdb}"
export SITE_DEFAULT_INGEST_THRESHOLD="${SITE_DEFAULT_INGEST_THRESHOLD:-1000000}"
export GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-fake_client_id}"
export GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-fake_client_secret}"
export HELP_SCOUT_APP_ID="${HELP_SCOUT_APP_ID:-fake_app_id}"
export HELP_SCOUT_APP_SECRET="${HELP_SCOUT_APP_SECRET:-fake_app_secret}"
export HELP_SCOUT_SIGNATURE_KEY="${HELP_SCOUT_SIGNATURE_KEY:-fake_signature_key}"
export HELP_SCOUT_VAULT_KEY="${HELP_SCOUT_VAULT_KEY:-ym9ZQg0KPNGCH3C2eD5y6KpL0tFzUqAhwxQO6uEv/ZM=}"
export S3_DISABLED="${S3_DISABLED:-false}"
export S3_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID:-minioadmin}"
export S3_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY:-minioadmin}"
export S3_REGION="${S3_REGION:-us-east-1}"
export S3_ENDPOINT="${S3_ENDPOINT:-http://localhost:10000}"
export S3_EXPORTS_BUCKET="${S3_EXPORTS_BUCKET:-test-exports}"
export S3_IMPORTS_BUCKET="${S3_IMPORTS_BUCKET:-test-imports}"
export VERIFICATION_ENABLED="${VERIFICATION_ENABLED:-true}"
export MIX_ENV="${MIX_ENV:-test}"

echo "[test-setup] ready: $REPO_DIR"
echo "[test-setup]   elixir=$(elixir --version 2>/dev/null | tail -1)"
echo "[test-setup]   postgres=$(pg_isready -h 127.0.0.1 -p 5432 -q && echo 'up' || echo 'down')"
echo "[test-setup]   clickhouse=$(clickhouse-client --query 'SELECT version()' 2>/dev/null || echo 'down')"
