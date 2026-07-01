"""Taste-judge prompt asset.

Holds ``taste_judge_prompt.md`` — the single system prompt for the taste judge
(exploration guidance + the ten-dimension rubric with per-grade 1-5 anchors).
``run_judge.py`` reads it directly; the ``### dimension`` headings must match the
field names in its PracticeAlignment / RelativeTaste models.
"""
