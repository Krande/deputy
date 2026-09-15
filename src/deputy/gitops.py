"""Pure logic for bumping a container image reference inside a gitops YAML file.

No git, no filesystem — YAML text in, patched YAML text out — so it unit-tests
without a repo. The orchestration (read file, write file, commit, push) lives in
:func:`deputy.flows.gitops_update`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from io import StringIO

from ruamel.yaml import YAML


def parse_path(path: str) -> list[str | int]:
    """Parse a dotted image path into keys/indices.

    ``"spec.template.spec.containers.0.image"`` ->
    ``["spec", "template", "spec", "containers", 0, "image"]``. A segment that is
    all digits (optionally leading ``-``) becomes a list index; everything else
    stays a mapping key.
    """
    parts: list[str | int] = []
    for seg in path.split("."):
        if not seg:
            raise ValueError(f"empty segment in image path {path!r}")
        parts.append(int(seg) if seg.lstrip("-").isdigit() else seg)
    return parts


def _set_at(node: object, path: list[str | int], value: str) -> None:
    cur = node
    for key in path[:-1]:
        cur = cur[key]  # type: ignore[index]
    cur[path[-1]] = value  # type: ignore[index]


def detect_sequence_indent(yaml_text: str) -> tuple[int, int]:
    """The (sequence, offset) ruamel needs to reproduce this file's list style.

    Kubernetes manifests are written both ways and neither is wrong::

        containers:            containers:
        - name: x                - name: x

    ruamel cannot infer this. It reformats every block sequence to whatever it
    was configured with, so hardcoding one convention rewrites the indentation of
    the entire file on any bump -- burying a one-line image change in a diff of
    dozens of lines, which is how a wrong tag slips through review.

    Learns from the first block sequence in the file. A file mixing both styles
    is already inconsistent and cannot be reproduced exactly either way. Falls
    back to ruamel's expanded defaults when there is no sequence to learn from.
    """
    lines = yaml_text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.endswith(":") or stripped.startswith(("#", "-")):
            continue
        key_indent = len(line) - len(line.lstrip(" "))
        for nxt in lines[i + 1 :]:
            t = nxt.strip()
            if not t or t.startswith("#"):
                continue
            if t.startswith("- ") or t == "-":
                offset = len(nxt) - len(nxt.lstrip(" ")) - key_indent
                if offset >= 0:
                    return offset + 2, offset
            break
    return 4, 2


def _round_trip_yaml(yaml_text: str) -> YAML:
    """A ruamel round-trip loader/dumper that reproduces ``yaml_text``'s style.

    Match the style the FILE already uses rather than a fixed convention, so a
    bump touches only the image line instead of reflowing every list. Both k8s
    styles are common and ruamel reformats to whatever it is told, so guessing
    rewrites the whole file. Wide width keeps long image refs on one line.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    sequence, offset = detect_sequence_indent(yaml_text)
    yaml.indent(mapping=2, sequence=sequence, offset=offset)
    yaml.width = 4096
    return yaml


# Pod-spec lists whose entries are containers. They are searched at any depth, so
# one selection works for a Deployment, StatefulSet, Job, CronJob or bare Pod.
CONTAINER_LIST_KEYS = ("initContainers", "containers")


def _named_containers(node: object) -> Iterator[dict]:
    """Every container mapping: an entry with a ``name`` under a container list."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CONTAINER_LIST_KEYS and isinstance(value, list):
                yield from (c for c in value if isinstance(c, dict) and "name" in c)
            else:
                yield from _named_containers(value)
    elif isinstance(node, list):
        for item in node:
            yield from _named_containers(item)


def container_images(yaml_text: str, names: Sequence[str]) -> list[tuple[str, str]]:
    """``(container name, image)`` for each container in ``yaml_text`` named in ``names``.

    File order, every document searched. Containers that are not named are never
    read, so a sidecar or an init helper on some other image cannot pass for the pin.
    """
    wanted = set(names)
    found: list[tuple[str, str]] = []
    for doc in YAML().load_all(yaml_text):
        for container in _named_containers(doc):
            if container["name"] in wanted:
                found.append((str(container["name"]), str(container.get("image", ""))))
    return found


def set_container_images(yaml_text: str, names: Sequence[str], image: str) -> tuple[str, int]:
    """Set the whole ``image`` of every container named in ``names`` to ``image``.

    The full reference is written -- registry, repository and tag -- whatever the
    field held before. A container that was pointed at another registry (a
    development build, say) is therefore put back on the release image, where a
    tag spliced into the old reference would have produced an image that does not
    exist. Returns ``(patched_text, count)``; raises ``ValueError`` when no
    container matches, so a renamed container fails loudly.

    The YAML is parsed only to *locate* each value (ruamel records every value's
    line and column); the new reference is then written over exactly that span of
    the original text. Re-dumping the document instead would normalise the whole
    file -- ``{ name: x }`` becomes ``{name: x}``, an anchor nothing aliases is
    dropped -- and bury a one-line change in a diff of unrelated lines. Quotes
    around the old value are kept. A value the span check cannot confirm (a
    multi-line scalar, say) raises instead of risking a corrupted manifest.
    """
    wanted = set(names)
    lines = yaml_text.splitlines(keepends=True)
    spans: list[tuple[int, int, int, str]] = []  # (line, column, old length, quote)
    for doc in YAML().load_all(yaml_text):
        for container in _named_containers(doc):
            name = container["name"]
            if name not in wanted:
                continue
            if "image" not in container:
                raise ValueError(f"container {name!r} has no image field to set")
            line, col = container.lc.value("image")
            old = str(container["image"])
            text = lines[line]
            quote = text[col] if text[col : col + 1] in ("'", '"') else ""
            start = col + len(quote)
            if text[start : start + len(old)] != old or (
                quote and text[start + len(old) : start + len(old) + 1] != quote
            ):
                raise ValueError(
                    f"container {name!r}: image at line {line + 1} is not a single-line "
                    "scalar deputy can rewrite in place"
                )
            spans.append((line, col, len(old) + 2 * len(quote), quote))
    if not spans:
        raise ValueError(f"no container named any of {sorted(wanted)} found to patch")

    # Right to left, so an earlier span on the same line (a flow mapping) keeps
    # its column.
    for line, col, length, quote in sorted(spans, reverse=True):
        text = lines[line]
        lines[line] = text[:col] + quote + image + quote + text[col + length :]
    return "".join(lines), len(spans)


def set_image(yaml_text: str, *, kind: str, image_path: str, image: str) -> tuple[str, int]:
    """Set the image at ``image_path`` to ``image`` in every document of
    ``yaml_text`` whose top-level ``kind`` matches.

    Returns ``(patched_text, matched)`` where ``matched`` is the number of
    documents updated. Comments, key order, and quoting are preserved (ruamel
    round-trip). Raises ``ValueError`` if no document of that kind is found, so a
    typo'd kind fails loudly instead of pushing an unchanged file.
    """
    yaml = _round_trip_yaml(yaml_text)
    docs = list(yaml.load_all(yaml_text))
    path = parse_path(image_path)

    matched = 0
    for doc in docs:
        if doc is None or doc.get("kind") != kind:
            continue
        _set_at(doc, path, image)
        matched += 1

    if matched == 0:
        raise ValueError(f"no YAML document with kind={kind!r} found to patch")

    buf = StringIO()
    yaml.dump_all(docs, buf)
    return buf.getvalue(), matched
