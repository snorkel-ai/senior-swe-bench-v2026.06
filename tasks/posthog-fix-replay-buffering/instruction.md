## Task

Customers are reporting that session replays sometimes hang on the "Buffering..." spinner forever. The repro is loading a recording URL whose `?t=<seconds>` parameter is past the end of the recording (usually a shared/exported URL where the timestamp got out of bounds). We fixed some of this before but the stuck-buffer state still reproduces on the same URL shape. Also seems to be a related case where clicking "back to start" on a recording that has already loaded leaves it stuck buffering at the very start. Investigate and resolve.

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
