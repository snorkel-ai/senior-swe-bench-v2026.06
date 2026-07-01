## Task

a user shipping a pruned monorepo to docker reported that after
`turbo prune <app>`, one of the app's source directories is just missing
from `out/`. the files are committed (not in .gitignore) and the
app needs them to build, but they don't make it into the pruned copy.
investigate and fix it so this doesn't happen.

## General instructions

- The code repo is at /repo/turborepo.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
