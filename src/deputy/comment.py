"""Render the sticky PR-review comment — pure, no I/O."""

from __future__ import annotations

from .pr_checks import PrChecks

# Hidden HTML marker used to find-and-update the single sticky comment.
# Override per-repo with the DEPUTY_MARKER env var (e.g. to keep an existing
# comment thread from a previous bot alive).
MARKER = "<!-- DEPUTY_PR_BOT -->"


def render_body(checks: PrChecks, version_line: str, note: str | None = None) -> str:
    """The visible markdown body (no marker).

    ``note`` is an optional line for something deputy *did* to the PR (as
    opposed to something it checked) — currently only the removal of its own
    superseded default label. Label edits made by a bot should be readable from
    the PR, not inferred from the label list changing under you.
    """
    lines = [
        " * ✅ PR title is ok"
        if checks.title_ok
        else " * ❌ PR title must start with one of: fix: feat: fix!: feat!: chore:",
        " * ✅ Exactly one release label"
        if checks.label_ok
        else " * ❌ Use exactly one release-* label (skip/auto/patch/minor/major)",
        " * ✅ SOURCE_KEY secret is set"
        if checks.has_source_key
        else " * ℹ️ SOURCE_KEY secret not set (only needed for automated release)",
        version_line,
    ]
    if note:
        lines.append(f" * ℹ️ {note}")
    header = (
        "👋 I checked your PR and found no issues. Thanks!\n\n"
        if checks.ok
        else "👋 I checked your PR and found some things to address:\n\n"
    )
    return "# PR Review\n\n" + header + "\n".join(lines) + "\n"


def render_sticky(checks: PrChecks, version_line: str, note: str | None = None) -> str:
    """The body actually posted: marker + visible body."""
    return MARKER + "\n" + render_body(checks, version_line, note)
