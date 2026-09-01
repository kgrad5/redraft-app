# ADR-44 — Yahoo ADP comes from the v2 public pseudo-league, not a v3 carrier league

**Status:** Accepted · 2026-08-31 · issue #7

**Context**
specs/draft-assistant.md §4.1 gives the source as `v3 players/nfl/{publicLeagueId}`, and
specs/draft-assistant.md §2.1 explains that unauthenticated v3 serves public leagues only, so this
private league's ADP must be fetched "through any public league id as a carrier". Appendix B item 1
leaves that unverified and estimates the cost at one curl. No URL for it is recorded anywhere:
`tests/test_http_layer.py`'s `pub-api-ro.fantasysports.yahoo.com/fantasy/v3/players/nfl/12345` is a
MockTransport placeholder and answers HTTP 404 live, as did roughly thirty other host and path
combinations probed on 2026-08-31 across pub-api-ro.fantasysports.yahoo.com,
api-secure.sports.yahoo.com and football.fantasysports.yahoo.com.

**Decision**
Yahoo ADP is fetched from

    https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/league/{game_key}.l.public
      /players;position=ALL;start=0;count=2000;sort=average_pick/draft_analysis?format=json_f

the read-only host the logged-out Draft Analysis page calls client-side. `470.l.public` is a
pseudo-league Yahoo publishes per game key, not a real league, so **no carrier league id is needed
and none is configured.** `game_key` is a constructor argument; 470 is 2026
(specs/draft-assistant.md §2.1). The module is `src/redraft/providers/yahoo.py`, not `yahoo_v3.py`,
because the endpoint is v2.

**Consequences**
- Appendix B item 1 is answered, and differently than it was posed. Measured 2026-08-31: one
  unauthenticated request returns 1,195 players in 4.16MB in about 1.2s. But "fetched through any
  public league id as a carrier" is wrong — nothing carries it, and issue #26's lobby discovery is
  not a prerequisite for ADP.
- **specs/draft-assistant.md §2.1's "All v3 responses are wrapped in a `service` envelope" is false
  for this endpoint**; the envelope is `fantasy_content`. `src/redraft/http/envelope.py` is thereby
  left with no caller anywhere in the tree. It is not deleted here — issue #24 may still meet a
  response that has one — but nothing this issue writes uses it, and a reader should not assume it
  is on the ADP path.
- The "v3" label is wrong for ADP throughout specs/draft-assistant.md. The other v3 facts in
  specs/draft-assistant.md §2.1 — the 403 "Unable to retrieve cookie" body, the HTTP 999 throttle,
  the measured `draftstatus` latency — were recorded against an endpoint nobody can now name. Only
  the 1,195-player pool size is reproduced here. **The 999 handling in
  `src/redraft/http/client.py` is therefore unexercised against this host**: it has not been
  observed to throttle, and whether it does is unknown rather than answered.
- The endpoint needs no OAuth and no app authorization, which is what lets it survive the dead
  Fantasy API of specs/draft-assistant.md §2.1.
- httpx2's default User-Agent answers HTTP 200 with a byte-identical body to a browser one
  (4,163,070 vs 4,163,069 bytes, the difference being the echoed URL). ADR-39's flagged
  WAF-fingerprint risk does not fire on this endpoint today, which is the first real-client
  evidence for that entry's open question.

**Alternatives rejected**
- **Keep hunting for the v3 endpoint specs/draft-assistant.md names.** Roughly thirty host and path
  combinations returned 404 or an RBAC denial, and neither the spec nor the repo's author can name
  it. An unauthenticated endpoint returning the exact pool size the spec measured is better
  evidence about what was actually used than the label attached to it.
- **Use a real public league id as a carrier**, which is what the spec's sentence describes. It
  would require discovering such an id and then depending on a stranger's league staying public
  through draft night. `470.l.public` is Yahoo's own and nobody outside Yahoo can withdraw it.
- **Name the module `yahoo_v3.py`** as issue #7 lists it. Rejected: the filename would assert a
  version the endpoint does not have, and a wrong fact in a filename outlives every comment
  correcting it.
