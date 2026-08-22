r"""JSON version declarations — bump a package's *own* version, nothing else.

semantic-release has two mechanisms for writing a new version into a file:
``version_toml`` (TOML-aware, surgical) and ``version_variables`` (a regex). The
regex it builds is ``<variable>\s*(:=|[:=])\s*(?P<quote>['"])<semver>(?P=quote)``
and it substitutes **every** match in the file. That is fine for
``package.json``, whose own ``"version"`` is the only match, but catastrophic for
``package-lock.json``: every dependency carries a ``"version"`` key. Measured on
adapy's lock, ``src/frontend/package-lock.json:"version"`` matches **471 times
across 230 distinct versions**, so pointing ``version_variables`` at it rewrites
the whole dependency tree to the project version — a corrupted lockfile, not a
bump. (Note the quotes around ``"version"``: the bare token matches TOML's
``version = "..."`` but never JSON, where the key carries a closing quote before
the colon.)

This module parses the JSON instead, so a dependency can never be matched by
accident. It edits only the package's own version:

* the root-level ``version`` — present in ``package.json`` and in
  ``package-lock.json`` of every lockfileVersion (1, 2 and 3); and
* ``packages[""]["version"]`` — the empty-string key npm uses for "the package
  this lockfile describes". Present in lockfileVersion 2 and 3; lockfileVersion 1
  has no ``packages`` table at all (only ``dependencies``), so only the root
  field is written there.

Everything under ``dependencies``, and every non-empty key under ``packages``, is
third-party and is left alone.

Formatting is preserved byte for byte. npm writes 2-space indent and a trailing
newline; the indent string, newline style (LF/CRLF), trailing newline and any BOM
are detected from the file and reproduced. The result is then *verified*: the
unmodified document is re-rendered and compared with the original text, and we
refuse to write unless it comes back byte-identical. A version bump must not hand
a reviewer a whole-file diff, and must not fight ``npm install`` on the next run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# npm's key for "the root package", i.e. the one this lockfile describes.
ROOT_PACKAGE_KEY = ""
VERSION_KEY = "version"
_BOM = "\ufeff"  # some tools write one; preserve it if present

# Only used to sanity-check a version *string*; never to locate one in the file.
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)*$")


class JsonVersionError(ValueError):
    """A JSON version declaration could not be applied. Always names the file.

    Raised rather than no-op'ing: a bump that silently does nothing is how a
    lockfile drifts several minor versions behind without anyone noticing.
    """


@dataclass(frozen=True)
class JsonFormat:
    """The cosmetic shape of a JSON file, so it can be written back unchanged."""

    indent: str | None  # None => the file is minified (no indentation at all)
    newline: str  # "\n" or "\r\n"
    trailing_newline: bool
    bom: bool


@dataclass(frozen=True)
class JsonVersionUpdate:
    """Result of rewriting one JSON document's own version."""

    text: str
    old_version: str
    new_version: str
    fields: tuple[str, ...]  # human-readable labels of the fields written

    @property
    def changed(self) -> bool:
        return self.old_version != self.new_version


def detect_format(raw: str) -> JsonFormat:
    """Work out how ``raw`` is laid out so it can be re-rendered identically."""
    bom = raw.startswith(_BOM)
    body = raw[1:] if bom else raw
    newline = "\r\n" if "\r\n" in body else "\n"

    indent = None
    for line in body.replace("\r\n", "\n").split("\n")[1:]:
        stripped = line.lstrip(" \t")
        if stripped and stripped != line:
            indent = line[: len(line) - len(stripped)]
            break

    return JsonFormat(indent=indent, newline=newline, trailing_newline=body.endswith("\n"), bom=bom)


def render(doc: Any, fmt: JsonFormat) -> str:
    """Serialise ``doc`` in ``fmt``'s layout.

    ``json.dumps`` with an indent produces exactly what ``JSON.stringify(o, null,
    2)`` does — ``"key": value``, ``[]``/``{}`` for empties, literal non-ASCII —
    which is what npm writes. ``render`` is the inverse of ``json.loads`` for any
    file :func:`set_version` accepts, because that function verifies it.
    """
    if fmt.indent is None:
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(doc, indent=fmt.indent, ensure_ascii=False)
    if fmt.trailing_newline:
        text += "\n"
    if fmt.newline != "\n":
        text = text.replace("\n", fmt.newline)
    return (_BOM + text) if fmt.bom else text


def read_version(text: str, *, source: str = "<json>") -> str:
    """The package's own version as declared in ``text``. Raises if it isn't there."""
    return _own_version(_parse(text, source), source)


def set_version(text: str, new_version: str, *, source: str = "<json>") -> JsonVersionUpdate:
    """Rewrite the package's own version in ``text``, leaving dependencies alone.

    Returns the new text plus the version it replaced and the fields written.
    Raises :class:`JsonVersionError` — naming ``source`` — when the file is not
    valid JSON, is not a JSON object, declares no root ``version``, declares one
    that is not a semantic version, or cannot be re-rendered byte-identically.
    """
    doc = _parse(text, source)
    fmt = detect_format(text)

    # Refuse to write a file we cannot reproduce. Without this a stray formatting
    # quirk (tabs, a duplicated key, an exotic escape) would turn a two-line
    # version bump into a whole-file diff, and npm would reformat it right back
    # on the next install.
    rendered = render(doc, fmt)
    if rendered != text:
        raise JsonVersionError(
            f"{source}: cannot rewrite this file without reformatting it. deputy "
            "re-renders the JSON and reproduces the original indent, newline style "
            "and trailing newline, but this file did not come back byte-identical, "
            "so writing it would produce a whole-file diff. Normalise the file "
            "(e.g. `npm install --package-lock-only`) or drop it from version_json."
        )

    old = _own_version(doc, source)
    fields: list[str] = []
    for label, holder in _version_holders(doc):
        holder[VERSION_KEY] = new_version
        fields.append(label)

    return JsonVersionUpdate(
        text=render(doc, fmt),
        old_version=old,
        new_version=new_version,
        fields=tuple(fields),
    )


def bump_file(path: str | Path, new_version: str) -> JsonVersionUpdate:
    """Apply :func:`set_version` to ``path`` on disk, writing only on a change.

    Read and written as bytes so nothing translates newlines under us: on Windows
    a CRLF lockfile read in text mode and written back would silently become LF —
    a whole-file diff, the exact failure this module exists to avoid.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError as exc:
        raise JsonVersionError(f"{path}: no such file (declared in version_json)") from exc
    except OSError as exc:
        raise JsonVersionError(f"{path}: could not be read ({exc})") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonVersionError(f"{path}: is not UTF-8 ({exc})") from exc

    update = set_version(text, new_version, source=str(path))
    if update.changed:
        p.write_bytes(update.text.encode("utf-8"))
    return update


# ── internals ─────────────────────────────────────────────────────────────────


def _parse(text: str, source: str) -> dict:
    try:
        # A BOM is preserved on write (see JsonFormat.bom) but json.loads rejects it.
        doc = json.loads(text.removeprefix(_BOM))
    except json.JSONDecodeError as exc:
        raise JsonVersionError(f"{source}: is not valid JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise JsonVersionError(
            f"{source}: expected a JSON object at the top level, got {type(doc).__name__}"
        )
    return doc


def _version_holders(doc: dict) -> list[tuple[str, dict]]:
    """The dicts holding the package's *own* version, with printable labels.

    The root object always; plus ``packages[""]`` when the lockfile has one (v2/v3
    do, v1 does not). ``packages[""]`` is only written when it already declares a
    version — adding the key would change the document's shape, not bump it.
    """
    holders = [(VERSION_KEY, doc)]
    packages = doc.get("packages")
    if isinstance(packages, dict):
        root_pkg = packages.get(ROOT_PACKAGE_KEY)
        if isinstance(root_pkg, dict) and VERSION_KEY in root_pkg:
            holders.append((f'packages[""].{VERSION_KEY}', root_pkg))
    return holders


def _own_version(doc: dict, source: str) -> str:
    """The root ``version``, validated. Raises when absent or not a semver string."""
    if VERSION_KEY not in doc:
        raise JsonVersionError(
            f"{source}: has no root {VERSION_KEY!r} field, so there is no package "
            "version to bump (an npm lockfile only omits it when its package.json "
            "does too)"
        )
    value = doc[VERSION_KEY]
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise JsonVersionError(
            f"{source}: root {VERSION_KEY!r} is {value!r}, which is not a semantic "
            "version; refusing to overwrite a field deputy does not understand"
        )
    return value
