"""Generate an ed25519 SSH key (e.g. a CI deploy key), remembering the email.

Pure helpers — filename slug, unique path, ssh-keygen argv, email resolution,
and a tiny last-used-email store — with no I/O of their own. The orchestration
(make the dir, run ssh-keygen, read the key files, persist the email) lives in
:func:`deputy.flows.create_sshkey`, so everything unit-tests with fakes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_KEY_DIR = "~/.ssh"
#: Where the last-used email is remembered so it can be auto-suggested next time.
STATE_PATH = "~/.config/deputy/sshkey.json"


def slugify_email(email: str) -> str:
    """``user@example.com`` -> ``user_at_example_com`` (filename-safe)."""
    slug = email.strip().lower().replace("@", "_at_")
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug or "key"


def key_basename(email: str) -> str:
    """Base filename for a key generated for ``email``."""
    return f"deputy_ed25519_{slugify_email(email)}"


def _pub(path: Path) -> Path:
    """The ``.pub`` sibling ssh-keygen writes next to the private key."""
    return Path(str(path) + ".pub")


def unique_path(directory: Path, base: str, exists) -> Path:
    """First ``<directory>/<base>`` that (with its ``.pub`` sibling) doesn't exist.

    Falls back to ``<base>_1``, ``<base>_2``, ... so repeated runs for the same
    email never clobber an earlier key. ``exists`` is injected (``Path.exists`` in
    production, a fake set in tests).
    """
    candidate = directory / base
    n = 0
    while exists(candidate) or exists(_pub(candidate)):
        n += 1
        candidate = directory / f"{base}_{n}"
    return candidate


def keygen_argv(path: Path, email: str) -> list[str]:
    """``ssh-keygen`` argv for a passphrase-less ed25519 key (deploy-key use)."""
    return ["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(path), "-N", ""]


def resolve_email(cli_email: str | None, stored_email: str | None) -> str:
    """CLI flag wins, else the remembered email; error if neither is set."""
    email = (cli_email or stored_email or "").strip()
    if not email:
        raise ValueError(
            "no email given and none remembered — pass --email once "
            "(it is remembered for next time)"
        )
    return email


# ── last-used-email store (tiny JSON file) ──────────────────────────────────────


def load_stored_email(path: Path) -> str | None:
    """Return the remembered email, or ``None`` if there's no (valid) store yet."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    email = data.get("email")
    return email or None


def save_stored_email(path: Path, email: str) -> None:
    """Persist ``email`` as the last-used one (creating the dir if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"email": email}, indent=2) + "\n", encoding="utf-8")
