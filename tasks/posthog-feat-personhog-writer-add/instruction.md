## Task

We're moving person-state persistence into a dedicated Rust pipeline. The
`personhog-leader` service already keeps the latest person-state in memory and
publishes deduplicated person-state updates (a `Person` protobuf from the
`personhog-proto` crate) onto a Kafka topic, but nothing consumes it yet so the leader cache is lost on restart.

Build a new Rust service `rust/personhog-writer` that consumes the
person-state topic and persists the latest person-state into the
persons Postgres database (the target table is supplied by configuration). It
needs to keep that store consistent with the latest person-state under Kafka
replay/redelivery and under high throughput. It should also hold up when
individual records are malformed or when Postgres is slow.

Wire it into the Rust workspace and into the dev tooling
(`bin/start-rust-service`, `bin/mprocs.yaml`) the same way the other personhog
services are.

## User stories / requirements

- A person-state update published to the topic is durably persisted: after it's sent, the person can be read back with the same identity, the version it carried, and the properties it included.
- When several updates for the same person arrive close together, the persisted person collapses to a single row at the latest version, and a later arriving older update (a replay or out-of-order redelivery) never rolls the person back to an earlier state.
- A record the writer cannot store does not prevent the other records in the same batch from being persisted and it does not stall the pipeline. Valid person updates sent alongside or after it are still persisted and readable.

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
