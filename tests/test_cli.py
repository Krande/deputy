"""Tests for the CLI wiring of gitops-update (config + flags + precedence)."""

from __future__ import annotations

import pytest

from deputy import cli

TOML = """\
[images]
viewer = "ablacr.azurecr.io/asa-adapy-viewer-capacity"

[[gitops]]
name = "beta"
image = "viewer"
repo_dir = "gitops"
file = "cluster_test/asa-viewer/asa-viewer-beta.yaml"
kind = "Deployment"
image_path = "spec.template.spec.containers.0.image"
message = "chore({name}): deploy {tag}"

[[gitops]]
name = "worker"
image = "ghcr.io/o/worker"
file = "worker.yaml"
kind = "Deployment"
image_path = "spec.template.spec.containers.0.image"
"""


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "deputy.toml"
    p.write_text(TOML, encoding="utf-8")
    return str(p)


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_gitops_update(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(cli, "gitops_update", fake_gitops_update)
    return calls


def test_target_composes_image_and_templates_message(captured, cfg_file):
    rc = cli.main(["gitops-update", "--config", cfg_file, "--target", "beta", "--tag", "sha-1"])
    assert rc == 0
    (call,) = captured
    assert call["image"] == "ablacr.azurecr.io/asa-adapy-viewer-capacity:sha-1"
    assert call["file"] == "cluster_test/asa-viewer/asa-viewer-beta.yaml"
    assert call["kind"] == "Deployment"
    assert call["repo_dir"] == "gitops"
    assert call["message"] == "chore(beta): deploy sha-1"


def test_all_bumps_every_target(captured, cfg_file):
    cli.main(["gitops-update", "--config", cfg_file, "--all", "--tag", "sha-9"])
    names = {c["image"] for c in captured}
    assert names == {
        "ablacr.azurecr.io/asa-adapy-viewer-capacity:sha-9",
        "ghcr.io/o/worker:sha-9",
    }


def test_cli_flag_overrides_toml(captured, cfg_file):
    cli.main(
        [
            "gitops-update",
            "--config",
            cfg_file,
            "--target",
            "beta",
            "--tag",
            "sha-1",
            "--file",
            "override.yaml",
            "--message",
            "custom {tag}",
        ]
    )
    (call,) = captured
    assert call["file"] == "override.yaml"  # CLI beats deputy.toml
    assert call["message"] == "custom sha-1"


def test_image_flag_overrides_composed_ref(captured, cfg_file):
    cli.main(
        ["gitops-update", "--config", cfg_file, "--target", "beta", "--image", "ghcr.io/x/y:1.0"]
    )
    (call,) = captured
    assert call["image"] == "ghcr.io/x/y:1.0"


def test_adhoc_mode_passes_flags_through(captured):
    cli.main(
        [
            "gitops-update",
            "--image",
            "ghcr.io/o/a:1.2.3",
            "--file",
            "d.yaml",
            "--kind",
            "Deployment",
            "--image-path",
            "spec.template.spec.containers.0.image",
        ]
    )
    (call,) = captured
    assert call["image"] == "ghcr.io/o/a:1.2.3"
    assert call["file"] == "d.yaml"


def test_target_and_all_are_mutually_exclusive(captured, cfg_file):
    with pytest.raises(SystemExit):
        cli.main(["gitops-update", "--config", cfg_file, "--all", "--target", "beta", "--tag", "x"])


def test_target_without_tag_or_image_fails(captured, cfg_file):
    with pytest.raises(SystemExit):
        cli.main(["gitops-update", "--config", cfg_file, "--target", "beta"])


def test_adhoc_missing_required_flags_fails(captured):
    with pytest.raises(SystemExit):
        cli.main(["gitops-update", "--image", "ghcr.io/o/a:1"])  # no --file/--kind/--image-path
