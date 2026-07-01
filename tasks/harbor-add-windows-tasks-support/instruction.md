## Task

We're going to add first-class support for Windows-based tasks. Tasks should be
able to declare their target operating system in `task.toml` (under the
`[environment]` table), and we also need some way for an agent to declare
whether it can run Windows tasks. This can't break existing Linux tasks.

## User stories / requirements

- A task can declare its target operating system in its task.toml under [environment]. Scripts need to be adapted (batch scripts for Windows that run through the Windows command interpreter), and PowerShell/cmd scripts are no longer valid task entrypoints.
- Windows containers should use Windows-style, drive-rooted mount points for the logs, tests, and solution directories (and the verifier reward path beneath logs).
- Agents should declare whether they can run Windows tasks. Off by default for real agents, but need to add no-op and oracle support. A Windows task paired with an agent that cannot run Windows fails fast with a clear error.

## General instructions

- The code repo is at /repo/harbor.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
