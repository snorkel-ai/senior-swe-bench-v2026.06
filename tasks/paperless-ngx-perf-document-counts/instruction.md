## Task

On larger paperless-ngx instances we're getting timeouts and ~30s
response times on `/api/tags/` and `/api/custom_fields/` whenever a
non-admin user hits them. Lag scales with document corpus size and shared-permission rows.
The numbers each user
sees in `document_count` are correct, the listing is just way too
slow. Similar issue on `/api/correspondents/`,
`/api/document_types/`, and `/api/storage_paths/` (but less bad). Make a backend fix to get those
listings fast again.

## General instructions

- The code repo is at /repo/paperless-ngx.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
