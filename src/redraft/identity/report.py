"""The unmatched-player report: who no tier placed, and how much it cost.

specs/draft-assistant.md §4.3 requires one on every run, and says why in a sentence this
module exists to honour: a silently-dropped player is a player missing from the board.
ADR-42 and ADR-46 both stopped at a count, and both recorded that a count is a tripwire
rather than the report — a number nobody can act on names nobody to go and look up.

`rank` is the source's own published draft position, handed over by the ingester that
read it. It is never a `SELECT` against `adp`: this run's ADP rows are not written yet, a
fresh database has none, and ADR-38's rollback would discard a failing run's anyway. It
orders the report and nothing else — it is never written to any table, and Appendix A
entry 9 of specs/draft-assistant.md keeps `adp` coming from Yahoo and FFC alone.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from redraft.http.client import Source


@dataclass(frozen=True, slots=True)
class Unmatched:
    """One source record that no tier could place.

    `source_key` is text for every source, including Yahoo's numeric id, so one shape
    serves both the report and the `(source, source_key)` an exception-file entry is
    written against. It is not a `player_id` and never becomes one (ADR-29).
    """

    source: Source
    source_key: str | None
    name: str
    position: str
    # The source's own figure. None where the source published none for this player.
    rank: float | None = None
    # What made it unplaceable, when that is more than "nothing matched" — an ambiguous
    # name names the rows it fell between, which is the thing an operator has to see.
    detail: str | None = None


def unmatched_report(unmatched: Iterable[Unmatched]) -> str:
    """The records as the text an operator reads, worst first.

    Ranks are not strictly comparable across sources — Sleeper publishes a PPR ADP, Yahoo
    a preseason average pick, FFC a PPR ADP — so each line names the source it came from
    rather than pretending to one scale. For ordering by "who does this cost me most",
    they are close enough.
    """
    records = sorted(unmatched, key=lambda r: (r.rank is None, r.rank or 0.0, r.source, r.name))
    if not records:
        # Not the empty string. A report that prints nothing on a clean run is
        # indistinguishable from a job that never ran, and trains the eye to skip it —
        # the same reflex as the Empty*Error raised one level down.
        return "unmatched players: none"

    by_source: dict[Source, int] = {}
    for record in records:
        by_source[record.source] = by_source.get(record.source, 0) + 1
    tally = ", ".join(f"{source} {count}" for source, count in sorted(by_source.items()))

    lines = [f"unmatched players: {len(records)} — {tally}"]
    for record in records:
        rank = "     —" if record.rank is None else f"{record.rank:6.1f}"
        line = f"  {rank}  {record.source:<8} {record.position:<3} {record.name}"
        if record.source_key is not None:
            line += f"  [{record.source_key}]"
        if record.detail is not None:
            line += f"  ({record.detail})"
        lines.append(line)
    return "\n".join(lines)
