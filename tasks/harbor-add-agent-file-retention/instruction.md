## Task

Give users a way on the run launcher CLI to control which agent files Harbor
keeps after each trial finishes: keep everything (the current behavior), only
the trajectory files that trace export needs, or none.

## User stories / requirements

- When a user opts to keep only trajectory files, the agent directory is pruned down to exactly the trajectory files in its root.
- When a user opts for keep-everything policy, everything should work like it does today. For keep nothing, the directory should be present but empty.
- The retention options work for remote environments as well.

## General instructions

- The code repo is at /repo/harbor.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
