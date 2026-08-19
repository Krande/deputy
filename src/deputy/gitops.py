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
    # Match the conventional k8s manifest style (block sequences indented under
    # their key, dash offset by 2) so a bump touches only the image line instead
    # of reflowing every list in the file. Wide width keeps long image refs on
    # one line.
    yaml.indent(mapping=2, sequence=4, offset=2)
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
