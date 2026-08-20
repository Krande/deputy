"""Tests for the pure release-watch logic: semver compare + pin rewrite."""

from __future__ import annotations

import pytest

from deputy.release_watch import (
    compare_versions,
    find_pinned,
    is_newer,
    normalize_version,
    parse_version,
    pick_latest_tag,
    replace_pinned,
)


def test_normalize_strips_leading_v():
    assert normalize_version("v1.2.3") == "1.2.3"
    assert normalize_version("V1.2.3") == "1.2.3"
    assert normalize_version("  1.2.3 ") == "1.2.3"
    assert normalize_version("1.2.3") == "1.2.3"


def test_parse_version_handles_partial_and_prerelease_and_build():
    assert parse_version("v1") == (1, 0, 0, None)
    assert parse_version("1.2") == (1, 2, 0, None)
    assert parse_version("1.2.3") == (1, 2, 3, None)
    assert parse_version("1.2.3-rc.1") == (1, 2, 3, "rc.1")
    assert parse_version("1.2.3+build.5") == (1, 2, 3, None)  # build metadata ignored


def test_parse_version_rejects_junk():
    assert parse_version("not-a-version") is None
    assert parse_version("latest") is None
    assert parse_version(None) is None
    assert parse_version("1.2.x") is None


def test_compare_core_precedence():
    assert compare_versions("1.2.3", "1.2.3") == 0
    assert compare_versions("1.2.4", "1.2.3") == 1
    assert compare_versions("1.3.0", "1.2.9") == 1
    assert compare_versions("2.0.0", "1.9.9") == 1
    assert compare_versions("1.2.3", "1.2.4") == -1
    # leading v and short forms normalise
    assert compare_versions("v1.2.0", "1.2") == 0


def test_compare_prerelease_precedence():
    # a prerelease is lower than its release
    assert compare_versions("1.0.0-rc.1", "1.0.0") == -1
    assert compare_versions("1.0.0", "1.0.0-rc.1") == 1
    # numeric identifiers compare numerically, and rank below alphanumeric
    assert compare_versions("1.0.0-alpha.2", "1.0.0-alpha.10") == -1
    assert compare_versions("1.0.0-alpha.1", "1.0.0-alpha.beta") == -1
    # more identifiers wins when the prefix matches
    assert compare_versions("1.0.0-alpha", "1.0.0-alpha.1") == -1


def test_compare_raises_on_junk():
    with pytest.raises(ValueError, match="not a version"):
        compare_versions("latest", "1.2.3")


def test_is_newer():
    assert is_newer("1.2.4", "1.2.3") is True
    assert is_newer("1.2.3", "1.2.3") is False
    assert is_newer("1.2.2", "1.2.3") is False
    assert is_newer("v2.0.0", "1.9.9") is True


def test_pick_latest_tag_skips_junk_and_returns_raw():
    tags = ["v1.2.0", "not-a-tag", "v1.10.0", "v1.9.0", "nightly"]
    assert pick_latest_tag(tags) == "v1.10.0"


def test_pick_latest_tag_prefers_release_over_prerelease():
    assert pick_latest_tag(["1.0.0-rc.1", "1.0.0", "0.9.0"]) == "1.0.0"


def test_pick_latest_tag_none_when_all_junk():
    assert pick_latest_tag(["latest", "stable", ""]) is None


REQUIREMENTS = "some-lib==1.2.3\nother-dep>=4.5.6  # pinned\n"


def test_find_pinned_uses_capture_group():
    assert find_pinned(REQUIREMENTS, r"some-lib==([0-9]+\.[0-9]+\.[0-9]+)") == "1.2.3"


def test_find_pinned_whole_match_without_group():
    assert find_pinned("version = 9.9.9", r"[0-9]+\.[0-9]+\.[0-9]+") == "9.9.9"


def test_find_pinned_returns_none_when_absent():
    assert find_pinned(REQUIREMENTS, r"missing==([0-9.]+)") is None


def test_replace_pinned_only_touches_capture_group():
    new_text, count = replace_pinned(REQUIREMENTS, r"some-lib==([0-9]+\.[0-9]+\.[0-9]+)", "1.3.0")
    assert count == 1
    assert "some-lib==1.3.0" in new_text
    assert "other-dep>=4.5.6  # pinned" in new_text  # untouched
    assert "1.2.3" not in new_text


def test_replace_pinned_preserves_surrounding_quotes_and_key():
    toml_line = 'dep = "owner/some-lib@v0.1.0"\n'
    new_text, count = replace_pinned(toml_line, r'some-lib@v([0-9.]+)"', "0.2.0")
    assert count == 1
    assert new_text == 'dep = "owner/some-lib@v0.2.0"\n'


def test_replace_pinned_multiple_occurrences():
    text = "pin 1.0.0 here and 1.0.0 there"
    new_text, count = replace_pinned(text, r"(1\.0\.0)", "2.0.0")
    assert count == 2
    assert new_text == "pin 2.0.0 here and 2.0.0 there"


def test_replace_pinned_no_group_replaces_whole_match():
    new_text, count = replace_pinned("v = 1.0.0", r"1\.0\.0", "2.0.0")
    assert count == 1
    assert new_text == "v = 2.0.0"
