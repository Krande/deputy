"""A bump must touch the image line and nothing else, in either k8s list style.

Kubernetes manifests are written both ways and neither is wrong::

    containers:            containers:
    - name: x                - name: x

ruamel cannot infer which a file uses — it reformats every block sequence to
whatever it was configured with. So a fixed configuration silently rewrites the
indentation of the whole file on any bump.

That is not cosmetic. A gitops write-back is meant to be a reviewable one-line
change; when it arrives as "38 insertions, 38 deletions" the actual change is
buried, and a wrong image tag reads exactly like whitespace. It happened: a
real bump reflowed a 76-line manifest and the version regression inside it went
unnoticed.
"""

from __future__ import annotations

from deputy.gitops import detect_sequence_indent, set_image

PATH = "spec.template.spec.containers.0.image"

COMPACT = """apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: app
        image: reg.example/app:1.1.6
        ports:
        - containerPort: 80
"""

EXPANDED = """apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: app
          image: reg.example/app:1.1.6
          ports:
            - containerPort: 80
"""


def _changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    # strict=False deliberately: if a regression DID change the line count, this
    # helper should still report which lines differ so the failure is readable.
    # strict=True would raise a ValueError instead, hiding it. The line count is
    # asserted separately, by test_line_count_is_unchanged_in_both_styles.
    return [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=False) if a != b]


def test_detects_compact_style():
    assert detect_sequence_indent(COMPACT) == (2, 0)


def test_detects_expanded_style():
    assert detect_sequence_indent(EXPANDED) == (4, 2)


def test_falls_back_when_there_is_no_sequence_to_learn_from():
    # ruamel's expanded defaults. Nothing to preserve, so nothing to get wrong.
    assert detect_sequence_indent("kind: Deployment\nspec:\n  replicas: 1\n") == (4, 2)


def test_a_comment_between_key_and_item_does_not_confuse_it():
    doc = "containers:\n  # the app\n  - name: app\n"
    assert detect_sequence_indent(doc) == (4, 2)


def test_compact_file_stays_compact_and_changes_one_line():
    out, matched = set_image(
        COMPACT, kind="Deployment", image_path=PATH, image="reg.example/app:1.1.7"
    )
    assert matched == 1
    changed = _changed_lines(COMPACT, out)
    assert len(changed) == 1, f"expected a one-line diff, got {changed}"
    assert changed[0][1].strip() == "image: reg.example/app:1.1.7"
    # The list style itself must survive, not just the line count.
    assert "\n      - name: app\n" in out


def test_expanded_file_stays_expanded_and_changes_one_line():
    out, matched = set_image(
        EXPANDED, kind="Deployment", image_path=PATH, image="reg.example/app:1.1.7"
    )
    assert matched == 1
    changed = _changed_lines(EXPANDED, out)
    assert len(changed) == 1, f"expected a one-line diff, got {changed}"
    assert "\n        - name: app\n" in out


def test_line_count_is_unchanged_in_both_styles():
    # A reflow shows up as a changed line count when a folded scalar wraps
    # differently, which is the failure that is easiest to miss in review.
    for doc in (COMPACT, EXPANDED):
        out, _ = set_image(doc, kind="Deployment", image_path=PATH, image="reg.example/app:1.1.7")
        assert len(out.splitlines()) == len(doc.splitlines())


def test_nested_sequences_keep_their_style_too():
    # `ports:` is a second sequence at a deeper level; a fixed configuration
    # reflows it as readily as the first.
    out, _ = set_image(COMPACT, kind="Deployment", image_path=PATH, image="reg.example/app:1.1.7")
    assert "\n        - containerPort: 80\n" in out
