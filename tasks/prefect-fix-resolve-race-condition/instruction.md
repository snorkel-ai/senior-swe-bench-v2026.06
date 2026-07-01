## Task

A few enterprise customers running Prefect Server using compound automations are reporting that under busy event traffic the parent automation sometimes never fires or fires twice when all child events from a single batch came through. Can you please investigate and fix the issue so the parent fire exactly once per legitimate batch?

## General instructions

- The code repo is at /repo/prefect.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
