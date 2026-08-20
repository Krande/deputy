"""Pure logic for the ``release-watch`` command — semver compare + file rewrite.

No network, no git, no filesystem: version strings and file text in, decisions
and patched text out, so every rule here unit-tests without a repo. The
orchestration (query upstream, read/write the file, commit, push, open the PR)
lives in :func:`deputy.flows.release_watch`.

Version pin discovery uses a **regex with one capture group**. The capture group
(group 1) marks the exact substring holding the pinned version; the rewrite
replaces only that span, leaving the rest of the line — package name, quotes,
comparators, comments — untouched. This keeps the mechanism explicit and easy to
reason about across arbitrary file formats (requirements files, TOML, YAML,
workflow files) without teaching deputy each one's schema.
"""

from __future__ import annotations

import re

# Defaults a target may override in deputy.toml.
DEFAULT_BRANCH_PREFIX = "deputy/release-watch"
DEFAULT_PR_TITLE = "chore: bump {name} to {version}"
DEFAULT_LABELS: list[str] = ["dependencies"]

# Idempotency marker embedded in the PR body: one open PR per target, matched by
# head branch, with this marker as a secondary, human-visible signal.
MARKER_TEMPLATE = "<!-- deputy-release-watch:{name} -->"

_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?"
    r"(?:\.(?P<patch>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"  # build metadata is ignored for precedence
)


def normalize_version(raw: str) -> str:
    """Strip a single leading ``v``/``V`` and surrounding whitespace."""
    s = raw.strip()
    return s[1:] if s[:1] in ("v", "V") else s


def parse_version(raw: str | None) -> tuple[int, int, int, str | None] | None:
    """Parse ``raw`` into ``(major, minor, patch, prerelease)`` or ``None``.

    Accepts a leading ``v``, missing minor/patch (default to 0), a ``-prerelease``
    suffix, and ``+build`` metadata (ignored). Returns ``None`` for anything that
    isn't a recognisable semver-ish tag, so callers can skip junk tags.
    """
    if raw is None:
        return None
    m = _SEMVER_RE.match(normalize_version(raw))
    if not m:
        return None
    return (
        int(m.group("major")),
        int(m.group("minor") or 0),
        int(m.group("patch") or 0),
        m.group("pre"),
    )


def _cmp_prerelease(a: str | None, b: str | None) -> int:
    """Compare two prerelease strings per semver §11: a released version (no
    prerelease) outranks a prerelease; otherwise compare dot-separated
    identifiers (numeric numerically, else lexically; numeric < alphanumeric;
    a longer set of identifiers wins when all prior ones are equal)."""
    if a is None and b is None:
        return 0
    if a is None:  # a is a full release, b is a prerelease
        return 1
    if b is None:
        return -1
    aids, bids = a.split("."), b.split(".")
    for ai, bi in zip(aids, bids, strict=False):
        an, bn = ai.isdigit(), bi.isdigit()
        if an and bn:
            if int(ai) != int(bi):
                return -1 if int(ai) < int(bi) else 1
        elif an != bn:
            return -1 if an else 1  # numeric identifiers rank lower
        elif ai != bi:
            return -1 if ai < bi else 1
    if len(aids) != len(bids):
        return -1 if len(aids) < len(bids) else 1
    return 0


def _cmp_parsed(pa: tuple[int, int, int, str | None], pb: tuple[int, int, int, str | None]) -> int:
    for x, y in zip(pa[:3], pb[:3], strict=False):
        if x != y:
            return -1 if x < y else 1
    return _cmp_prerelease(pa[3], pb[3])


def compare_versions(a: str, b: str) -> int:
    """Return -1/0/1 for ``a`` <, ==, > ``b`` by semver precedence.

    Raises ``ValueError`` if either side isn't parseable — callers comparing a
    pinned value are expected to hand in real versions.
    """
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        bad = a if pa is None else b
        raise ValueError(f"not a version: {bad!r}")
    return _cmp_parsed(pa, pb)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly newer version than ``current``."""
    return compare_versions(candidate, current) > 0


def pick_latest_tag(tags: list[str]) -> str | None:
    """Return the highest semver-valid tag from ``tags`` (junk tags skipped)."""
    best_parsed: tuple[int, int, int, str | None] | None = None
    best_raw: str | None = None
    for tag in tags:
        parsed = parse_version(tag)
        if parsed is None:
            continue
        if best_parsed is None or _cmp_parsed(parsed, best_parsed) > 0:
            best_parsed, best_raw = parsed, tag
    return best_raw


def find_pinned(text: str, pattern: str) -> str | None:
    """Return the currently-pinned version in ``text`` per ``pattern``.

    ``pattern`` is a regex; capture group 1 (if present) is the version, else the
    whole match is used. Returns ``None`` when the pattern doesn't match.
    """
    m = re.search(pattern, text)
    if m is None:
        return None
    return m.group(1) if m.groups() else m.group(0)


def replace_pinned(text: str, pattern: str, new_version: str) -> tuple[str, int]:
    """Rewrite the pinned version in ``text`` to ``new_version``.

    Replaces only capture group 1's span for each match (or the whole match when
    the pattern has no group), preserving everything around it. Returns
    ``(new_text, count)`` where ``count`` is the number of replacements.
    """
    rx = re.compile(pattern)
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        whole = m.group(0)
        if not m.groups():
            return new_version
        # Splice new_version into group 1's span, relative to the whole match.
        base = m.start(0)
        start, end = m.start(1) - base, m.end(1) - base
        return whole[:start] + new_version + whole[end:]

    return rx.sub(_sub, text), count


def render_pr_body(
    name: str, current: str, new_version: str, upstream_repo: str, marker: str
) -> str:
    """Render the dependency-bump PR body (Markdown), carrying ``marker``."""
    return (
        f"{marker}\n\n"
        f"Bumps **{name}** from `{current}` to `{new_version}`.\n\n"
        f"Upstream: `{upstream_repo}` — release `{new_version}`.\n\n"
        f"_Opened automatically by `deputy release-watch`._\n"
    )
