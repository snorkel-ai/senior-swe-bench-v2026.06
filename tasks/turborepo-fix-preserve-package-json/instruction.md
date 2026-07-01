## Task

running `turbo prune` on a monorepo with pnpm patches gives us a pruned output we can ship to docker, but builds inside that pruned output always miss the upstream cache. every build acts like a cold rebuild, even when nothing about the package's deps actually changed. we seem to only see this when pnpm patches are in the mix. repos without patches seem to prune and cache just fine. diagnose and ship a fix.

## General instructions

- The code repo is at /repo/turborepo.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
