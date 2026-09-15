"""Tests for the CLI wiring of release-watch (target selection + flag passing)."""

from __future__ import annotations

import pytest

from deputy import cli

TOML = """\
[[release_watch]]
name = "some-lib"
repo = "owner/some-lib"
file = "requirements.txt"
pattern = 'some-lib==([0-9]+\\.[0-9]+\\.[0-9]+)'

[[release_watch]]
name = "my-service"
repo = "owner/my-service"
file = "deps/service.txt"
pattern = 'my-service@v([0-9.]+)'
labels = ["deps"]
"""


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "deputy.toml"
    p.write_text(TOML, encoding="utf-8")
    return str(p)


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_release_watch(targets, client, **kwargs):
        calls.append({"targets": targets, "client": client, **kwargs})
        return 0

    monkeypatch.setattr(cli, "release_watch", fake_release_watch)
    # a token so the client constructs; GITHUB_REPOSITORY is optional.
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/consumer")
    return calls


def test_all_selects_every_target(captured, cfg_file):
    rc = cli.main(["release-watch", "--config", cfg_file, "--all"])
    assert rc == 0
    (call,) = captured
    names = [t["name"] for t in call["targets"]]
    assert names == ["some-lib", "my-service"]
    assert call["base"] == "main"
    assert call["dry_run"] is False


def test_target_selects_named(captured, cfg_file):
    cli.main(["release-watch", "--config", cfg_file, "--target", "my-service"])
    (call,) = captured
    assert [t["name"] for t in call["targets"]] == ["my-service"]


def test_dry_run_and_base_flags_pass_through(captured, cfg_file):
    cli.main(["release-watch", "--config", cfg_file, "--all", "--dry-run", "--base", "develop"])
    (call,) = captured
    assert call["dry_run"] is True
    assert call["base"] == "develop"


def test_target_and_all_mutually_exclusive(captured, cfg_file):
    with pytest.raises(SystemExit):
        cli.main(["release-watch", "--config", cfg_file, "--all", "--target", "some-lib"])


def test_neither_target_nor_all_fails(captured, cfg_file):
    with pytest.raises(SystemExit):
        cli.main(["release-watch", "--config", cfg_file])


def test_all_with_no_targets_fails(captured, tmp_path):
    empty = tmp_path / "deputy.toml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["release-watch", "--config", str(empty), "--all"])


def test_unknown_target_name_raises(captured, cfg_file):
    with pytest.raises(KeyError):
        cli.main(["release-watch", "--config", cfg_file, "--target", "nope"])


def test_dry_run_without_repo_env_still_runs(monkeypatch, cfg_file):
    calls = []
    monkeypatch.setattr(cli, "release_watch", lambda *a, **k: calls.append(k) or 0)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    rc = cli.main(["release-watch", "--config", cfg_file, "--all", "--dry-run"])
    assert rc == 0
    assert calls[0]["dry_run"] is True


# -- forge selection: where the PRs go vs where releases are looked up ---------


@pytest.fixture
def clean_forge_env(monkeypatch):
    for var in (
        "DEPUTY_API_URL",
        "GITHUB_API_URL",
        "DEPUTY_UPSTREAM_API_URL",
        "DEPUTY_UPSTREAM_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_public_github_with_one_client(captured, cfg_file, clean_forge_env):
    cli.main(["release-watch", "--config", cfg_file, "--all"])
    (call,) = captured
    assert call["client"]._api == "https://api.github.com"
    # no separate upstream: the flow falls back to the PR client
    assert call["upstream_client"] is None


def test_consumer_api_follows_actions_github_api_url(
    captured, cfg_file, clean_forge_env, monkeypatch
):
    # Forgejo Actions sets GITHUB_API_URL to the instance API.
    monkeypatch.setenv("GITHUB_API_URL", "https://git.example.com/api/v1")
    cli.main(["release-watch", "--config", cfg_file, "--all"])
    (call,) = captured
    assert call["client"]._api == "https://git.example.com/api/v1"


def test_deputy_api_url_beats_github_api_url(captured, cfg_file, clean_forge_env, monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://git.example.com/api/v1")
    monkeypatch.setenv("DEPUTY_API_URL", "https://other.example.com/api/v1/")
    cli.main(["release-watch", "--config", cfg_file, "--all"])
    (call,) = captured
    assert call["client"]._api == "https://other.example.com/api/v1"


def test_upstream_api_builds_a_separate_client(captured, cfg_file, clean_forge_env, monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://git.example.com/api/v1")
    monkeypatch.setenv("DEPUTY_UPSTREAM_API_URL", "https://api.github.com")
    monkeypatch.setenv("DEPUTY_UPSTREAM_TOKEN", "gh-read")
    cli.main(["release-watch", "--config", cfg_file, "--all"])
    (call,) = captured
    assert call["client"]._api == "https://git.example.com/api/v1"
    assert call["client"]._token == "x"
    upstream = call["upstream_client"]
    assert upstream._api == "https://api.github.com"
    assert upstream._token == "gh-read"


def test_upstream_without_token_is_anonymous(captured, cfg_file, clean_forge_env, monkeypatch):
    monkeypatch.setenv("DEPUTY_UPSTREAM_API_URL", "https://api.github.com")
    cli.main(["release-watch", "--config", cfg_file, "--all"])
    (call,) = captured
    # never the PR token: that one belongs to another forge
    assert call["upstream_client"]._token == ""
