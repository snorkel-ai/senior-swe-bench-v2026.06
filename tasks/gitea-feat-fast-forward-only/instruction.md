## Task

Let's make fast-forward-only merges work on a branch that requires signed commits,
even when the instance has no signing key (provided the pull request's head
commits are themselves already verified). It just advances the
protected base branch to point at the pull request's existing head commits. So
there is nothing for us to sign. Don't change behavior for other merge styles.

## User stories / requirements

- With "require signed commits" enabled on the base branch and no instance signing key, any merge style where gitea rewrites and re-signs the user's commits should be refused with the unchanged "the instance cannot sign" reason.
- A fast-forward-only merge creates no new gitea commit (it only advances the base branch pointer), so the instance's inability to sign should not block it. But the protection should stay meaningful: with an unverified head commit the merge is still refused (otherwise the push would later be rejected by the pre-receive hook), just for a reason other than the generic "instance cannot sign" one.
- A regular merge keeps the user's head commits on the branch, so when signed commits are required its head-commit verification fires first: with an unverified head it is rejected for the "head commits are not verified" reason rather than the generic "instance cannot sign" reason.
- The REST API merge endpoint surfaces the fast-forward-only unverified-head rejection as HTTP 405 Method Not Allowed, and the reported reason is the "head commits are not verified" reason rather than the generic "instance cannot sign" reason.

## General instructions

- The code repo is at /repo/gitea.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
