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
#
# Keyed by basename rather than by path, because a superseded entry moves to
# arch/archive/ and a path-keyed exemption breaks twice over when it does: the path
# stops matching, and the moved file loses the only thing making its bare section marks
# legal. The entry most likely to move is exactly a pre-convention one — 0026 is
# superseded already — so the path-keyed form would have made these unarchivable.
GRANDFATHERED = {
    "0024-alembic-for-schema-migrations.md",
    "0025-jsonb-for-snapshots-raw-payload.md",
    "0026-draft-events-append-only-by-trigger.md",
    "0027-draft-events-surrogate-event-id.md",
    "0028-draft-events-nullable-player-id.md",
    "0029-player-identity-internal-surrogate.md",
    "0030-adp-keyed-by-snapshot-and-player.md",
    "0031-snapshots-source-closed-set.md",
}
# Both directories the log lives in: the live set and the entries later ones retired.
ARCH_DIRS = ("arch/", "arch/archive/")


def is_grandfathered(path: str) -> bool:
    """Whether `path` is a pre-convention entry, wherever in the log it now sits.

    Scoped to the log's own directories so the exemption cannot leak to a same-named
    file elsewhere in the repo, and keyed on the basename so it follows the entry when
    a supersede moves it into arch/archive/.
    """
    return path.startswith(ARCH_DIRS) and Path(path).name in GRANDFATHERED


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
        if path.startswith("specs/") or is_grandfathered(path):
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


@pytest.mark.parametrize("name", sorted(GRANDFATHERED))
def test_grandfathered_entries_still_exist(name):
    """A renamed or deleted grandfathered entry would silently widen the exemption.

    Either directory satisfies this: an entry a later decision retired is archived, not
    removed, and the exemption is meant to survive that move.
    """
    homes = [REPO_ROOT / directory / name for directory in ARCH_DIRS]
    assert any(home.is_file() for home in homes), (
        f"{name} is exempted but exists in neither {' nor '.join(ARCH_DIRS)}"
    )


def test_the_exemption_follows_an_entry_into_the_archive():
    """The reason this list is keyed by basename at all.

    A superseded entry moves to arch/archive/, and its bare section marks have to stay
    legal there — while a same-named file outside the log must gain nothing.
    """
    entry = min(GRANDFATHERED)
    assert is_grandfathered(f"arch/{entry}")
    assert is_grandfathered(f"arch/archive/{entry}")
    assert not is_grandfathered(f"docs/{entry}")
    assert not is_grandfathered("arch/9999-a-later-entry-with-no-exemption.md")
