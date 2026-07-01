## Task

our oauth/oidc endpoints are coming back wrong when the framework adapters forward an HTTP request to them. the body ends up wrapped like `{ response: { ... } }` instead of the actual oauth payload. `Set-Cookie` is missing too.

spec-strict OAuth clients are rejecting the shape. weirdly, the same endpoints look fine when our internal plugin code calls them without an HTTP request in hand. can we fix this? needs to be generic, not path-scoped.

## General instructions

- The code repo is at /repo/better-auth.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
