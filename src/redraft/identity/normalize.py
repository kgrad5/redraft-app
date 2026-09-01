"""The fold that closes the spelling gap between a source's name and this repo's.

specs/draft-assistant.md §4.3 names the two variants it has to handle — `D.K.` against
`DK`, and the `Jr.` / `III` family — and measurement on 2026-08-31 says those two are the
whole problem. Every one of ADR-46's nine unresolved ADP records is a suffix or a
punctuation variant, and the one Sleeper record inside the top 200 that ADR-42 left
unplaced is `Mike Washington` against a `players` row spelled `Mike Washington Jr.`

The fold is deliberately narrow. It closes case, punctuation and one trailing suffix, and
nothing else — no initials, no nicknames, no edit distance. specs/draft-assistant.md §4.3
rules out fuzzy matching without a review step, and a fold wide enough to need reviewing
is one that has already merged two players by the time anyone reads it. Ambiguity is what
`redraft.identity.resolve` does with the result, not something this module can prevent.
"""

import re

# Both directions occur: `players` carries the suffix where Sleeper omits it (Mike
# Washington Jr., Kenneth Walker III) and omits it where FFC and Yahoo add one (Kyle
# Pitts Sr., James Cook III). Stripping from both sides is what makes the pair meet.
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

# Digits survive. The shared fixture pool names players `Aaron Abbott QB01`, and folding
# digits away collapses 64 of its keys and 160 of its 244 rows — every pool-based test in
# the repo would then resolve the wrong player while still passing. Real names carry no
# digits, so keeping them costs nothing against the live pool.
_PUNCTUATION = re.compile(r"[^a-z0-9 ]")
_RUNS_OF_SPACE = re.compile(r" +")


def normalized(name: str) -> str:
    """`name` folded to the key the second name tier matches on.

    Punctuation is deleted rather than replaced with a space, so `D.K. Metcalf` becomes
    `dk metcalf` and not `d k metcalf` — the latter matches no spelling anyone publishes.
    """
    tokens = _RUNS_OF_SPACE.sub(" ", _PUNCTUATION.sub("", name.lower())).strip().split(" ")
    # `len > 1` because a suffix that is the entire name is somebody's actual surname,
    # and dropping it would fold every such player onto the empty string.
    if len(tokens) > 1 and tokens[-1] in SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)
