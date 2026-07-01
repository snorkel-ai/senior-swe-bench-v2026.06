## Task

turbo runs against our biggest monorepo feel laggy before any task even starts. i pulled a `--profile` trace on a warm run to see where the time goes, and two spots stood out. 1. file hashing for the packages that pin explicit `inputs` in turbo.json 2. extra time around the lockfile parser. the runs are correct, just slower than they should be. look if you can trim the pre-task setup work without changing what turbo actually computes.

## General instructions

- The code repo is at /repo/turborepo.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
