"""Tests for the JSON version declarations (``[release].version_json``).

The point of the module under test is that it must never touch a dependency's
version, and must never reformat the file. Both properties are asserted here
against a lockfile shaped like a real one — 250 dependencies, many of them
deliberately pinned to the exact versions being bumped from and to, which is the
case semantic-release's regex mechanism gets wrong.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deputy.jsonversion import (
    JsonVersionError,
    bump_file,
    detect_format,
    read_version,
    render,
    set_version,
)

OLD = "0.35.1"
NEW = "0.38.0"


# ── fixtures ──────────────────────────────────────────────────────────────────


def _npm_dump(
    doc: dict, *, newline: str = "\n", trailing: bool = True, indent: int | str = 2
) -> str:
    """Serialise like npm does: JSON.stringify(doc, null, 2) + a trailing newline."""
    text = json.dumps(doc, indent=indent, ensure_ascii=False)
    if trailing:
        text += "\n"
    return text.replace("\n", newline) if newline != "\n" else text


def _dependency_tree(n: int = 250) -> dict:
    """``packages`` entries for n third-party deps.

    Every fourth one is pinned to OLD and every fifth to NEW, so a bump that
    leaks past the root package is guaranteed to show up as a changed dependency
    version rather than needing luck to be caught.
    """
    packages: dict[str, dict] = {}
    for i in range(n):
        if i % 4 == 0:
            version = OLD
        elif i % 5 == 0:
            version = NEW
        else:
            version = f"{i // 100}.{i // 10 % 10}.{i % 10}"
        packages[f"node_modules/pkg-{i:03d}"] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/pkg-{i:03d}/-/pkg-{i:03d}-{version}.tgz",
            "integrity": f"sha512-fake{i:03d}",
            "dependencies": {"nested-helper": "^1.2.3"},
        }
    return packages


def lock_v3(version: str = OLD, *, n_deps: int = 250) -> dict:
    return {
        "name": "ada-py-viewer",
        "version": version,
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {
                "name": "ada-py-viewer",
                "version": version,
                "workspaces": ["packages/plugins/*"],
                "dependencies": {"meshoptimizer": "^1.1.1"},
                "devDependencies": {"three": "0.176.0"},
            },
            **_dependency_tree(n_deps),
        },
    }


def lock_v2(version: str = OLD) -> dict:
    """v2 carries both shapes: the v3-style ``packages`` and a v1-style ``dependencies``."""
    doc = lock_v3(version, n_deps=20)
    doc["lockfileVersion"] = 2
    doc["dependencies"] = {
        f"pkg-{i:03d}": {"version": OLD, "resolved": "https://example.invalid", "requires": {}}
        for i in range(20)
    }
    return doc


def lock_v1(version: str = OLD) -> dict:
    """v1 has no ``packages`` table at all — only ``dependencies``."""
    return {
        "name": "ada-py-viewer",
        "version": version,
        "lockfileVersion": 1,
        "requires": True,
        "dependencies": {
            f"pkg-{i:03d}": {"version": OLD, "resolved": "https://example.invalid"}
            for i in range(20)
        },
    }


def package_json(version: str = OLD) -> dict:
    return {
        "name": "ada-py-viewer",
        "version": version,
        "type": "module",
        "devDependencies": {"three": "0.176.0", "vite": OLD},
        "dependencies": {"meshoptimizer": "^1.1.1"},
    }


# ── the core promise: the package's own version, and nothing else ─────────────


def test_lockfile_v3_updates_both_of_its_own_version_fields():
    text = _npm_dump(lock_v3())
    update = set_version(text, NEW, source="package-lock.json")

    doc = json.loads(update.text)
    assert doc["version"] == NEW
    assert doc["packages"][""]["version"] == NEW
    assert update.old_version == OLD
    assert update.new_version == NEW
    assert update.changed
    assert update.fields == ("version", 'packages[""].version')


def test_dependency_versions_are_provably_untouched():
    before = lock_v3()
    text = _npm_dump(before)
    after = json.loads(set_version(text, NEW, source="package-lock.json").text)

    deps_before = {k: v for k, v in before["packages"].items() if k != ""}
    deps_after = {k: v for k, v in after["packages"].items() if k != ""}

    # The fixture really does contain the collision the regex mechanism trips on.
    assert sum(1 for v in deps_before.values() if v["version"] == OLD) > 50
    assert sum(1 for v in deps_before.values() if v["version"] == NEW) > 10

    assert deps_after == deps_before
    # ...and nothing anywhere gained the new version except the two root fields.
    assert sum(1 for v in deps_after.values() if v["version"] == NEW) == sum(
        1 for v in deps_before.values() if v["version"] == NEW
    )


def test_v2_leaves_the_legacy_dependencies_table_alone():
    before = lock_v2()
    after = json.loads(set_version(_npm_dump(before), NEW).text)
    assert after["version"] == NEW
    assert after["packages"][""]["version"] == NEW
    assert after["dependencies"] == before["dependencies"]
    assert all(d["version"] == OLD for d in after["dependencies"].values())


def test_v1_has_no_packages_table_and_only_the_root_version_is_written():
    before = lock_v1()
    update = set_version(_npm_dump(before), NEW)
    after = json.loads(update.text)
    assert after["version"] == NEW
    assert "packages" not in after
    assert update.fields == ("version",)
    assert after["dependencies"] == before["dependencies"]


def test_package_json_is_handled_by_the_same_path():
    before = package_json()
    update = set_version(_npm_dump(before), NEW)
    after = json.loads(update.text)
    assert after["version"] == NEW
    assert update.fields == ("version",)
    # A devDependency pinned to the old project version is not a project version.
    assert after["devDependencies"]["vite"] == OLD


def test_packages_root_without_a_version_key_is_not_invented():
    doc = lock_v3(n_deps=3)
    del doc["packages"][""]["version"]
    update = set_version(_npm_dump(doc), NEW)
    assert update.fields == ("version",)
    assert "version" not in json.loads(update.text)["packages"][""]


def test_read_version_reports_the_packages_own_version():
    assert read_version(_npm_dump(lock_v3(n_deps=2))) == OLD


# ── formatting fidelity ───────────────────────────────────────────────────────


def test_roundtrip_with_no_version_change_is_byte_identical():
    text = _npm_dump(lock_v3())
    assert set_version(text, OLD).text == text


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("trailing", [True, False])
def test_newline_style_and_trailing_newline_are_preserved(newline, trailing):
    text = _npm_dump(lock_v3(n_deps=5), newline=newline, trailing=trailing)
    out = set_version(text, NEW).text

    assert out.endswith(newline) is trailing
    assert ("\r" in out) == (newline == "\r\n")
    # Only the two version values differ; the line count and shape are unchanged.
    assert len(out.split(newline)) == len(text.split(newline))
    assert (
        sum(1 for a, b in zip(out.split(newline), text.split(newline), strict=True) if a != b) == 2
    )


@pytest.mark.parametrize("indent", ["  ", "    ", "\t"])
def test_indent_is_detected_and_reproduced(indent):
    text = _npm_dump(lock_v3(n_deps=5), indent=indent)
    assert detect_format(text).indent == indent
    out = set_version(text, NEW).text
    assert f'{indent}"version": "{NEW}"' in out


def test_minified_json_stays_minified():
    text = json.dumps(lock_v3(n_deps=3), separators=(",", ":"))
    assert detect_format(text).indent is None
    out = set_version(text, NEW).text
    assert "\n" not in out
    assert json.loads(out)["version"] == NEW


def test_bom_is_preserved():
    text = "\ufeff" + _npm_dump(lock_v3(n_deps=2))
    out = set_version(text, NEW).text
    assert out.startswith("\ufeff")
    assert json.loads(out[1:])["version"] == NEW


def test_render_is_the_inverse_of_loads_for_npm_output():
    text = _npm_dump(lock_v3(n_deps=7))
    assert render(json.loads(text), detect_format(text)) == text


def test_bump_file_writes_bytes_and_preserves_crlf(tmp_path):
    p = tmp_path / "package-lock.json"
    text = _npm_dump(lock_v3(n_deps=5), newline="\r\n")
    p.write_bytes(text.encode("utf-8"))

    update = bump_file(p, NEW)

    raw = p.read_bytes()
    assert update.changed and update.old_version == OLD
    assert b"\r\n" in raw and raw.count(b"\r\n") == raw.count(b"\n")
    assert json.loads(raw.decode("utf-8"))["packages"][""]["version"] == NEW


def test_bump_file_is_a_no_op_when_already_at_the_target_version(tmp_path):
    p = tmp_path / "package-lock.json"
    p.write_bytes(_npm_dump(lock_v3(NEW, n_deps=5)).encode("utf-8"))
    before = p.stat().st_mtime_ns

    update = bump_file(p, NEW)

    assert not update.changed
    assert p.stat().st_mtime_ns == before  # not rewritten at all


# ── failure modes: loud, and naming the file ──────────────────────────────────


def test_missing_file_names_it(tmp_path):
    with pytest.raises(JsonVersionError) as exc:
        bump_file(tmp_path / "nope.json", NEW)
    assert "nope.json" in str(exc.value)


def test_invalid_json_names_the_file(tmp_path):
    p = tmp_path / "package-lock.json"
    p.write_bytes(b'{"version": "1.0.0",}')
    with pytest.raises(JsonVersionError) as exc:
        bump_file(p, NEW)
    assert "package-lock.json" in str(exc.value)
    assert "not valid JSON" in str(exc.value)


def test_no_root_version_field_is_an_error():
    text = _npm_dump({"name": "x", "lockfileVersion": 3, "packages": {"": {"name": "x"}}})
    with pytest.raises(JsonVersionError) as exc:
        set_version(text, NEW, source="package-lock.json")
    assert "no root 'version' field" in str(exc.value)
    assert "package-lock.json" in str(exc.value)


def test_non_semver_root_version_is_an_error():
    text = _npm_dump({"name": "x", "version": "latest"})
    with pytest.raises(JsonVersionError) as exc:
        set_version(text, NEW, source="package.json")
    assert "not a semantic version" in str(exc.value)
    assert "package.json" in str(exc.value)


def test_non_string_root_version_is_an_error():
    with pytest.raises(JsonVersionError):
        set_version(_npm_dump({"name": "x", "version": 3}), NEW)


def test_top_level_array_is_an_error():
    with pytest.raises(JsonVersionError) as exc:
        set_version("[]", NEW, source="weird.json")
    assert "weird.json" in str(exc.value)


def test_a_file_that_cannot_be_reproduced_is_refused_rather_than_reformatted():
    # A duplicate key would silently vanish on round-trip; the fidelity guard
    # catches it instead of handing the reviewer a rewritten file.
    text = '{\n  "version": "0.35.1",\n  "name": "a",\n  "name": "b"\n}\n'
    with pytest.raises(JsonVersionError) as exc:
        set_version(text, NEW, source="package.json")
    assert "without reformatting" in str(exc.value)
    assert "package.json" in str(exc.value)


def test_the_fidelity_guard_leaves_the_file_on_disk_untouched(tmp_path):
    p = tmp_path / "package.json"
    original = b'{\n\t"version": "0.35.1",\n        "name": "a"\n}\n'  # inconsistent indent
    p.write_bytes(original)
    with pytest.raises(JsonVersionError):
        bump_file(p, NEW)
    assert p.read_bytes() == original


def test_non_utf8_file_is_an_error(tmp_path):
    p = tmp_path / "package.json"
    p.write_bytes(b'{"version": "\xff\xfe1.0.0"}')
    with pytest.raises(JsonVersionError) as exc:
        bump_file(p, NEW)
    assert "UTF-8" in str(exc.value)


# ── the real thing ────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("DEPUTY_TEST_LOCKFILE"),
    reason="set DEPUTY_TEST_LOCKFILE to a real package-lock.json to run this",
)
def test_real_lockfile_roundtrips_byte_identically():
    """The bar: re-writing a real npm lockfile with no version change is a no-op.

    Opt-in because it needs a checkout of a real npm project. Verified against
    adapy's src/frontend/package-lock.json (lockfileVersion 3, 223 KB, CRLF,
    471 regex-matchable "version" occurrences): byte-identical, `git diff` empty.
    """
    path = Path(os.environ["DEPUTY_TEST_LOCKFILE"])
    raw = path.read_bytes().decode("utf-8")
    current = read_version(raw, source=str(path))

    assert set_version(raw, current, source=str(path)).text == raw

    bumped = set_version(raw, "99.99.99", source=str(path))
    doc = json.loads(bumped.text)
    assert doc["version"] == "99.99.99"
    assert doc["packages"][""]["version"] == "99.99.99"
    # Exactly two lines differ from the original.
    diff = [
        (a, b) for a, b in zip(raw.splitlines(), bumped.text.splitlines(), strict=True) if a != b
    ]
    assert len(diff) == 2
