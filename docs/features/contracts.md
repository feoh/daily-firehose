# Cross-feature behavioral contracts

This document defines repository-wide invariants that multiple features or adapters
must share. It complements the exhaustive [feature catalog](catalog.md), detailed
[current-suite test traceability matrix](test-traceability.md), and the
[current-state architecture](../architecture/current-state.md). These are contracts
of the current repository, including explicitly identified violations; they are not
a claim that every normative rule is presently satisfied.

## Status vocabulary and precedence

- **Conformant** means current executable evidence agrees with the normative contract.
- **Known violation** means a deterministic `expectedFailure`, architecture finding,
  or other cited evidence demonstrates that current behavior contradicts the rule.
- A feature-specific rule may narrow a cross-feature rule only where the invariant's
  scope says so. Domain policy and persistence invariants take precedence over
  adapter convenience; authentication precedes matched-route input handling; URL
  resolution and middleware remain outside adapter decorators unless stated.
- Stable invariant IDs are never reused. Retire an ID with a replacement pointer
  rather than changing its meaning.

## Data invariants

### DATA-INV-001 — Article identity is stable within a Feed

- **Normative current contract:** an Article belongs to exactly one Feed and is unique
  independently by `(feed, guid)` and `(feed, url)`; refresh must reconcile an
  already-known logical item without creating a duplicate.
- **Scope / precedence:** database uniqueness applies to every writer. Refresh's
  reconciliation policy must satisfy both constraints; database integrity wins over
  adapter success reporting.
- **Executable evidence:** SQLite coverage proves stable-URL/new-GUID reconciliation
  preserves the original row, first-seen timestamp, read/save/newsletter associations,
  and rejects both direct and order-dependent split GUID/URL evidence without merging.
  PostgreSQL separate-connection tests prove same-GUID and stable-URL/new-GUID races
  each produce one Article through the actual per-Feed reconciliation lock.
- **Known violation status:** **Conformant** — under the per-Feed lock, the complete
  document resolves against one immutable pre-write identity snapshot before a
  deterministic write plan runs; both durable uniqueness constraints remain guards.
- **Source / feature IDs:** `feeds/models.py`, `feeds/services.py`; ING-005, ING-006.

### DATA-INV-002 — First-seen and publication time have distinct meanings

- **Normative current contract:** `fetched_at` is immutable first-seen time and owns
  Today/week/month membership, digest ordering, and bulk cutoff eligibility;
  `published_at` is source metadata and owns Feed-detail model ordering.
- **Scope / precedence:** period-based session, legacy digest, and bearer list surfaces
  use first-seen windows. Feed detail has no date window and is the explicit
  publication-order exception.
- **Executable evidence:** `test_behavioral_contracts.py` pins repeated-refresh
  timestamp preservation and exact windows; `test_article_state_propagation.py` pins
  opposing fetched/publication ordering.
- **Known violation status:** **Conformant** for sequential refresh and reading paths;
  concurrent refresh is outside this evidence.
- **Source / feature IDs:** `feeds/models.py`, `feeds/services.py`, `feeds/views.py`;
  WEB-003, WEB-006, WEB-011, ING-005.

### DATA-INV-003 — Personal article state is user-isolated

- **Normative current contract:** Feeds and Articles are global, while explicit read
  state, bulk markers, saves, and preferences belong to one user and must not alter
  another user's queues or API flags.
- **Scope / precedence:** applies to session, legacy JSON, bearer, signed actions, and
  saved/archive views. Global Feed/Category mutation authority is a separate unknown
  authorization decision.
- **Executable evidence:** `test_article_state_propagation.py` cross-user matrices and
  `test_digest_views.py::test_visibility_state_is_scoped_to_the_authenticated_user`.
- **Known violation status:** **Conformant** for tested state paths.
- **Source / feature IDs:** `feeds/models.py`, `feeds/views.py`, `feeds/api.py`;
  WEB-004–WEB-011, SAVE-001, API-007–API-010, OPS-015.

### DATA-INV-004 — Deletion follows explicit lifecycle policy

- **Normative current contract:** deleting a Feed through ORM cascades its Articles
  and therefore their NewsletterIssues, SavedArticles, and read states; deleting an
  Article likewise cascades its saves. Category deletion sets Feed and SavedArticle
  category references null. Bearer `DELETE /api/v1/feeds/<id>/` instead
  soft-deactivates the Feed and preserves its Articles and saves.
- **Scope / precedence:** adapter DELETE semantics override generic model deletion only
  for the bearer Feed resource. The optional SavedArticle Feed snapshot FK is
  `SET_NULL`, but it does not preserve a save whose required Article was cascade
  deleted.
- **Executable evidence:** `test_behavioral_contracts.py::test_api_soft_delete_preserves_content_and_orm_deletes_cascade`
  covers soft deletion, Category `SET_NULL`, Feed cascade, and direct Article cascade.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/models.py`, `feeds/api.py`; ING-002, API-012,
  SAVE-001.

## Read invariants

### READ-INV-001 — Calendar windows are inclusive UTC-local dates

- **Normative current contract:** Today is the current UTC local date; week is Monday
  through Sunday; month is the complete calendar month, including leap day and
  December/year rollover endpoints.
- **Scope / precedence:** session Today/week/month, bearer named periods, signed
  period marking, and default bulk windows share these bounds. Explicit ordered API
  dates replace the named bounds.
- **Executable evidence:** `test_behavioral_contracts.py` exact Monday/Sunday,
  leap-day, month-end, and year-boundary tests; `test_digest_views.py` UTC-midnight
  test.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/views.py`, `feeds/api.py`; WEB-002, WEB-003,
  API-007, API-010, API-018.

### READ-INV-002 — Normal queues exclude effective-read and locally saved Articles

- **Normative current contract:** Today/week/month, legacy digest, briefing, and the
  default bearer collection contain only Articles in their first-seen window that are
  neither effectively read nor locally saved. Feed detail has no first-seen date
  window but shares the read/save exclusion. Archive and Saved views have separate
  inclusion rules.
- **Scope / precedence:** first-seen windowing applies only to period surfaces; shared
  visibility filtering also applies to Feed detail. Explicit bearer include flags are
  the supported collection exception.
- **Executable evidence:** `test_digest_views.py` and
  `test_article_state_propagation.py` cross-surface visibility tests, plus
  `test_query_bounds.py` proving the SQL visibility filter obeys every read-state rule.
- **Known violation status:** **Conformant**. The exclusion is now applied in SQL rather
  than in Python after the rows were fetched, which is what makes READ-INV-007's bound
  safe.
- **Source / feature IDs:** `feeds/queries.py`, `feeds/views.py`, `feeds/api.py`;
  WEB-002–WEB-006, WEB-011, API-001, API-006–API-007.

### READ-INV-007 — A reading window is bounded and says when it bounded

- **Normative current contract:** every period surface, Feed detail, the legacy digest
  JSON, and the default bearer collection fetch at most `DIGEST_ARTICLE_LIMIT` (Feed
  detail: `FEED_ARTICLE_LIMIT`) rows, and the bound is applied *after* the read/save
  exclusion so it counts articles the reader can still see. A surface that reached its
  bound reports it: the HTML views render an explicit notice and the JSON surfaces carry
  `has_more` and `limit`. Resolving read state costs a fixed number of queries
  regardless of how many articles or historical markers exist.
- **Scope / precedence:** Archive and Saved keep their own separate 50-row bounds. The
  bearer `include_read`/`include_saved` flags change which rows are eligible, and the
  bound then applies to that eligible set.
- **Executable evidence:** `test_query_bounds.py` limit-counts-visible-rows,
  exact-`has_more`-boundary, bounded-surface notice, fixed-query-cost, and bearer
  include-flag tests; `test_read_state_queries.py` marker-narrowing and cost tests.
- **Known violation status:** **Conformant.** The limit-counts-visible-rows and Feed
  detail tests both fail against the previous ordering, which sliced before excluding
  and so rendered nothing at all when the first page of a window was entirely read.
- **Source / feature IDs:** `feeds/queries.py`, `feeds/views.py`, `feeds/api.py`;
  WEB-002–WEB-006, API-001, API-006–API-007.

### READ-INV-003 — Explicit unread is the final read-precedence override

- **Normative current contract:** effective read is explicit true plus eligible bulk
  markers, minus explicit false. A bulk action overwrites an existing explicit-unread
  row to true; a later individual unread overrides the durable marker.
- **Scope / precedence:** applies to session and bearer individual/bulk operations and
  all read projections.
- **Executable evidence:** `test_digest_views.py` bulk-overwrite coverage and
  `test_article_state_propagation.py` later-unread and adapter-parity coverage.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/views.py`, `feeds/api.py`; WEB-008–WEB-011,
  API-008, API-010, API-013.

### READ-INV-004 — Bulk cutoff includes equality, not later arrivals

- **Normative current contract:** a bulk marker affects only matching Articles with
  `fetched_at <= marked_read_at`; exact equality is eligible and any later first-seen
  time remains unread until another action.
- **Scope / precedence:** Feed and period markers in effective-read calculation and
  materialization share the same cutoff.
- **Executable evidence:** `test_behavioral_contracts.py::test_bulk_cutoff_includes_exact_equality_and_excludes_later_fetches`
  and `test_digest_views.py::test_bulk_read_marker_does_not_hide_articles_fetched_later`.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/views.py`, `feeds/api.py`; WEB-009–WEB-011,
  API-010, API-013.

### READ-INV-005 — Repeating a logical bulk action advances one marker

- **Normative current contract:** repeating the same user/scope/feed-or-date action
  upserts its logical marker and replaces `marked_read_at`, extending the cutoff;
  current matching explicit states are materialized again.
- **Scope / precedence:** session, bearer, and signed adapters call one command in
  `feeds/commands.py`, so they share both logical behavior and transaction boundary.
- **Executable evidence:** `test_digest_views.py::test_mark_period_read_updates_existing_marker_timestamp`,
  state-propagation Feed-marker tests, and `test_mutation_commands.py` adapter-agreement
  tests that drive all three period adapters and both Feed adapters into one marker row.
- **Known violation status:** **Conformant** for sequential valid markers.
- **Source / feature IDs:** `feeds/views.py`, `feeds/api.py`, `feeds/models.py`;
  WEB-009–WEB-011, API-010, API-013, API-018.

### READ-INV-006 — Marker shape, uniqueness, and write atomicity are durable

- **Normative current contract:** Feed markers have a Feed and no dates; period markers
  have ordered dates and no Feed; each logical marker is unique. Explicit-state
  materialization and marker upsert should commit or roll back together.
- **Scope / precedence:** database shape applies to all writers. Every adapter now
  marks read through `feeds/commands.py`, which validates the marker shape and wraps
  materialization and marker upsert in one transaction.
- **Executable evidence:** four SQLite model/direct-write tests require validation and
  database rejection; PostgreSQL coverage tries all invalid shapes, introspects the
  named constraints, and races duplicate period and Feed markers from separate
  connections. Migration-audit coverage proves invalid or duplicate preexisting rows
  stop the migration without modifying user state. Fault injection covers the rollback
  envelope of the bearer adapter and of both session bulk commands, and is not labeled
  concurrency.
- **Known violation status:** **Conformant.** Session bulk atomicity was the outstanding
  violation; `test_mutation_commands.py` fails against the previous session code, which
  left articles materialized read with no marker when the marker write failed. Marker
  shape and logical uniqueness are conformant at model/database boundaries on SQLite
  and PostgreSQL 17, including concurrent duplicate inserts.
- **Source / feature IDs:** `feeds/models.py`, `feeds/commands.py`, `feeds/views.py`,
  `feeds/api.py`; WEB-009, WEB-010, WEB-019, API-010, API-013.

## Save invariants

### SAVE-INV-001 — Re-save is a timestamp-preserving local upsert

- **Normative current contract:** one SavedArticle exists per user/Article. Re-save
  keeps its primary key and original `saved_at`, refreshes Article/Feed/Category
  snapshots, updates `updated_at`, and retries Linkding.
- **Scope / precedence:** browser, bearer, and signed saves call the domain service;
  direct ORM/admin writes are outside service orchestration.
- **Executable evidence:** the sequential service contract remains in
  `test_behavioral_contracts.py`; PostgreSQL separate-connection coverage proves the
  durable `(user, Article)` constraint permits one concurrent insert and rejects the
  other with a real database `IntegrityError`.
- **Known violation status:** **Conformant** for sequential saves and database-level
  concurrent uniqueness; full command retry/response semantics remain unproved.
- **Source / feature IDs:** `feeds/models.py`, `feeds/services.py`; SAVE-001–SAVE-003,
  API-009, API-018.

### SAVE-INV-002 — Read and save states are independent

- **Normative current contract:** a user may have read and saved state simultaneously;
  either hides an Article from normal queues. Unsave removes only local saved state
  and preserves effective read state.
- **Scope / precedence:** every projection and mutation adapter must report both flags
  from their independent stores.
- **Executable evidence:** `test_article_state_propagation.py` combined transitions and
  `test_known_correctness_failures.py::test_unsave_preserves_true_read_state_in_response`.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/models.py`, `feeds/views.py`, `feeds/api.py`;
  WEB-011, SAVE-001, SAVE-004, API-008–API-009.

### SAVE-INV-003 — Local save survives Linkding failure

- **Normative current contract:** local state and its owed delivery commit together,
  before synchronous Linkding I/O. A canonically equal returned URL confirms success;
  missing credentials, request/HTTP/JSON errors, or URL mismatch persist
  `linkding_saved=false` plus an error. Unsave is local only.
- **Scope / precedence:** local visibility follows the local row regardless of remote
  success; adapters must not treat remote failure as no local save.
- **Executable evidence:** `test_article_actions.py` exact payload/URL tests,
  `test_article_state_propagation.py` local-failure visibility tests, and
  `test_linkding_delivery.py` survival of the local row and its delivery.
- **Known violation status:** **Conformant.** Timeout-after-remote-success is no longer
  an ambiguity; SAVE-INV-006 now owns it.
- **Source / feature IDs:** `feeds/services.py`; SAVE-002–SAVE-004, API-009.

### SAVE-INV-005 — Remote delivery is a bounded, recoverable state machine

- **Normative current contract:** a `LinkdingDelivery` records each save's remote
  progress as `queued`, `succeeded`, `transient_failed`, or `permanent_failed`, with
  attempts, timestamps, a classified error, and the remote bookmark id. Timeouts,
  connection errors, 429, 401/403, 5xx, and missing credentials are transient and are
  retried with exponential backoff until `LINKDING_MAX_DELIVERY_ATTEMPTS`; other 4xx
  and a URL mismatch are permanent and never retried. The save request makes the first
  attempt, and the refresh worker or `deliver_saved_articles` drains what remains owed.
  Database check constraints reject any state whose timestamps or error class disagree
  with it. `SavedArticle.linkding_saved` and `linkding_error` are projections of the
  delivery, so every existing adapter reads unchanged fields.
- **Scope / precedence:** deleting a local save cancels its owed delivery, because the
  delivery exists only to serve a live save intent. Re-saving is a fresh intent and
  restarts the retry budget, which is also how a permanent failure is retried
  deliberately. Already-delivered remote bookmarks are never deleted.
- **Executable evidence:** `test_linkding_delivery.py` state, classification, backoff,
  bounded-budget, claim-race, cancel-on-unsave, requeue, and constraint tests.
- **Known violation status:** **Conformant** at service, model, and command
  boundaries. A drain race is proved by compare-and-set on a single connection only;
  separate-connection PostgreSQL coverage belongs with the other concurrency work.
- **Source / feature IDs:** `feeds/models.py`, `feeds/services.py`,
  `feeds/management/commands/deliver_saved_articles.py`; SAVE-002–SAVE-004, API-009.

### SAVE-INV-006 — A retry reconciles by canonical URL before creating

- **Normative current contract:** an attempt after the first looks the delivery URL up
  through Linkding's bookmark check endpoint and adopts an existing bookmark instead of
  creating a second one, which is what repairs a create whose response was lost. URLs
  are compared canonically: scheme and host case and a bare-versus-slash root do not
  read as different bookmarks, while path and query differences do. The lookup is
  total — an unavailable endpoint, transport error, unreadable body, or a bookmark for
  another URL all mean "no match" and fall back to creating.
- **Scope / precedence:** a first attempt never performs the lookup, because no earlier
  attempt could have created anything. Reconciliation never widens what is sent to
  Linkding: the bookmark still carries the article's exact URL.
- **Executable evidence:** `test_linkding_delivery.py` timeout-after-create adoption,
  adopt-rather-than-overwrite, first-attempt-skips-lookup, lookup degradation matrix, and
  canonical matching tests, all against fixtures carrying the live response envelope.
- **Known violation status:** **Conformant.** The remote contract was verified against
  the live instance on 2026-08-17: `GET /api/bookmarks/check/?url=` answers `200` with
  a `bookmark` object or `null` alongside `metadata`/`auto_tags`, matching the modeled
  envelope; it treats a trailing-slash and host-case difference as the same bookmark and
  an added fragment as a different one; and a missing or bad token answers `401`. The
  probe also established that `POST /api/bookmarks/` upserts by URL, so a retry could
  not have duplicated a bookmark after all — but it would have overwritten one the
  reader had since edited, which is why adopting remains the correct retry behavior.
  Every unexpected response still degrades to creating; that fallback is now a
  belt-and-braces guard rather than the load-bearing assumption.
- **Source / feature IDs:** `feeds/services.py`; SAVE-002, SAVE-003, API-009.

### SAVE-INV-004 — Newsletter save denial is domain-owned

- **Normative current contract:** a persisted NewsletterIssue makes its Article
  unsaveable through browser, bearer, signed, domain-service, and admin-form paths;
  rejection performs no local or Linkding write.
- **Scope / precedence:** a fresh persisted capability check overrides stale prefetched
  state and adapter affordances. Direct ORM writes remain outside enforcement.
- **Executable evidence:** `test_newsletter_save_policy.py` adapter/domain/admin matrix.
- **Known violation status:** **Conformant** at covered boundaries.
- **Source / feature IDs:** `feeds/services.py`, `feeds/admin.py`; AUTH-005, NEWS-005,
  SAVE-001–SAVE-003, API-005, API-009, API-018.

## Newsletter invariants

### NEWS-INV-001 — MessageID replay is idempotent

- **Normative current contract:** `MessageID`/`MessageId` identifies a NewsletterIssue;
  sequential or concurrent replay returns the existing issue without another Article
  and without surfacing a database conflict to either caller.
- **Scope / precedence:** Postmark adapter status mirrors the domain result; database
  uniqueness is the final identity boundary.
- **Executable evidence:** `test_newsletters.py` proves sequential replay. The
  PostgreSQL barrier waits until both real MessageID lookups miss, then proves both
  concurrent service callers return the same committed issue without errors.
- **Known violation status:** **Conformant**, including concurrent replay.
- **Source / feature IDs:** `feeds/services.py`, `feeds/api.py`, `feeds/models.py`;
  NEWS-001, API-017.

### NEWS-INV-002 — Newsletter creation is all-or-nothing

- **Normative current contract:** new synthetic Feed (if needed), Article, and
  NewsletterIssue should commit as one idempotent unit, with no orphan on failure.
- **Scope / precedence:** applies to domain service and webhook retries, including
  failures after Article insertion.
- **Executable evidence:** the ordinary SQLite regression and a PostgreSQL fault
  placed after Article creation require the Article and any newly-created synthetic
  Feed to roll back. This fault test is transaction-boundary evidence, not concurrency.
- **Known violation status:** **Conformant** at the covered transaction boundary.
- **Source / feature IDs:** `feeds/services.py`; NEWS-002, API-017.

### NEWS-INV-003 — Synthetic Feed activity is creation-sensitive

- **Normative current contract:** creating the synthetic “Email Newsletters” Feed sets
  it inactive so refresh ignores it; reusing an existing synthetic Feed preserves its
  active state. Newsletter Articles remain visible by first-seen window regardless.
- **Scope / precedence:** `get_or_create` defaults apply only on creation; ingestion
  must not silently rewrite an existing operator state.
- **Executable evidence:** two synthetic-Feed tests in `test_behavioral_contracts.py`,
  Today rendering in `test_newsletters.py`, and a PostgreSQL separate-connection test
  proving concurrent `newsletter_feed()` calls return one inactive Feed.
- **Known violation status:** **Conformant**, including concurrent Feed initialization.
- **Source / feature IDs:** `feeds/services.py`, `feeds/views.py`; NEWS-001, WEB-002,
  ING-007.

### NEWS-INV-004 — Public newsletter GET is display-only

- **Normative current contract:** anonymous UUID detail GET renders sanitized public
  archive content with noindex and creates no read, save, preference, or open-event
  state. “Open” is navigation, not analytics.
- **Scope / precedence:** authenticated rendering may lazily create preferences and
  show current read state, but GET still does not mark read or save.
- **Executable evidence:** `test_behavioral_contracts.py::test_public_newsletter_get_creates_no_read_save_preference_or_open_state`
  and sanitization tests in `test_newsletters.py`.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/views.py`, `feeds/services.py`; NEWS-003–NEWS-005.

### NEWS-INV-005 — Postmark accepts a deliberately minimal current schema

- **Normative current contract:** after POST and secret checks, only a truthy
  `MessageID`/`MessageId` is required. Other fields are optional/string-coerced;
  subject defaults, invalid/missing date becomes now, and model `full_clean()` is not
  called. Adapter 422 mapping is not proof of payload/model validation.
- **Scope / precedence:** strict JSON-object parsing still applies before the service;
  this invariant describes service semantics after parsing.
- **Executable evidence:** `test_behavioral_contracts.py::test_postmark_persists_invalid_emails_without_model_clean`
  proves invalid email strings persist successfully and that calling `full_clean()` on
  the stored issue then raises `ValidationError`; mocked adapter-error mappings remain
  in `test_api_validation.py`.
- **Known violation status:** **Conformant current fact**; this records permissiveness,
  not a recommendation.
- **Source / feature IDs:** `feeds/services.py`, `feeds/api.py`; NEWS-001, API-004,
  API-017.

## Feed and OPML invariants

### FEED-INV-001 — Only active, retry-eligible Feeds refresh

- **Normative current contract:** refresh enumerates active Feeds only. A future
  `next_retry_at` produces a skipped result without attempt changes; equality is
  eligible and each Feed is evaluated when reached.
- **Scope / precedence:** worker, browser, and bearer refresh adapters share the same
  four terminal states. A superseded result was attempted, remains a checked result row,
  and is neither failed nor skipped.
- **Executable evidence:** sequential eligibility remains covered by the existing
  suites. PostgreSQL tests prove only the newest overlapping failure persists terminal
  state; staged events prove both older failure/newer success and older success/newer
  failure orderings retain the newest terminal status and authoritative retry metadata.
  API, browser, and command adapter tests prove mixed and all-superseded reporting.
- **Known violation status:** **Conformant** for persisted refresh status: a monotonic
  per-Feed generation fences stale completion, whose result is explicitly
  `superseded`, while retaining per-Feed isolation.
- **Source / feature IDs:** `feeds/services.py`; ING-007–ING-009, API-016.

### FEED-INV-002 — Repeated same-GUID refresh updates in place

- **Normative current contract:** refreshing the same `(feed, guid)` returns one
  Article, reports `updated` rather than `created`, preserves original `fetched_at`,
  and refreshes metadata/`updated_at`.
- **Scope / precedence:** GUID and URL are independent evidence; if they identify two
  different stored rows, reconciliation fails safely instead of choosing or deleting.
- **Executable evidence:** sequential tests preserve first-seen/associations and reject
  split evidence. The PostgreSQL same-GUID race proves the owning attempt creates once,
  the superseded attempt writes zero Articles, and one Article remains; the
  stable-URL/new-GUID race proves one reconciled Article remains.
- **Known violation status:** **Conformant** for sequential and concurrent refresh.
- **Source / feature IDs:** `feeds/services.py`, `feeds/models.py`; DATA-INV-001,
  DATA-INV-002, ING-005.

### FEED-INV-003 — Feed soft-delete preserves content

- **Normative current contract:** bearer Feed DELETE sets `is_active=false`, excludes
  future refresh, and preserves its Articles for historical/read surfaces.
- **Scope / precedence:** applies only to bearer resource DELETE; ORM deletion still
  cascades under DATA-INV-004.
- **Executable evidence:** `test_behavioral_contracts.py::test_api_soft_delete_preserves_content_and_orm_deletes_cascade`.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/api.py`, `feeds/services.py`; API-012, ING-002,
  ING-007.

### FEED-INV-004 — OPML import upserts by URL and reactivates

- **Normative current contract:** import updates one Feed selected by exact `feed_url`,
  refreshes title/site/category, and sets `is_active=true`; repeated import does not
  duplicate the Feed.
- **Scope / precedence:** imported values replace stored values, including category.
  Within one document, an identical repeated URL is counted as skipped; a conflicting
  repeated URL rejects the whole import. The complete document is parsed and validated
  before an atomic write transaction, so any later invalid entry or write failure
  preserves all existing rows.
- **Executable evidence:** sequential update/reactivation and duplicate policy are
  covered by `test_opml.py`; rollback faults cover the transaction boundary; a
  PostgreSQL separate-connection/barrier test proves two concurrent Feed upserts
  return one create and one update.
- **Known violation status:** **Conformant**, including atomic import and concurrent
  Feed URL upsert.
- **Source / feature IDs:** `feeds/services.py`; ING-011.

### FEED-INV-005 — OPML category round trip preserves classification

- **Normative current contract:** export followed by import should preserve active
  Feed title, source URL, site URL, and Category classification.
- **Scope / precedence:** applies to the repository's own export imported without
  external modification.
- **Executable evidence:**
  `test_behavioral_contracts.py::test_opml_export_import_round_trip_preserves_category`
  and `test_opml.py::OPMLExportTests` prove deterministic, escaped export and round trip.
- **Known violation status:** **Conformant** for active Feed title, source URL, site URL,
  and category. Description and inactive-state serialization remain explicitly outside
  this contract.
- **Source / feature IDs:** `feeds/services.py`; ING-011, ING-013.

### FEED-INV-006 — OPML category reuse respects unique name and slug

- **Normative current contract:** importing a parent category name that already exists
  must reuse it even when its slug differs from the newly generated slug, without a
  duplicate-name error.
- **Scope / precedence:** Category's unique name and unique slug both constrain import;
  existing exact name is the reusable identity for this case.
- **Executable evidence:** the sequential alternate-slug test and PostgreSQL barrier
  after two real name/slug misses both pass and return one Category.
- **Known violation status:** **Conformant** — lookup is name-first and creation retries
  safely after uniqueness races.
- **Source / feature IDs:** `feeds/services.py`, `feeds/models.py`; ING-002, ING-011.

## Route posture invariants

### ROUTE-INV-001 — Every route declares and enforces an auth, method, and isolation posture

- **Normative current contract:** every route in the URLconf is classified as session,
  bearer, signed, webhook, or deliberately public. A session route redirects an
  anonymous caller to the login page; a bearer route answers `401`; a signed route
  without a signature and a webhook without credentials are refused. No protected
  mutation writes anything for an anonymous caller. A route answers `405` to every
  method it does not implement, reading surfaces accept only safe methods, and every
  session mutation requires a CSRF token and commits nothing without one. A reader's
  read state, saves, archive, and preferences are visible and writable only to that
  reader.
- **Scope / precedence:** the classification table is driven from the URLconf, so a
  route added without a declared posture fails the inventory test rather than escaping
  the sweep. The legacy digest is the one deliberate exception to the method rule and
  keeps its API-COMPAT-INV-001 characterization; CSRF still guards its unsafe methods.
  Django admin owns its own redirect behavior and is out of scope here.
- **Executable evidence:** `test_route_contracts.py` inventory, anonymous-access,
  method, CSRF, and isolation sweeps.
- **Known violation status:** **Conformant.** Reading surfaces were the outstanding
  looseness — `today`, `week`, `month`, `archived`, `saved-links`, `feed-detail`,
  `opml-export`, and the public newsletter archive answered `200` to POST, PUT, PATCH,
  and DELETE. 73 subtests fail against the undecorated views.
- **Source / feature IDs:** `feeds/views.py`, `feeds/api.py`, `feeds/urls.py`,
  `daily_firehose/urls.py`; AUTH-001–AUTH-005, WEB-001–WEB-021, API-001–API-019.

## API authentication, input, schema, and capability invariants

### API-AUTH-INV-001 — Matched bearer routes enforce method before authentication

- **Normative current contract:** unsupported methods return JSON 405 before token
  processing; supported methods require authentication before query, body, schema,
  lookup, or mutation validation.
- **Scope / precedence:** applies after URL resolution. Postmark and signed endpoints
  follow analogous method-before-secret/signature ordering.
- **Executable evidence:** `test_behavioral_contracts.py::test_bearer_method_precedes_auth_and_auth_precedes_validation`
  and `test_api_validation.py` method matrices.
- **Known violation status:** **Conformant** on matched routes.
- **Source / feature IDs:** `feeds/api.py`; API-003, API-017, API-018.

### API-AUTH-INV-003 — Token capability is enforced by method after authentication

- **Normative current contract:** every token carries at least one explicit capability.
  Safe methods require `read` and state-changing methods require `write`. A token
  lacking the required capability returns `403 insufficient_capability`, after the 405
  method check and after authentication, so an anonymous caller still receives `401`.
- **Scope / precedence:** capabilities bound what a leaked token can do on a
  single-user deployment; they are not per-user authorization. Signed actions are
  governed by API-CAP-INV-002 instead, having no token.
- **Executable evidence:** `test_api_capabilities.py::TokenCapabilityTests` proves a
  read-only token reads but cannot mark, save, create, or refresh; a write-only token
  is refused reads; the precedence order holds; and a token cannot be created with an
  empty, unknown, or repeated capability. A migration test proves tokens created
  before this field kept `read,write`.
- **Known violation status:** **Conformant.**
- **Source / feature IDs:** `feeds/models.py`, `feeds/api.py`; API-002, API-003.

### API-AUTH-INV-002 — Token compatibility and use timestamp are stable

- **Normative current contract:** case-insensitive `Bearer` and compatibility `Token`
  schemes authenticate only an active token for an active user. Successful
  authentication updates `last_used_at` before endpoint validation, even if the
  endpoint later rejects input.
- **Scope / precedence:** `last_used_at` is authentication-attempt evidence, not
  successful-operation audit.
- **Executable evidence:** bearer compatibility/inactive-principal and timestamp tests
  in `test_behavioral_contracts.py`.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/api.py`, `feeds/models.py`; API-002, API-003.

### API-INPUT-INV-001 — Structured input is strict after authentication

- **Normative current contract:** nonempty structured bodies require UTF-8 JSON objects
  and JSON media types; duplicate keys, nonobjects, nonstandard constants, unknown
  fields, wrong primitive types, unknown/repeated queries, and semantic bodies on
  bodyless operations are rejected. Zero-field multipart is a compatibility exception.
- **Scope / precedence:** authentication timestamp may precede rejection; endpoint
  validation precedes domain writes.
- **Executable evidence:** comprehensive matrices in `test_api_validation.py`.
- **Known violation status:** **Conformant** on matched routes.
- **Source / feature IDs:** `feeds/api.py`, `feeds/api_validation.py`; API-004,
  API-007–API-018.

### API-SCHEMA-INV-001 — Shared semantic values are canonical

- **Normative current contract:** dates are canonical ISO and ordered; booleans are
  native JSON/lowercase query values; database IDs are positive signed 64-bit; URLs
  are credential-free HTTP(S); interest score is finite 0–5.
- **Scope / precedence:** applies to fields that reach shared validators. ORM/admin
  writes and path resolution are separate boundaries.
- **Executable evidence:** `test_api_validation.py` type/range/date/URL matrices and
  native-false regressions in `test_known_correctness_failures.py`.
- **Known violation status:** **Conformant** for reached validators.
- **Source / feature IDs:** `feeds/api_validation.py`; API-004, API-007–API-015.

### API-SCHEMA-INV-002 — Negative path IDs use API auth and JSON validation

- **Normative current contract:** API-shaped negative resource IDs should remain inside
  the API boundary: unsupported/missing auth precedence remains stable and an
  authenticated negative ID is a JSON validation error, not an HTML resolver page.
- **Scope / precedence:** bearer Article and Feed ID paths; this rule deliberately
  identifies the URL converter as part of the public API contract.
- **Executable evidence:** expected failure
  `test_behavioral_contracts.py::test_negative_path_ids_use_auth_and_json_validation_envelopes`
  sends valid JSON to read/save resources and a truly empty body to bodyless Feed GET
  and mark-read resources, isolating router/auth/ID-envelope behavior.
- **Known violation status:** **Known violation** — Django's unsigned `<int:…>` route
  fails before authentication and returns HTML 404.
- **Source / feature IDs:** `feeds/urls.py`, `feeds/api_validation.py`; API-003,
  API-004, API-008–API-009, API-012–API-013.

### API-CAP-INV-001 — Representations carry exact per-Article actions

- **Normative current contract:** every v1 Article has capabilities and concrete
  actions. Ordinary Articles advertise exact `/api/v1/articles/<id>/read/` and
  `/saved/` URLs; newsletter Articles deny save and omit its action. Briefing retains
  exact `{id}` templates only for compatibility.
- **Scope / precedence:** clients must honor per-record capability over generic
  briefing templates.
- **Executable evidence:** exact action/template test in `test_behavioral_contracts.py`
  and newsletter capability matrix in `test_newsletter_save_policy.py`.
- **Known violation status:** **Conformant**.
- **Source / feature IDs:** `feeds/api.py`, `feeds/services.py`; API-005, API-006,
  NEWS-005.

### API-CAP-INV-002 — Signed mutation capabilities are non-replayable unsafe-method actions

- **Normative current contract:** a signed mutation capability uses an unsafe method
  and is bounded by expiry and one-use state, with the signature binding purpose,
  target, deadline, and nonce together so previews and replay cannot repeat state
  changes.
- **Scope / precedence:** applies to signed save-and-go and period-read actions; this
  security rule takes precedence over convenience-link compatibility.
- **Executable evidence:** `test_api_capabilities.py::SignedActionSecurityTests` proves
  replay returns `409` without repeating the save or advancing the marker, expired and
  over-horizon deadlines are refused, every tampered field is refused, and `GET`
  returns `405` without writing.
- **Known violation status:** **Conformant.** Signed actions are single-use expiring
  POSTs; `AGENT_LINK_MAX_LIFETIME_SECONDS` bounds how far ahead one may be minted, so
  an expiry field cannot be used to mint a permanent capability.
- **Source / feature IDs:** `feeds/api.py`, `feeds/models.py`; API-018, API-019.

### API-COMPAT-INV-001 — Legacy digest remains behind session and CSRF middleware

- **Normative current contract:** the legacy digest uses session auth and no v1 error
  envelope. Its view is method/query/body agnostic, but CSRF middleware rejects unsafe
  requests without a valid token; safe and CSRF-valid unsafe methods reach JSON.
- **Scope / precedence:** middleware runs before the permissive view. This is a legacy
  characterization, not permission for v1 endpoints to loosen methods.
- **Executable evidence:** `test_behavioral_contracts.py::test_legacy_digest_is_method_agnostic_after_session_and_csrf_boundary`.
- **Known violation status:** **Conformant current fact**.
- **Source / feature IDs:** `feeds/views.py`, `daily_firehose/settings.py`; API-001,
  AUTH-003.

### API-COMPAT-INV-002 — Signed actions accept POST only and keep their check order

- **Normative current contract:** signed actions accept POST only, authenticate an HMAC
  binding purpose, target, expiry, and nonce, execute as one configured active user,
  and check method, then signature, then nonce shape and deadline, then query/body,
  then semantic input. The nonce is spent before the action runs.
- **Scope / precedence:** the previous GET form was removed outright rather than kept
  behind a compatibility window; a caller holding an old URL receives `405`.
- **Executable evidence:** signed happy-path and precedence tests in `test_api.py`,
  `test_api_validation.py`, and `test_api_capabilities.py`.
- **Known violation status:** **Conformant.**
- **Source / feature IDs:** `feeds/api.py`; API-018, API-019.

## Progressive enhancement, accessibility, and mobile invariants

### UI-INV-001 — Article mutations work without JavaScript

- **Normative current contract:** read/save controls are native CSRF POST forms; JS
  enhances only marked forms and server persistence remains authoritative.
- **Scope / precedence:** network/schema errors retain the card and native redirect
  behavior is the fallback.
- **Executable evidence:** `test_article_actions_browser.py` executes AJAX DOM behavior
  in Chromium and submits the real read/save forms against Django with JavaScript
  disabled; request and live-server Playwright suites cover persistence and removal.
- **Known violation status:** **Conformant** for covered paths.
- **Source / feature IDs:** `templates/feeds/includes/article_card.html`,
  `static/js/article-actions.js`; WEB-008, WEB-012, SAVE-003.

### UI-INV-002 — Accessibility baseline is shared across responsive surfaces

- **Normative current contract:** base-derived pages provide skip-to-main, focusable
  main, labeled navigation/cards/dialog, semantic headings, native controls, visible
  focus, and live status/error regions.
- **Scope / precedence:** shared template semantics apply across desktop/mobile; each
  specialized surface may add labels but must not remove the baseline.
- **Executable evidence:** template assertions, Today Playwright geometry/action
  tests, executed Chromium DOM tests, and live-Django Chromium matrices at 375, 768,
  1280 and 400%-equivalent reflow. Enumerated dependency-free checks cover shared
  landmarks, explicit and associated names/labels, headings, duplicate IDs, image
  alternatives, target size, overflow, exact polite clipboard feedback, editable
  suppression, and help focus restoration. Synthetic regressions prove select option
  text and nav/dialog descendants cannot stand in for accessible names, and main must
  explicitly declare `tabindex="-1"`.
- **Known violation status:** **Conformant for the enumerated automated checks**;
  comprehensive automated rule scanning, manual screen-reader, automated contrast,
  help focus-trap/inert, and removal-focus proof remain open.
- **Source / feature IDs:** `templates/base.html`, `static/css/site.css`,
  `static/js/article-actions.js`; WEB-001, WEB-007, WEB-012–WEB-014, WEB-021.

### UI-INV-003 — Mobile and desktop share Article identity and state

- **Normative current contract:** server data does not fork by User-Agent; Today card
  IDs match desktop, 390×844, 320×844, legacy JSON, and reload. Shared/auth pages
  reflow without horizontal overflow at 375, 768, dedicated 1280 and 400%-equivalent
  320 CSS pixels; compact/focus modes preserve discoverable controls. A mobile mutation
  removes only its target and persistence survives reload.
- **Scope / precedence:** responsive CSS changes presentation only, never query/state
  semantics.
- **Executable evidence:** `test_mobile_today_browser.py`,
  `test_responsive_accessibility_browser.py`, and Today User-Agent parity in
  `test_digest_views.py`.
- **Known violation status:** **Conformant** for covered Chromium pages/modes; physical
  devices, landscape, non-Chromium engines and unusual content remain manual.
- **Source / feature IDs:** `feeds/views.py`, `static/css/site.css`; WEB-002, WEB-020,
  API-001.

### UI-INV-004 — Authenticated GET responses are private and non-storable

- **Normative current contract:** every authenticated session, legacy JSON, and bearer
  GET response should emit at least `Cache-Control: private, no-store`, including
  authenticated newsletter rendering and authenticated OPML export, so caches cannot
  reuse state or authenticated content across users.
- **Scope / precedence:** anonymous public newsletter detail is explicitly excluded;
  authenticated-response safety overrides whether a particular payload (such as OPML)
  is global rather than personalized.
- **Executable evidence:** expected failure
  `test_behavioral_contracts.py::test_all_authenticated_get_responses_are_private_no_store`
  first asserts every route returns 200 before checking headers. The public newsletter
  test separately proves anonymous rendering is outside this policy; Today's narrower
  passing assertion remains in `test_digest_views.py`.
- **Known violation status:** **Known violation** — Today is protected, but the other
  characterized authenticated surfaces lack the contract.
- **Source / feature IDs:** `feeds/views.py`, `feeds/api.py`; WEB-002–WEB-006,
  WEB-016, ING-013, API-001, API-003, API-006–API-007.

### UI-INV-005 — Browser redirect inputs are validated for their trust boundary

- **Normative current contract:** login/logout and all five session mutation handlers
  accept only unambiguous same-origin `next` destinations or replace them with their
  documented local fallback. External/scheme-relative hosts, credentials, backslashes,
  controls, malformed escapes, and recursively encoded bypasses are rejected. The
  signed save-and-go route separately permits its intentional stored Article URL only
  when it is a credential-free absolute HTTP(S) destination; unsafe stored values fall
  back to Today.
- **Scope / precedence:** request host and trusted proxy scheme define same-origin.
  Successful authentication, logout, or mutation never authorizes caller-controlled
  outbound navigation. API-018's validated Article URL is domain data, not a caller
  return target; JSON API response contracts are outside this redirect policy.
- **Executable evidence:** `test_browser_redirects.py` covers login, logout, all five
  mutation handlers, relative success, bypass matrices, signed outbound navigation,
  proxy host/scheme context, and live Chromium auth/session behavior. The former
  expected failure
  `test_known_correctness_failures.py::test_mark_article_rejects_external_next_redirect`
  now passes through the shared resolver.
- **Known violation status:** **Conformant** for inventoried first-party redirect inputs.
- **Source / feature IDs:** `daily_firehose/redirects.py`,
  `daily_firehose/auth_views.py`, `feeds/views.py`, `feeds/api.py`; AUTH-001–AUTH-002,
  WEB-018, WEB-008–WEB-010, SAVE-003, ING-008, API-018.

## Observability and recovery invariants

### OPS-INV-001 — Refresh outcomes are safe and diagnosable

- **Normative current contract:** every attempted Feed produces a bounded safe result
  and completion log with identity, status, duration, write counts or error code,
  failure count, and retry time; unexpected exceptions include a traceback without
  exposing it to user/API payloads.
- **Scope / precedence:** worker, browser, and bearer summaries derive from the same
  results; skipped and superseded Feeds remain distinguishable from failures.
  Superseded is attempted but does not increment failed or trigger failure exit status.
- **Executable evidence:** service logging/sanitization, four-state browser/API/command
  result tests, and PostgreSQL locking/fencing tests. Strict generation fencing lets
  only the newest attempt persist terminal state and prevents stale failure replacing
  newer success.
- **Known violation status:** **Conformant** for persisted outcome ownership and
  diagnostics. Metrics, correlation, retention, and alerting remain absent, and process
  exit signaling is OPS-INV-005.
- **Source / feature IDs:** `feeds/services.py`, browser/API refresh adapters;
  ING-007–ING-009, API-016, OPS-011.

### OPS-INV-002 — Running status proves semantic web and worker health

- **Normative current contract:** health/readiness should prove HTTP handling, database
  access, migration readiness, and recent successful worker progress rather than only
  process/TCP existence.
- **Scope / precedence:** Compose health and operator verification must detect a stale
  or hung refresh worker.
- **Executable evidence:** `feeds/tests/test_health.py` proves liveness without a
  database, readiness over real database access and applied migrations, and stale/hung
  worker detection from heartbeat age, last successful cycle, and consecutive failures;
  `feeds/tests/test_refresh_worker.py::WorkerHealthCommandTests` proves the worker's
  container health command exits nonzero without recent successful progress; the
  Compose probes are asserted in
  `feeds/tests/test_production_settings.py::ComposeConfigurationTests::test_probes_and_restart_policies_are_explicit`.
- **Known violation status:** **Conforms in the current suite** — semantic web
  readiness and worker staleness are executed. Live observation of the probes against
  a running production stack remains unautomated.
- **Source / feature IDs:** `feeds/health.py`, `feeds/jobs.py`, `docker-compose.yml`,
  `docs/incidents/2026-08-11-mobile-today-empty.md`; OPS-008, OPS-014.

### OPS-INV-003 — Deployment fails before application restart on unsafe state

- **Normative current contract:** canonical deployment uses the documented checkout,
  preserves secrets/volume, starts DB, builds and runs deploy checks, proves a real DB
  connection, then rebuilds/recreates application services. Failed preflight stops.
- **Scope / precedence:** this is the normal production deployment path; it does not
  waive migration-specific backup/reversal needs.
- **Executable evidence:** production settings/deploy checks and operator commands in
  `AGENTS.md` and `README.md`; the required PostgreSQL 17 lane proves all disk leaf
  migrations are applied and key durable unique constraints exist in its catalog.
- **Known violation status:** **Conformant as documented procedure** with forward
  migration integration evidence; production execution and rollback remain manual.
- **Source / feature IDs:** `AGENTS.md`, `README.md`; OPS-009, OPS-010, OPS-013.

### OPS-INV-004 — Durable data has a verified recovery path

- **Normative current contract:** production data needs scheduled backup creation,
  retention, off-host protection, integrity verification, defined RPO/RTO, and a
  practiced restore path before destructive migrations or host loss.
- **Scope / precedence:** a Docker named volume is persistence, not a backup; code
  rollback does not reverse schema/data changes.
- **Executable evidence:** repository scripts and explicit fake-transport/receiver
  cases cover exact local Compose dump/list operations, anonymous temporary files,
  pinned restricted SSH push/read commands with no deletion operation, atomic receiver
  writes, receipt-controlled local-only maintenance that preserves immutable
  earliest-received tier points, a shared advisory lock, an exact effective ZFS mount,
  quota/size bounds, path/symlink and overwrite defenses, complete metadata, and
  isolated restore failure/evidence/cleanup checks. The runbook records the
  owner-approved direct application-host-to-TrueNAS architecture and activation gates.
- **Known violation status:** **Partially mitigated in production** — at revision
  `45c97cc`, the restricted TrueNAS account/datasets/receiver/key and local maintenance
  were installed; one 15,173,740-byte receipt-backed production pair was independently
  confirmed; and an isolated PostgreSQL 17/application restore passed in 15.384 seconds
  with exact cleanup. Administrator rekey/read/restore and post-rekey backup evidence,
  owner-verified Fastmail monitor transitions, an hourly TrueNAS age monitor, and the
  enabled 00:00/12:00 UTC timer now exist. Continuing scheduled RPO evidence,
  independent off-site confirmation, and full incident RPO/RTO remain open.
- **Source / feature IDs:** `docker-compose.yml`, `README.md`,
  `docs/operations/postgresql-backups.md`, `scripts/postgres_backup.py`,
  `scripts/postgres_backup_receiver.py`, `scripts/postgres_restore_verify.py`,
  `tests/postgres_backup_script_cases.py`;
  OPS-002, OPS-013, OPS-014.

### OPS-INV-005 — Failed refresh commands signal failure to supervision

- **Normative current contract:** a management-command refresh cycle with one or more
  failed Feed results exits nonzero after reporting all per-Feed outcomes, allowing
  shell/container supervision to distinguish degradation from success.
- **Scope / precedence:** backoff skips are not attempted; superseded results are
  attempted but are not failures. Neither state alone triggers nonzero exit. Failure
  isolation and complete diagnostics are retained before the nonzero exit.
- **Executable evidence:** `test_feed_refresh.py::RefreshCommandTests::test_command_reports_all_four_states_and_fails_only_for_failure`
  proves mixed four-state output, all-superseded success exit, and failure-only
  `CommandError` behavior.
- **Known violation status:** **Conformant** — actual failed results produce nonzero
  status after the complete summary; skipped and superseded-only cycles do not.
- **Source / feature IDs:** `feeds/management/commands/refresh_feeds.py`; ING-009,
  ING-010, OPS-011.

## Traceability matrix

This post-snapshot companion maps the **current suite: 30 test modules, 452 test
methods, and 2 expected failures**. The pinned catalog retains its independent
15/191/8 snapshot counts. Exact `module::class::method` identities, evidence levels,
and dimension-specific gaps are maintained in the [detailed matrix](test-traceability.md).

| Invariant group | Stable invariant IDs | Primary executable evidence | Feature catalog IDs |
| --- | --- | --- | --- |
| Data | DATA-INV-001–004 | `test_feed_refresh.py`, `test_article_state_propagation.py`, `test_behavioral_contracts.py`, `test_postgresql_integration.py` | ING-002, ING-005–006, WEB-004–011, API-012, SAVE-001 |
| Read | READ-INV-001–006 | `test_digest_views.py`, `test_article_state_propagation.py`, `test_api_validation.py`, `test_known_correctness_failures.py`, `test_behavioral_contracts.py`, `test_postgresql_integration.py` | WEB-002–011, WEB-019, API-007–010, API-013, API-018 |
| Save | SAVE-INV-001–004 | `test_article_actions.py`, `test_article_state_propagation.py`, `test_newsletter_save_policy.py`, `test_behavioral_contracts.py`, `test_postgresql_integration.py` | AUTH-005, NEWS-005, SAVE-001–004, API-005, API-009, API-018 |
| Newsletter | NEWS-INV-001–005 | `test_newsletters.py`, `test_newsletter_save_policy.py`, `test_api_validation.py`, `test_known_correctness_failures.py`, `test_behavioral_contracts.py`, `test_postgresql_integration.py` | NEWS-001–005, API-004, API-017 |
| Feed/OPML | FEED-INV-001–006 | `test_feed_refresh.py`, `test_opml.py`, `test_behavioral_contracts.py`, `test_postgresql_integration.py` | ING-002, ING-005, ING-007–013, API-012, API-016 |
| API auth/input/schema/capability | API-AUTH-INV-001–003, API-INPUT-INV-001, API-SCHEMA-INV-001–002, API-CAP-INV-001–002, API-COMPAT-INV-001–002 | `test_api.py`, `test_api_capabilities.py`, `test_api_validation.py`, `test_newsletter_save_policy.py`, `test_behavioral_contracts.py` | AUTH-003, API-001–019 |
| Progressive/a11y/mobile | UI-INV-001–005 | `test_article_actions.py`, `test_article_actions_browser.py`, `test_browser_redirects.py`, `test_digest_views.py`, `test_mobile_today_browser.py`, `test_responsive_accessibility_browser.py`, `test_known_correctness_failures.py`, `test_behavioral_contracts.py` | WEB-001–002, WEB-007–010, WEB-012–014, WEB-018, WEB-020–021, ING-008, SAVE-003, API-001 |
| Observability/recovery | OPS-INV-001–005 | `test_feed_refresh.py`, `test_api.py`, `test_production_settings.py`, `test_postgresql_integration.py`; documented manual evidence where automation is absent | ING-007–010, API-016, OPS-002, OPS-008–014 |

## Expected-failure ledger

The current suite contains **2 expected failures**. Both remaining markers are focused
cross-feature characterizations in `test_behavioral_contracts.py`:

1. Authenticated GET responses outside Today still lack consistent `private, no-store`
   policy — UI-INV-004.
2. Negative numeric path IDs still resolve before the intended authentication and JSON
   validation envelopes — API-SCHEMA-INV-002.

Redirect, OPML, Article identity, and refresh-generation fencing characterizations now
pass as ordinary regressions. A green run means the two acknowledged failures were
observed; it does not mean those invariants conform. Unexpected success is a suite
failure and requires removing the marker only after the fixed contract and
documentation are reviewed.

## Maintenance protocol

For every change that can alter a cross-feature invariant:

1. Identify affected invariant and feature IDs before editing. Preserve existing ID
   meaning; add a new ID for a distinct rule and mark a superseded ID as retired.
2. Update **all five fields** in each affected invariant: normative contract,
   scope/precedence, executable evidence, violation status, and source/feature IDs.
3. Add deterministic evidence at the lowest shared boundary, plus adapter tests where
   precedence or representation differs. Avoid live providers, real clocks, and
   order-dependent assertions.
4. Never turn a known violation into **Conformant** while its expected-failure marker
   remains. A fix removes the marker, makes the normative assertion pass, updates the
   expected-failure ledger/counts, and updates catalog/architecture/README drift in the
   same change.
5. Update the traceability matrix when a module or feature mapping changes. Keep
   operational claims bounded to repository evidence; never imply backups, alerts,
   deployment, or provider validation that was not executed.
6. Run the full Django suite and verify exact expected-failure count, Ruff/format,
   mypy, pre-commit, Markdown/link diagnostics, mechanical invariant and catalog
   counts, Django checks/migration checks, Compose rendering where available, and a
   staged-file/diff review. Published documentation must not reference ignored/private
   audit artifacts or contain credentials.

Mechanical invariant check:

```bash
python - <<'PY'
from collections import Counter
from pathlib import Path
import ast
import re

text = Path("docs/features/contracts.md").read_text()
ids = re.findall(r"^### ([A-Z]+(?:-[A-Z]+)*-INV-\d{3}) —", text, re.M)
assert len(ids) == len(set(ids)) == 44
blocks = re.split(r"^### [A-Z]+(?:-[A-Z]+)*-INV-\d{3} —.*$", text, flags=re.M)[1:]
required = ("Normative current contract", "Scope / precedence", "Executable evidence",
            "Known violation status", "Source / feature IDs")
assert all(all(label in block for label in required) for block in blocks)
statuses = Counter("Known violation" if "**Known violation**" in block else "Conformant"
                   for block in blocks)
assert statuses == Counter({"Conformant": 38, "Known violation": 6})
test_paths = list(Path("feeds/tests").glob("test_*.py"))
methods = expected = 0
for path in test_paths:
    tree = ast.parse(path.read_text())
    methods += sum(1 for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name.startswith("test_"))
    expected += sum(1 for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and any(isinstance(d, ast.Name) and d.id == "expectedFailure"
                            for d in node.decorator_list))
assert (len(test_paths), methods, expected) == (20, 276, 2)
print(len(ids), statuses, len(test_paths), methods, expected)
PY
```
