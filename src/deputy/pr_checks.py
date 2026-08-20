"""Conventional-commit PR-hygiene checks — pure, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Conventional-commit types deputy recognises. They map to release bumps
#: (feat -> minor, fix -> patch, chore -> no-op; see commit_parser_options), so
#: these are the only types the title check accepts.
VALID_TYPES: tuple[str, ...] = ("feat", "fix", "chore")

#: A recognised type, an optional ``(scope)``, an optional ``!`` breaking marker,
#: then ``": "`` — e.g. ``feat: x``, ``fix(api): y``, ``chore(ci)!: z``.
_TITLE_RE = re.compile(r"^(?:" + "|".join(VALID_TYPES) + r")(?:\([^()\r\n]+\))?!?: ")


def title_ok(title: str) -> bool:
    """True if the title is a conventional commit whose type deputy recognises.

    Accepts an optional ``(scope)`` and ``!`` breaking marker after the type, so
    ``feat: add x``, ``fix(api): bug`` and ``chore(ci)!: drop y`` all pass. An
    unknown type (``ci:``, ``docs:``), a missing space after the colon, an empty
    ``()`` scope, or a capitalised type do not.
    """
    return bool(_TITLE_RE.match(title))


@dataclass(frozen=True)
class PrChecks:
    title_ok: bool
    label_ok: bool  # exactly one release-* label
    has_source_key: bool

    @property
    def ok(self) -> bool:
        # SOURCE_KEY is advisory only — it never fails the check.
        return self.title_ok and self.label_ok
