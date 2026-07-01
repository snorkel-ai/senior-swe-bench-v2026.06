"""Test harness for plausible-feat-shared-dashboard-deeplink validation stories.

Drives a real Phoenix server over HTTP. Provides:

- ``BASE_URL``                     — http://localhost:8000
- ``seed_shared_link(...)``        — return the credentials of the
                                     baseline password-protected shared
                                     link that ``validation-setup.sh``
                                     pre-inserted (or, if the caller
                                     asks for non-default values, seed
                                     a fresh one via direct SQL).
- ``extract_form_action(html)``    — parses the rendered password page
                                     and returns the ``action``
                                     attribute of the first <form>.
- ``extract_form_hidden_inputs``   — returns hidden ``<input>``
                                     name/value pairs from the same
                                     form.

The harness deliberately does NOT import any task-introduced module.
All round-trip behaviour is observed only through the agent's HTTP
endpoints — the same way a real browser would interact with the site.
"""

from __future__ import annotations

import json
import os
import subprocess
from html.parser import HTMLParser

BASE_URL = "http://localhost:8000"
REPO_DIR = "/repo/plausible"
SEED_SCRIPT = "/repo/plausible/senior_swe_bench_seed.exs"

# Defaults match the values seeded by validation-setup.sh.
DEFAULT_DOMAIN = "deeplink-test.example"
DEFAULT_SLUG = "deeplink-slug-abc"
DEFAULT_PASSWORD = "correct horse battery"


# ---------------------------------------------------------------------------
# seed_shared_link — credentials lookup for a pre-seeded link
# ---------------------------------------------------------------------------


def seed_shared_link(
    domain: str = DEFAULT_DOMAIN,
    slug: str = DEFAULT_SLUG,
    password: str = DEFAULT_PASSWORD,
) -> dict:
    """Return the credentials of the baseline password-protected
    shared link that ``validation-setup.sh`` inserted.

    If the caller passes the default values (the common case), this is
    a pure-Python identity function — no DB I/O happens, and the call
    is safe even while the Phoenix server is running. If the caller
    requests non-default values, the function shells out to
    ``mix run --no-start /repo/plausible/senior_swe_bench_seed.exs`` to seed
    a fresh row; that path is only safe when no other ``mix`` task is
    holding port 8000 (i.e. only between calls to
    ``validation-setup.sh``).

    The reason for the soft default: starting the Plausible mix
    application a second time conflicts with the running ``mix
    phx.server`` on port 8000. Story procedures that rely on the
    baseline credentials call this function with no arguments and
    avoid that conflict entirely.
    """
    if (domain, slug, password) == (DEFAULT_DOMAIN, DEFAULT_SLUG, DEFAULT_PASSWORD):
        return {"domain": domain, "slug": slug, "password": password}

    # Fallback: full re-seed for non-default values.
    env = {
        **os.environ,
        "MIX_ENV": "e2e_test",
        "SEED_DOMAIN": domain,
        "SEED_SLUG": slug,
        "SEED_PASSWORD": password,
    }
    proc = subprocess.run(
        ["mix", "run", "--no-start", SEED_SCRIPT],
        cwd=REPO_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)

    raise RuntimeError(
        "Seed script produced no JSON line. stdout=%r stderr=%r"
        % (proc.stdout, proc.stderr)
    )


# ---------------------------------------------------------------------------
# HTML parsing — find the password form's action + hidden inputs
# ---------------------------------------------------------------------------


class _FirstFormExtractor(HTMLParser):
    """Capture the first <form>'s action attribute and any hidden
    <input> name/value pairs nested inside it."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str = ""
        self.hidden: dict[str, str] = {}
        self._captured_form: bool = False
        self._in_form: bool = False

    def handle_starttag(self, tag: str, attrs):  # type: ignore[override]
        attrs_d = {k: v or "" for k, v in attrs}
        if tag.lower() == "form" and not self._captured_form:
            self.action = attrs_d.get("action", "")
            self._captured_form = True
            self._in_form = True
            return
        if (
            tag.lower() == "input"
            and self._in_form
            and attrs_d.get("type", "").lower() == "hidden"
        ):
            name = attrs_d.get("name")
            if name:
                self.hidden[name] = attrs_d.get("value", "")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag.lower() == "form":
            self._in_form = False


def extract_form_action(html: str) -> str:
    """Return the ``action`` attribute of the first <form> in ``html``.

    Returns an empty string if no form is found. The story procedure
    must POST to this URL — never reconstruct it from implementation-known
    pieces, because alternative implementations may carry the
    deep-path information differently (in the URL, in a hidden field,
    or in a cookie set on the GET).
    """
    p = _FirstFormExtractor()
    p.feed(html)
    return p.action


def extract_form_hidden_inputs(html: str) -> dict[str, str]:
    """Return the {name: value} dict of hidden <input> fields nested
    inside the first <form> in ``html``.

    Stories MUST forward these in the POST body alongside the
    password so a hidden-field design (where the deep-path is
    carried as a hidden form input rather than as a URL fragment)
    is exercised faithfully.
    """
    p = _FirstFormExtractor()
    p.feed(html)
    return p.hidden
