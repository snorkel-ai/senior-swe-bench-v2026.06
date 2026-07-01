## Task

Let's add named versions for prompts. Right now the LLM-prompt management page treats them as single mutable objects, but we want to add versioning so we can add "view previous versions", parallel edits, etc. Just do BE for now.

## User stories / requirements

- Creating a fresh prompt establishes version 1. The created response carries enough information for callers to see that the new prompt is the only existing version: an integer version equal to 1, and the prompt is treated as the most recent version of its name.
- Editing an existing prompt creates a new immutable version rather than overwriting the original. After a publish, the original version's content is still retrievable when fetched by its version number.
- Fetching a prompt by name returns the latest version's content by default. After a publish, a follow-up fetch reflects the new content. Fetching with a specific version number returns that version's content.
- When a publish is attempted with a stale base-version token (the caller's view of the current version is no longer current), the server rejects it with a 4xx response and adds no new version. The currently-active version is unchanged.
- Archiving a multi-version prompt removes it from active reads. After archive, creating a fresh prompt with the same name succeeds (the name is reusable from a clean slate) and starts at version 1.
- Listing prompts returns one entry per active name (the latest version for that name), not every historical version. Archived prompts do not appear in the list at all.

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
