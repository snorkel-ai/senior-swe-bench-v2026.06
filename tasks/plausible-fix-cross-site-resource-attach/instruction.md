## Task

We had a recent security review that found a cross-site access vulnerability in some of our provisioning flows. When a user creates or edits one of the per-site resources the app provisions, a carefully crafted request can cause the resource to be attached to *or read out of* a site other than the one the user is actually working on. ie a user operating on their own site can access another site's data by tampering with the request.

Find every path where this is possible and implement a fix. Ownership of a provisioned resource should be determined by the site the user is acting on, not by anything coming from the request.

## General instructions

- The code repo is at /repo/plausible.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
