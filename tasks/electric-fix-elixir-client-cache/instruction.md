## Task

on stacks where a cdn or proxy runs in front of the electric sync
service, our long-running elixir shape streams occasionally get
stuck after a shape rotation and never recover. the app
process keeps making requests, the server keeps responding, but the
shape never makes progress. same handle mismatch, over and over.
restarting the elixir process clears it for a while, then the next
rotation hits the same shape and it gets stuck again. the
ts client on the same backend doesn't have the issue, so
it's something specific to the elixir client. the elixir
client should match what the typescript client is doing on this
protocol path, fix it.

## General instructions

- The code repo is at /repo/electric.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
