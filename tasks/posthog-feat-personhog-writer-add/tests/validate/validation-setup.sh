#!/usr/bin/env bash
# Validation setup for posthog-feat-personhog-writer-add.
#
# Sourced (not exec'd) by the validation runner, so exported env vars and
# background services persist for the validation agent and its story tests.
#
# Brings up the framework mechanics the black-box stories need:
#   1. Postgres (already migrated in the image; just start the daemon).
#   2. A single-node Apache Kafka broker (KRaft) on localhost:9092 + the
#      person-state topic.
#   3. The pre-existing PersonHogReplica gRPC read API (the personhog-replica
#      service) on 127.0.0.1:50051, reading the canonical persons store — this
#      is how the stories read persisted person state back.
#   4. Compiled python gRPC stubs for that read API, on the stories' path.
#
# It deliberately does NOT launch the agent's personhog-writer and does NOT
# export the writer's target-table / flush-interval configuration: those env
# var names are the agent's own choice, so a story discovers them from the
# writer's source and launches the writer via the harness's
# ensure_writer_running(...). On the nop / pre-fix tree the writer crate does
# not exist; ensure_writer_running fails fast and the fail_to_pass stories
# correctly observe no persisted state.

set -u

REPO_DIR="${REPO_DIR:-/repo/posthog}"
RUST_DIR="${REPO_DIR}/rust"
KAFKA_HOME="${KAFKA_HOME:-/opt/kafka}"
GRPC_STUBS="${PERSONHOG_GRPC_STUBS:-/tmp/personhog_grpc_stubs}"

# ── Conventional shared env (from the workspace's shared crates / safe
#    defaults) — NOT the agent-chosen target-table / flush knobs. ───────────
export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog_persons"
export PERSONS_DATABASE_URL="$DATABASE_URL"
export KAFKA_HOSTS="localhost:9092"
export KAFKA_TOPIC="personhog_updates"
export KAFKA_CONSUMER_GROUP="personhog-writer"
export METRICS_PORT="9103"
export REPLICA_GRPC_ADDR="127.0.0.1:50051"
export PERSONHOG_GRPC_STUBS="$GRPC_STUBS"
export PATH="/usr/local/cargo/bin:${PATH}"

# ── 1. Postgres ─────────────────────────────────────────────────────────────
echo "=== Starting Postgres ==="
service postgresql start >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
    pg_isready -h localhost >/dev/null 2>&1 && break
    sleep 0.5
done
psql "$DATABASE_URL" -tAc \
    "SELECT 1 FROM information_schema.tables WHERE table_name='posthog_person'" \
    2>/dev/null | grep -q 1 \
    || echo "[setup] WARNING: posthog_person not found — image migration issue"

# ── 2. Kafka (KRaft, single node) ───────────────────────────────────────────
echo "=== Starting Kafka (KRaft) ==="
if ! "${KAFKA_HOME}/bin/kafka-topics.sh" --bootstrap-server localhost:9092 --list >/dev/null 2>&1; then
    KRAFT_CFG="${KAFKA_HOME}/config/kraft/server.properties"
    CLUSTER_ID="$("${KAFKA_HOME}/bin/kafka-storage.sh" random-uuid)"
    "${KAFKA_HOME}/bin/kafka-storage.sh" format -t "$CLUSTER_ID" -c "$KRAFT_CFG" --ignore-formatted >/dev/null 2>&1 || true
    nohup "${KAFKA_HOME}/bin/kafka-server-start.sh" "$KRAFT_CFG" >/tmp/kafka.log 2>&1 &
fi
for _ in $(seq 1 90); do
    "${KAFKA_HOME}/bin/kafka-topics.sh" --bootstrap-server localhost:9092 --list >/dev/null 2>&1 && break
    sleep 1
done
"${KAFKA_HOME}/bin/kafka-topics.sh" --bootstrap-server localhost:9092 \
    --create --if-not-exists --topic personhog_updates --partitions 1 --replication-factor 1 >/dev/null 2>&1 || true
"${KAFKA_HOME}/bin/kafka-topics.sh" --bootstrap-server localhost:9092 \
    --create --if-not-exists --topic client_iwarnings_ingestion --partitions 1 --replication-factor 1 >/dev/null 2>&1 || true
echo "[setup] kafka topics: $("${KAFKA_HOME}/bin/kafka-topics.sh" --bootstrap-server localhost:9092 --list 2>/dev/null | tr '\n' ' ')"

# ── 3. Compile python gRPC stubs for the PersonHogReplica read API ──────────
echo "=== Compiling PersonHogReplica gRPC stubs ==="
rm -rf "$GRPC_STUBS"; mkdir -p "$GRPC_STUBS"
cd "$REPO_DIR" || true
if python3 -m grpc_tools.protoc -I proto \
        --python_out="$GRPC_STUBS" --grpc_python_out="$GRPC_STUBS" \
        $(find proto/personhog -name '*.proto') >/tmp/grpc_stubs.log 2>&1; then
    # Namespace packages (PEP 420) — no __init__.py needed; ensure dirs exist.
    echo "[setup] gRPC stubs compiled to $GRPC_STUBS"
else
    echo "[setup] WARNING: gRPC stub compilation failed:"; tail -20 /tmp/grpc_stubs.log 2>/dev/null || true
fi

# ── 4. Launch the PersonHogReplica read API (reference infra, reads the
#       canonical persons store) ────────────────────────────────────────────
echo "=== Starting personhog-replica (gRPC read API) ==="
cd "$RUST_DIR" || true
# PRIMARY_DATABASE_URL points the replica at the migrated persons DB; the
# default GRPC_ADDRESS is 127.0.0.1:50051.
if PRIMARY_DATABASE_URL="$DATABASE_URL" GRPC_ADDRESS="$REPLICA_GRPC_ADDR" \
        cargo build -p personhog-replica >/tmp/personhog_replica_build.log 2>&1; then
    # The replica binds its own metrics port. Give it one distinct from the
    # writer's METRICS_PORT (9103) — otherwise the replica (which also reads
    # METRICS_PORT) grabs 9103 first and the writer dies with AddrInUse.
    PRIMARY_DATABASE_URL="$DATABASE_URL" REPLICA_DATABASE_URL="$DATABASE_URL" \
        GRPC_ADDRESS="$REPLICA_GRPC_ADDR" METRICS_PORT="9100" \
        nohup cargo run -p personhog-replica >/tmp/personhog_replica.log 2>&1 &
    for _ in $(seq 1 90); do
        if (echo > "/dev/tcp/127.0.0.1/50051") >/dev/null 2>&1 \
            || grep -qiE "grpc|listening|serving" /tmp/personhog_replica.log 2>/dev/null; then
            echo "[setup] personhog-replica is up on $REPLICA_GRPC_ADDR"
            break
        fi
        sleep 1
    done
else
    echo "[setup] WARNING: personhog-replica build FAILED:"; tail -20 /tmp/personhog_replica_build.log 2>/dev/null || true
fi

# ── 5. Warm-build the agent's writer (best-effort; the harness launches it
#       with the agent-discovered config). On nop the crate is absent. ───────
echo "=== Warm-building personhog-writer (launched later by the stories) ==="
cargo build -p personhog-writer >/tmp/personhog_writer_build.log 2>&1 \
    && echo "[setup] personhog-writer builds" \
    || { echo "[setup] note: personhog-writer build failed (expected on the nop tree):"; tail -10 /tmp/personhog_writer_build.log 2>/dev/null || true; }

# ── 6. Smoke-check the read path is importable ──────────────────────────────
PYTHONPATH="$GRPC_STUBS:${PYTHONPATH:-}" python3 -c \
    "import grpc; from personhog.replica.v1 import replica_pb2_grpc; from personhog.types.v1 import person_pb2; print('[setup] gRPC read client import OK')" \
    2>/tmp/grpc_import.log || { echo "[setup] WARNING: gRPC client import failed:"; tail -10 /tmp/grpc_import.log 2>/dev/null || true; }

# Harvest the replica + build logs into the harvested verifier dir for diagnosis.
mkdir -p /logs/verifier 2>/dev/null || true
cp /tmp/personhog_replica.log /logs/verifier/personhog_replica.log 2>/dev/null || true
cp /tmp/personhog_replica_build.log /logs/verifier/personhog_replica_build.log 2>/dev/null || true
cp /tmp/personhog_writer_build.log /logs/verifier/personhog_writer_build.log 2>/dev/null || true

cd "$REPO_DIR" || true
echo "=== Validation setup complete ==="
return 0 2>/dev/null || true
