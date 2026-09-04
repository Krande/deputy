import urllib.error

import pytest

from deputy.github import GitHubError, RestGitHubClient, upsert_sticky_comment
from fakes import FakeGitHubClient

MARK = "<!-- MARK -->"


def test_creates_comment_when_none_exists():
    client = FakeGitHubClient()
    cid = upsert_sticky_comment(client, 1, MARK, MARK + "\nhello")
    assert len(client.comments) == 1
    assert client.comments[0].id == cid


def test_updates_in_place_when_marker_present():
    client = FakeGitHubClient()
    first = client.create_comment(1, MARK + "\nold")
    cid = upsert_sticky_comment(client, 1, MARK, MARK + "\nnew")
    assert cid == first.id
    assert len(client.comments) == 1  # not duplicated
    assert client.comments[0].body == MARK + "\nnew"


def test_ignores_unrelated_comments():
    client = FakeGitHubClient()
    client.create_comment(1, "an ordinary human comment")
    upsert_sticky_comment(client, 1, MARK, MARK + "\nx")
    assert len(client.comments) == 2


# -- REST adapter: label removal -----------------------------------------------


def _stub_client(error: GitHubError | None = None):
    """A RestGitHubClient whose _request records calls instead of making them.

    `error` is raised by the FIRST request only. Recovery paths issue a second
    request that has to be allowed to succeed — ensure_label answers a 422 by
    PATCHing the colour — and a stub that raised on every call cannot model one.
    """
    client = RestGitHubClient("tok", "owner/repo")
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, body=None, repo=None):
        first = not calls
        calls.append((method, path))
        if error is not None and first:
            raise error
        return None

    client._request = fake_request  # type: ignore[method-assign]
    return client, calls


def _http_error(code: int) -> GitHubError:
    """What _request ACTUALLY raises.

    This used to build a bare urllib.error.HTTPError, which _request never lets
    escape — it wraps every one into a GitHubError. So the doubles raised a type
    the production handlers caught but production could not produce, and the
    tolerate-404 / tolerate-422 paths passed here while being dead in the field.
    """
    return GitHubError(f"stub failed: HTTP {code}", status=code)


def test_remove_label_deletes_the_named_label():
    client, calls = _stub_client()
    client.remove_label(7, "release-auto")
    assert calls == [("DELETE", "/issues/7/labels/release-auto")]


def test_remove_label_tolerates_a_label_that_is_already_gone():
    # A human removing it first is a race, not a failure worth reddening a check.
    client, _calls = _stub_client(_http_error(404))
    client.remove_label(7, "release-auto")


def test_remove_label_still_raises_on_a_real_failure():
    client, _calls = _stub_client(_http_error(403))
    with pytest.raises(GitHubError):
        client.remove_label(7, "release-auto")


# -- REST adapter: label creation ----------------------------------------------


def test_ensure_label_creates_a_missing_label():
    client, calls = _stub_client()
    client.ensure_label("release-auto", "ffff00")
    assert calls == [("POST", "/labels")]


def test_ensure_label_refreshes_the_colour_when_it_already_exists():
    # The regression that mattered: every repo that already carried the
    # release-* labels got HTTP 422 already_exists and failed EVERY pr-review.
    client, calls = _stub_client(_http_error(422))
    client.ensure_label("release-auto", "ffff00")
    assert calls[0] == ("POST", "/labels")
    assert calls[1] == ("PATCH", "/labels/release-auto")


def test_ensure_label_quotes_the_name_in_the_patch_path():
    client, calls = _stub_client(_http_error(422))
    client.ensure_label("needs triage", "ffff00")
    assert calls[1] == ("PATCH", "/labels/needs%20triage")


def test_ensure_label_still_raises_on_a_real_failure():
    client, _calls = _stub_client(_http_error(403))
    with pytest.raises(GitHubError):
        client.ensure_label("release-auto", "ffff00")


def test_github_error_status_is_none_when_unset():
    # Callers branch on .status; a plain raise must not look like a 404/422.
    assert GitHubError("boom").status is None
