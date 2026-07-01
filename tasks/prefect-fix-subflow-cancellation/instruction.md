## Task

We need to add user cancellation for flows with in-process subflows. The current implementation has both the parent and child flow appearing to be crashed when the user cancels a flow with an in-process nested subflow. Because of this neither run their `on_cancellation` hook. User cancellation should cause these flows to properly exit.

## User stories / requirements

- When a user cancels a flow that has an in-process nested subflow, the cancellation propagates to the nested subflow: both the outer flow run and the in-process child subflow run reach the Cancelled state, and both flows' `on_cancellation` hooks fire (their on-disk markers are present and contain the corresponding flow_run_id). This holds for both the sync parent/child entrypoint and the async one.
- A normal nested-flow execution still works end-to-end: a parent flow that calls a child flow in-process and is NOT cancelled completes successfully. Both runs reach the Completed state, the subprocess exits 0, and both flow bodies' user-side side effects (the `parent-finished` and `child-finished` markers) are present. Pass-to-pass regression guard for the new control mechanism.

## General instructions

- The code repo is at /repo/prefect.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
