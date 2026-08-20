"""Tests for the `deputy sshkey` helper — pure logic + the injected flow."""

from __future__ import annotations

import pathlib

import pytest

from deputy.flows import create_sshkey
from deputy.sshkey import (
    key_basename,
    keygen_argv,
    load_stored_email,
    resolve_email,
    save_stored_email,
    slugify_email,
    unique_path,
)


def test_slugify_email_is_filename_safe():
    assert slugify_email("Dev.User+ci@Example.COM") == "dev_user_ci_at_example_com"
    assert slugify_email("a@b.c") == "a_at_b_c"
    assert slugify_email("!!!") == "key"  # degenerate input still yields something


def test_key_basename():
    assert key_basename("dev@example.com") == "deputy_ed25519_dev_at_example_com"


def test_keygen_argv_is_passphraseless_ed25519():
    p = pathlib.Path("/keys/id")
    argv = keygen_argv(p, "dev@example.com")
    assert argv == ["ssh-keygen", "-t", "ed25519", "-C", "dev@example.com", "-f", str(p), "-N", ""]


def test_resolve_email_prefers_flag_then_stored():
    assert resolve_email("flag@example.com", "stored@example.com") == "flag@example.com"
    assert resolve_email(None, "stored@example.com") == "stored@example.com"
    assert resolve_email("  spaced@example.com  ", None) == "spaced@example.com"


def test_resolve_email_errors_when_nothing_available():
    with pytest.raises(ValueError, match="pass --email"):
        resolve_email(None, None)


def test_unique_path_bumps_past_existing_keys_and_pubs():
    base = "deputy_ed25519_dev_at_example_com"
    d = pathlib.Path("/keys")
    taken = {d / base, d / (base + "_1.pub")}  # base taken; _1 taken via its .pub sibling

    got = unique_path(d, base, exists=lambda p: p in taken)

    assert got == d / f"{base}_2"


def test_unique_path_first_slot_when_free():
    assert unique_path(pathlib.Path("/k"), "b", exists=lambda p: False) == pathlib.Path("/k/b")


def test_stored_email_roundtrip(tmp_path):
    state = tmp_path / "nested" / "sshkey.json"
    assert load_stored_email(state) is None  # missing file
    save_stored_email(state, "dev@example.com")
    assert load_stored_email(state) == "dev@example.com"


def test_load_stored_email_tolerates_garbage(tmp_path):
    state = tmp_path / "sshkey.json"
    state.write_text("not json", encoding="utf-8")
    assert load_stored_email(state) is None


def _fake_adapters(existing=(), files=None):
    """Return (kwargs, calls) wiring create_sshkey to in-memory fakes."""
    calls = {"runner": [], "saved": [], "made_dirs": []}
    files = dict(files or {})

    def runner(cmd):
        calls["runner"].append(list(cmd))
        # emulate ssh-keygen writing the key pair
        path = cmd[cmd.index("-f") + 1]
        files[path] = "PRIVATE-KEY-BODY\n"
        files[path + ".pub"] = "ssh-ed25519 AAAA... dev@example.com\n"

    kwargs = dict(
        load_email=lambda p: None,
        save_email=lambda p, e: calls["saved"].append((str(p), e)),
        exists=lambda p: str(p) in set(existing),
        make_dir=lambda d: calls["made_dirs"].append(str(d)),
        runner=runner,
        read_text=lambda p: files[str(p)],
    )
    return kwargs, calls


def test_create_sshkey_generates_and_remembers_email(tmp_path):
    kwargs, calls = _fake_adapters()
    result = create_sshkey(
        cli_email="dev@example.com",
        key_dir=tmp_path,
        state_path=tmp_path / "state.json",
        print_private=False,
        **kwargs,
    )

    priv = tmp_path / "deputy_ed25519_dev_at_example_com"
    assert result["private_key_path"] == str(priv)
    assert result["public_key_path"] == str(priv) + ".pub"
    assert result["email"] == "dev@example.com"
    assert result["email_source"] == "flag"
    assert result["public_key"].startswith("ssh-ed25519")
    assert "private_key" not in result  # not printed unless asked
    assert calls["runner"] == [keygen_argv(priv, "dev@example.com")]
    assert calls["saved"] == [(str(tmp_path / "state.json"), "dev@example.com")]
    assert calls["made_dirs"] == [str(tmp_path)]


def test_create_sshkey_uses_remembered_email_and_can_print_private(tmp_path):
    kwargs, _ = _fake_adapters()
    kwargs["load_email"] = lambda p: "remembered@example.com"

    result = create_sshkey(
        cli_email=None,
        key_dir=tmp_path,
        state_path=tmp_path / "state.json",
        print_private=True,
        **kwargs,
    )

    assert result["email"] == "remembered@example.com"
    assert result["email_source"] == "remembered"
    assert result["private_key"] == "PRIVATE-KEY-BODY\n"


def test_create_sshkey_picks_unique_name_when_key_exists(tmp_path):
    base = tmp_path / "deputy_ed25519_dev_at_example_com"
    kwargs, _ = _fake_adapters(existing=[str(base)])

    result = create_sshkey(
        cli_email="dev@example.com",
        key_dir=tmp_path,
        state_path=tmp_path / "state.json",
        print_private=False,
        **kwargs,
    )

    assert result["private_key_path"] == str(base) + "_1"
