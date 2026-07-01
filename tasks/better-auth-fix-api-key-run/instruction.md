## Task

Have a couple issues with running the API-key plugin against a secondary
storage backend (Redis-style KV store).

- Listing a user's API keys is too slow, and it gets slower the more keys
  the user has. References with many keys are noticeably laggy to list.
- When several API keys are created for the same user in quick succession
  (e.g. burst from concurrent requests), one of the freshly created keys
  sometimes goes missing from later `list` responses. The key works, but
  it doesn't show up in the list. This is hitting deployments that use
  secondary storage as a cache in front of the DB.

Investigate and fix these without any behavior regressions, important to
not break existing deploys.

## General instructions

- The code repo is at /repo/better-auth.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
