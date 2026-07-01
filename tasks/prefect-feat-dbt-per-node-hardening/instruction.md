## Task

We need to make a few improvements to the dbt orchestrator's per-node execution mode: failing build handling, re-runs with cached models, dry-run preview noise, and the graph display.

## User stories / requirements

- A per-node dbt build where a node errors should fail the flow by default.
- Callers should be able to opt out and inspect a partial-failure build instead of having it raise. It should return the legacy results map vs. error when the opt-out is enabled.
- Under per-node cross-run caching with the immediate test strategy, a model that has an upstream data test never gets treated as cached on an identical second run. It re-executes every time, unlike any other unchanged model. Need to fix that.
- In per-node mode the flow-run graph should show the real dependency edges for dbt nodes (like normal DAG does).
- Previewing a build (dry-run plan) shouldn't leak dbt's console progress output to the caller's stdout.

## General instructions

- The code repo is at /repo/prefect.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
