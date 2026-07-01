## Task

Now that kube clusters can be scoped, unscoped users are losing visibility: when the same cluster name exists in two different scopes they only ever see one of them in resource listings, and `tsh kube ls` gives them no way to tell the duplicates apart.

Make resource listing return every cluster across scope boundaries while still collapsing genuine duplicates, and make `tsh kube ls` distinguish same-named clusters.

## General instructions

- The code repo is at /repo/teleport.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
