"""Tests for the deputy.toml config layer + built-in release defaults."""

from __future__ import annotations

import tomllib

import pytest

from deputy.config import (
    RELEASE_DEFAULTS,
    compose_image,
    deep_merge,
    fill_template,
    find_release_watch_target,
    find_target,
    gitops_targets,
    load_config,
    release_watch_targets,
    render_release_config,
    resolve_default_label,
    resolve_image_ref,
)

TOML = """\
[images]
app = "registry.example.com/web-app"

[[gitops]]
name = "web-app-beta"
image = "app"
file = "clusters/prod/web-app.yaml"
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

[[release_watch]]
name = "some-lib"
repo = "owner/some-lib"
file = "requirements.txt"
pattern = 'some-lib==([0-9.]+)'

[[release_watch]]
name = "my-service"
repo = "owner/my-service"
file = "deps.txt"
pattern = 'my-service@v([0-9.]+)'
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
    assert resolve_image_ref(cfg, "app") == "registry.example.com/web-app"
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


def test_release_watch_targets_read(tmp_path):
    cfg = load_config(_write(tmp_path))
    targets = release_watch_targets(cfg)
    assert [t["name"] for t in targets] == ["some-lib", "my-service"]
    assert targets[0]["repo"] == "owner/some-lib"


def test_release_watch_targets_empty_config():
    assert release_watch_targets({}) == []


def test_find_release_watch_target_and_missing(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert find_release_watch_target(cfg, "my-service")["file"] == "deps.txt"
    with pytest.raises(KeyError):
        find_release_watch_target(cfg, "nope")


# ── [pr_review].default_label ─────────────────────────────────────────────────


def _with_default_label(value: str) -> str:
    """TOML above, with default_label added to its [pr_review] table."""
    return TOML.replace("[pr_review]\n", f'[pr_review]\ndefault_label = "{value}"\n')


def test_default_label_falls_back_to_release_skip_when_unconfigured(monkeypatch):
    monkeypatch.delenv("DEPUTY_DEFAULT_LABEL", raising=False)
    assert resolve_default_label({}) == "release-skip"
    assert resolve_default_label({"pr_review": {"marker": "<!-- X -->"}}) == "release-skip"


def test_default_label_read_from_pr_review_table(tmp_path, monkeypatch):
    monkeypatch.delenv("DEPUTY_DEFAULT_LABEL", raising=False)
    cfg = load_config(_write(tmp_path, _with_default_label("release-auto")))
    assert resolve_default_label(cfg) == "release-auto"


def test_default_label_env_beats_toml(tmp_path, monkeypatch):
    cfg = load_config(_write(tmp_path, _with_default_label("release-auto")))
    monkeypatch.setenv("DEPUTY_DEFAULT_LABEL", "release-minor")
    assert resolve_default_label(cfg) == "release-minor"


def test_empty_env_counts_as_unset(tmp_path, monkeypatch):
    cfg = load_config(_write(tmp_path, _with_default_label("release-auto")))
    monkeypatch.setenv("DEPUTY_DEFAULT_LABEL", "")  # unset Actions expression
    assert resolve_default_label(cfg) == "release-auto"


def test_invalid_default_label_in_toml_raises_with_the_valid_options(tmp_path, monkeypatch):
    monkeypatch.delenv("DEPUTY_DEFAULT_LABEL", raising=False)
    cfg = load_config(_write(tmp_path, _with_default_label("release-atuo")))
    with pytest.raises(ValueError) as exc:
        resolve_default_label(cfg)
    msg = str(exc.value)
    assert "release-atuo" in msg
    assert "[pr_review].default_label" in msg
    assert "release-auto" in msg and "release-skip" in msg


def test_invalid_default_label_in_env_raises(monkeypatch):
    monkeypatch.setenv("DEPUTY_DEFAULT_LABEL", "nope")
    with pytest.raises(ValueError) as exc:
        resolve_default_label({})
    assert "DEPUTY_DEFAULT_LABEL" in str(exc.value)


def test_default_label_is_not_a_release_table_key():
    # [release] is deep-merged into the generated semantic-release config, so a
    # deputy-only key must not live there.
    assert "default_label" not in RELEASE_DEFAULTS
    rendered = tomllib.loads(render_release_config({"default_label": "release-auto"}))
    assert "default_label" in rendered["tool"]["semantic_release"]  # ...it would leak
