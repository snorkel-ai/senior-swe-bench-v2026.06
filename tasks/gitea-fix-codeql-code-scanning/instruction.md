## Task

We ran a static analysis over the Go backend and ten `incorrect-integer-conversion` warnings were flagged. Seemed like they were flagged on values that round trip between `int` and `int64` in some functions. There were also a few allocation size warnings about slice capacities dervied from values the analyzer couldn't prove were bounded. Could you look into these issues and fix them for real (e.g. no global cast)?

## General instructions

- The code repo is at /repo/gitea.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
