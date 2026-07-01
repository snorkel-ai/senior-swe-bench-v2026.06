## Task

Customers want the ability to actually enforce event schemas. Today they're advisory, so events violating a schema still flow through and downstream queries see missing or wrong-typed values. Add an opt-in "reject" mode per event definition: when an event is missing a required property or has a value that can't be coerced to the configured type, drop it at ingestion and surface a warning a user can find in the data mgmt UI to debug. Build it end-to-end (API, ingestion pipeline, data mgmt UI).

## User stories / requirements

- A user can flip an event definition into "reject" mode through the existing event-definitions API, and a subsequent read returns the new value. The toggle round-trips through the API surface.
- Partial updates are supported by the event-schemas resource so the data-management UI can swap a schema's property group in place without recreating the row.
- When the team has an event definition in reject mode with a required property, an event missing that property is dropped at ingestion with a schema-validation warning (should name the missing property).
- When a required property's value can't be coerced to the configured type, the event is dropped with a type-mismatch reason (likewise should name the offending property). Includes ClickHouse-unsafe Numeric values (Inf, -Inf, NaN, booleans, the corresponding string sentinels, empty and whitespace-only strings), i.e. values that JS can hold but ClickHouse cannot store.
- An event whose properties satisfy the schema flows through the validation step unchanged. Includes Numeric values that are valid for ClickHouse (finite numbers, negatives, zero, trimmed numeric strings), i.e. the validator doesn't over-reject valid traffic.
- Teams with no event definitions in reject mode short-circuit the validation step (events flow through regardless of whether their properties match any registered schemas). The validator shouldn't accidentally over-enforce on teams that haven't opted in.
- The event-definition schema management page shows an interactive toggle for enforcing the schema on incoming events (a checkbox or switch with a label related to enforcement / rejection / strictness).

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
