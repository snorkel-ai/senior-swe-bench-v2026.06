## Task

We want to add support for iterative tasks with multiple ordered steps in the same environment but not a shared context. Each step runs its verifier when the agent finishes, and then an overall reward gets aggregated at the end.

Mirror existing task config and output structures (each step gets its own subdir).

## User stories / requirements

- A task can declare an ordered sequence of steps that run in order inside one environment, verifying after each. Results for each step get saved (with at least name and reward). The trial-level reward is configured as either `mean` (average across steps, absent keys count as 0) or `final` (last step).
- A trial short-circuits when a step fails fatally or when a step's reward misses a configurable minimum threshold (a single threshold for the overall reward, or a per-key threshold).
- Per-step agent configuration overlays the task-level defaults: a step that sets its own agent user uses it; a step that omits it falls back to the task-level agent user.

## General instructions

- The code repo is at /repo/harbor.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
