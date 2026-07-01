## Task

We want a "recently added" view of the timeline which shows assets ordered by when they
were uploaded to Immich, not when they were captured. The timeline is served by the
timeline-bucket endpoints, so make sure this comes through them.

Note - we are rolling out the backend change first, so existing clients must work unchanged.

## User stories / requirements

- The timeline's month grouping can now be based on upload date instead of the default capture date. In the "recently added" view, assets are grouped by their upload month; the default capture-month grouping stays unchanged.
- In the "recently added" view, a month holds exactly the photos uploaded that month. An old photo imported this month shows up under this month; a photo taken this month but imported at another time shows up under the month it was imported.
- When a user opens a single month of the timeline, they can see when each photo was added: every asset in that month is shown with its own upload date, and a month with no assets shows none.

## General instructions

- The code repo is at /repo/immich.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
