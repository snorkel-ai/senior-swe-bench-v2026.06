## Task

Getting lots of complaints about cloud sandbox SDK installs so we need to add
support for optional installs. Will need to restructure a bit to get that working.

Also we should add proper support for custom registered environments while we're
at it.

## User stories / requirements

- The default Harbor install shouldn't install any cloud sandbox SDKs and work correctly with local envs.
- When a user tries to invoke a cloud environment without its SDK installed, they should get an error with the missing package and install instructions.
- Custom local environments can be registered with string labels. Things like validation should work correctly.

## General instructions

- The code repo is at /repo/harbor.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
