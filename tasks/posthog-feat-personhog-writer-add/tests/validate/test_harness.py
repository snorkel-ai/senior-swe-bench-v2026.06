"""Validation harness for posthog-feat-personhog-writer-add.

Black-box, implementation-agnostic test infrastructure. The validation
stories drive the agent's `personhog-writer` service through its public
contracts only:

    Kafka topic `personhog_updates`  (Person protobufs in)
              │
              ▼   [ the agent's service — any internal design ]
              │
    persons Postgres  (rows out)
              │
              ▼
    PersonHogReplica gRPC API  (person state read back)

The harness NEVER imports or references the agent's crate, modules, or
types. It:
  * encodes `personhog.types.v1.Person` protobufs by hand (stable wire
    format from proto/personhog/types/v1/person.proto) and produces them
    to Kafka via confluent-kafka,
  * reads persisted person state back through the pre-existing
    `PersonHogReplica` gRPC read API (the same API the rest of the persons
    pipeline uses), and
  * launches the agent's writer on demand via `ensure_writer_running(...)`,
    where the story supplies the configuration it discovered from the
    writer's own source (which env var selects the target store, which env
    var controls the periodic flush interval).

Postgres, Kafka, and the `personhog-replica` read API are brought up by
validation-setup.sh; the writer is NOT — a story launches it after
discovering its config.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid as _uuid

import psycopg2

from confluent_kafka import Producer

# ── Contract (conventional shared env, set by validation-setup.sh) ──────────
# The persons database the writer persists into and the replica reads from.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgres://posthog:posthog@localhost:5432/posthog_persons"
)
KAFKA_HOSTS = os.environ.get("KAFKA_HOSTS", "localhost:9092")
TOPIC = os.environ.get("KAFKA_TOPIC", "personhog_updates")
# The canonical persons table — the table the PersonHogReplica read API serves
# and the store any correct writer persists into. Used here only for test
# setup (cleanup / NULL-version preinsert); reads go through the gRPC API.
PERSONS_TABLE = "posthog_person"
# Where validation-setup.sh launched the replica read API.
REPLICA_GRPC_ADDR = os.environ.get("REPLICA_GRPC_ADDR", "127.0.0.1:50051")
# Compiled PersonHogReplica gRPC python stubs (validation-setup.sh writes them).
GRPC_STUBS_DIR = os.environ.get("PERSONHOG_GRPC_STUBS", "/tmp/personhog_grpc_stubs")
if GRPC_STUBS_DIR not in sys.path:
    sys.path.insert(0, GRPC_STUBS_DIR)

# Person proto created_at is epoch *seconds*; pick a fixed, valid timestamp.
DEFAULT_CREATED_AT = 1700000000

_producer: Producer | None = None

REPO_DIR = os.environ.get("REPO_DIR", "/repo/posthog")
RUST_DIR = f"{REPO_DIR}/rust"


# ── Protobuf encoding (personhog.types.v1.Person) ───────────────────────────
# Field numbers / wire types from proto/personhog/types/v1/person.proto:
#   1 id            int64   (varint)
#   2 uuid          string  (len-delimited)
#   3 team_id       int64   (varint)
#   4 properties    bytes   (len-delimited)
#   5 properties_last_updated_at bytes (len-delimited)
#   6 properties_last_operation  bytes (len-delimited)
#   7 created_at    int64   (varint, epoch seconds)
#   8 version       int64   (varint)
#   9 is_identified bool    (varint)
#  10 is_user_id    optional bool (varint)
#  11 last_seen_at  optional int64 (varint)
def _varint(n: int) -> bytes:
    """Unsigned base-128 varint (values used here are non-negative, < 2**63)."""
    if n < 0:
        n &= (1 << 64) - 1  # two's-complement, 10-byte form
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_field(field: int, raw: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(raw)) + raw


def encode_person(
    person_id: int,
    uuid_str: str,
    team_id: int,
    properties: bytes = b"{}",
    created_at: int = DEFAULT_CREATED_AT,
    version: int = 0,
    is_identified: bool = False,
) -> bytes:
    """Encode a minimal but complete Person message to protobuf wire bytes."""
    buf = bytearray()
    buf += _tag(1, 0) + _varint(person_id)
    buf += _len_field(2, uuid_str.encode("utf-8"))
    buf += _tag(3, 0) + _varint(team_id)
    buf += _len_field(4, properties)
    buf += _tag(7, 0) + _varint(created_at)
    buf += _tag(8, 0) + _varint(version)
    buf += _tag(9, 0) + (b"\x01" if is_identified else b"\x00")
    return bytes(buf)


# ── Kafka producing ─────────────────────────────────────────────────────────
def _get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer(
            {
                "bootstrap.servers": KAFKA_HOSTS,
                "enable.idempotence": False,
                "acks": "all",
            }
        )
    return _producer


def valid_uuid(team_id: int, person_id: int) -> str:
    """Deterministic, valid UUID string for a (team_id, person_id) pair."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"personhog-{team_id}-{person_id}"))


def produce(
    team_id: int,
    person_id: int,
    version: int,
    uuid_str: str | None = None,
    properties=None,
    created_at: int = DEFAULT_CREATED_AT,
    is_identified: bool = False,
) -> None:
    """Produce one Person to the person-state topic.

    `properties` may be a dict (json-encoded), a str, raw bytes, or None
    (defaults to an empty JSON object). `uuid_str` defaults to a valid,
    deterministic UUID; pass an explicit string (e.g. an invalid one) to
    exercise the poison-record path.
    """
    if uuid_str is None:
        uuid_str = valid_uuid(team_id, person_id)

    if properties is None:
        props_bytes = b"{}"
    elif isinstance(properties, dict):
        props_bytes = json.dumps(properties, separators=(",", ":")).encode("utf-8")
    elif isinstance(properties, str):
        props_bytes = properties.encode("utf-8")
    else:
        props_bytes = bytes(properties)

    payload = encode_person(
        person_id=person_id,
        uuid_str=uuid_str,
        team_id=team_id,
        properties=props_bytes,
        created_at=created_at,
        version=version,
        is_identified=is_identified,
    )
    p = _get_producer()
    p.produce(TOPIC, key=f"{team_id}:{person_id}".encode("utf-8"), value=payload)
    p.flush(15)


def oversized_props(email_value: str, big_key: str = "zzz_big", size: int = 700_000) -> dict:
    """A properties dict whose raw JSON exceeds the persons table's size
    constraint (~640KB) but is trimmable: a small *protected* `email` key plus
    one large *non-protected* custom key.
    """
    return {"email": email_value, big_key: "x" * size}


def untrimmable_props(protected_key: str = "email", size: int = 700_000) -> dict:
    """A properties dict that exceeds the size constraint using only a single
    *protected* key, so it cannot be brought under the limit.
    """
    return {protected_key: "y" * size}


# ── Launching the agent's writer (story-driven, after config discovery) ──────
_WRITER_LOG = "/tmp/personhog_writer.log"
_WRITER_LOCK = "/tmp/personhog_writer.launch.lock"
_WRITER_FAIL = "/tmp/personhog_writer.failed"
_METRICS_PORT = os.environ.get("METRICS_PORT", "9103")


def _writer_healthy() -> bool:
    """True if the writer's metrics/health endpoint answers, or its log shows
    the metrics server came up."""
    try:
        with urllib.request.urlopen(
            f"http://localhost:{_METRICS_PORT}/_readiness", timeout=2
        ) as r:
            if r.status < 500:
                return True
    except Exception:
        pass
    try:
        with open(_WRITER_LOG, "r") as fh:
            return "metrics server listening" in fh.read().lower()
    except Exception:
        return False


def ensure_writer_running(env_overrides: dict | None = None, readiness_timeout: float = 90.0) -> None:
    """Idempotently launch the agent's `personhog-writer` daemon.

    `env_overrides` is the configuration the STORY discovered by reading the
    writer's own source: at minimum the env var that points the writer at the
    persons store the read API serves, and the env var (with the unit it
    expects) that makes the periodic flush interval short enough for rows to
    land within the poll windows. Pass them as a dict of {ENV_NAME: value}.

    Safe to call from every story/case: the first caller launches the daemon
    detached (it persists across story processes); later callers detect it is
    already up and return. On the nop tree the crate does not build, so the
    launch fails fast, a failure marker is written, and subsequent calls do not
    retry — the stories then observe no persisted state and fail as intended.
    """
    if _writer_healthy():
        return
    os.makedirs(GRPC_STUBS_DIR, exist_ok=True)
    lock_fh = open(_WRITER_LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        # Re-check under the lock — another story process may have launched it.
        if _writer_healthy():
            return
        if os.path.exists(_WRITER_FAIL):
            return
        env = dict(os.environ)
        for k, v in (env_overrides or {}).items():
            env[str(k)] = str(v)
        env.setdefault("PATH", "/usr/local/cargo/bin:" + os.environ.get("PATH", ""))
        log = open(_WRITER_LOG, "w")
        proc = subprocess.Popen(
            ["cargo", "run", "-p", "personhog-writer"],
            cwd=RUST_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        ready = False
        deadline = time.time() + readiness_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                break  # the writer process exited (build/runtime failure)
            if _writer_healthy():
                ready = True
                break
            time.sleep(1)
        # Harvest the writer's runtime log into the harvested verifier dir for
        # diagnosis (and record whether the process is still alive).
        try:
            import shutil
            shutil.copyfile(_WRITER_LOG, "/logs/verifier/personhog_writer_launch.log")
            with open("/logs/verifier/personhog_writer_launch.log", "a") as fh:
                fh.write(f"\n[ensure_writer_running] ready={ready} proc_alive={proc.poll() is None}\n")
        except Exception:
            pass
        if ready:
            # Let the Kafka consumer fully join the group and position itself
            # before the story produces — otherwise a record produced before the
            # consumer is assigned can be missed.
            time.sleep(12)
            return
        # Did not come up (e.g. nop tree: crate absent / build failure) — mark so
        # later stories do not retry the launch.
        open(_WRITER_FAIL, "w").close()
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


# ── Direct-SQL test setup (cleanup + NULL-version preinsert) ────────────────
# These touch the canonical persons table directly only to SET UP a case; all
# verification of persisted state goes through the gRPC read API below.
def _connect():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def cleanup_team(team_id: int) -> None:
    """Remove any persisted rows for a team so each case starts clean."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {PERSONS_TABLE} WHERE team_id = %s", (team_id,))


def preinsert_null_version(team_id: int, person_id: int) -> None:
    """Insert a person row directly with a NULL stored version (no service),
    to set up the 'update over an unset version' case."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {PERSONS_TABLE}
                    (id, team_id, uuid, properties, created_at, version, is_identified)
                VALUES (%s, %s, %s, '{{}}'::jsonb, to_timestamp(%s), NULL, false)
                ON CONFLICT (team_id, id) DO UPDATE SET version = NULL""",
            (person_id, team_id, valid_uuid(team_id, person_id), DEFAULT_CREATED_AT),
        )


# ── Reads through the PersonHogReplica gRPC API ─────────────────────────────
_grpc_stub = None


def _stub():
    global _grpc_stub
    if _grpc_stub is None:
        import grpc
        from personhog.replica.v1 import replica_pb2_grpc  # compiled by setup

        channel = grpc.insecure_channel(REPLICA_GRPC_ADDR)
        _grpc_stub = replica_pb2_grpc.PersonHogReplicaStub(channel)
    return _grpc_stub


def get_person(team_id: int, person_id: int):
    """Read a person's persisted state via the gRPC read API, addressing it by
    (team_id, id). Returns a dict {version, properties, is_identified, uuid,
    created_at} or None if no such person is persisted.

    read_options is left unset (eventual consistency), which the replica
    serves; the validation database is the replica's own database, so reads
    reflect committed writes immediately.
    """
    from personhog.types.v1 import person_pb2

    req = person_pb2.GetPersonRequest(team_id=team_id, person_id=person_id)
    resp = _stub().GetPerson(req, timeout=10)
    if not resp.HasField("person"):
        return None
    p = resp.person
    try:
        props = json.loads(p.properties.decode("utf-8")) if p.properties else {}
    except Exception:
        props = None
    return {
        "id": p.id,
        "uuid": p.uuid,
        "team_id": p.team_id,
        "version": p.version,
        "is_identified": p.is_identified,
        "properties": props,
    }


def get_version(team_id: int, person_id: int):
    """The persisted `version` for a person, or None if not persisted."""
    person = get_person(team_id, person_id)
    return None if person is None else person["version"]


def get_properties(team_id: int, person_id: int):
    """The persisted properties dict for a person, or None if not persisted."""
    person = get_person(team_id, person_id)
    return None if person is None else person["properties"]


def wait_for_person(team_id: int, person_id: int, timeout: float = 30.0) -> bool:
    """Poll the read API until the person is persisted. True if it appeared."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_person(team_id, person_id) is not None:
            return True
        time.sleep(0.5)
    return False


def wait_for_version(team_id: int, person_id: int, expected_version: int, timeout: float = 30.0):
    """Poll until the persisted version equals `expected_version`; return the
    final persisted version (or None if the person never appeared)."""
    deadline = time.time() + timeout
    cur = get_version(team_id, person_id)
    while time.time() < deadline and cur != expected_version:
        time.sleep(0.5)
        cur = get_version(team_id, person_id)
    return cur


def settle(seconds: float = 6.0) -> None:
    """Wait out a no-op/ignored/skipped update — i.e. a case where there is no
    positive state change to poll for (a stale replay, or a record the writer
    must drop). Sized to comfortably exceed a short flush interval."""
    time.sleep(seconds)
