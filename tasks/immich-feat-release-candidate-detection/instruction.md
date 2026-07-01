## Task

Add a **release channel** so an admin can opt in to release candidates: stable
(the default) and release-candidate, selectable through the system-config API
and carried through to the upstream latest-release lookup. Existing deployments
must be unaffected.

The version objects the server reports now need to carry a release's numeric
pre-release number (null for a stable release), and the notification must name
the kind of release found.

Don't disturb the rest of the version-check behaviour, and stay within the
`server/` workspace.

## User stories / requirements

- When the new-version check is set to the release-candidate channel, a newer pre-release build is reported as available. The notification that goes out names how far ahead the release is and exposes the pre-release number of the version it found. On the default channel, an admin is only told about newer stable releases — a newer pre-release is silently ignored.
- When a client connects and the server has already recorded a newer release, the server re-sends that release notification to the connecting client. The re-sent notification uses the same structured shape as a live notification — it exposes the found version's pre-release number and the kind of release it is.

## General instructions

- The code repo is at /repo/immich.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
