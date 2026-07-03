# Senior SWE-Bench (v2026.06)
This repo contains the Harbor dataset for the public tasks in Senior SWE-Bench (v2026.06). For more, check out:

* [***Senior SWE-Bench →***](https://senior-swe-bench.snorkel.ai/tasks)
* [***Harbor docs →***](https://www.harborframework.com/docs)

## Quick start

Harbor can read directly from this repo, so you can get started without downloading anything.
Once you [install Harbor](https://www.harborframework.com/docs/getting-started), you can run:

```bash
# Set env vars required for your model
# export ANTHROPIC_API_KEY=sk-ant-...
# export OPENAI_API_KEY=sk-proj-...
# export MY_PROVIDERS_API_KEY=...

# [Optional] Set the models for the test stage (defaults below)
# export SSB_OVERRIDE_VA_HARNESS=mini-swe-agent
# export SSB_OVERRIDE_VA_MODEL=anthropic/claude-sonnet-4-6
# export SSB_OVERRIDE_ALL_JUDGE_MODEL=anthropic/claude-sonnet-4-6
# export SSB_OVERRIDE_CLASSIFIER_MODEL=anthropic/claude-haiku-4-5

# Set depending on what you want to run
MODEL=anthropic/claude-opus-4-8
AGENT=mini-swe-agent

# Run Harbor

# Option 1: via Harbor Hub
harbor run -d snorkel-ai/senior-swe-bench-v2026.06 -a $AGENT -m $MODEL

# Option 2: via GitHub repo
harbor run --repo snorkel-ai/senior-swe-bench-v2026.06 -a $AGENT -m $MODEL
```
