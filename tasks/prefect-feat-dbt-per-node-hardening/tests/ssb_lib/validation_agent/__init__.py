"""Validation-agent assets — the prompt template and mandate the agent runs under.

These two files belong to the validation *agent* (rendered by
``run_validate.py`` into the agent's task prompt), not the judge. The judge
imports ``DEFAULT_MANDATE_PATH`` from here only so it scores the agent against
the exact same rule text the agent was given — a single source keeps the agent
and judge from drifting apart.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).parent

# The validation agent's prompt template, rendered by run_validate.py.
DEFAULT_VALIDATION_PROMPT_PATH = _HERE / "validation_prompt.md.j2"
# The agent's non-negotiable rules. run_validate.py prepends this to the prompt;
# the judge embeds it to score compliance and must never suggest a change that
# violates it.
DEFAULT_MANDATE_PATH = _HERE / "validation_agent_mandate.md"
