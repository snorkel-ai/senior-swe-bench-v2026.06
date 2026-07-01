## Task

Getting more usage on LLM analytics taggers so let's improve the create/update contract.
Right now the write path is too permissive which can cause issues:

- A tagger's soft-deletion state can be supplied at creation time, meaning a
caller can create a tagger that is already marked deleted.
- The nested model configuration accepts a human-readable provider key
name that the server actually derives on its own from the stored key.

Let's add clear field-scoped validation (without breaking legitimate creates and edits).

## User stories / requirements

- When a user tries to create a tagger while trying to set its soft-deletion state, it should be rejected with a field-scoped client error.
- When a user tries to write the server-derived provider key name on the nested model configuration, it should be rejected with a client error attributable to that nested field.
- Soft-deleting an existing tagger by editing it should stay as a supported flow.
- When a user makes a valid create request, it works and returns the full read representation with the response body shape clients rely on.
- When a user edits an ordinary field of an existing tagger (its name and enabled flag), it updates the tagger.

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
