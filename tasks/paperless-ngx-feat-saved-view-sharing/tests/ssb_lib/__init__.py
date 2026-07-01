"""Reusable verifier/reward library for Senior SWE-Bench.

Dual-namespace contract: this package must import correctly under BOTH
``src.python.ssb_lib`` (offline) and ``ssb_lib`` (in-container). This works
because every intra-package import is RELATIVE, so it binds via the runtime
``__package__`` regardless of absolute name and without touching ``sys.path``;
data files are resolved relative to their module file.
"""

from __future__ import annotations

from . import llm_utils as llm_utils
from .patch_classify import (
    FileCategory as FileCategory,
)
from .patch_classify import (
    FileClassification as FileClassification,
)
from .patch_classify import (
    classify_patch as classify_patch,
)
from .patch_classify import (
    dropped_paths as dropped_paths,
)
from .patch_classify import (
    filter_patch_to_behavioral as filter_patch_to_behavioral,
)
from .patch_sloc import compute_patch_sloc as compute_patch_sloc
from .validation_agent import (
    DEFAULT_MANDATE_PATH as DEFAULT_MANDATE_PATH,
)
from .validation_agent import (
    DEFAULT_VALIDATION_PROMPT_PATH as DEFAULT_VALIDATION_PROMPT_PATH,
)
from .validation_judge import (
    DEFAULT_RUBRIC_PATH as DEFAULT_RUBRIC_PATH,
)
from .validation_judge import (
    JudgeInputs as JudgeInputs,
)
from .validation_judge import (
    JudgeResult as JudgeResult,
)
from .validation_judge import (
    Rubric as Rubric,
)
from .validation_judge import (
    SeverityFindings as SeverityFindings,
)
from .validation_judge import (
    StoryJudgement as StoryJudgement,
)
from .validation_judge import (
    judge as judge,
)
from .validation_judge import (
    parse_response as parse_response,
)
from .validation_judge import (
    render_judge_prompt as render_judge_prompt,
)
from .validation_judge import (
    should_discard as should_discard,
)
