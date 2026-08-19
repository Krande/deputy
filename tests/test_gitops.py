"""Tests for the gitops image-bump logic and flow."""

from __future__ import annotations

import pytest

from deputy.flows import gitops_update
from deputy.gitops import parse_path, set_image

DEPLOY = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app  # keep me
spec:
  template:
    spec:
      containers:
        - name: app
          image: ghcr.io/owner/app:0.1.0
"""


def test_parse_path_splits_keys_and_indices():
    assert parse_path("spec.template.spec.containers.0.image") == [
        "spec",
        "template",
        "spec",
        "containers",
        0,
        "image",
    ]


def test_set_image_updates_the_target_and_preserves_comments():
    out, matched = set_image(
        DEPLOY,
        kind="Deployment",
        image_path="spec.template.spec.containers.0.image",
        image="ghcr.io/owner/app:1.2.3",
    )
    assert matched == 1
    assert "image: ghcr.io/owner/app:1.2.3" in out
    assert "0.1.0" not in out
    assert "# keep me" in out  # round-trip preserved the comment


def test_set_image_only_touches_matching_kind():
    multi = (
        DEPLOY
        + """\
---
apiVersion: v1
kind: Service
metadata:
  name: app
spec:
  selector:
    app: app
"""
    )
    out, matched = set_image(
        multi,
        kind="Deployment",
        image_path="spec.template.spec.containers.0.image",
        image="ghcr.io/owner/app:9.9.9",
    )
    assert matched == 1
    assert "9.9.9" in out
    assert "kind: Service" in out  # the Service doc survived untouched


def test_set_image_raises_when_kind_absent():
    with pytest.raises(ValueError, match="kind='StatefulSet'"):
        set_image(
            DEPLOY,
            kind="StatefulSet",
            image_path="spec.template.spec.containers.0.image",
            image="x",
        )


def test_gitops_update_flow_reads_patches_writes_and_commits():
    files = {"gitops/deploy.yaml": DEPLOY}
    commits: list[dict] = []

    rc = gitops_update(
        repo_dir="gitops",
        file="deploy.yaml",
        kind="Deployment",
        image_path="spec.template.spec.containers.0.image",
        image="ghcr.io/owner/app:2.0.0",
        reader=lambda p: files[p],
        writer=lambda p, text: files.__setitem__(p, text),
        commit_fn=lambda cwd, paths, message, push: commits.append(
            {"cwd": cwd, "paths": list(paths), "message": message, "push": push}
        ),
    )

    assert rc == 0
    assert "ghcr.io/owner/app:2.0.0" in files["gitops/deploy.yaml"]
    assert commits == [
        {
            "cwd": "gitops",
            "paths": ["deploy.yaml"],
            "message": "chore(gitops): set Deployment image to ghcr.io/owner/app:2.0.0",
            "push": True,
        }
    ]


def test_gitops_update_flow_respects_no_push_and_custom_message():
    files = {"g/d.yaml": DEPLOY}
    commits: list[dict] = []

    gitops_update(
        repo_dir="g",
        file="d.yaml",
        kind="Deployment",
        image_path="spec.template.spec.containers.0.image",
        image="img:3",
        push=False,
        message="bump beta",
        reader=lambda p: files[p],
        writer=lambda p, text: files.__setitem__(p, text),
        commit_fn=lambda cwd, paths, message, push: commits.append(
            {"message": message, "push": push}
        ),
    )

    assert commits == [{"message": "bump beta", "push": False}]
