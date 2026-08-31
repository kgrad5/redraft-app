"""Repo conventions that are cheaper to enforce than to remember.

The spec-reference rule: outside `specs/` itself, any line citing a spec section names
the spec file, as in specs/draft-assistant.md §4.4. The bare form was unambiguous while
the repo held one spec; it means nothing once there are several, and a reader has no way
to tell which document was meant.

This is a test rather than a note in CLAUDE.md because the convention was written down
and then broken in the very next file authored under it. A rule that depends on being
remembered decays; this one fails the build instead.

This file obeys its own rule rather than exempting itself — an exemption here would carve
out the one file guaranteed to discuss the convention, and therefore the one most likely
to drift. The section sign is written escaped where it appears alone.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_REFERENCE = re.compile(r"specs/[\w.-]+\.md")
SECTION_MARK = "\u00a7"  # escaped, so this line carries no bare citation

# Entries recorded before the convention existed. arch/ is append-only — a decision is
# never rewritten to satisfy a later style rule — so these are grandfathered by name
# rather than by pattern. The list is a ratchet: it may shrink, never grow.
GRANDFATHERED = {
    "arch/0024-alembic-for-schema-migrations.md",
    "arch/0025-jsonb-for-snapshots-raw-payload.md",
    "arch/0026-draft-events-append-only-by-trigger.md",
    "arch/0027-draft-events-surrogate-event-id.md",
    "arch/0028-draft-events-nullable-player-id.md",
    "arch/0029-player-identity-internal-surrogate.md",
    "arch/0030-adp-keyed-by-snapshot-and-player.md",
    "arch/0031-snapshots-source-closed-set.md",
}


def tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return listing.stdout.split()


def offending_lines() -> list[str]:
    """Every line citing a section without naming the spec it belongs to."""
    offenders = []
    for path in tracked_files():
        # A spec cites its own sections; inside one the bare form is correct.
        if path.startswith("specs/") or path in GRANDFATHERED:
            continue
        try:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
        except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if SECTION_MARK in line and not SPEC_REFERENCE.search(line):
                offenders.append(f"{path}:{number}: {line.strip()}")
    return offenders


def test_section_references_name_their_spec():
    offenders = offending_lines()
    assert not offenders, (
        "A section reference must name the spec it belongs to, as in\n"
        "  specs/draft-assistant.md §4.4\n"
        "rather than the bare form, which says nothing once the repo holds more than "
        "one spec. Naming the spec once per line is enough; a later mark on the same "
        "line reads against it.\n\nOffending lines:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", sorted(GRANDFATHERED))
def test_grandfathered_entries_still_exist(path):
    """A renamed or deleted grandfathered entry would silently widen the exemption."""
    assert (REPO_ROOT / path).is_file(), f"{path} is exempted but no longer exists"
