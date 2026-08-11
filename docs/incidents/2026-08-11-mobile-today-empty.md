# Incident: fresh Today views showed no articles

**Date investigated:** 2026-08-11
**Affected surface:** authenticated Today view, most visible on mobile
**Status:** root cause confirmed; refresh worker restarted as a temporary mitigation

## User-visible symptoms

A fresh mobile Today view showed no articles even though a desktop tab showed many. Some mobile reports described article cards as blank or devoid of useful information.

## Root cause

The background `refresh-feeds` process had been blocked for more than three days in an unbounded network read while fetching the Planet KDE feed.

The application calls `feedparser.parse(feed.feed_url)` directly. That network path has no application-controlled connect/read timeout. `refresh_active_feeds()` processes feeds serially and has no per-feed exception or timeout boundary, so one stalled feed prevents every later refresh cycle and every feed after the stalled feed from running.

Production evidence on 2026-08-11:

- The Compose container was reported `Up`, but its child `python manage.py refresh_feeds` process had been running for **3 days, 14 hours**.
- The process kernel stack was blocked in `tcp_recvmsg`/`sock_read_iter`.
- `strace` showed it blocked in `read(4, ...)`.
- File descriptor 4 was an established TLS connection to `85.10.198.55:443`.
- DNS/feed correlation identified that address as `planet.kde.org`, feed ID 28, **Planet KDE | English**.
- The last completed worker cycle was logged at `2026-08-07T15:07:05Z`.
- At `2026-08-11T06:32:45Z`, production had:
  - 4,391 total articles;
  - 45 articles first seen on August 10;
  - **0 articles first seen on August 11**;
  - no Today read or saved rows that could explain the empty result;
  - one user with compact and focus modes enabled;
  - feed `last_fetched_at` values no newer than August 10, from a manual/web-triggered refresh rather than the stuck worker.

The server therefore rendered an accurate empty state for a fresh August 11 request. The desktop view was stale: it retained August 10 cards loaded before the UTC day changed. The difference was not caused by a mobile-specific query or a responsive CSS rule.

## Contributing factors

1. **Container liveness was mistaken for job health.** Compose showed the worker container as `Up`; there is no heartbeat or stale-job healthcheck.
2. **Serial all-or-nothing refresh.** One feed can block or abort all remaining feeds.
3. **No network safety boundary.** Feed fetching has no explicit timeout, response-size limit, redirect validation, or SSRF policy.
4. **UTC Today boundary and stale tabs.** `TIME_ZONE` is UTC, Today uses `timezone.localdate()`, and open pages do not refresh automatically. A desktop tab can retain the prior day while a fresh device requests the new empty day.
5. **Mobile discoverability.** At a 390×844 viewport, the wrapped header/navigation—especially with focus mode—can push the first valid card below the initial viewport. This does not explain the zero-card production response, but it can make nonempty pages appear empty.
6. **Insufficient diagnostics.** There is no durable refresh-run record, per-feed error state, structured timing, or alert for stale refreshes.

## Immediate mitigation

At `2026-08-11T06:35:01Z`, the `refresh-feeds` container was restarted without removing volumes or modifying PostgreSQL. This interrupts the blocked socket and starts a new refresh cycle. It is only a mitigation: the worker can hang again until bounded fetching and failure isolation are deployed.

## Corrective work

The permanent fix must include:

1. Fetch feeds through an application-owned HTTP gateway with connect/read timeouts, maximum response bytes, HTTP(S)-only validation, redirect revalidation, and private-address denial.
2. Parse fetched bytes with `feedparser` instead of allowing `feedparser` to own network I/O.
3. Isolate each feed failure and continue processing the batch.
4. Persist and expose `last_attempt_at`, `last_success_at`, `last_error`, and consecutive-failure state.
5. Add a refresh-run heartbeat/healthcheck so an `Up` but stale worker is unhealthy.
6. Add regression tests for a never-ending response, timeout, mixed good/bad feeds, worker restart, and stale Today data.
7. Improve mobile navigation/header density and assert that the first article is discoverable at supported viewports.
8. Document and test the intended Today timezone/freshness contract.

## Verification contract

A permanent fix is complete when:

- a deliberately hanging feed is terminated within the configured timeout;
- later feeds still refresh;
- the command, API, and UI report partial failure accurately;
- worker heartbeat and per-feed status expose the failure;
- Today receives newly fetched articles after the failure;
- desktop and mobile hard reloads return the same article IDs for the same user;
- 320–390 px browser tests show nonempty card text and a discoverable first card;
- the production worker remains fresh across multiple scheduled cycles.
