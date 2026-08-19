"""Tests for the deputy.toml config layer + built-in release defaults."""

from __future__ import annotations

import tomllib

import pytest

from deputy.config import (
    RELEASE_DEFAULTS,
    compose_image,
    deep_merge,
    fill_template,
    find_target,
    gitops_targets,
    load_config,
    render_release_config,
    resolve_image_ref,
)

TOML = """\
[images]
viewer = "ablacr.azurecr.io/asa-adapy-viewer-capacity"

[[gitops]]
name = "asa-viewer-beta"
image = "viewer"
file = "cluster_test/asa-viewer/asa-viewer-beta.yaml"
kind = "Deployment"
image_path = "spec.template.spec.containers.0.image"
message = "chore({name}): deploy {tag}"

[[gitops]]
name = "other"
image = "ghcr.io/o/a"
file = "b.yaml"
kind = "Deployment"
image_path = "spec.template.spec.containers.0.image"

[pr_review]
marker = "<!-- X -->"

[release]
tag_format = "release-{version}"
version_toml = ["action_config.toml:tool.action.version"]
"""


def _write(tmp_path, text=TOML):
    p = tmp_path / "deputy.toml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_deep_merge_recurses_and_replaces():
    base = {"a": 1, "nested": {"x": 1, "y": 2}, "list": [1, 2]}
    over = {"a": 9, "nested": {"y": 20, "z": 30}, "list": [3]}
    assert deep_merge(base, over) == {
        "a": 9,
        "nested": {"x": 1, "y": 20, "z": 30},
        "list": [3],  # lists replace, not append
    }


def test_load_config_missing_returns_empty():
    assert load_config("does/not/exist.toml") == {}


def test_load_config_reads_tables(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg["pr_review"]["marker"] == "<!-- X -->"
    assert len(cfg["gitops"]) == 2


def test_render_release_config_merges_defaults_with_overrides(tmp_path):
    cfg = load_config(_write(tmp_path))
    rendered = tomllib.loads(render_release_config(cfg["release"]))["tool"]["semantic_release"]
    # override applied
    assert rendered["tag_format"] == "release-{version}"
    assert rendered["version_toml"] == ["action_config.toml:tool.action.version"]
    # defaults preserved
    assert rendered["commit_parser"] == "angular"
    assert rendered["remote"]["ignore_token_for_push"] is True
    assert rendered["publish"]["upload_to_vcs_release"] is True


def test_render_release_config_defaults_only():
    rendered = tomllib.loads(render_release_config(None))["tool"]["semantic_release"]
    assert rendered["tag_format"] == RELEASE_DEFAULTS["tag_format"]
    assert rendered["version_toml"] == ["pyproject.toml:project.version"]


def test_resolve_image_ref_maps_or_passes_through(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert resolve_image_ref(cfg, "viewer") == "ablacr.azurecr.io/asa-adapy-viewer-capacity"
    assert resolve_image_ref(cfg, "ghcr.io/o/a") == "ghcr.io/o/a"  # not a key -> literal


def test_compose_image():
    assert compose_image("reg/app", "1.2.3") == "reg/app:1.2.3"
    assert compose_image("reg/app:already", None) == "reg/app:already"


def test_fill_template():
    assert fill_template("chore({name}): {tag}", name="beta", tag="v1") == "chore(beta): v1"


def test_find_target_and_missing(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert find_target(cfg, "other")["file"] == "b.yaml"
    with pytest.raises(KeyError):
        find_target(cfg, "nope")


def test_gitops_targets_empty_config():
    assert gitops_targets({}) == []
