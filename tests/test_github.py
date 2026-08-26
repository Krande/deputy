import urllib.error

import pytest

from deputy.github import RestGitHubClient, upsert_sticky_comment
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


def _stub_client(error: urllib.error.HTTPError | None = None):
    """A RestGitHubClient whose _request records calls instead of making them."""
    client = RestGitHubClient("tok", "owner/repo")
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, body=None, repo=None):
        calls.append((method, path))
        if error is not None:
            raise error
        return None

    client._request = fake_request  # type: ignore[method-assign]
    return client, calls


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "msg", {}, None)  # type: ignore[arg-type]


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
    with pytest.raises(urllib.error.HTTPError):
        client.remove_label(7, "release-auto")
