"""Release-label rules — pure, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

# Each release-* label maps to a semantic-release force flag:
#   None -> do not release at all (release-skip)
#   ""   -> release, but let semantic-release derive the bump from commit history
#   "--patch"/"--minor"/"--major" -> force that bump level
RELEASE_FORCE_FLAG: dict[str, str | None] = {
    "release-skip": None,
    "release-auto": "",
    "release-patch": "--patch",
    "release-minor": "--minor",
    "release-major": "--major",
}

# Label name -> hex colour. Used to (re)create the label palette on the repo.
LABEL_PALETTE: dict[str, str] = {
    "release-skip": "b3b3b3",
    "release-auto": "ffff00",
    "release-patch": "00ff00",
    "release-minor": "0000ff",
    "release-major": "ff0000",
    "silence-bot": "000000",
}

SILENCE_LABEL = "silence-bot"
# Built-in fallback when a repo configures nothing. Overridable per-repo via
# deputy.toml's [pr_review].default_label (see config.resolve_default_label).
DEFAULT_LABEL = "release-skip"


@dataclass(frozen=True)
class BumpDecision:
    """Outcome of interpreting a PR's release-* labels."""

    release: bool  # should a tag/release be produced?
    flag: str | None  # semantic-release force flag ("" | --patch | ...), None if no release
    label: str  # the single effective label (or "release-skip")
    present: tuple[str, ...]  # recognised release-* labels found on the PR
    multiple: bool  # more than one release-* label present (invalid)

    @property
    def reason(self) -> str:
        if self.multiple:
            return "multiple release-* labels"
        return self.label


def decide_bump(labels: list[str], default_label: str = DEFAULT_LABEL) -> BumpDecision:
    """Interpret a PR's labels into a release decision.

    A missing release label falls back to ``default_label`` — the repo's
    configured default (``[pr_review].default_label``), which the review flow
    also applies to the PR — defaulting to ``release-skip``. More than one
    release-* label is invalid.

    Whether to release is decided by the label's *flag*, not by comparing the
    label to the default: ``None`` means "do not release", every other flag
    means "release". A PR explicitly labelled ``release-skip`` therefore never
    releases, even under a ``release-auto`` default.
    """
    present = tuple(label for label in labels if label in RELEASE_FORCE_FLAG)
    if not present:
        present = (default_label,)
    if len(present) > 1:
        return BumpDecision(False, None, present[0], present, True)
    label = present[0]
    flag = RELEASE_FORCE_FLAG[label]
    return BumpDecision(flag is not None, flag, label, present, False)
