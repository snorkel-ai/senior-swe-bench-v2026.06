## Task

Bumping a v3 idea up our list: saved views need to be shareable like tags and correspondents are. An owner should be able to give specific users (or groups) view-only or view-plus-edit access through the saved-views API, and recipients should see those views in their list and in global search. While we're in there, the per-view "show on dashboard" / "show in sidebar" toggles really shouldn't live on the saved view itself. That's a per-user preference (two people might share a view but want it on different surfaces in their own UI), so move that to each user's existing settings store. Existing installations must keep their current dashboard / sidebar choices through the upgrade and a downgrade.

## User stories / requirements

- Saved views can be shared between users. When the owner grants another user object-level view permission on a saved view, that user can see the view in their saved-views list and retrieve it by id.
- The saved-views API write surface accepts in the payload a setting that grants view/change permissions to specific users and groups, but only the owner (or a superuser) is allowed to use it.
- Querying global search returns saved views shared with the requester.
- On the frontend, the dashboard / sidebar visibility of a saved view respects the current user's settings.
- Existing owner-only saved-view CRUD continues to work.
- Upgrading preserves each user's existing dashboard/sidebar saved-view visibility: a view a user had pinned to their dashboard before the upgrade stays pinned afterwards (visibility is now carried per user, so the upgrade must migrate the existing data rather than drop it), and downgrading restores the original per-view state.

## General instructions

- The code repo is at /repo/paperless-ngx.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
