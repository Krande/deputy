"""Tests for release-watch image targets: containers selected by name, full image refs."""

from __future__ import annotations

import pytest

from deputy.flows import release_watch
from deputy.gitops import container_images, set_container_images
from deputy.release_watch import image_target_containers, release_pin, split_image_ref
from fakes import FakeGitHubClient

RELEASE_IMAGE = "ghcr.io/example/app"
DEV_IMAGE = "registry.example.com/library/app:sha-abc1234"

API = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      initContainers:
        - name: wait-for-db
          image: busybox:1.36
        - name: migrate
          image: ghcr.io/example/app:1.2.3
          env: &env
            - name: LOG_LEVEL
              value: info
      containers:
        - name: api  # the web process
          image: ghcr.io/example/app:1.2.3
          env: *env
"""

WORKER = f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-worker
spec:
  template:
    spec:
      containers:
      - name: worker
        image: {DEV_IMAGE}
"""


# -- image reference parsing ---------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("ghcr.io/example/app:1.2.3", ("ghcr.io/example/app", "1.2.3")),
        ("registry.example.com:5000/app", ("registry.example.com:5000/app", None)),
        ("registry.example.com:5000/app:2.0.0", ("registry.example.com:5000/app", "2.0.0")),
        ("ghcr.io/example/app@sha256:abc", ("ghcr.io/example/app", None)),
        ("app:1.0@sha256:abc", ("app", "1.0")),
        ("busybox", ("busybox", None)),
    ],
)
def test_split_image_ref(ref, expected):
    assert split_image_ref(ref) == expected


def test_release_pin_reads_the_version_of_the_release_image():
    assert release_pin("ghcr.io/example/app:1.2.3", RELEASE_IMAGE) == "1.2.3"
    assert release_pin("ghcr.io/example/app:v1.2.3", RELEASE_IMAGE) == "1.2.3"


def test_release_pin_is_none_off_the_release_image():
    assert release_pin(DEV_IMAGE, RELEASE_IMAGE) is None  # another registry
    assert release_pin("ghcr.io/example/app:sha-abc1234", RELEASE_IMAGE) is None  # not a version
    assert release_pin("ghcr.io/example/app:latest", RELEASE_IMAGE) is None
    assert release_pin("ghcr.io/example/app", RELEASE_IMAGE) is None  # no tag


# -- target validation -----------------------------------------------------------


def test_image_target_needs_containers():
    with pytest.raises(KeyError, match="containers"):
        image_target_containers({"name": "app", "image": RELEASE_IMAGE})


def test_image_target_rejects_a_pattern_too():
    with pytest.raises(KeyError, match="not both"):
        image_target_containers(
            {"name": "app", "image": RELEASE_IMAGE, "pattern": "x", "containers": ["api"]}
        )


def test_image_target_rejects_an_unknown_on_other_image():
    with pytest.raises(KeyError, match="on_other_image"):
        image_target_containers(
            {"name": "app", "image": RELEASE_IMAGE, "containers": ["api"], "on_other_image": "x"}
        )


# -- YAML: reading and writing container images ----------------------------------


def test_container_images_reads_only_the_named_containers():
    assert container_images(API, ["migrate", "api"]) == [
        ("migrate", "ghcr.io/example/app:1.2.3"),
        ("api", "ghcr.io/example/app:1.2.3"),
    ]


def test_set_container_images_writes_the_full_ref_and_keeps_the_rest():
    out, count = set_container_images(API, ["migrate", "api"], "ghcr.io/example/app:1.3.0")
    assert count == 2
    assert out.count("image: ghcr.io/example/app:1.3.0") == 2
    assert "image: busybox:1.36" in out  # an unselected init helper is untouched
    assert "# the web process" in out
    assert "env: &env" in out and "env: *env" in out  # anchors survive the round trip


def test_set_container_images_replaces_another_registry_entirely():
    out, count = set_container_images(WORKER, ["worker"], "ghcr.io/example/app:1.3.0")
    assert count == 1
    assert "registry.example.com" not in out
    assert "      - name: worker\n        image: ghcr.io/example/app:1.3.0\n" in out


def test_set_container_images_fails_loud_when_nothing_matches():
    with pytest.raises(ValueError, match="no container"):
        set_container_images(API, ["renamed"], "ghcr.io/example/app:1.3.0")


def _changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new)  # an in-place rewrite never adds or drops lines
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


def test_set_container_images_changes_only_the_image_lines():
    # A re-dump would normalise `{ name: x }` to `{name: x}` and drop the unused
    # anchor; the PR diff has to be the image lines and nothing else.
    text = """\
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: worker
          image: registry.example.com/library/app:sha-abc1234   # dev build
          env: &env
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef: { name: app-secrets, key: db-password }
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
"""
    out, count = set_container_images(text, ["worker"], "ghcr.io/example/app:1.3.0")
    assert count == 1
    assert _changed_lines(text, out) == [
        (
            "          image: registry.example.com/library/app:sha-abc1234   # dev build",
            "          image: ghcr.io/example/app:1.3.0   # dev build",
        )
    ]


def test_set_container_images_keeps_quotes():
    text = 'kind: Pod\nspec:\n  containers:\n    - name: api\n      image: "ghcr.io/example/app:1.2.3"\n'
    out, _ = set_container_images(text, ["api"], "ghcr.io/example/app:1.3.0")
    assert '      image: "ghcr.io/example/app:1.3.0"\n' in out


def test_set_container_images_handles_flow_containers_on_one_line():
    text = """\
kind: Pod
spec:
  containers: [{name: api, image: ghcr.io/example/app:1.2.3}, {name: side, image: busybox:1.36}]
  initContainers: [{name: migrate, image: ghcr.io/example/app:1.2.3}]
"""
    out, count = set_container_images(text, ["api", "migrate"], "ghcr.io/example/app:10.0.0")
    assert count == 2
    assert (
        "  containers: [{name: api, image: ghcr.io/example/app:10.0.0}, "
        "{name: side, image: busybox:1.36}]\n"
    ) in out
    assert "  initContainers: [{name: migrate, image: ghcr.io/example/app:10.0.0}]\n" in out


def test_set_container_images_across_documents():
    text = f"{API}---\n{WORKER}"
    out, count = set_container_images(text, ["api", "worker"], "ghcr.io/example/app:1.3.0")
    assert count == 2
    changed = _changed_lines(text, out)
    assert [b.strip() for _, b in changed] == [
        "image: ghcr.io/example/app:1.3.0",
        "image: ghcr.io/example/app:1.3.0",
    ]
    assert "image: ghcr.io/example/app:1.2.3" in out  # migrate was not selected


def test_set_container_images_refuses_a_multiline_image():
    text = "kind: Pod\nspec:\n  containers:\n    - name: api\n      image: >-\n        ghcr.io/example/app:1.2.3\n"
    with pytest.raises(ValueError, match="in place"):
        set_container_images(text, ["api"], "ghcr.io/example/app:1.3.0")


# -- flow ------------------------------------------------------------------------


def _target(**over):
    base = {
        "name": "app",
        "repo": "example/app",
        "image": RELEASE_IMAGE,
        "files": ["deploy/api.yaml", "deploy/worker.yaml"],
        "containers": ["migrate", "api", "worker"],
        "labels": [],
    }
    base.update(over)
    return base


def _run(target, files, *, release="v1.3.0", dry_run=False):
    client = FakeGitHubClient()
    client.releases["example/app"] = release
    commits: list[dict] = []
    rc = release_watch(
        [target],
        client,
        repo_dir="repo",
        dry_run=dry_run,
        reader=lambda p: files[p],
        writer=lambda p, text: files.__setitem__(p, text),
        commit_fn=lambda cwd, branch, paths, message, base, push: commits.append(
            {"branch": branch, "paths": list(paths), "message": message, "base": base}
        ),
    )
    return rc, client, commits


def _files(api=API, worker=WORKER):
    return {"repo/deploy/api.yaml": api, "repo/deploy/worker.yaml": worker}


def _on_release(text, version):
    return text.replace(DEV_IMAGE, f"{RELEASE_IMAGE}:{version}").replace(
        f"{RELEASE_IMAGE}:1.2.3", f"{RELEASE_IMAGE}:{version}"
    )


def test_new_release_sets_every_selected_container_including_a_dev_build():
    files = _files()
    rc, client, commits = _run(_target(), files)

    assert rc == 0
    assert files["repo/deploy/api.yaml"].count("image: ghcr.io/example/app:1.3.0") == 2
    assert "image: ghcr.io/example/app:1.3.0" in files["repo/deploy/worker.yaml"]
    assert commits == [
        {
            "branch": "deputy/release-watch/app",
            "paths": ["deploy/api.yaml", "deploy/worker.yaml"],
            "message": "chore(app): set image to ghcr.io/example/app:1.3.0",
            # An image target branches from base too, not from whatever the
            # previous target of the same run left the checkout on.
            "base": "main",
        }
    ]
    (pr,) = client.pulls
    assert pr.title == "chore: bump app to 1.3.0"
    assert DEV_IMAGE in pr.body and "ghcr.io/example/app:1.3.0" in pr.body


def test_up_to_date_release_images_are_a_noop():
    files = _files(api=_on_release(API, "1.3.0"), worker=_on_release(WORKER, "1.3.0"))
    before = dict(files)
    rc, client, commits = _run(_target(), files)
    assert rc == 0
    assert files == before
    assert commits == [] and client.pulls == []


def test_a_dev_build_on_the_latest_release_line_is_put_back_by_default():
    # Everything else already runs the latest release; the worker runs a dev build.
    files = _files(api=_on_release(API, "1.3.0"))
    rc, client, _ = _run(_target(), files)
    assert rc == 0
    assert "image: ghcr.io/example/app:1.3.0" in files["repo/deploy/worker.yaml"]
    assert len(client.pulls) == 1


def test_on_other_image_skip_leaves_a_dev_build_alone():
    files = _files()
    before = dict(files)
    rc, client, commits = _run(_target(on_other_image="skip"), files)
    assert rc == 0
    assert files == before
    assert commits == [] and client.pulls == []


def test_tag_template_shapes_the_written_tag():
    files = _files()
    _run(_target(tag_template="v{version}"), files)
    assert "image: ghcr.io/example/app:v1.3.0" in files["repo/deploy/worker.yaml"]


def test_a_container_found_in_no_file_fails_before_writing():
    files = _files()
    before = dict(files)
    rc, client, commits = _run(_target(containers=["migrate", "api", "worker", "gone"]), files)
    assert rc == 1
    assert files == before
    assert commits == [] and client.pulls == []


def test_a_file_with_no_selected_container_fails_before_writing():
    files = _files()
    before = dict(files)
    rc, _, commits = _run(_target(containers=["migrate", "api"]), files)
    assert rc == 1
    assert files == before
    assert commits == []


def test_dry_run_writes_nothing():
    files = _files()
    before = dict(files)
    rc, client, commits = _run(_target(), files, dry_run=True)
    assert rc == 0
    assert files == before
    assert commits == [] and client.pulls == []
