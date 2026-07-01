## Task

The analytics team flagged some noise on our internal PostHog project (project 2). Basically Twig's traces are getting buried under junk. We're seeing thousands of near-identical $ai_generation events, all the same shape. The output is a single character ("I"), finish_reason is "length", and the $ai_input is always just [{"content": "count", "role": "user"}]. $ai_output_tokens stays at 1 but $ai_input_tokens jumps around. One trace can rack up hundreds of these.

Over the last seven days that totalled 74,765 events across 1,131 traces. Bursts of ~4,500 an hour. The pain is that all this noise buries legitimate LLM activity, so it's hard to debug Twig regressions. Anyway, can you find what's causing these and stop them being captured?

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
