## Task

Some of our users running flows with lots of tasks are reporting an issue with task runs never showing up in the UI or the API. What's odd is the flow itself finishes just fine, but when the users check themselves, the task run count is short. Random handful of tasks just go missing. Tasks are definitely running fine. Sometimes one of these problem tasks is stuck showing an old state instead of its final one. Only seen this happen when a flow kicks off a lot of tasks at once or when under heavy load. Diagnose and fix. We want to ensure that every task run that actually gets kicked off gets recorded correctly with its latest state.

## General instructions

- The code repo is at /repo/prefect.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
