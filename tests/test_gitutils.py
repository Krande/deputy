"""Tests for the git helpers (runner injected, so nothing shells out)."""

from __future__ import annotations

from deputy.gitutils import stage_paths


def test_stage_paths_adds_without_committing():
    seen = []
    stage_paths(["a.json", "b.json"], runner=seen.append)
    assert seen == [["git", "add", "--", "a.json", "b.json"]]


def test_stage_paths_honours_cwd():
    seen = []
    stage_paths(["a.json"], cwd="repo", runner=seen.append)
    assert seen == [["git", "-C", "repo", "add", "--", "a.json"]]


def test_stage_paths_is_a_no_op_for_an_empty_list():
    seen = []
    stage_paths([], runner=seen.append)
    assert seen == []
