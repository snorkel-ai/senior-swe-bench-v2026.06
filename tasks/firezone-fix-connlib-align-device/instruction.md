## Task

after the last client release, static device pool resources stopped working entirely. the portal authorizes the devices fine and the control plane looks healthy, but on the client the tray shows `connected_devices = 0` and no client-to-client routes ever get installed. the device-pool peers just never come online. this used to work fine. dig into the connlib data plane and fix it.

## General instructions

- The code repo is at /repo/firezone.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
