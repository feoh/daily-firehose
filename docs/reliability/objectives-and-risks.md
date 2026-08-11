# Reliability objectives and risk register

This document defines desired reliability objectives, the signals that can and cannot
be measured today, and a ranked risk register for Daily Firehose. It is a current
planning/control document, not a snapshot inventory. Only the pinned
[architecture](../architecture/current-state.md) and [feature catalog](../features/catalog.md)
own snapshot facts and counts. The [cross-feature contracts](../features/contracts.md)
and [current-suite test traceability matrix](../features/test-traceability.md) are
current/post-snapshot sources; they track live contracts, executable evidence, and
gaps without changing the pinned documents' semantics or counts.

Evidence also comes from the
[2026-08-11 stale-ingestion incident](../incidents/2026-08-11-mobile-today-empty.md),
current [`docker-compose.yml`](../../docker-compose.yml), production
[`settings.py`](../../daily_firehose/settings.py), operator procedures in
[`README.md`](../../README.md) and [`AGENTS.md`](../../AGENTS.md), the current
suite under [`feeds/tests/`](../../feeds/tests/), and the bounded, non-secret host
reconnaissance in the [PostgreSQL backup runbook](../operations/postgresql-backups.md).
No external monitoring, provider, or secret state is assumed beyond evidence explicitly
cited there.

## 1. Scope, terminology, and ownership

- **Desired objective** means a target to instrument and operate toward. It is not a
  claim that the target is met.
- **Currently measurable** means repository evidence exposes enough data for the
  stated observation, even if collection is manual. A log line or current database
  field is not historical telemetry unless it is durably collected.
- **Unknown** means the repository cannot establish the signal or outcome.
- An **eligible request event** is a request known to satisfy the documented method,
  authentication and input contract, or a synthetic constructed to do so. Expected
  invalid-client/authentication `4xx` events are counted separately and excluded from
  both SLI numerator and denominator. An eligible request is **good** only when it
  finishes inside the route timeout, has the expected success status and content type,
  and passes the route's semantic body predicate. An eligible request that times out,
  returns any `4xx`/`5xx`, is malformed, or fails that predicate is **bad**. Thus a
  syntactically valid request returning `404` or `422` is not hidden as client error.
  `SLI = good / (good + bad)`; events whose validity cannot be classified are reported
  as `unknown` and never silently added to `good`.
- A **semantic HTML success** is `200` with the expected page heading plus its required
  content marker: Today has either a labeled Article-card collection or the canonical
  quiet empty state; another page has its route heading and principal form/list/detail
  region; public newsletter detail has the expected issue identity, body region and
  `noindex`. A **semantic JSON success** has the documented content type and required
  top-level keys/envelope for that endpoint. Redirect probes use their explicitly
  documented `301`/`302` destination instead of the `200` predicate.
- **Route families** are measured separately: Today; other authenticated reading pages
  (week/month/archive/saved/feed detail); authenticated management pages
  (Feeds/OPML/preferences); public newsletter detail; bearer API reads; bearer local
  writes; synchronous external/refresh API operations; and Postmark webhook. No family
  may hide another's errors through aggregation.
- **Current cadence** is fixed-delay, not wall-clock hourly: Compose starts a refresh
  command, waits for its full duration, then sleeps 3600 seconds. If a run starts at
  `t` and lasts `d`, the next starts near `t + d + 3600s`; drift accumulates and there
  is no nominal-slot identity. **Desired cadence** is one owned cycle for every UTC
  wall-clock hour slot. A slot is eligible when scheduling is enabled and no declared
  maintenance window covers it. It starts by slot `+10m`, heartbeats at least every
  5m, evaluates every active Feed, and completes by `+45m`—a provisional bound based
  on the observed 33 Feeds and the configured cooperative 60s per-feed total limit.
  A slot with no start by `+10m` is missed. If the prior owner is still active, no
  second owner starts: the new slot is recorded as a bad `overlap/missed` cycle and
  the old run is contained. Exactly one run ID owns each slot and only its fenced
  per-feed outcomes may update run status.
- An **eligible Feed-cycle event** is an active Feed when its owned cycle reaches it
  and `next_retry_at` is absent or due. A future-backoff Feed produces a good explicit
  skip, not an attempt. An eligible event is good only if exactly one owned result is
  durably recorded by cycle `+45m`; success or a classified origin failure is an
  outcome, but every failure is also charged to the per-feed success/error SLI. A
  missing, duplicate, stale-owner or application/configuration failure is bad.
- **Healthy-origin classification** is operational and time-bounded. Codes
  `dns_failure`, `timeout`, `tls_failure`, `network_failure`, `http_failure`,
  `invalid_content`, `unsupported_encoding`, `response_too_large`, `redirect_limit`,
  `invalid_redirect`, and `parse_error` are origin-attributable only after two
  consecutive owned failures. That exclusion lasts at most 24h from the latest attempt
  and ends immediately on success. `invalid_url`, `blocked_port`, `blocked_target` and
  `invalid_policy` are configuration/policy failures; `integrity_error` and
  `unexpected_error` are application failures; neither class is excluded. A first
  origin failure, an expired classification, and every never-successful active Feed
  remain unclassified and bad for healthy-origin freshness. All active Feeds are also
  evaluated without exclusions; any active Feed with no success for >24h (including
  never-successful) is a hard alert.
- **Owner boundary** names code or operational responsibility, never an invented
  person. The production operator owns host/provider procedures; the application owns
  repository code and schema; an external provider owns its remote result only.

### Non-double-counting rule

Each consequence has one primary risk ID. Cross-links identify shared controls but do
not create another score: permanent data loss is `REL-RISK-002`, while deployment
incompatibility/downtime is `REL-RISK-004`; worker staleness is `REL-RISK-001`, while
non-worker web/database observability is `REL-RISK-003`; Postmark write atomicity is
`REL-RISK-007`, while webhook authentication is `REL-RISK-009`; Linkding ambiguity is
`REL-RISK-010`, while insecure endpoint configuration is `REL-RISK-022`. Scheduler,
worker and process staleness belong only to `REL-RISK-001`; `REL-RISK-016` owns only
feed-destination transport-security validation seams. Responsive parity belongs to
`REL-RISK-019`, accessibility to `REL-RISK-020`, newsletter remote-image privacy to
`REL-RISK-024`, and refresh log-level configuration to `REL-RISK-025`. A mitigation
may reduce several risks without merging their different triggers or owners.

## 2. Risk scoring and review method

Every material risk is scored on three 1–5 axes. **Detectability is inverse**: a high
number is difficult to detect before impact.

| Score | Severity (S) | Likelihood in the next 12 months (L) | Detectability before user/data impact (D) |
| --- | --- | --- | --- |
| 1 | Negligible; cosmetic or trivial recovery | Exceptional; requires several unlikely conditions | Automatically prevented or immediately alerted with a precise signal |
| 2 | Minor; bounded feature degradation, no durable corruption | Unlikely but plausible | Usually exposed by an existing test or explicit operator check |
| 3 | Material; feature unavailable/wrong or manual repair needed | Possible; credible trigger or recurring external failure | Evidence exists, but discovery is manual or delayed |
| 4 | Major; broad correctness/security/availability impact | Likely; common trigger or known recurring pressure | Weak/indirect signal; users commonly discover it first |
| 5 | Critical; unrecoverable data loss, compromise, or prolonged total outage | Frequent/active; repeatedly observed without a full control | No trustworthy current signal or the signal reports healthy during failure |

**Priority score = S × L × D** (1–125). Bands are: **critical** 60–125,
**high** 36–59, **medium** 18–35, and **low** 1–17. Severity breaks ties, then
likelihood, then stable ID. Scores prioritize investigation; they are not probability
estimates and do not override a severity-5 stop gate.

The index's **Confidence** column uses exactly one normalized value—`high`, `medium`,
or `low`—for confidence that current evidence supports the complete S/L/D score. It
is not another likelihood estimate and never combines separate labels:

- **High:** directly demonstrated by code, an executable characterization, or the
  dated incident, with little uncertainty material to the score.
- **Medium:** strongly implied by present topology/control gaps, but an occurrence,
  external behavior or part of the score is not directly observed.
- **Low:** depends materially on an unresolved external/product assumption.

Re-score after a control is deployed and observed for its stated window, after an
incident, or at least quarterly. Do not lower likelihood merely because work is
planned, or detectability merely because a log can be read manually.

## 3. Desired objectives versus present signals

All thresholds are **provisional initial targets**. Current Compose behavior is the
fixed-delay `duration + 3600s` cadence defined above; it does not satisfy the desired
wall-clock scheduler merely because the sleep value is 3600. The desired `+45m`
completion bound is consistent with the observed 33-Feed batch and configured
cooperative 60s per-feed total limit, while still leaving 15m before the next slot.
A failed cycle is bad immediately and cannot remain acceptable until a two-hour age.
Baselines may tighten latency targets but may not silently weaken correctness, backup,
security, or hard freshness gates.

### Objective register

| ID | Desired SLI and target | Window and error budget | Currently measurable signal (not proof of attainment) | Missing/preferred signal |
| --- | --- | --- | --- | --- |
| `REL-OBJ-001` Feed freshness | At 5m samples, **≥99%** of healthy-origin active Feeds have committed `last_fetched_at` age **≤105m**: one 60m slot plus the `+45m` completion bound. A first/unknown/application/configuration failure is bad immediately. Independently, **100%** of all active Feeds have a success age ≤24h. | Rolling 30d; 1% healthy-origin bad feed-samples. The all-active 24h condition has zero budget and no exclusions. | Current Feed timestamps/status permit a point-in-time query, but not historical samples or the required finite classification. | Durable sampled age by Feed and class, oldest-all-active panel, error-class transition/recovery history and hard >24h alert including never-successful Feeds. |
| `REL-OBJ-002` Scheduled run heartbeat/completion | For desired wall-clock hourly slots, **≥99.5%** have exactly one owner, start by `+10m`, heartbeat every 5m with no gap >10m, and finish by `+45m`. Any missed/overlap slot or failed cycle is bad at its deadline; it is not deferred to freshness age. | Rolling 30d: about 720 eligible slots and at most 3 bad cycles; the fourth exhausts budget. Deadline alerts fire per event regardless of budget. | Current logs/container state can be inspected, but fixed-delay Compose has no slot/run identity, heartbeat, wall-clock schedule or ownership. | Durable slot/run owner, start/heartbeat/finish/outcome, overlap/missed reason, wall-clock scheduler and semantic health. |
| `REL-OBJ-003` Per-feed attempt, failure, and backoff integrity | **≥99%** of eligible Feed-cycle events have exactly one fenced outcome by cycle `+45m`; separately **≥99%** succeed among attempted healthy/unclassified events. **100%** preserve isolation and the documented 5m exponential backoff capped at 24h. | Rolling 30d; 1% event/outcome and 1% attempt-success budgets. Duplicate ownership/backoff mismatch has zero budget; first failure alerts by class, not after 2h. | Current fields/logs expose latest attempt/failure/retry only; no run ownership/history, and command failures exit zero. | Per-run/per-feed outcomes, success/error-class counters, skip reason, lease owner/loss and computed-vs-stored backoff mismatch. |
| `REL-OBJ-004` Today availability and latency | Eligible authenticated Today events are good under the semantic predicate within a **3s timeout**, availability **≥99.5%**, p95 **≤1s**. A 5m synthetic also correlates the empty state with `REL-OBJ-001` and `REL-OBJ-002` so stale ingestion is bad, not a semantic success. | Rolling 30d, 0.5% budget. Evaluate p95 per family over 24h only with ≥100 eligible events; otherwise use rolling 30d if ≥100, else report insufficient volume and enforce every 5m synthetic's 3s maximum. | Tests/manual use; Today is `never_cache`. No production status/latency/semantic/freshness series exists. | Bounded route metrics and authenticated semantic synthetic with explicit good/bad/unknown classification. |
| `REL-OBJ-005` Other page availability and latency | Each page family defined above has eligible-event availability **≥99.5%**, semantic timeout **5s**, and p95 **≤1.5s**. Public newsletter is its own family and predicate. | Rolling 30d, 0.5% budget per family. Use the same ≥100-event p95 rule; below it, report insufficient volume and enforce 5m synthetics for critical routes plus per-synthetic 5s maximum. | Request tests and manual public/proxy curls cover selected behavior; no production route series exists. | Per-family status/latency/predicate metrics, authenticated reading/management probes, and anonymous public-newsletter probe. |
| `REL-OBJ-006` Bearer API availability, error rate, and latency | Eligible API reads are good within **3s**, local writes within **5s**; availability **≥99.5%**, p95 reads **≤1s**, writes **≤2s**. Refresh/Linkding operations use a **20s request maximum** and separate latency families, but their eligible `4xx`/`5xx`/timeout events still count bad. | Rolling 30d, 0.5% per endpoint family; same ≥100-event p95 rule. Below minimum volume, a known-valid synthetic enforces the family maximum and p95 is `insufficient volume`. | Deterministic tests; token `last_used_at` proves auth attempt only. No production metrics exist. | Endpoint-family request/status/latency/dependency metrics and safe valid/invalid/unknown classification without credentials/bodies. |
| `REL-OBJ-007` Authenticated cache correctness | **100%** of authenticated session, legacy JSON, bearer GET and authenticated OPML/newsletter responses emit at least `Cache-Control: private, no-store`. The public newsletter URL varies by auth: authenticated and anonymous responses both emit `Vary: Cookie`; anonymous may be cacheable only under an explicit public policy, otherwise it also uses `no-store`. Zero authenticated-to-anonymous or cross-user reuse. | Every release/continuous sample; zero budget. Probe the same newsletter UUID anonymously and authenticated and compare headers/body predicates without retaining private body data. | Today has `never_cache`; `UI-INV-004` proves other authenticated surfaces are not conformant. Public newsletter anonymous GET is display-only, but auth/cache variation is not established. | Passing all-route header matrix, same-URL anonymous/auth synthetic and cache-isolation canary. |
| `REL-OBJ-008` Postmark acceptance and idempotency | Eligible valid webhooks are good within **10s**, availability **≥99.5%**, p95 **≤5s**; **100%** of accepted MessageIDs resolve to one complete Article/Issue. Lost response after commit resolves by replay, with no accepted ID ambiguous >24h. | Rolling 30d, 0.5% availability; zero atomicity/idempotency budget. Use p95 only at ≥100 eligible events/30d; below it, every provider-correlated event uses the 10s maximum and exact failure thresholds. | Response and Issue ID can be checked manually; sequential dedupe passes. No provider series, ambiguity state, orphan detector or concurrency proof exists. | Provider/app correlation by non-secret event ID, outcome/ambiguity/orphan/replay/race counters and PostgreSQL atomic tests. |
| `REL-OBJ-009` Linkding outcome and ambiguity | Local save survives **100%** of remote failures. **≥99%** of remote attempts reach definitive confirmed/failed state within **20s**; ambiguous/lost-response outcomes are **<0.1%**, never blindly retried and reconciled within 24h. | Rolling 30d; 1% definitive-outcome and 0.1% ambiguity budgets; local preservation zero budget. Low volume uses first-ambiguity notification and 24h deadline, not percentages. | Current SavedArticle boolean/error collapses definite failure and timeout-after-success; tests cover exact URL and ordinary failures. | Durable attempt/idempotency/reconciliation state and provider-aware counters, without token/header values. |
| `REL-OBJ-010` Backup and restore | Unencrypted custom-format backups push over restricted SSH to TrueNAS at **00:00 and 12:00 UTC**, complete/verify by `+2h`, and provide **RPO ≤24h**. Missing the `+2h` completion is actionable; page when latest verified backup age reaches **20h**, and at/before **24h** stop destructive changes and contain writes until recovery. Quarterly isolated restore demonstrates **RTO ≤4h**, integrity, migration compatibility and semantic reads. | Continuous age; 90d drill. Zero budget for crossing 24h, failed integrity or missed drill. Twice-daily schedule provides ≥10h operational margin before the RPO boundary. | The direct application-host SSH push with no delete operation, receiver-controlled immutable first-receipt retention, exact effective-ZFS mount validation, 1 GiB object bound, 20 GiB dataset quota, and serialized receiver are approved and locally tested with fakes. One custom compression-9 stream measured 14.47 MiB, but no installed middleware-created account/datasets/key/receiver, production receipt-backed pair, activated jobs, independent NAS/off-site confirmation, quota alert, or drill exists. Production RPO/RTO remain unknown. | Installed restricted receiver/credentials and local maintenance, activated schedule/deadline/result/age, tested quota/off-site alerts, administrator rekey/read/restore recovery drill, independent NAS/off-site confirmation, scheduled-failure plus 20h/24h alerts, and recurring timed restore records including retrieval/cutover. |
| `REL-OBJ-011` Deployment verification and rollback readiness | **100%** of deploys pass deploy check, DB connection, migration/backup review, Compose state/logs, direct 301, proxy/public 302, and authenticated semantic smoke within 15m; every schema change has tested stop/rollback. | Per deploy/rolling 90d; zero skipped-gate budget. | Manual procedure and dated observation only; no durable release record or automatic rollback. | Revision-tagged gate results/durations, backup reference, smoke and explicit rollback decision. |
| `REL-OBJ-012` 320/390/desktop parity and accessibility | Every release has identical Today Article IDs/state at desktop, 390×844 and 320×844; zero overflow; first content discoverable; target-only save/read survives reload. Shared pages have no critical automated accessibility violations and native keyboard paths work. | Every change/release; zero correctness budget, 100% matrix pass. | Chrome Today geometry/state and markup baseline only; other pages/browsers/AT/zoom/full keyboard unmeasured. | Release browser matrix, executable JS/keyboard tests, accessibility scan and periodic manual AT check. |

### Alert policy and burn rates

Alerts are desired controls; none is claimed to exist now.

- **SEV-1 / page immediately:** observed/suspected unrecoverable data loss,
  credential/capability compromise, cross-user cache disclosure, corrupt restore, or
  backup age reaching 24h; contain writes/exposure and block destructive changes.
- **SEV-2 / page during the owner's support window:** no cycle start by slot `+10m`,
  heartbeat gap >10m, no successful completion by `+45m`, any overlap owner, two
  consecutive 5m semantic synthetic failures (or 3 failures in 10m), all active Feeds
  stale >105m because of application/scheduler failure, any active Feed with no
  success >24h (including never-successful), 3 eligible Postmark failures in 10m (or 2
  consecutive provider deliveries), or verified backup age ≥20h.
- **SEV-3 / actionable notification:** missed backup completion at scheduled `+2h`, one
  Feed reaches 3 consecutive failures, first Linkding ambiguity,
  first valid Postmark failure at low volume, deployment verification incomplete,
  refresh log-level configuration invalid/unreachable, or a responsive/accessibility
  release gate fails.
- **Ticket only:** one isolated origin-attributable Feed failure with correct backoff,
  a definite Linkding provider failure with local state preserved, or low-confidence
  policy drift. Never-successful and >24h active Feeds are not ticket-only.

Fast/slow multi-window burn alerts apply **only** to the request availability SLIs in
`REL-OBJ-004`–`006` and Postmark availability in `REL-OBJ-008`, never to scheduler,
backup, correctness, freshness or provider-ambiguity zero-budget gates. Fast burn is
14.4× over both 1h and 5m (SEV-2) and requires ≥100 eligible events in 1h and ≥20 in
5m. Slow burn is 6× over both 6h and 30m (SEV-3) and requires ≥100 in 6h and ≥20 in
30m. If either window lacks minimum volume, do not claim a burn rate: use the 5m
semantic synthetics, exact consecutive/count windows above, and event deadlines. A
route-family p95 is published only under the objective's ≥100-event rule; otherwise it
is explicitly `insufficient volume`.

### Staged instrumentation plan

1. **Stage 0 — define/manual baseline, no schema rewrite.** Preserve bounded feed logs;
   document point-in-time queries for Feed freshness/failure/backoff; record deployment
   checklist results and test/browser gate results. Do not label these historical SLIs.
   Stop if collection would expose request bodies, tokens, webhook path secrets, email
   content, or production credentials.
2. **Stage 1 — structured process signals.** Add non-secret refresh cycle IDs and
   start/finish summaries, Gunicorn request status/latency logs with bounded route
   labels, a stale-worker watchdog based on current fields/log completion, semantic web
   probes, backup job result/age, and alert routing. First observe for at least three
   current fixed-delay cycles before paging; rollback noisy alerts independently without removing
   logs or backup evidence.
3. **Stage 2 — durable ownership and metrics.** Add additive refresh-run/heartbeat and
   per-feed outcome/lease records, metrics export/retention, semantic readiness, and
   SLO dashboards. Deploy schema expand-first with old-code compatibility. Tested,
   independently deployable emission-disable and claim-disable switches are rollout
   prerequisites because they do not exist today. Stop on duplicate ownership,
   cardinality explosion, sensitive labels, >10% measured request overhead, or changed
   refresh results; invoke those new switches and retain records.
4. **Stage 3 — integration and release evidence.** Add provider-correlated Postmark
   outcomes, explicit Linkding ambiguous/reconciliation state, revision-tagged deploy
   verification, recurring restore evidence, and broader browser/accessibility gates.
   Provider identifiers must be bounded/hashed where possible; never emit content or
   secrets. Stop blind retries on ambiguity and stop releases on failed restore/parity.
5. **Stage 4 — tune from evidence.** After at least 30 days, review latency/freshness
   distributions and exclusions. Tightening is normal; weakening requires a recorded
   capacity/product decision, risk re-score, and no concealment of prior misses.

## 4. Ranked risk index

| Rank | ID | Primary class | S | L | D | Score | Band | Confidence | Present status |
| ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| 1 | `REL-RISK-001` | availability/correctness | 4 | 4 | 5 | 80 | critical | high | open-demonstrated |
| 2 | `REL-RISK-002` | data loss | 5 | 3 | 5 | 75 | critical | high | open-known |
| 3 | `REL-RISK-003` | operability/availability | 4 | 3 | 5 | 60 | critical | high | open-known |
| 4 | `REL-RISK-004` | deployment/data integrity | 5 | 2 | 5 | 50 | high | high | open-known |
| 5 | `REL-RISK-005` | availability | 4 | 3 | 4 | 48 | high | high | open-known |
| 6 | `REL-RISK-006` | correctness/integration | 4 | 3 | 4 | 48 | high | medium | open-inferred |
| 7 | `REL-RISK-010` | integration/correctness | 3 | 4 | 4 | 48 | high | high | open-known |
| 8 | `REL-RISK-012` | availability/performance | 3 | 4 | 4 | 48 | high | medium | open-inferred |
| 9 | `REL-RISK-023` | correctness/maintainability | 3 | 4 | 4 | 48 | high | high | open-known |
| 10 | `REL-RISK-016` | destination security | 5 | 2 | 4 | 40 | high | medium | residual-known |
| 11 | `REL-RISK-007` | correctness/data integrity | 4 | 3 | 3 | 36 | high | high | open-demonstrated |
| 12 | `REL-RISK-008` | security/correctness | 4 | 3 | 3 | 36 | high | high | open-known |
| 13 | `REL-RISK-011` | correctness/data integrity | 4 | 3 | 3 | 36 | high | high | open-demonstrated |
| 14 | `REL-RISK-013` | security/correctness | 4 | 3 | 3 | 36 | high | high | open-demonstrated |
| 15 | `REL-RISK-019` | responsive/mobile parity | 3 | 3 | 4 | 36 | high | high | partially controlled |
| 16 | `REL-RISK-020` | accessibility | 3 | 3 | 4 | 36 | high | high | partially controlled |
| 17 | `REL-RISK-024` | newsletter privacy | 3 | 3 | 4 | 36 | high | medium | conditional-unknown |
| 18 | `REL-RISK-009` | security/integration | 4 | 2 | 4 | 32 | medium | medium | open-known |
| 19 | `REL-RISK-017` | authorization/security | 4 | 2 | 4 | 32 | medium | low | conditional-unknown |
| 20 | `REL-RISK-025` | operability/configuration | 3 | 3 | 3 | 27 | medium | high | open-known |
| 21 | `REL-RISK-014` | correctness/ingestion | 3 | 4 | 2 | 24 | medium | high | open-demonstrated |
| 22 | `REL-RISK-015` | correctness/data integrity | 3 | 3 | 2 | 18 | medium | high | open-demonstrated |
| 23 | `REL-RISK-018` | security | 3 | 3 | 2 | 18 | medium | high | open-demonstrated |
| 24 | `REL-RISK-022` | security/integration | 4 | 2 | 2 | 16 | low | medium | open-known |
| 25 | `REL-RISK-021` | transport security | 3 | 2 | 2 | 12 | low | high | accepted-deferred |

The table is sorted by descending priority score, then severity, likelihood, and
stable ID. A severity-5 risk is still a stop-gate candidate even when another risk has
a higher product score.

## 5. Detailed risk register

### REL-RISK-001 — Scheduled refresh can be stale while the worker appears Up

- **Affected IDs/objectives:** ING-007–ING-010, OPS-008, OPS-011, OPS-014;
  FEED-INV-001, OPS-INV-001, OPS-INV-002, OPS-INV-005; `REL-OBJ-001`–`003`.
- **Present status/evidence:** **open-demonstrated**. The incident recorded an `Up`
  worker blocked for 3d14h and zero same-day Articles. Bounded per-feed fetch reduces
  that exact path, but Compose still schedules `run duration + 3600s`, failures exit
  zero, and no slot owner, heartbeat, semantic healthcheck or watchdog exists.
- **Owner boundary:** scheduler, refresh process/command and Compose worker. Feed
  destination-security validation is excluded and owned by `REL-RISK-016`.
- **Triggers:** process/resolver/socket stall, wedged command, repeated zero-exit failed
  cycles, fixed-delay drift, invalid sleep behavior, host restart, dependency loss, or
  overlapping browser/API/worker invocation without run ownership.
- **Current → preferred detection:** container state, latest Feed fields and console
  logs inspected manually → one durable owner per UTC slot, start `+10m`, 5m heartbeat,
  completion `+45m`, overlap/missed outcome, nonzero attempted-failure exit and exact
  alerts at those deadlines.
- **Mitigation sequence:** add a no-schema completion watchdog and exit contract;
  validate for three current fixed-delay cycles; introduce desired wall-clock slot/run
  records and single-owner fencing; add a hard process/job bound. A kill switch for
  new scheduler/claim behavior is a rollout prerequisite, not a present control.
- **Rollback/containment:** **present:** restart only `refresh-feeds`, preserve volumes,
  inspect fields/logs and run the bounded command manually. **Future rollout:** use the
  prerequisite switch to return to the current fixed-delay worker if slot ownership
  skips/duplicates work; retain run records and alerts.
- **Residual risk:** process/host supervision remains necessary after per-feed bounds;
  origin failures are tracked by `REL-OBJ-003`, not treated as scheduler health.
- **Linked Witan work:** `tk-add-structured-observability-health-readiness-an-16ba73`,
  `tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c`.

### REL-RISK-002 — PostgreSQL has no evidenced production backup or verified restore

- **Affected IDs/objectives:** OPS-002, OPS-013–OPS-014; DATA-INV-004,
  OPS-INV-003–OPS-INV-004; `REL-OBJ-010`–`011`.
- **Present status/evidence:** **open-known**. Compose has one local named volume.
  Owner-approved tooling runs on `daily-firehose` from
  `/home/ubuntu/daily-firehose`, validates a custom-format dump through the exact local
  db-container command, and pushes an unencrypted dump plus complete metadata through
  pinned restricted SSH to a forced TrueNAS receiver at `192.168.1.2`. SSH has no
  deletion command. Receiver receipts drive local-only fixed retention; immutable
  earliest-received tier points prevent later compromised-key uploads from displacing
  already received points. The receiver serializes operations, each dump is limited to
  1 GiB, and the exact data dataset has
  a specified 20 GiB quota. One compression-9 dump measured 14.47 MiB. No
  middleware-created account/datasets, installed key/receiver/jobs, production pair,
  independent NAS/off-site confirmation, alert, or restore drill exists. Production
  RPO/RTO remain unknown.
- **Owner boundary:** production database/infrastructure operator; application supplies
  migrations and semantic restore verification.
- **Triggers:** host/disk/volume loss, operator deletion, corruption, destructive
  migration, credential recovery mistake, or ransomware/privileged compromise.
- **Current → preferred detection:** volume/container existence plus unexecuted local
  tooling → twice-daily scheduled result by `+2h`, verified-backup age,
  checksum/integrity, independent off-host confirmation, 20h page, 24h containment,
  quarterly timed restore and application read smoke.
- **Mitigation sequence:** create the dedicated TrueNAS dataset/account through
  supported middleware, install the root-owned persistent receiver/control files and
  restricted authorized key, configure local maintenance plus scheduled-failure,
  quota, 20h-age, and 24h-containment alerts, pin the NAS host key, and stage the
  dedicated client key with systemd credentials; manually create and independently
  confirm one NAS/off-site pair; pass an administrator rekey/read/restore drill and an
  isolated restore; only then activate the supplied 00:00/12:00 UTC application-host
  timer and alerts. Permit destructive migrations only after observed age and drill
  evidence. Final all-feature rehearsal stays later and does not block this baseline.
- **Rollback/containment:** **present:** stop application writes on suspected corruption,
  preserve volume/forensic copy, never `down -v`, and restore only to a new location
  before cutover. There is no current backup kill switch to rely on.
- **Residual risk:** once activated, the objective permits up to 24h loss; dumps are
  intentionally unencrypted on the protected dataset, and restore depends on TrueNAS,
  TrueNAS administrator recovery access, 20 GiB quota exhaustion or future-bucket
  occupation from a write flood, receiver/control dataset availability, and any
  independently managed off-site copy. Twice-daily
  margin, dataset/access controls, quota alerts/local maintenance,
  independent NAS monitoring, and recurring drills are required.
- **Linked Witan work:** `tk-establish-production-postgresql-backup-and-resto-01b0c1`,
  later `tk-document-and-rehearse-backup-restore-rollback-an-3bf520`, and
  `tk-harden-api-operations-security-and-release-verif-5f790a`.

### REL-RISK-003 — Semantic web/database failure lacks durable detection and evidence

- **Affected IDs/objectives:** OPS-008–OPS-010, OPS-014; OPS-INV-001–OPS-INV-003;
  `REL-OBJ-004`–`006`, `REL-OBJ-011`.
- **Present status/evidence:** **open-known**. Web health is TCP-only; access logs,
  metrics, retention, error collection, semantic readiness and alerts are absent from
  repository evidence. This excludes worker freshness (`REL-RISK-001`) and explicit
  refresh log-level reachability/parsing (`REL-RISK-025`).
- **Owner boundary:** web/Compose readiness and production monitoring/log retention.
- **Triggers:** DB/query/migration failure after socket accept, application 500s,
  capacity exhaustion, disk/log loss, proxy breakage, or silent restart loop.
- **Current → preferred detection:** manual curls, `compose ps/logs`, point-in-time DB
  probe → HTTP+DB semantic readiness, route status/latency metrics, bounded retained
  logs, public/authenticated synthetics and SLO alerts.
- **Mitigation sequence:** enable safe access/structured logs and retention; add
  semantic probes; establish dashboards/burn alerts; then make health dependency-aware.
- **Rollback/containment:** **present:** manual curls, Compose/log inspection and the
  documented code rollback while preserving DB. **Future rollout:** keep TCP probe in
  parallel until semantic readiness is proven; exporter disablement and bounded-label
  rollback controls are prerequisites before enabling production metrics.
- **Residual risk:** external Funnel/host monitoring remains outside app control;
  synthetics cannot prove every personalized behavior.
- **Linked Witan work:** `tk-add-structured-observability-health-readiness-an-16ba73`,
  `tk-add-ci-quality-gates-coverage-ratchet-migration--3ae78f`.

### REL-RISK-004 — Automatic migrations lack a backup and rollback gate

- **Affected IDs/objectives:** OPS-008–OPS-010, OPS-013–OPS-014; DATA-INV-004,
  OPS-INV-003–OPS-INV-004; `REL-OBJ-010`–`011`.
- **Present status/evidence:** **open-known**. Web startup runs `migrate`; deployment is
  manual, and code rollback cannot reverse schema/data. Permanent loss is scored only
  in `REL-RISK-002`; this risk owns incompatible schema and deployment outage.
- **Owner boundary:** release/migration author and production operator.
- **Triggers:** irreversible/data migration, old/new code overlap, failed migration,
  migration lock, rollback to schema-incompatible code, or skipped preflight.
- **Current → preferred detection:** deploy check and DB connection before restart →
  migration plan/dry-run, verified backup reference, compatibility check, explicit
  migration job, revision-tagged smoke and rollback gate.
- **Mitigation sequence:** enforce `REL-OBJ-010`; add PostgreSQL migration CI; require
  expand-contract plan; separate migration from web boot after compatibility proof.
- **Rollback/containment:** stop before app restart on preflight failure; preserve old
  app until additive migration succeeds; use migration-specific reversal or verified
  restore, never checkout alone.
- **Residual risk:** PostgreSQL locks and data volume can still make a tested migration
  slower in production.
- **Linked Witan work:** early `tk-establish-production-postgresql-backup-and-resto-01b0c1`,
  `tk-add-foundational-continuous-integration-quality--b3ffba`, and
  `tk-add-required-postgresql-integration-and-concurre-0719fc`; later
  `tk-add-ci-quality-gates-coverage-ratchet-migration--3ae78f` and
  `tk-document-and-rehearse-backup-restore-rollback-an-3bf520`.

### REL-RISK-005 — Synchronous external work can consume web serving capacity

- **Affected IDs/objectives:** ING-003, ING-008, SAVE-002–SAVE-003, API-009,
  API-011, API-016, OPS-008; `REL-OBJ-004`–`006`, `REL-OBJ-009`.
- **Present status/evidence:** **open-known**. Feed discovery/global refresh and
  Linkding calls run in Gunicorn; worker count/timeouts are not repository-configured.
- **Owner boundary:** browser/API adapters, integration services and Gunicorn runtime.
- **Triggers:** slow origin/provider, global serial refresh, concurrent expensive token
  calls, DNS/socket delay, or insufficient worker capacity.
- **Current → preferred detection:** client timeout and per-feed/Linkding result →
  dependency duration, active request/worker saturation, queue age, route latency and
  rate-limit signals.
- **Mitigation sequence:** instrument and bound calls; configure Gunicorn; restrict
  expensive operation authority/rate; then introduce database-backed jobs with a
  versioned async contract rather than a service rewrite.
- **Rollback/containment:** **present:** restart wedged web without DB volume changes
  and have operators avoid the expensive endpoints; no current route kill switch is
  evidenced. **Future rollout:** an independently tested refresh/integration kill
  switch and bounded synchronous fallback are prerequisites before async enablement.
- **Residual risk:** ordinary page queries and provider status endpoints still share
  finite host capacity.
- **Linked Witan work:** `tk-split-external-gateways-from-pure-domain-service-5bf313`,
  `tk-add-structured-observability-health-readiness-an-16ba73`,
  `tk-harden-api-operations-security-and-release-verif-5f790a`.

### REL-RISK-006 — Overlapping refresh callers have no ownership or fencing

- **Affected IDs/objectives:** ING-005, ING-007–ING-009, API-016; DATA-INV-001–002,
  FEED-INV-001–002; `REL-OBJ-003`.
- **Present status/evidence:** **open-inferred**. Worker/browser/API may overlap;
  eligibility is unlocked and a stale failure may overwrite concurrent success.
  SQLite-heavy tests do not establish PostgreSQL concurrency behavior.
- **Owner boundary:** refresh orchestration, Feed status persistence and PostgreSQL.
- **Triggers:** manual/API refresh during worker cycle, worker restart overlap, slow
  origin, multiple web requests, or retry at a boundary.
- **Current → preferred detection:** conflicting latest Feed fields may be noticed →
  run/lease owner, contention/lost-lease/stale-write counters and PostgreSQL concurrent
  test.
- **Mitigation sequence:** add PostgreSQL CI; characterize races; additive lease/run
  ownership; fence status commits; only later enqueue durable work.
- **Rollback/containment:** **present:** operators avoid browser/API refresh during the
  worker cycle and restart the single worker if overlap is suspected; no claim switch
  exists. **Future rollout:** a tested claim-disable switch is prerequisite, additive
  records remain across rollback, and stale-owner writes stay fenced.
- **Residual risk:** remote fetch may be duplicated after lease loss even if stale DB
  commit is fenced.
- **Linked Witan work:** `tk-reconcile-article-identity-and-concurrent-refres-7a1b44`,
  `tk-add-required-postgresql-integration-and-concurre-0719fc`.

### REL-RISK-007 — Postmark creation is not atomic or race-idempotent

- **Affected IDs/objectives:** NEWS-001–NEWS-002, API-017; NEWS-INV-001–002;
  `REL-OBJ-008`.
- **Present status/evidence:** **open-demonstrated**. Expected-failure evidence leaves
  an orphan Article when Issue creation fails; concurrent MessageID replay is unproved.
- **Owner boundary:** newsletter domain transaction and PostgreSQL uniqueness; provider
  owns delivery/retry timing.
- **Triggers:** DB/model failure between writes, duplicate/concurrent delivery,
  process crash, synthetic Feed race, or retry after orphaning.
- **Current → preferred detection:** adapter response and unique Issue query → orphan
  detector, atomic outcome/replay counters, provider correlation and concurrency test.
- **Mitigation sequence:** detect/audit orphans; one atomic idempotent use case; race
  handling against DB uniqueness; repair command only after backup gate.
- **Rollback/containment:** **present:** return a safe retryable error; if corruption
  continues, unset/rotate the configured inbound secret and restart web so requests are
  rejected; preserve payload outside logs. Future repair rollout retains its audit and
  schema-compatible rows on code rollback.
- **Residual risk:** provider may retry outside retention and base URL remains a separate
  configuration concern.
- **Linked Witan work:** `tk-make-postmark-newsletter-ingestion-atomic-and-ra-e3d06e`,
  `tk-add-required-postgresql-integration-and-concurre-0719fc`.

### REL-RISK-008 — Signed GET capabilities are replayable and non-expiring

- **Affected IDs/objectives:** API-018–API-019; API-CAP-INV-002,
  API-COMPAT-INV-002; `REL-OBJ-006`.
- **Present status/evidence:** **open-known**. Deterministic global HMAC links mutate as
  one configured user with no expiry, nonce, use record, revocation, or purpose audit.
- **Owner boundary:** signed API capability design and client migration.
- **Triggers:** URL leak through history/log/referrer, scanner/prefetch, repeated click,
  shared-secret compromise, or period URL reused on a later date.
- **Current → preferred detection:** no capability-use audit → capability ID/purpose,
  expiry/replay/revocation counters without signature values.
- **Mitigation sequence:** instrument legacy use; add one-use purpose/user-bound
  expiring POST capability; dual support for a dated window; retire old links to 410.
- **Rollback/containment:** **present:** rotate or unset `AGENT_LINK_SECRET` and restart
  so signed routes reject on leak. **Future rollout:** dual-auth rollback and preserved
  use/idempotency records are prerequisites; never roll back by deleting those records.
- **Residual risk:** a valid capability can still be stolen and used before expiry;
  client-side URL handling remains relevant.
- **Linked Witan work:** `tk-add-explicit-api-capabilities-and-replace-replay-69180c`.

### REL-RISK-009 — Postmark authentication relies on a loggable path secret

- **Affected IDs/objectives:** API-017, OPS-005, OPS-015; API-AUTH-INV-001,
  `REL-OBJ-008`.
- **Present status/evidence:** **open-known**. Constant-time secret comparison exists,
  but no provider signature/header, source restriction or rate limit is evidenced;
  upstream path redaction is unknown.
- **Owner boundary:** webhook adapter plus provider/Funnel ingress configuration.
- **Triggers:** path logging/leak, brute force, provider misconfiguration, replay, or
  public request flooding.
- **Current → preferred detection:** 403/adapter response only → auth-mode/version,
  rejected request rate, provider delivery correlation and ingress alert, never path.
- **Mitigation sequence:** verify Postmark-supported auth; add signature/header if
  available, otherwise rotating dual secrets; rate/source controls and payload limits;
  redact upstream paths.
- **Rollback/containment:** **present:** rotate/unset the path secret and restart; deny
  ingress only where an independently evidenced Funnel/host control exists. **Future
  rollout:** require a tested dual-credential window before changing auth mode.
- **Residual risk:** allowlists can drift and provider credentials can still be
  compromised.
- **Linked Witan work:** `tk-harden-postmark-authentication-payload-limits-an-601f5f`.

### REL-RISK-010 — Linkding timeout can leave an unknowable remote outcome

- **Affected IDs/objectives:** SAVE-001–SAVE-004, API-009; SAVE-INV-001–003;
  `REL-OBJ-009`.
- **Present status/evidence:** **open-known**. Local-first persistence is correct, but
  timeout-after-remote-success is collapsed into `linkding_saved=false/error`; no
  idempotency, outbox or reconciliation exists.
- **Owner boundary:** save service and Linkding gateway; provider owns remote create
  semantics. This risk excludes insecure URL configuration (`REL-RISK-022`).
- **Triggers:** lost response, process crash after POST, malformed success, provider
  retry behavior, network partition, or re-save after ambiguous result.
- **Current → preferred detection:** SavedArticle boolean/error → durable attempt state
  separating definite failure from ambiguity, provider key/upsert evidence and
  reconciliation result.
- **Mitigation sequence:** investigate provider idempotency/upsert; model explicit
  pending/confirmed/failed/ambiguous; outbox/reconcile; never blind retry ambiguity.
- **Rollback/containment:** keep local save; suppress remote retry while ambiguous;
  allow operator reconciliation; preserve attempt records through rollback.
- **Residual risk:** exactly-once remote effect may be impossible; objective is an
  auditable ambiguity with bounded reconciliation.
- **Linked Witan work:** `tk-implement-recoverable-linkding-delivery-state-ma-2ec860`,
  `tk-split-external-gateways-from-pure-domain-service-5bf313`.

### REL-RISK-011 — Bulk-read marker shape, uniqueness, and session atomicity are unsafe

- **Affected IDs/objectives:** WEB-009–WEB-011, WEB-019, API-010, API-013;
  READ-INV-003–006; `REL-OBJ-004`–`007`.
- **Present status/evidence:** **open-demonstrated**. Four expected failures prove
  absent shape/order constraints; PostgreSQL nullable uniqueness is insufficient;
  session materialization and marker upsert are not one transaction.
- **Owner boundary:** shared read command, BulkReadMarker schema and PostgreSQL.
- **Triggers:** malformed browser/admin/direct write, concurrent same-scope action,
  failure between materialization/marker, or duplicate NULL-shaped rows.
- **Current → preferred detection:** suite characterizations/manual DB inspection →
  shared validation, cleanup audit, conditional constraints, rollback/concurrency
  tests and old/new read mismatch counter.
- **Mitigation sequence:** backup; PostgreSQL CI; dry-run audit/dedupe; shared atomic
  command; additive checks/conditional uniqueness; dual-read compare.
- **Rollback/containment:** preserve cleanup export/backup. A dual-reader comparison and
  old-reader switch are **future rollout prerequisites**, not current controls; dropping
  constraints cannot recover deleted duplicates.
- **Residual risk:** large marker evaluation remains until `REL-RISK-012` mitigation.
- **Linked Witan work:** `tk-enforce-bulkreadmarker-scope-and-uniqueness-inva-db62c6`,
  `tk-extract-validated-transactional-commands-for-mut-466545`.

### REL-RISK-012 — Unbounded and marker×article reads can degrade pages/API

- **Affected IDs/objectives:** WEB-004–WEB-006, WEB-011, API-006–API-007;
  DATA-INV-002, READ-INV-001–005; `REL-OBJ-004`–`006`.
- **Present status/evidence:** **open-inferred**. Marker evaluation is Python
  marker×article work, date extraction lacks a dedicated index, and API arbitrary
  windows are unpaginated/unbounded. Production-sized latency is unknown.
- **Owner boundary:** shared visibility/query policy, ORM/PostgreSQL and API contract.
- **Triggers:** article/marker growth, large explicit date window, many users/markers,
  concurrent requests, or unfavorable query plan.
- **Current → preferred detection:** tests and user latency → query count/rows/memory,
  p50/p95, production-sized fixture and PostgreSQL plan fingerprints.
- **Mitigation sequence:** extract shared policy; establish cardinality/latency baseline;
  bound/paginate contract; half-open indexed times; dual-read before removing marker
  evaluation.
- **Rollback/containment:** **present:** deactivate an abusive bearer token and use code
  rollback while preserving data; no API window cap/flag is current. **Future rollout:**
  a prior-reader/pagination compatibility switch is prerequisite; indexes may remain.
- **Residual risk:** larger data always increases cost; SLO-based capacity review remains.
- **Linked Witan work:** `tk-bound-query-size-pagination-and-read-state-cost-1f9da0`,
  `tk-extract-shared-article-visibility-and-read-state-6708e0`.

### REL-RISK-013 — Authenticated responses can be cached without private/no-store

- **Affected IDs/objectives:** WEB-002–WEB-006, WEB-016, NEWS-003, ING-013,
  API-001, API-003, API-006–API-007; UI-INV-004; `REL-OBJ-007`.
- **Present status/evidence:** **open-demonstrated**. Today is protected, while the
  all-authenticated-GET expected failure demonstrates missing headers elsewhere.
- **Owner boundary:** Django response middleware/decorators and proxy/browser cache
  behavior.
- **Triggers:** shared/intermediary cache, browser history/storage, authenticated OPML,
  per-user state, or the same public newsletter UUID rendered once anonymously and once
  with authenticated chrome/read state without `Vary: Cookie`.
- **Current → preferred detection:** one expected-failure matrix → passing all-route
  contract plus same-newsletter-URL anonymous/authenticated header/body synthetics.
- **Mitigation sequence:** central authenticated `private, no-store` policy; require
  `Vary: Cookie` for both newsletter variants; keep anonymous `no-store` until an
  explicit public-cache policy is tested; verify proxy path before release.
- **Rollback/containment:** **present:** deploy corrected headers and force affected
  sessions to log out if disclosure is suspected; no intermediary purge control is
  repository-evidenced. Never restore unsafe caching for performance.
- **Residual risk:** client screenshots/downloads and compromised endpoints remain
  outside HTTP cache control.
- **Linked Witan work:** `tk-harden-api-operations-security-and-release-verif-5f790a`,
  `tk-complete-browser-view-form-command-and-api-contr-55d622`.

### REL-RISK-014 — Stable URL with a changed GUID fails a Feed refresh

- **Affected IDs/objectives:** ING-005–ING-007; DATA-INV-001, FEED-INV-002;
  `REL-OBJ-001`, `REL-OBJ-003`.
- **Present status/evidence:** **open-demonstrated** expected failure: upsert by GUID
  conflicts with independent `(feed,url)` uniqueness and rolls back the Feed.
- **Owner boundary:** Article identity policy, refresh service and schema.
- **Triggers:** publisher changes GUID while preserving URL, alternate-link shift, or
  concurrent identity observation.
- **Current → preferred detection:** classified integrity failure/Feed backoff →
  identity-reconciliation counter and deterministic/concurrent tests.
- **Mitigation sequence:** record canonical identity decision; characterize collisions;
  reconcile deterministically under transaction; PostgreSQL concurrency proof.
- **Rollback/containment:** isolate/back off Feed; operator may deactivate it; do not
  delete Articles ad hoc. Keep repair compatible with old schema.
- **Residual risk:** publishers can change both GUID and URL, requiring an explicit
  non-identity/duplicate policy.
- **Linked Witan work:** `tk-reconcile-article-identity-and-concurrent-refres-7a1b44`.

### REL-RISK-015 — OPML import can error, partially commit, or lose categories

- **Affected IDs/objectives:** ING-011–ING-013; FEED-INV-004–006;
  `REL-OBJ-005`, `REL-OBJ-011`.
- **Present status/evidence:** **open-demonstrated**. Malformed XML expected failure,
  non-atomic valid processing, same-name/different-slug failure and flat lossy export
  are characterized.
- **Owner boundary:** OPML parser/service/form and Category/Feed persistence.
- **Triggers:** malformed/large file, later outline conflict, category slug drift,
  export-reimport, or exception after prior writes.
- **Current → preferred detection:** 500/messages and partial DB inspection → bounded
  validation summary, atomic rollback tests and round-trip contract.
- **Mitigation sequence:** size/input validation; parse to validated plan; transaction;
  name/slug reuse policy; hierarchical round-trip; preserve compatibility fixtures.
- **Rollback/containment:** stop import, export/current DB audit before repair; restore
  backup only for broad corruption; code rollback safe if schema unchanged.
- **Residual risk:** third-party OPML dialects remain best-effort and should report
  skipped entries explicitly.
- **Linked Witan work:** `tk-make-opml-import-export-atomic-validated-and-rou-df59b7`.

### REL-RISK-016 — Feed destination validation retains SSRF seams

- **Affected IDs/objectives:** ING-003–ING-004, OPS-005, OPS-015.
- **Present status/evidence:** **residual-known**. Strong scheme, port, resolved-address,
  redirect and response bounds exist, but validation DNS is separate from connection
  DNS and repository Compose defines no egress allow/deny policy.
- **Owner boundary:** feed-fetch destination validation and host/container egress only.
  Scheduler, process deadline, worker staleness and freshness consequences are excluded
  and owned by `REL-RISK-001`.
- **Triggers:** DNS rebinding between validation/connect, redirect target manipulation,
  private-address resolution race, or absent network egress restriction.
- **Current → preferred detection:** `blocked_target`/redirect classifications and tests
  → connection-pinned destination evidence, egress-denial logs and rebinding/redirect
  security tests without destination secrets.
- **Mitigation sequence:** preserve per-hop validation; evaluate connection pinning;
  restrict container/host egress; test redirects and rebinding at the transport seam.
- **Rollback/containment:** **present:** deactivate the offending Feed and keep the
  address/redirect policy strict. Host egress denial is future infrastructure unless
  independently evidenced; it is a rollout prerequisite before claiming closure.
- **Residual risk:** outbound public HTTP inherently contacts untrusted destinations;
  layered host isolation remains necessary.
- **Linked Witan work:** `tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c`.

### REL-RISK-017 — Token authority may exceed unresolved account trust policy

- **Affected IDs/objectives:** API-002–API-003, API-011–API-016, OPS-015;
  DATA-INV-003, API-AUTH-INV-002; `REL-OBJ-006`.
- **Present status/evidence:** **conditional-unknown**. Tokens have no scope/expiry and
  can mutate global Feeds/Categories/refresh; whether all accounts are mutually
  trusted is not encoded. Confidence is low until the owner model is decided.
- **Owner boundary:** product authorization decision, bearer token model and API.
- **Triggers:** multiple mutually untrusted users, leaked long-lived token, abandoned
  integration, or expensive/global mutation by a limited client.
- **Current → preferred detection:** token `last_used_at` only → scoped operation audit,
  expiry/rotation state, denied-capability counters and ownership decision.
- **Mitigation sequence:** ADR single-owner vs multi-user; inventory token consumers;
  add least-privilege scopes/expiry with grace rotation if required; rate expensive
  operations.
- **Rollback/containment:** **present:** deactivate token/user and rotate without
  printing secrets. **Future rollout:** a tested dual-token grace path is prerequisite;
  do not infer global data ownership before ADR.
- **Residual risk:** authorized privileged tokens remain high impact and require secure
  client storage.
- **Linked Witan work:** `tk-harden-api-operations-security-and-release-verif-5f790a`,
  `tk-add-explicit-api-capabilities-and-replace-replay-69180c`.

### REL-RISK-018 — Browser mutation redirects accept external destinations

- **Affected IDs/objectives:** WEB-008–WEB-010, WEB-018, ING-008, SAVE-003;
  UI-INV-005; `REL-OBJ-004`–`005`.
- **Present status/evidence:** **open-demonstrated**. One expected failure and the same
  direct `next` pattern across five mutation handlers show open redirects.
- **Owner boundary:** browser redirect-resolution policy and mutation adapters.
- **Triggers:** attacker-supplied posted `next`, malicious link/form, or copied external
  redirect value after a successful mutation.
- **Current → preferred detection:** expected-failure test → shared resolver, rejection
  counter and all-handler contract tests.
- **Mitigation sequence:** central same-origin fallback; apply to all five handlers;
  convert expected failure and add adapter matrix.
- **Rollback/containment:** remove malicious links; session logout if phishing follows;
  code rollback must not restore an unsafe redirect once clients rely on local fallback.
- **Residual risk:** safe same-origin destinations can still be confusing; mutation
  confirmation remains important.
- **Linked Witan work:** `tk-validate-all-browser-redirect-targets-99209e`.

### REL-RISK-019 — Responsive mobile/desktop parity evidence is narrow

- **Affected IDs/objectives:** WEB-001–WEB-002, WEB-007–WEB-008, WEB-012,
  WEB-016, WEB-020; UI-INV-001, UI-INV-003; `REL-OBJ-012`.
- **Present status/evidence:** **partially controlled**. Today Chrome tests cover
  320/390/desktop identity, geometry and target persistence after the incident, but
  other surfaces, browsers, landscape, long content and full JS behavior do not.
- **Owner boundary:** responsive templates/CSS/JS, viewport browser matrix and release
  parity gate. Accessibility consequences are excluded and owned by `REL-RISK-020`.
- **Triggers:** header/nav growth, long content, CSS/theme change, JS card-selection
  drift, browser engine differences, focus/compact mode or localization.
- **Current → preferred detection:** Today Playwright geometry/state → release matrix
  across key pages/modes, executable JS flows and failure artifacts.
- **Mitigation sequence:** retain Today tests; execute JS behavior tests; add shared
  320/390/desktop smoke for other pages; expand browsers/modes from observed risk.
- **Rollback/containment:** **present:** revert the frontend asset/template revision and
  preserve native form behavior; there is no general JS kill switch. A tested
  enhancement-disable mechanism is a future rollout prerequisite if introduced.
- **Residual risk:** physical devices and unusual content cannot be exhaustively tested.
- **Linked Witan work:** `tk-add-real-browser-responsive-theme-keyboard-and-a-147e09`,
  `tk-execute-javascript-behavior-tests-instead-of-sou-141553`,
  `tk-stabilize-mobile-today-and-article-rendering-5f1421`.

### REL-RISK-020 — Accessibility behavior lacks executable and assistive-technology proof

- **Affected IDs/objectives:** WEB-012–WEB-014, WEB-017, WEB-021; UI-INV-001–002;
  `REL-OBJ-012`.
- **Present status/evidence:** **partially controlled**. Semantic markup and visible
  focus exist, but there is no axe/screen-reader/contrast proof, help-dialog focus trap,
  removal-focus contract or end-to-end keyboard suite.
- **Owner boundary:** accessible templates/CSS/JavaScript and accessibility release
  testing. Responsive layout parity is `REL-RISK-019`; image privacy is `REL-RISK-024`.
- **Triggers:** keyboard dialog/removal flow, contrast/theme change, dynamic live-region
  behavior, zoom, screen-reader/browser variation or inaccessible error state.
- **Current → preferred detection:** markup assertions/limited geometry → automated
  accessibility scan, executable keyboard/focus tests and periodic manual AT checklist.
- **Mitigation sequence:** establish automated baseline; execute keyboard/focus flows;
  fix critical findings; add periodic manual screen-reader/zoom review.
- **Rollback/containment:** **present:** revert inaccessible UI while retaining native
  controls and block release on a known critical regression. Any future enhancement
  kill switch must be tested before it is listed as containment.
- **Residual risk:** automated tooling cannot prove usability across all assistive
  technologies.
- **Linked Witan work:** `tk-add-real-browser-responsive-theme-keyboard-and-a-147e09`,
  `tk-execute-javascript-behavior-tests-instead-of-sou-141553`.

### REL-RISK-021 — HSTS is intentionally disabled

- **Affected IDs/objectives:** OPS-003–OPS-004, OPS-009–OPS-010;
  OPS-INV-003; `REL-OBJ-011`.
- **Present status/evidence:** **accepted-deferred**. HTTPS redirect/secure cookies and
  Funnel TLS are configured, but `SECURE_HSTS_SECONDS=0` is explicit until recovery
  paths are verified.
- **Owner boundary:** production transport/Funnel operator and Django settings.
- **Triggers:** first-visit downgrade/host interception, proxy misconfiguration, or
  enabling HSTS before all recovery names/paths are safe.
- **Current → preferred detection:** deploy check silences only W004 and manual curls →
  staged header verification, external TLS/redirect synthetic and rollback rehearsal.
- **Mitigation sequence:** verify every hostname/recovery path; low max-age canary;
  observe; increase deliberately; consider subdomains/preload only after proof.
- **Rollback/containment:** remove header and wait out short canary max-age; do not start
  with long duration/preload.
- **Residual risk:** HSTS cannot protect initial contact and long-lived policy makes
  certificate/proxy recovery less forgiving.
- **Linked Witan work:** `tk-harden-api-operations-security-and-release-verif-5f790a`.

### REL-RISK-022 — Linkding endpoint transport is not fail-closed

- **Affected IDs/objectives:** SAVE-002–SAVE-003, OPS-005; SAVE-INV-003;
  `REL-OBJ-009`.
- **Present status/evidence:** **open-known**. Default is HTTPS, but startup/service does
  not enforce scheme/host; an operator can send the token/bookmark over an insecure or
  unintended configured endpoint.
- **Owner boundary:** settings validation, Linkding gateway and operator configuration.
- **Triggers:** mistyped/malicious environment value, DNS compromise, accidental HTTP,
  or unexpected redirect/provider topology.
- **Current → preferred detection:** request-time failure or success only → fail-closed
  HTTPS/credential-free URL validation, allowed deployment target decision and TLS
  failure metric.
- **Mitigation sequence:** validate URL without values in errors; reject non-HTTPS in
  production; verify redirects/provider behavior; preflight reachability without
  exposing token.
- **Rollback/containment:** **present:** unset `LINKDING_TOKEN`, restart web and rotate
  the token if it crossed an untrusted path; local saves remain. Never relax HTTPS for
  availability; any future integration switch must be tested before rollout.
- **Residual risk:** HTTPS does not establish provider trust or prevent compromised DNS/
  CA/host.
- **Linked Witan work:** `tk-implement-recoverable-linkding-delivery-state-ma-2ec860`,
  `tk-harden-api-operations-security-and-release-verif-5f790a`.

### REL-RISK-023 — Shared policy is spread across presentation adapters

- **Affected IDs/objectives:** WEB-008–WEB-011, ING-007–ING-009, SAVE-004,
  API-007–API-010, API-013, API-016; DATA-INV-002–003, READ-INV-001–006,
  OPS-INV-001; `REL-OBJ-003`–`009`.
- **Present status/evidence:** **open-known**. `api.py` imports private browser helpers;
  read/refresh/save orchestration and atomicity differ by adapter; URLconf dynamically
  imports API. Dead/duplicated presentation paths increase drift risk.
- **Owner boundary:** application/query/command boundaries below browser/API/CLI.
- **Triggers:** behavior change in one adapter, private-helper refactor, new endpoint,
  atomicity fix applied only once, or divergent summaries/validation.
- **Current → preferred detection:** cross-feature tests and expected failures → shared
  command/query contract tests, import-direction check and adapter parity metrics.
- **Mitigation sequence:** preserve characterization; extract shared visibility policy;
  extract validated transactional commands; split external gateways; then thin adapters.
  This is incremental boundary repair, not a rewrite.
- **Rollback/containment:** code-only rollback by module; keep endpoint/method/schema
  contracts unchanged; stop extraction on query-count, Article-ID or error-envelope
  drift.
- **Residual risk:** Django framework and adapter-specific representation remain, so
  parity tests are still required.
- **Linked Witan work:** `tk-inventory-architecture-and-refactor-domain-bound-dc9eb3`,
  `tk-extract-shared-article-visibility-and-read-state-6708e0`,
  `tk-extract-validated-transactional-commands-for-mut-466545`,
  `tk-split-external-gateways-from-pure-domain-service-5bf313`.

### REL-RISK-024 — Newsletter remote images can disclose reader network metadata

- **Affected IDs/objectives:** NEWS-003–NEWS-004, OPS-015; NEWS-INV-004;
  `REL-OBJ-005`, `REL-OBJ-007`.
- **Present status/evidence:** **conditional-unknown**. Sanitization allows HTTP(S)
  remote images, so opening a public issue can contact the image host; the intended
  privacy policy is unresolved and no CSP/image proxy/click-to-load control exists.
- **Owner boundary:** newsletter archive privacy/content policy and rendering; external
  image hosts own their request logs. Accessibility is excluded to `REL-RISK-020`.
- **Triggers:** opening an issue containing a tracking image, an image-host compromise,
  or an authenticated/anonymous cache variant rendering remote content.
- **Current → preferred detection:** sanitizer tests prove allowed images only → policy
  decision, CSP/reporting where appropriate, image-load tests and privacy review without
  contacting live trackers.
- **Mitigation sequence:** record the remote-image ADR; choose strip, proxy or explicit
  click-to-load; add sanitizer/render/cache tests; monitor only non-sensitive counts.
- **Rollback/containment:** **present:** remove an offending stored image/body through
  controlled administration and advise users not to open the issue; no global image
  kill switch is evidenced. A tested policy switch is prerequisite to a staged rollout.
- **Residual risk:** a permitted external link can still disclose data when deliberately
  followed, and a proxy would become a new sensitive boundary.
- **Linked Witan work:** `tk-harden-postmark-authentication-payload-limits-an-601f5f`.

### REL-RISK-025 — Refresh log-level configuration is unreachable and not fail-closed

- **Affected IDs/objectives:** OPS-005, OPS-011; OPS-INV-001;
  `REL-OBJ-001`–`003`.
- **Present status/evidence:** **open-known**. `FEED_REFRESH_LOG_LEVEL` is read directly
  by Django logging but is absent from `.env.example` and canonical Compose pass-through;
  `.env` therefore cannot configure it, and an invalid direct-process value may fail
  logging setup during startup.
- **Owner boundary:** Django logging configuration, environment parsing, Compose
  reachability and operator documentation. General log retention is `REL-RISK-003`.
- **Triggers:** operator expects `.env` override, mistyped direct environment value,
  noisy emergency level change, or missing success logs during diagnosis.
- **Current → preferred detection:** default INFO console output/settings-import failure
  → allowlisted fail-closed parsing, Compose-render test, documented variable and a
  startup/refresh log smoke that proves the effective level without printing values.
- **Mitigation sequence:** validate against supported levels; add `.env.example` name
  and Compose pass-through; add settings/Compose/log smoke tests; preserve INFO default.
- **Rollback/containment:** **present:** remove an invalid direct-process override and
  restart with default INFO, then inspect console logs; there is no dynamic log-level
  control. Future structured-logging disablement must be tested before rollout.
- **Residual risk:** a valid restrictive level can still suppress useful diagnostics;
  retention/aggregation remains separately open under `REL-RISK-003`.
- **Linked Witan work:** `tk-add-structured-observability-health-readiness-an-16ba73`,
  `tk-harden-api-operations-security-and-release-verif-5f790a`.

## 6. Dependency-ordered no-rewrite mitigation portfolio

This portfolio maps existing work to risks/objectives and cross-feature contracts. It
uses expand-contract changes and preserves public behavior until an explicit API or
product decision. A later wave cannot substitute for an unmet earlier stop gate.

| Wave | Work and Witan mapping | Risks/objectives/contracts | Entry and stop gate | Rollback/containment gate |
| --- | --- | --- | --- | --- |
| 0. Preserve contracts and safety | Keep catalogs/contracts current; complete `tk-create-feature-to-test-traceability-matrix-b382a2` and `tk-complete-browser-view-form-command-and-api-contr-55d622`. | All; especially `REL-OBJ-007`, `REL-OBJ-012` and cross-feature invariants. | Live graph: both tasks are open on their existing prerequisites; foundational CI remains blocked by these Wave-0 tasks. Stop on snapshot-count mutation, false fix claims or unexpected suite change. | Documentation/test-only revert; retain incident and immutable snapshots. |
| 1. Establish recovery and foundational CI | Start `tk-establish-production-postgresql-backup-and-resto-01b0c1`, `tk-add-foundational-continuous-integration-quality--b3ffba`, and `tk-add-required-postgresql-integration-and-concurre-0719fc`. | `REL-RISK-002`, `REL-RISK-004`, `REL-RISK-006`, `REL-RISK-011`; `REL-OBJ-010`, `REL-OBJ-011`; DATA-INV-004, OPS-INV-003, OPS-INV-004. | Live graph: backup baseline is blocked by this doc task and already-closed production-settings work; foundational CI is blocked by the two Wave-0 tasks; PostgreSQL integration retains its maintainable-test-structure prerequisite. No destructive migration until off-host restore meets RPO/RTO and PostgreSQL migration tests pass. | Present containment is stop writes/preserve volume/restore to new DB. CI jobs may be reverted; verified backup evidence is never discarded. |
| 2. Immediate detection and bounded operation | `tk-add-structured-observability-health-readiness-an-16ba73`, `tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c`. | `REL-RISK-001`, `REL-RISK-003`, `REL-RISK-005`, `REL-RISK-016`, `REL-RISK-025`; `REL-OBJ-001`–`006`; FEED-INV-001, OPS-INV-001, OPS-INV-002, OPS-INV-005. | Test alerts without paging, then observe three current cycles; stop on sensitive labels, false paging, cardinality, >10% overhead or changed outcomes. Claim/emission/route switches must exist and be tested before affected rollout. | Present restart/manual probes remain; new switches may roll back their features while retaining logs/run records and parallel TCP check. |
| 3. Deterministic confirmed correctness repairs | Redirect `tk-validate-all-browser-redirect-targets-99209e`; OPML `tk-make-opml-import-export-atomic-validated-and-rou-df59b7`; Postmark atomicity `tk-make-postmark-newsletter-ingestion-atomic-and-ra-e3d06e`; identity/concurrency `tk-reconcile-article-identity-and-concurrent-refres-7a1b44`. | `REL-RISK-007`, `REL-RISK-014`, `REL-RISK-015`, `REL-RISK-018`; `REL-OBJ-001`, `REL-OBJ-003`, `REL-OBJ-005`, `REL-OBJ-008`; DATA-INV-001, NEWS-INV-001, NEWS-INV-002, FEED-INV-002, FEED-INV-004–FEED-INV-006, UI-INV-005. | Required expected failures become ordinary passing tests; PostgreSQL proves concurrency; no orphan/partial writes. Stop on status/schema/redirect drift. | Code rollback only while schema compatible; preserve repair audit; destructive repair requires verified restore. |
| 4. Read-state durability and shared commands | `tk-enforce-bulkreadmarker-scope-and-uniqueness-inva-db62c6`, `tk-extract-validated-transactional-commands-for-mut-466545`, `tk-extract-shared-article-visibility-and-read-state-6708e0`. | `REL-RISK-011`, `REL-RISK-012`, `REL-RISK-023`; `REL-OBJ-004`–`007`; READ-INV-001–READ-INV-006. | Backup/audit/dedupe first; PostgreSQL rejects invalid shape; shared transactions and dual-read IDs match. A tested old-reader switch is an entry prerequisite. | Use the newly tested switch; preserve cleanup export. Dropping constraints cannot recover deleted duplicates. |
| 5. Clarify boundaries without endpoint rewrite | `tk-inventory-architecture-and-refactor-domain-bound-dc9eb3`, `tk-split-external-gateways-from-pure-domain-service-5bf313`. | `REL-RISK-005`, `REL-RISK-010`, `REL-RISK-023`; `REL-OBJ-003`–`009`; cross-feature invariants. | Characterization green; API imports no private view helpers; methods/envelopes/templates unchanged; no query/latency regression. | Code-only module rollback; old adapters remain until parity proof. |
| 6. Recoverable integrations and capability security | Linkding `tk-implement-recoverable-linkding-delivery-state-ma-2ec860`; signed capabilities `tk-add-explicit-api-capabilities-and-replace-replay-69180c`; Postmark/privacy `tk-harden-postmark-authentication-payload-limits-an-601f5f`; API/ops `tk-harden-api-operations-security-and-release-verif-5f790a`. | `REL-RISK-008`–`010`, `REL-RISK-013`, `REL-RISK-017`, `REL-RISK-021`, `REL-RISK-022`, `REL-RISK-024`; `REL-OBJ-006`–`011`; SAVE-INV-003, API-CAP-INV-002, NEWS-INV-004. | Provider/policy decisions first; no blind ambiguity retry; tested dual auth/capabilities and cache/security zero-budget tests. | Present secrets may be unset/rotated. Future dual-auth/integration switches are entry prerequisites; preserve local saves and ambiguity/use records. |
| 7. Responsive/accessibility executable gates | Build on closed mobile baseline `tk-stabilize-mobile-today-and-article-rendering-5f1421`; complete `tk-execute-javascript-behavior-tests-instead-of-sou-141553` and `tk-add-real-browser-responsive-theme-keyboard-and-a-147e09`. | `REL-RISK-019`, `REL-RISK-020`; `REL-OBJ-012`; UI-INV-001–UI-INV-003. | 320/390/desktop IDs/actions/overflow/discoverability pass; zero critical accessibility findings. Stop release on parity/state/a11y failure. | Present rollback is frontend revision revert with native controls retained; any enhancement switch must be tested before use. |
| 8. Evidence-led scale, full CI aggregation and release closure | `tk-bound-query-size-pagination-and-read-state-cost-1f9da0`, later aggregate gates in `tk-add-ci-quality-gates-coverage-ratchet-migration--3ae78f`, then `tk-document-and-rehearse-backup-restore-rollback-an-3bf520`. | `REL-RISK-012`; all objectives, especially `REL-OBJ-004`–`006/010/011`. | Record production-sized baseline; version pagination; require full CI, SLO evidence, restore and deploy verification. Stop on ordering/ID/latency regression. | A tested compatibility switch is prerequisite; indexes may remain; use verified release/migration-specific recovery. |

## 7. Mechanical maintenance and acceptance checks

When this file changes:

1. Preserve stable IDs. Retire with a replacement pointer; never reuse meaning.
2. Keep the objective register explicit about desired versus measurable behavior.
3. Keep objective/risk/rank sequences contiguous; score rows and bands mechanically
   correct; normalized confidence valid; and every risk field complete.
4. Resolve referenced feature IDs against the pinned catalog and invariant IDs against
   the companion contracts. The explicit 26-ID Witan set in the script was copied from
   the live graph on 2026-08-11; repository-local validation can prove document use
   matches that set, while graph existence/status is separately verified with
   `witan tasks --output-format json` because Witan is not repository data.
5. Validate repository-relative links and Markdown; run catalog/contract mechanics
   without changing pinned counts, pre-commit, the current full suite, Django and
   migration checks, Compose rendering and diff/secret checks. A green suite still
   includes its documented expected failures.

Mechanical reliability check:

```bash
python - <<'PY'
from pathlib import Path
import re

text = Path("docs/reliability/objectives-and-risks.md").read_text()
catalog = Path("docs/features/catalog.md").read_text()
contracts = Path("docs/features/contracts.md").read_text()

objectives = re.findall(r"^\| `(REL-OBJ-\d{3})`", text, re.M)
details = re.findall(r"^### (REL-RISK-\d{3}) —", text, re.M)
assert objectives == [f"REL-OBJ-{number:03d}" for number in range(1, 13)]
assert details == [f"REL-RISK-{number:03d}" for number in range(1, 26)]
assert len(objectives) == len(set(objectives)) == 12
assert len(details) == len(set(details)) == 25

row_pattern = re.compile(
    r"^\| (?P<rank>\d+) \| `(?P<id>REL-RISK-\d{3})` \| [^|]+ "
    r"\| (?P<s>[1-5]) \| (?P<l>[1-5]) \| (?P<d>[1-5]) "
    r"\| (?P<score>\d+) \| (?P<band>critical|high|medium|low) "
    r"\| (?P<confidence>high|medium|low) \| [^|]+ \|$",
    re.M,
)
rows = []
for match in row_pattern.finditer(text):
    row = match.groupdict()
    for key in ("rank", "s", "l", "d", "score"):
        row[key] = int(row[key])
    assert row["score"] == row["s"] * row["l"] * row["d"]
    expected_band = (
        "critical" if row["score"] >= 60 else
        "high" if row["score"] >= 36 else
        "medium" if row["score"] >= 18 else "low"
    )
    assert row["band"] == expected_band
    rows.append(row)
assert len(rows) == 25
assert [row["rank"] for row in rows] == list(range(1, 26))
assert {row["id"] for row in rows} == set(details)
assert rows == sorted(
    rows,
    key=lambda row: (-row["score"], -row["s"], -row["l"], row["id"]),
)

required = (
    "Affected IDs/objectives", "Present status/evidence", "Owner boundary",
    "Triggers", "Current → preferred detection", "Mitigation sequence",
    "Rollback/containment", "Residual risk", "Linked Witan work",
)
blocks = re.split(r"^### REL-RISK-\d{3} —.*$", text, flags=re.M)[1:]
assert len(blocks) == 25
assert all(all(label in block for label in required) for block in blocks)
assert all(re.search(r"\btk-[a-z0-9-]+", block) for block in blocks)

feature_ids = set(re.findall(
    r"^### ((?:AUTH|WEB|ING|NEWS|SAVE|API|OPS)-\d{3}) —", catalog, re.M
))
invariant_ids = set(re.findall(
    r"^### ([A-Z]+(?:-[A-Z]+)*-INV-\d{3}) —", contracts, re.M
))
feature_refs = set(re.findall(
    r"\b(?:AUTH|WEB|ING|NEWS|SAVE|API|OPS)-\d{3}\b", text
))
invariant_refs = set(re.findall(
    r"\b[A-Z]+(?:-[A-Z]+)*-INV-\d{3}\b", text
))
def expand_ranges(prefix_pattern, references):
    pattern = re.compile(
        rf"\b(?P<prefix>{prefix_pattern})(?P<start>\d{{3}})–"
        rf"(?:(?P=prefix))?(?P<end>\d{{3}})\b"
    )
    for match in pattern.finditer(text):
        prefix = match.group("prefix")
        start, end = int(match.group("start")), int(match.group("end"))
        assert start <= end
        references.update(f"{prefix}{number:03d}" for number in range(start, end + 1))

expand_ranges(r"(?:AUTH|WEB|ING|NEWS|SAVE|API|OPS)-", feature_refs)
expand_ranges(r"[A-Z]+(?:-[A-Z]+)*-INV-", invariant_refs)
assert feature_refs <= feature_ids, sorted(feature_refs - feature_ids)
assert invariant_refs <= invariant_ids, sorted(invariant_refs - invariant_ids)

objective_refs = set(re.findall(r"\bREL-OBJ-\d{3}\b", text))
risk_refs = set(re.findall(r"\bREL-RISK-\d{3}\b", text))
expand_ranges(r"REL-OBJ-", objective_refs)
expand_ranges(r"REL-RISK-", risk_refs)
assert objective_refs == set(objectives)
assert risk_refs == set(details)
assert not re.search(r"\b(?:REL-(?:OBJ|RISK)-|[A-Z]+(?:-[A-Z]+)*-INV-)\d{3}/\d{3}", text)

WITAN_TASKS = {
    "tk-add-ci-quality-gates-coverage-ratchet-migration--3ae78f",
    "tk-add-explicit-api-capabilities-and-replace-replay-69180c",
    "tk-add-foundational-continuous-integration-quality--b3ffba",
    "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
    "tk-add-required-postgresql-integration-and-concurre-0719fc",
    "tk-add-structured-observability-health-readiness-an-16ba73",
    "tk-bound-query-size-pagination-and-read-state-cost-1f9da0",
    "tk-complete-browser-view-form-command-and-api-contr-55d622",
    "tk-create-feature-to-test-traceability-matrix-b382a2",
    "tk-document-and-rehearse-backup-restore-rollback-an-3bf520",
    "tk-enforce-bulkreadmarker-scope-and-uniqueness-inva-db62c6",
    "tk-establish-production-postgresql-backup-and-resto-01b0c1",
    "tk-execute-javascript-behavior-tests-instead-of-sou-141553",
    "tk-extract-shared-article-visibility-and-read-state-6708e0",
    "tk-extract-validated-transactional-commands-for-mut-466545",
    "tk-harden-api-operations-security-and-release-verif-5f790a",
    "tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c",
    "tk-harden-postmark-authentication-payload-limits-an-601f5f",
    "tk-implement-recoverable-linkding-delivery-state-ma-2ec860",
    "tk-inventory-architecture-and-refactor-domain-bound-dc9eb3",
    "tk-make-opml-import-export-atomic-validated-and-rou-df59b7",
    "tk-make-postmark-newsletter-ingestion-atomic-and-ra-e3d06e",
    "tk-reconcile-article-identity-and-concurrent-refres-7a1b44",
    "tk-split-external-gateways-from-pure-domain-service-5bf313",
    "tk-stabilize-mobile-today-and-article-rendering-5f1421",
    "tk-validate-all-browser-redirect-targets-99209e",
}
document_tasks = set(re.findall(r"\btk-[a-z0-9-]+", text))
assert len(WITAN_TASKS) == 26 and document_tasks == WITAN_TASKS
print(len(objectives), len(details), len(rows), len(WITAN_TASKS), "all mechanics valid")
PY
```
