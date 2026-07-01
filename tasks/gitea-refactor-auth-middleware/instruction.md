## Task

We need to improve how the web framework decides which auth methods to attempt on each request; the current approach is brittle. Fix how the framework routes these auth methods so they work reliably across endpoints. As a deliberate user-visible consequence, the workflow status badge endpoint should now respond to Basic auth and OAuth2 tokens in addition to the existing browser session cookie, and existing endpoints that authenticate via Basic auth should continue to do so without regression.

## User stories / requirements

- A repository's workflow status badge URL on a private repository now responds to Basic auth and OAuth2 personal access token credentials, in addition to the previously-supported browser session cookie. CI workers and other non-browser clients can finally embed the badge image in private READMEs. Without any credentials, the URL still returns 404 because the repo is private.
- Repository feed URLs (the .rss and .atom variants) authenticate Basic auth on private repositories. Non-browser feed readers and scripts that subscribe to private repo feeds depend on this.
- Repository archive downloads and raw blob URLs authenticate Basic auth on private repositories. Build pipelines and `curl`-based fetch scripts depend on these endpoints.

## General instructions

- The code repo is at /repo/gitea.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
