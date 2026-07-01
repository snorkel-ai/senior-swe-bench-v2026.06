## Task

Editors should be able to move issues or PRs between columns on a given
project board from the issue sidebar (where the labels, milestone, and project
controls are) instead of going to the board directly.

Any updates should be immediate and visible the next time the sidebar renders.

Notes:
- For read-only viewers, the current column should be visible but have no picker
- Read vs write behavior should match the existing sidebar controls

## User stories / requirements

- On an issue or PR that is on a project board, an editor sees a column picker in the sidebar showing that project's columns, with the issue's current column marked.
- When an editor moves an issue to another column of the same project from the sidebar, the change persists.
- A move that crosses a boundary (e.g. a column from a different project, or an issue not in this repository) is refused.
- A viewer who cannot edit the issue sees the current column as static text (no interactive picker and the other columns are not offered).
- Only users who can edit the repository's issues can move an issue between columns.

## General instructions

- The code repo is at /repo/gitea.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
