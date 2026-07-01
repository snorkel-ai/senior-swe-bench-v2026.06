## Task

it looks like pg replication slot lag is growing unbounded on prod stacks. the flush lsn we send back to the db just stops advancing. clients are still getting new data, storage is still writing, nothing crashes. lag keeps growing for hours until someone restarts the stack.
it always seems to happen right after a txn whose changes spanned multiple wal fragments.
find what's causing the global flush boundary to get stuck and fix it. note: the upstream tracker should only see flush acks at txn boundaries.

## General instructions

- The code repo is at /repo/electric.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
