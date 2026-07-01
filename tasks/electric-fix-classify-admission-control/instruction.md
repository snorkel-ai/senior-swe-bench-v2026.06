## Task

We've had reports of the sync service having problems throttling under load.

The first problem we've seen is whenever a stack is redeployed or restarted, the clients
that reconnect to shapes they were already subscribed to start getting
`503` "concurrent request limit exceeded" responses in large numbers, but these
reconnections are supposed to be cheap.

The second problem seems to be with the safeguard that is meant to cap how
many new shape subscriptions can be set up at once doesn't always hold.
Some requests that really do spin up a new subscription manage to slip
past the cap.

Can you look into these and implement a fix?

## General instructions

- The code repo is at /repo/electric.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
