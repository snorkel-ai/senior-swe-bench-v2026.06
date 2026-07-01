## Task

We have a few bug reports that indicate issues with the storage-template migration.
When the template uses {{album}}:
- the still-photo component moves to the right folder
- the motion-video component (i.e. .mp4) is split off and lands somewhere else (e.g. other/ or root directory)

We see similar behavior when a user changes their storage label and re-runs the migration. The still moves to the new label folder and the motion stays at the old one.

Can you PTAL?

## General instructions

- The code repo is at /repo/immich.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
