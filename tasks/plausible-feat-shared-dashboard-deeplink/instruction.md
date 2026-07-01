## Task

Let's make the flow for deep-linking with password protected dashboards better.
Today if someone emails their teammate a shared dashboard URL with filters/query params, etc.
they get redirected to the dashboard root after entering the password.

Add support so the post-auth redirect lands on the same deep path the teammate originally clicked.

## User stories / requirements

- A user clicks a deep link into a password-protected shared dashboard (a URL with extra path segments under `/share/{domain}/...` plus filter query parameters) and enters the correct password. The post-authentication redirect URL preserves both the deep path and the original filter query string, so the user lands on the same page their teammate intended to share.
- Sometimes a shareable URL contains odd or malformed path segments, either through hand-editing or a sloppy link generator. After authenticating with the correct password, the user lands at a URL that stays inside the shared-link namespace, never at a destination outside it that an unsanitized echo of the visited path could imply. The legitimate trailing segments and the original query parameters are preserved.
- A shareable link could have extra query parameters tacked onto it, either by the original sharer or by a malicious link-rewriter. After authenticating with the correct password, the user should always land on the page their teammate's URL actually pointed to, never on a destination implied by tacked-on query parameters.

## General instructions

- The code repo is at /repo/plausible.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
