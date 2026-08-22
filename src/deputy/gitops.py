"""Pure logic for bumping a container image reference inside a gitops YAML file.

No git, no filesystem — YAML text in, patched YAML text out — so it unit-tests
without a repo. The orchestration (read file, write file, commit, push) lives in
:func:`deputy.flows.gitops_update`.
"""

from __future__ import annotations

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


def set_image(yaml_text: str, *, kind: str, image_path: str, image: str) -> tuple[str, int]:
    """Set the image at ``image_path`` to ``image`` in every document of
    ``yaml_text`` whose top-level ``kind`` matches.

    Returns ``(patched_text, matched)`` where ``matched`` is the number of
    documents updated. Comments, key order, and quoting are preserved (ruamel
    round-trip). Raises ``ValueError`` if no document of that kind is found, so a
    typo'd kind fails loudly instead of pushing an unchanged file.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    # Match the style the FILE already uses rather than a fixed convention, so a
    # bump touches only the image line instead of reflowing every list. Both k8s
    # styles are common and ruamel reformats to whatever it is told, so guessing
    # rewrites the whole file. Wide width keeps long image refs on one line.
    sequence, offset = detect_sequence_indent(yaml_text)
    yaml.indent(mapping=2, sequence=sequence, offset=offset)
    yaml.width = 4096
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
