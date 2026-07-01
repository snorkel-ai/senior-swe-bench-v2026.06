## Task

we're seeing odd behavior on the PR timeline after force-pushes.

it looks like the events listed in the timeline are missing entries after a force-push.

the header at the top of the PR shows the correct commit count, but earlier 'pushed N commits' events are missing and the force-push event itself shows fewer commits than the branch actually contains.

simplest repro: push commit A, push commit B, amend B and force-push.
the PR should show 2 commits in the timeline, but it only shows 1

## General instructions

- The code repo is at /repo/gitea.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
