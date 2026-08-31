# ADR-39 — httpx2 replaces httpx as the HTTP client

**Status:** Accepted · 2026-08-31 · issue #36

**Context**
#4 built the shared fetch layer on httpx 0.28.1. Upstream httpx has gone quiet while it sits in
the critical path of every ingester, and Pydantic has picked up stewardship under the name httpx2.
Starlette 1.6.0's TestClient already prefers httpx2 (`import httpx2 as httpx`) and emits a
StarletteDeprecationWarning on the httpx fallback — the warning that surfaced in this repo's test
output while verifying #4. specs/draft-assistant.md Appendix A's second clause makes the client
choice architecture-significant: the losing option produces a different dependency set. No spec
sentence names an HTTP library and no file in arch/ mentions httpx, so nothing is superseded.

**Decision**
The HTTP client is httpx2 (2.12.0 at adoption). `src/redraft/http/client.py` and
`tests/test_http_layer.py` import httpx2, and httpx leaves the tree entirely. Net dependency
change: +httpx2, +httpcore2, +truststore; −httpx, −httpcore, −certifi.

**Consequences**
- **TLS trust moves from certifi's pinned CA bundle to the OS trust store, via truststore.** No
  test observes this — the suite runs entirely through MockTransport — which is why issue #36's
  verification includes a real TLS handshake against each of the four
  specs/draft-assistant.md §4.1 source hosts. Certificate trust now follows the machine's
  keychain rather than a pip-installed bundle, except where `SSL_CERT_FILE`/`SSL_CERT_DIR` is
  set: httpx2 honors those ahead of truststore under its default `trust_env=True`.
- The two libraries are disjoint class hierarchies: `except httpx.HTTPStatusError` does not catch
  the httpx2-raised one, and mixing one library's Client with the other's MockTransport or
  Response fails on a bare, message-less AssertionError. The migration is therefore atomic, and no
  future module may import httpx.
- The User-Agent presented to Yahoo's WAF becomes `python-httpx2/2.12.0`, and `Accept-Encoding`
  gains zstd. specs/draft-assistant.md §2.1 records the 999 as not User-Agent dependent, so the
  fingerprint change is flagged rather than known-harmful; it stays latent until #5–#7 construct
  real clients.
- Everything the fetch layer relies on was probed identical before adoption: `Response(999,
  text=...)` round-trips with the plain-text body intact, `raise_for_status()` raises the same
  exception type and message on 999/403/301/302, MockTransport and `Client.get(url, params=...)`
  are unchanged, exceptions still carry `.request`/`.response`, and the default timeout remains
  `Timeout(timeout=5.0)` — unchanged from httpx, and per-phase (connect/read/write/pool), not
  per-request, so the pick-clock budget of specs/draft-assistant.md §2.2 still needs its own
  timeout math when #5–#7 and #9 construct real clients.

**Alternatives rejected**
- **Stay on httpx 0.28.1.** No code change, and certifi's deterministic pip-installed trust
  bundle. Rejected: upstream is dormant, this repo's own web framework deprecates the pairing,
  and #32's lockfile would pin a stack already superseded — the swap only gets more expensive
  after pinning lands.
- **Adopt httpx2 but pin trust back to certifi** (a `verify=` context built from certifi).
  Preserves the old trust model through the swap. Rejected: it recreates the pinned-bundle
  maintenance the OS trust store removes, keeps a dependency httpx2 no longer needs, and diverges
  from httpx2's defaults with no observed incompatibility against the four
  specs/draft-assistant.md §4.1 sources.
