## Task

Multi-tenant customers running on our dynamic baseURL config
(allowedHosts + protocol) are reporting wrong origins baked into URLs
constructed inside endpoint handlers (ex: verification emails, OAuth
redirects) but only on the server-side code
paths that call our API methods directly instead of going through the
HTTP handler. The HTTP handler resolves the per-tenant origin fine.
The direct programmatic path treats every call like no host info was
provided. Make both paths consistent so the resolved origin follows
the request's host headers either way.

## General instructions

- The code repo is at /repo/better-auth.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
