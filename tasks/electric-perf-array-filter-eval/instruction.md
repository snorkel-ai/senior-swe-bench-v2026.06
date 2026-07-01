## Task

sync engine fanout latency goes off a cliff once a tenant has a
few hundred shapes whose WHERE clauses use `= ANY(...)` or
`IN (...)` predicates — we're seeing multi-second freezes per
committed transaction at production scale. p50 latency at 1000
shapes is 6-15x worse for these clause shapes vs plain equality.
clients fan-out fine and replication looks fine in metrics. the
shape filter is the suspected stage. fix it.

## General instructions

- The code repo is at /repo/electric.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
