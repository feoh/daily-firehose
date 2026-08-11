# Daily Firehose feature and behavioral-contract catalog

> Snapshot: commit [`03965d98aa51522a98266df28aa2ba45e80c03e7`](https://github.com/feoh/daily-firehose/tree/03965d98aa51522a98266df28aa2ba45e80c03e7). This is an inventory of behavior evidenced at that commit, not a roadmap or a claim about the currently deployed revision.

## How to read and maintain this catalog

A **feature** below is an observable capability or an operational boundary. A **contract** is behavior pinned by code, tests, or operator documentation. Implementation facts are not automatically desirable product contracts: limitations and accidents are recorded so maintainers can decide compatibility deliberately. No deferred or unknown item implies that a fix exists.

Stable IDs have a domain prefix and never change meaning: `AUTH` (identity), `WEB` (browser UI), `ING` (feed ingestion), `NEWS` (newsletters), `SAVE` (saving), `API` (machine adapters), and `OPS` (configuration/runtime). Retire an ID rather than reusing it. Status has exactly one of these meanings:

- **fact** — directly evidenced current behavior, whether intentional or not;
- **known-defect** — current behavior contradicts an executable or documented expectation;
- **deferred** — a recorded future choice or improvement, not implemented;
- **unknown** — repository evidence cannot establish the behavior.

“Owner” names the code or operational boundary that owns behavior; it never invents a person. Every `Source` link is immutable at the snapshot. Relative architecture links are reading aids only.

### Maintenance protocol

For every behavior-changing change:

1. Update affected records in the same change: inputs, output, state, failure, mobile/accessibility, evidence, gaps, and source links. Preserve IDs; add IDs for new independent contracts and retire removed ones explicitly.
2. Add or update executable tests, then update the [test traceability matrix](#test-module-traceability). A known defect becomes `fact` only when its expected-failure marker is removed and the replacement contract passes. A deferred/unknown item changes status only with evidence.
3. Update links to the [current-state architecture](../architecture/current-state.md) when component, route, persistence, trust, integration, deployment, or recovery boundaries change. Re-pin this catalog when taking a new whole-repository snapshot; do not mix source commits silently.
4. Run the mechanical inventory check described in [coverage summary](#coverage-summary), Markdown/link diagnostics, pre-commit, and the full suite. Review diffs for secret values and private/ignored artifact links.

## Compact feature index

| Domain | IDs | Status summary |
| --- | --- | --- |
| Authentication/session/admin | AUTH-001–AUTH-005 | 5 fact |
| Browser reading and interaction | WEB-001–WEB-021 | 19 fact, 2 known-defect |
| Feeds, discovery, refresh, OPML | ING-001–ING-013 | 10 fact, 3 known-defect |
| Newsletters and saving | NEWS-001–NEWS-005, SAVE-001–SAVE-004 | 8 fact, 1 known-defect |
| Legacy, bearer, webhook, signed APIs | API-001–API-019 | 18 fact, 1 known-defect |
| Configuration, build, deployment, operations | OPS-001–OPS-015 | 12 fact, 1 known-defect, 1 deferred, 1 unknown |
| **Total** | **82 IDs** | **72 fact, 8 known-defect, 1 deferred, 1 unknown** |

The summary is mechanically checked against the detailed `###` records; see [coverage summary](#coverage-summary).

## Authentication, session, and admin

### AUTH-001 — Session login

- **Actor / status / owner:** anonymous browser user; **fact**; Django authentication and project URL/settings boundary.
- **Entry / validated input:** `GET|HEAD|POST|PUT|OPTIONS /accounts/login/`; Django username/password form, CSRF on normal POST, and framework validation. A protected-route redirect supplies `next`; Django advertises PUT/OPTIONS dispatch even though POST is the client login contract.
- **Output / presentation:** HTML login form; valid credentials redirect to `/`; protected pages redirect anonymous users to login. There is no first-party signup or password-reset route.
- **State / side effects:** successful POST creates an authenticated session.
- **Failure:** invalid credentials render form errors; unsupported framework methods return 405.
- **Mobile / accessibility:** native labeled form and heading; no project-specific login mobile or assistive-technology contract.
- **Test evidence:** `test_mobile_today_browser.py` performs real-browser login; production redirect behavior is in `test_production_settings.py`.
- **Known gaps / expected failures:** exact Django 5.x form/error wording is framework-owned and not snapshot-tested.
- **Source:** [`daily_firehose/urls.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/urls.py), [`login.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/registration/login.html), [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py).

### AUTH-002 — Session logout and protected-page boundary

- **Actor / status / owner:** authenticated or anonymous browser; **fact**; Django auth middleware/view.
- **Entry / validated input:** CSRF-protected `POST` or framework `OPTIONS` at `/accounts/logout/`; all first-party human routes except newsletter detail and login use `login_required`.
- **Output / presentation:** logout redirects to login; protected anonymous access redirects with `next`.
- **State / side effects:** destroys an existing session; anonymous POST is harmless.
- **Failure:** GET logout is not supported; bad CSRF is framework 403.
- **Mobile / accessibility:** semantic form button in authenticated navigation; same responsive nav behavior as WEB-001.
- **Test evidence:** protected view suites force login; production proxy tests assert anonymous login redirects.
- **Known gaps / expected failures:** no focused logout lifecycle test.
- **Source:** [`daily_firehose/urls.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/urls.py), [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html), [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py).

### AUTH-003 — Session and CSRF cookie policy

- **Actor / status / owner:** browser and operator; **fact**; Django settings/security middleware.
- **Entry / validated input:** production mode plus trusted `X-Forwarded-Proto: https`; session mutations require Django CSRF tokens.
- **Output / presentation:** production session cookie is Secure, HttpOnly, SameSite=Lax; CSRF cookie is Secure and SameSite=Lax.
- **State / side effects:** standard Django session/CSRF persistence.
- **Failure:** bad/missing CSRF yields framework 403; direct production HTTP redirects before normal page handling.
- **Mobile / accessibility:** N/A; transport contract is viewport-independent.
- **Test evidence:** `test_production_settings.py` checks flags and a forwarded-HTTPS CSRF cookie.
- **Known gaps / expected failures:** no session expiry/rotation customization is evidenced.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`test_production_settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_production_settings.py).

### AUTH-004 — Django admin inventory

- **Actor / status / owner:** staff/superuser; **fact**; Django admin and `feeds.admin`.
- **Entry / validated input:** `/admin/**`; framework login/permissions/forms. Nine app models are registered: Category, Feed, Article, NewsletterIssue, SavedArticle, ArticleReadState, BulkReadMarker, ApiToken, UserPreference.
- **Output / presentation:** framework CRUD/search/filter/date views; feed health and token hash/prefix/use fields have configured readonly treatment.
- **State / side effects:** admin forms write ORM state; deletion follows model cascades/`SET_NULL` rules.
- **Failure:** nonstaff users cannot access protected admin children; form/model errors render inline.
- **Mobile / accessibility:** Django admin baseline; no project-specific mobile/a11y evidence.
- **Test evidence:** newsletter policy tests exercise SavedArticle admin rejection; other admin configuration has no dedicated tests.
- **Known gaps / expected failures:** direct admin/ORM writes bypass service orchestration; UserPreference list omits `focus_mode`.
- **Source:** [`admin.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/admin.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### AUTH-005 — Admin newsletter-save guard

- **Actor / status / owner:** staff editing SavedArticle; **fact**; `SavedArticleAdminForm` and save-capability service.
- **Entry / validated input:** admin create or Article reassignment; persisted NewsletterIssue relationship is rechecked.
- **Output / presentation:** newsletter selection gets form validation; ordinary SavedArticle edit proceeds.
- **State / side effects:** accepted admin write changes only local ORM state and never invokes Linkding.
- **Failure:** newsletter create/reassignment is blocked; unchanged legacy rows and direct ORM/shell writes bypass the form guard.
- **Mobile / accessibility:** Django form baseline; no dedicated evidence.
- **Test evidence:** `test_newsletter_save_policy.py::test_admin_cannot_create_or_reassign_a_save_to_a_newsletter`.
- **Known gaps / expected failures:** model/database does not enforce this policy.
- **Source:** [`admin.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/admin.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

## Browser surfaces and interactions

### WEB-001 — Global shell and navigation

- **Actor / status / owner:** authenticated browser user; **fact**; base template/CSS.
- **Entry / validated input:** every base-derived page; authenticated state controls chrome.
- **Output / presentation:** masthead; Today, Week, Month, Archived, Saved, Feeds, Preferences, legacy JSON links; refresh and logout forms; Django messages; keyboard hint/help.
- **State / side effects:** rendering may lazily create UserPreference through the calling view; shell itself is read-only.
- **Failure:** messages are escaped; no project custom 403/404/500 templates.
- **Mobile / accessibility:** skip link to focusable main, labeled primary nav, descriptive image alt, live message region; at ≤42rem nav becomes two columns. No active-route indicator.
- **Test evidence:** digest/newsletter markup tests and all Playwright Today tests.
- **Known gaps / expected failures:** mobile nav hit area is only asserted at 24px, not a 44px target; anonymous newsletter page still shows a mostly inapplicable keyboard hint.
- **Source:** [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html), [`site.css`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/css/site.css).

### WEB-002 — Today reading queue

- **Actor / status / owner:** authenticated reader; **fact**; digest views/query helpers.
- **Entry / validated input:** permissive `GET* /`; UTC current date and user state; no client fields.
- **Output / presentation:** `digest.html`, count/label, article cards, period bulk action; empty text “No articles here yet. The hose is quiet.” Response is never-cache.
- **State / side effects:** lazily creates default UserPreference; selects by `fetched_at` local date and excludes effective-read and locally saved articles.
- **Failure:** ordinary framework errors; anonymous redirect to login.
- **Mobile / accessibility:** identical card IDs for desktop, iPhone 390×844 and 320×844 UAs; headings, labeled card grid, no horizontal overflow and initially visible first-card content are browser-tested.
- **Test evidence:** `test_digest_views.py` Today contracts; all nine `test_mobile_today_browser.py` tests.
- **Known gaps / expected failures:** Chrome-only mobile evidence; accessibility semantics are markup/geometry, not screen-reader/axe evidence.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`digest.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/digest.html).

### WEB-003 — Week and month queues

- **Actor / status / owner:** authenticated reader; **fact**; digest views/query helpers.
- **Entry / validated input:** permissive `GET* /week/` and `/month/`; current UTC Monday–Sunday or calendar month.
- **Output / presentation:** same digest/card/empty/bulk contract as Today, labeled for the period.
- **State / side effects:** preference lazy-create; state query only. Uses first-seen/fetched date, not publication date.
- **Failure:** framework errors/login redirect; unlike Today these responses are not explicitly never-cache.
- **Mobile / accessibility:** shared responsive template and semantics, but only Today has browser geometry evidence.
- **Test evidence:** `test_digest_views.py` verifies windows, hidden read state, first-seen semantics and bulk propagation.
- **Known gaps / expected failures:** no Week/Month Playwright test.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`digest.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/digest.html).

### WEB-004 — Archive queue

- **Actor / status / owner:** authenticated reader; **fact**; archive query/view.
- **Entry / validated input:** permissive `GET* /archived/`; user identity.
- **Output / presentation:** latest 50 explicitly read states by state-update recency, common cards/empty state, mark-unread controls.
- **State / side effects:** preference lazy-create; GET is otherwise read-only. AJAX mark-unread removes the card.
- **Failure:** framework errors/login redirect.
- **Mobile / accessibility:** shared responsive card semantics; no archive-specific mobile/a11y browser test.
- **Test evidence:** `test_digest_views.py` and `test_article_state_propagation.py` cover recency and transitions.
- **Known gaps / expected failures:** no pagination; older rows and marker-only historical articles may be absent.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`digest.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/digest.html).

### WEB-005 — Saved-links queue

- **Actor / status / owner:** authenticated reader; **fact**; saved query/view.
- **Entry / validated input:** permissive `GET* /saved-links/`; user identity.
- **Output / presentation:** latest 50 local saves by `saved_at`, snapshot title/URL, saved timestamp, Linkding confirmed/failed state, current read state; no repeat-save control.
- **State / side effects:** preference lazy-create; GET read-only.
- **Failure:** framework errors/login redirect.
- **Mobile / accessibility:** shared responsive cards; no surface-specific browser/a11y test.
- **Test evidence:** `test_digest_views.py` and state-propagation suite verify recency, snapshot and local-failure visibility.
- **Known gaps / expected failures:** no browser unsave and no pagination.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`article_card.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/includes/article_card.html).

### WEB-006 — Feed detail queue

- **Actor / status / owner:** authenticated reader; **fact**; feed-detail view.
- **Entry / validated input:** permissive `GET* /feeds/<positive-int>/`; existing Feed.
- **Output / presentation:** feed metadata/site/source links and up to first 100 model-ordered articles, filtered read/saved; feed-specific empty state and bulk-read form.
- **State / side effects:** preference lazy-create; GET read-only.
- **Failure:** unknown Feed 404; login redirect.
- **Mobile / accessibility:** heading and labeled cards use shared responsive CSS; no dedicated feed-detail mobile/a11y evidence.
- **Test evidence:** `test_article_state_propagation.py` tests feed/user isolation and read behavior.
- **Known gaps / expected failures:** no pagination; raw stored feed/site URLs are destinations.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`feed_detail.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/feed_detail.html).

### WEB-007 — Article-card presentation contract

- **Actor / status / owner:** reader on digest/feed/archive/saved; **fact**; shared article-card template/query context.
- **Entry / validated input:** Article plus user read/save state and newsletter capability.
- **Output / presentation:** labeled focusable `article`; status chips, feed/category, title, published/seen time, 55-word stripped RSS summary, Open, read/unread and eligible Save forms. Newsletter card says “Read newsletter,” suppresses summary and Save.
- **State / side effects:** presentation only.
- **Failure:** missing optional category/summary/time is rendered conditionally; long text/URLs rely on general wrapping.
- **Mobile / accessibility:** heading labels card; native links/forms; programmatic selection; shared responsive wrapping. `tabindex=-1` and `aria-current=true` selection semantics are not assistive-tech validated.
- **Test evidence:** `test_article_actions.py`, `test_newsletters.py`, newsletter policy tests and Playwright card assertions.
- **Known gaps / expected failures:** no explicit external-link context/new-tab policy for ordinary article links.
- **Source:** [`article_card.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/includes/article_card.html), [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py).

### WEB-008 — Individual read/unread action

- **Actor / status / owner:** authenticated reader; **fact**; browser action view and progressive JS.
- **Entry / validated input:** `POST /articles/<id>/mark/`, CSRF; literal `state=read` means read and every other value means unread; optional AJAX headers, remove flag, `next`.
- **Output / presentation:** JSON status/message for enhanced forms or flash+redirect for non-JS; queue removal follows template flags.
- **State / side effects:** upserts user/Article `ArticleReadState`; explicit unread overrides effective bulk state.
- **Failure:** missing Article 404; AJAX network or non-JSON error prepends `role=alert` and retains the submitted card.
- **Mobile / accessibility:** native form works without JS; enhanced result uses status/alert; Playwright proves only target removal at 390px.
- **Test evidence:** action, digest, state-propagation and mobile suites.
- **Known gaps / expected failures:** redirect safety is separately cataloged as WEB-018; state values are permissive rather than choice-validated.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js).

### WEB-009 — Browser bulk period marker

- **Actor / status / owner:** authenticated reader; **fact**; digest view/read helpers.
- **Entry / validated input:** `POST /mark-period-read/`, CSRF, hidden `scope`, `period_start`, `period_end`, `next`; the browser template supplies valid current periods.
- **Output / presentation:** flash then redirect.
- **State / side effects:** marks matching current articles through operation cutoff, then upserts a durable period marker; later-fetched items remain unread.
- **Failure:** missing/malformed/reversed inputs can raise rather than form an error; writes are not in one encompassing transaction.
- **Mobile / accessibility:** native button/form; no confirmation and no dedicated mobile/a11y test.
- **Test evidence:** digest and state-propagation suites cover normal/cutoff behavior.
- **Known gaps / expected failures:** invalid marker persistence is WEB-019; open redirects are WEB-018.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### WEB-010 — Browser bulk feed marker

- **Actor / status / owner:** authenticated reader; **fact**; feed action/read helpers.
- **Entry / validated input:** `POST /feeds/<id>/mark-read/`, CSRF, existing Feed, optional `next`.
- **Output / presentation:** success message and redirect.
- **State / side effects:** materializes read rows for that feed through cutoff and upserts feed marker; scoped to user/feed.
- **Failure:** missing Feed 404; explicit rows and marker are not one transaction.
- **Mobile / accessibility:** native button/form; no dedicated browser geometry/a11y test.
- **Test evidence:** state-propagation suite proves no cross-feed/user leak.
- **Known gaps / expected failures:** WEB-018 and WEB-019 apply.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### WEB-011 — Effective read and visibility semantics

- **Actor / status / owner:** authenticated reader; **fact**; shared article-query/read fold.
- **Entry / validated input:** user, selected Article window, explicit states and all user bulk markers.
- **Output / presentation:** effective read = explicit true plus eligible markers minus explicit false; normal queues then also exclude local saves. Day matching uses local date of `fetched_at`.
- **State / side effects:** query only; bulk actions separately materialize states.
- **Failure:** malformed/duplicate marker state can make results divergent or raise; computation is marker×article Python work.
- **Mobile / accessibility:** N/A to computation; output drives identical responsive cards.
- **Test evidence:** `test_article_state_propagation.py` and `test_digest_views.py` cover user isolation, overrides, cutoff and surfaces.
- **Known gaps / expected failures:** no pagination/arbitrary-window bound; no dedicated fetched-time index; invalid marker defects are WEB-019.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### WEB-012 — Progressive article actions and failure feedback

- **Actor / status / owner:** browser with or without JavaScript; **fact**; article templates/static JS/browser views.
- **Entry / validated input:** same-origin CSRF forms marked `data-article-action`; JS blocks pending/repeated save/mark requests. Save forms carry target ID/URL and save responses are identity-checked; mark forms/responses carry no article identity.
- **Output / presentation:** enhanced inline `role=status`/`role=alert`, optional exact-card removal; without JS, POST/redirect/messages remain functional.
- **State / side effects:** server action owns persistence; JS changes selection/DOM after a successful mark response or an identity-verified save response.
- **Failure:** fetch/non-JSON or mismatched save identity keeps the card and reports an error; repeated errors accumulate. A successful mark response is associated with the submitted form/card rather than response identity.
- **Mobile / accessibility:** live-region roles and focus-visible styles; success removal does not explicitly manage focus.
- **Test evidence:** `test_article_actions.py`, digest AJAX tests and Playwright target-removal tests.
- **Known gaps / expected failures:** newsletter-detail mark form is not AJAX-marked; no reduced-motion/assistive-tech test.
- **Source:** [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js), [`article_card.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/includes/article_card.html).

### WEB-013 — Keyboard navigation and actions

- **Actor / status / owner:** keyboard user; **fact**; global JS/base markup.
- **Entry / validated input:** `j/k` selection, `s` save, `m` mark, `o` open, uppercase `T/W/M/A/L/F` navigation; input/textarea/select/button/contenteditable generally suppress shortcuts; safe URL parser restricts schemes/origins by action.
- **Output / presentation:** selected visual/ARIA card or feed, clamped traversal, form submission/navigation/open.
- **State / side effects:** selection/scroll/focus and resulting normal server mutations; held save/mark repeats are blocked.
- **Failure:** absent action is ignored; unsafe URL is not opened.
- **Mobile / accessibility:** keyboard-first; cards/list items are programmatically selectable. No touch equivalent beyond native controls.
- **Test evidence:** script assertions and newsletter shortcut-presence tests; no end-to-end keyboard behavior suite.
- **Known gaps / expected failures:** advertised shortcuts diverge on newsletter pages; `?` is processed before typing suppression.
- **Source:** [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js), [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html).

### WEB-014 — Keyboard help dialog

- **Actor / status / owner:** keyboard/browser user; **fact**; base template/global JS.
- **Entry / validated input:** `?` opens the dialog; Escape or its close button closes it. The page displays a keyboard hint but has no Help button.
- **Output / presentation:** modal with shortcut list; close restores selected-card or main focus.
- **State / side effects:** DOM dialog open state only.
- **Failure:** unsupported dialog behavior depends on browser; no server state.
- **Mobile / accessibility:** `dialog`, heading association and close control; lacks focus trap and inert background.
- **Test evidence:** markup/script presence only.
- **Known gaps / expected failures:** no real-browser focus-order, screen-reader or Escape test.
- **Source:** [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html), [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js).

### WEB-015 — Clipboard copy for inbound newsletter address

- **Actor / status / owner:** authenticated user on Feeds page; **fact**; feed template/global JS.
- **Entry / validated input:** configured displayed inbound address and copy button.
- **Output / presentation:** Clipboard API or hidden-textarea `execCommand` fallback; “Copied!” and polite live feedback reset after 2.5s.
- **State / side effects:** writes clipboard only; button temporarily disabled.
- **Failure:** “Copy failed” with manual-selection instruction.
- **Mobile / accessibility:** native button and `role=status`; address is text in `<strong>`, not a dedicated selectable input.
- **Test evidence:** `test_feed_views.py` verifies address/reminder presence only.
- **Known gaps / expected failures:** no browser success/failure/fallback test.
- **Source:** [`feed_list.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/feed_list.html), [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js).

### WEB-016 — Preferences, themes, compact and focus modes

- **Actor / status / owner:** authenticated user; **fact**; UserPreference/form/view/CSS.
- **Entry / validated input:** permissive `GET/POST* /preferences/`; Django choice `theme`, checkboxes `compact`, `focus_mode`.
- **Output / presentation:** system, accessible light/dark and named palette classes; compact tightens cards; focus narrows/reframes layout; valid POST messages+redirects.
- **State / side effects:** lazily creates one preference row per user, then updates all form fields.
- **Failure:** form/model errors inline; unknown theme rejected.
- **Mobile / accessibility:** native labeled controls; system follows `prefers-color-scheme`; normal/compact/focus Today geometry tested at 390 and focus/normal at 320.
- **Test evidence:** digest focus test, API preference tests and Playwright modes.
- **Known gaps / expected failures:** no automated WCAG contrast audit or `color-mix` compatibility test.
- **Source:** [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py), [`preferences.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/preferences.html), [`site.css`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/css/site.css).

### WEB-017 — Empty, error, and message presentation

- **Actor / status / owner:** browser user; **fact**; templates/messages/JS.
- **Entry / validated input:** empty result sets, Django form/messages, enhanced action results.
- **Output / presentation:** digest quiet state; feed-list “No feeds yet”; feed-detail no-articles state; newsletter empty-body state; polite server message list; JS status/alert blocks.
- **State / side effects:** presentation only.
- **Failure:** default Django error pages for 403/404/500; message count grammar can say “1 articles”; repeated AJAX errors accumulate.
- **Mobile / accessibility:** semantic headings and live roles; no error focus transfer; `.error` uses warning color.
- **Test evidence:** view suites assert representative empty/messages; no complete error-page/accessibility suite.
- **Known gaps / expected failures:** empty state is not exercised on every viewport/surface.
- **Source:** [`digest.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/digest.html), [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html), [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js).

### WEB-020 — Responsive mobile and desktop presentation

- **Actor / status / owner:** desktop or mobile browser user; **fact**; server templates, responsive CSS and Playwright boundary.
- **Entry / validated input:** one server-rendered representation for all User-Agents; desktop layout and CSS breakpoint at 42rem; regression viewports 390×844 and 320×844 with iPhone UA.
- **Output / presentation:** desktop header/navigation and spacious cards; mobile compressed header, two-column navigation, narrower page/form padding and wrapping card actions. Today Article IDs remain identical across mobile, desktop, legacy JSON and reload.
- **State / side effects:** presentation does not fork persistence; mobile save/read removes only the target and survives reload.
- **Failure:** tested Today layouts must have no horizontal overflow, readable card content/actions and visible initial first-card content.
- **Mobile / accessibility:** normal/compact/focus are covered at 390px; normal/focus and first-card discovery at 320px. Native responsive controls remain keyboard reachable.
- **Test evidence:** all nine `test_mobile_today_browser.py` tests and digest User-Agent parity.
- **Known gaps / expected failures:** no browser matrix for Week/Month/archive/saved/feed/newsletter/login/preferences/help, landscape, zoom, reduced motion, non-Chromium or screen readers.
- **Source:** [`site.css`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/css/site.css), [`test_mobile_today_browser.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_mobile_today_browser.py).

### WEB-021 — Cross-cutting accessibility baseline

- **Actor / status / owner:** keyboard and assistive-technology user; **fact**; templates, CSS and progressive JavaScript.
- **Entry / validated input:** all project templates and controls; native keyboard activation plus global shortcuts.
- **Output / presentation:** semantic headings/sections/articles, labeled primary nav/card grids/actions/newsletter body, skip link, visible `:focus-visible`, native labeled forms/buttons, message/status/alert live regions, masthead alt and dialog labeling.
- **State / side effects:** skip/help/selection alter focus or DOM; server state changes only through normal forms.
- **Failure:** no custom accessible 403/404/500 pages; AJAX removal does not explicitly relocate focus.
- **Mobile / accessibility:** this is the baseline itself; it applies to desktop and responsive layouts, but is evidenced by markup and geometry rather than an assistive-technology audit.
- **Test evidence:** template assertions across digest/newsletter/feed suites and Playwright geometry/action tests.
- **Known gaps / expected failures:** no axe/WCAG/contrast/screen-reader tests; help lacks focus trap/inert background; selection ARIA semantics and some touch targets are questionable.
- **Source:** [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html), [`site.css`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/css/site.css), [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js).

### WEB-018 — Browser mutation redirect validation

- **Actor / status / owner:** authenticated browser user or attacker supplying `next`; **known-defect**; browser mutation views.
- **Entry / validated input:** posted `next` on mark-article, save, mark-period, mark-feed and refresh; currently no same-origin validation.
- **Output / presentation:** handlers can issue a 302 to an external attacker URL.
- **State / side effects:** underlying mutation may complete before redirect.
- **Failure:** expected safe behavior is reject/fallback; current behavior is an open redirect.
- **Mobile / accessibility:** N/A; security behavior is viewport-independent.
- **Test evidence:** expected failure `test_mark_article_rejects_external_next_redirect`.
- **Known gaps / expected failures:** only mark-article is directly characterized; the same direct-redirect pattern exists on the other named handlers.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`test_known_correctness_failures.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_known_correctness_failures.py).

### WEB-019 — Bulk marker shape/order constraints

- **Actor / status / owner:** browser/API/admin/direct ORM writers; **known-defect**; BulkReadMarker model/database and read commands.
- **Entry / validated input:** scope/feed/date marker fields; database currently has no checks enforcing feed-vs-period shape or date ordering, and nullable uniqueness is not logical uniqueness on PostgreSQL.
- **Output / presentation:** invalid marker rows can persist and later distort/error read calculation.
- **State / side effects:** invalid/duplicate durable state.
- **Failure:** expected database `IntegrityError` is not raised for four invalid shapes.
- **Mobile / accessibility:** N/A persistence contract.
- **Test evidence:** four expected failures: missing feed, missing dates, period with feed, reversed dates.
- **Known gaps / expected failures:** browser period parsing also can 500; concurrency uniqueness requires PostgreSQL-specific coverage.
- **Source:** [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py), [`test_known_correctness_failures.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_known_correctness_failures.py).

## Feeds, discovery, refresh, and OPML

### ING-001 — Browser feed list and create

- **Actor / status / owner:** authenticated user; **fact**; feed form/view/template.
- **Entry / validated input:** permissive `GET/POST* /feeds/`; ModelForm URL, optional title/site/description/category, active flag; blank title invokes discovery.
- **Output / presentation:** add form, grouped category/Uncategorized feed list, OPML links and optional inbound address; valid create redirects with message.
- **State / side effects:** creates globally shared Feed; discovery may perform bounded network GET.
- **Failure:** model errors inline; classified discovery error attaches to URL and writes nothing; empty list has explicit state.
- **Mobile / accessibility:** headings/native labels, keyboard-selectable feed items, responsive shared forms; no mobile feed-list test.
- **Test evidence:** `test_feed_views.py` covers grouping, reminder and discovery failure/no-write.
- **Known gaps / expected failures:** no dedicated duplicate-submission or discovery success browser test.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`forms.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/forms.py), [`feed_list.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/feed_list.html).

### ING-002 — Feed/category persistence boundary

- **Actor / status / owner:** admin, browser, API, OPML and refresh; **fact**; Feed/Category ORM models.
- **Entry / validated input:** unique Category name/slug; globally unique Feed URL; optional category; model URL/length/choice validation.
- **Output / presentation:** ordered categories/feeds and health fields consumed by UI/API/admin.
- **State / side effects:** Category deletion nulls Feed/saved snapshot; Feed deletion cascades Articles and feed markers; API DELETE instead deactivates.
- **Failure:** database/model uniqueness and validation errors; callers map differently.
- **Mobile / accessibility:** N/A persistence contract.
- **Test evidence:** builders, API validation, feed views, OPML tests.
- **Known gaps / expected failures:** all authenticated API tokens mutate shared catalog; intended multi-user authorization is unknown under OPS-015.
- **Source:** [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py), [`admin.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/admin.py).

### ING-003 — Metadata discovery

- **Actor / status / owner:** feed-create browser/API caller; **fact**; services and feed-fetch gateway.
- **Entry / validated input:** credential-free HTTP(S) feed URL; blank supplied title triggers bounded fetch, feedparser metadata and relative URL resolution.
- **Output / presentation:** discovered title/site/description; API or form subsequently persists it.
- **State / side effects:** network/DNS only until caller writes Feed.
- **Failure:** classified `FeedFetchError` transport/policy failures stop the caller from writing. Feedparser `bozo` or otherwise empty metadata is not rejected; discovery succeeds with the submitted URL as fallback title and blank optional metadata.
- **Mobile / accessibility:** N/A service; classified transport errors appear on the labeled browser form.
- **Test evidence:** `test_feed_fetch.py` metadata/charset/final-URL tests; API/feed view transport-failure no-write tests.
- **Known gaps / expected failures:** discovery does not classify unusable parses as errors and may persist a Feed titled with its URL; synchronous network work occupies the request process.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`feed_fetch.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/feed_fetch.py).

### ING-004 — Feed fetch trust and bounds

- **Actor / status / owner:** discovery/refresh caller; **fact**; `feed_fetch` outbound transport.
- **Entry / validated input:** absolute credential-free HTTP(S), ports 80/443, every DNS answer globally routable; policy has positive finite connect/read/total/bytes and nonnegative redirects.
- **Output / presentation:** bounded bytes, final URL and selected lowercase headers.
- **State / side effects:** outbound streamed GET; environment proxies disabled; each response closed.
- **Failure:** classified invalid URL/policy, blocked target, DNS, timeout, TLS/connection, redirect, encoding, size, HTTP and empty-body errors; every redirect is revalidated.
- **Mobile / accessibility:** N/A network service.
- **Test evidence:** `test_feed_fetch.py` extensively covers schemes, IDNA, address classes, redirects, sessions, deadlines, encoding and limits.
- **Known gaps / expected failures:** DNS validation and connection DNS are separate; cooperative total cannot interrupt resolver/socket or slow drip.
- **Source:** [`feed_fetch.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/feed_fetch.py), [`test_feed_fetch.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_feed_fetch.py).

### ING-005 — Feed parse and article upsert

- **Actor / status / owner:** worker/browser/API refresh; **fact**; refresh service/Article model.
- **Entry / validated input:** eligible Feed and bounded fetched bytes; feedparser entries need usable link; alternate original link is preferred.
- **Output / presentation:** `RefreshResult` created/updated counts and updated feed metadata.
- **State / side effects:** records attempt before work; per-feed atomic content/success transaction upserts by `(feed,guid)`, updates timestamps/metadata, clears failures on success.
- **Failure:** fetch/parse/model/integrity failures roll back content transaction and become classified results; unexpected exception is safely returned and traceback logged.
- **Mobile / accessibility:** N/A service; adapters format results.
- **Test evidence:** `test_feed_fetch.py` integration seams and `test_feed_refresh.py` parsing, rollback, metadata and logging.
- **Known gaps / expected failures:** identity contradiction is ING-006.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### ING-006 — Stable-URL/new-GUID reconciliation

- **Actor / status / owner:** refresh service; **known-defect**; Article identity/upsert boundary.
- **Entry / validated input:** existing `(feed,url)` with a changed incoming GUID.
- **Output / presentation:** expected contract is one reconciled Article; current upsert-by-GUID collides with separate `(feed,url)` uniqueness and fails the feed refresh.
- **State / side effects:** content transaction rolls back; feed failure/backoff state is recorded.
- **Failure:** classified integrity failure rather than an update.
- **Mobile / accessibility:** N/A ingestion contract.
- **Test evidence:** expected failure `test_refresh_reconciles_changed_guid_for_same_url`.
- **Known gaps / expected failures:** canonical identity policy is not encoded beyond contradictory constraints/service behavior.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py), [`test_known_correctness_failures.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_known_correctness_failures.py).

### ING-007 — Refresh failure isolation and persistent backoff

- **Actor / status / owner:** refresh caller; **fact**; refresh orchestration/Feed health state.
- **Entry / validated input:** all active feeds; eligibility is evaluated per feed against `next_retry_at`.
- **Output / presentation:** succeeded, failed or skipped result per Feed; safe bounded title/code/message/retry.
- **State / side effects:** failure transaction locks Feed, increments failures and sets 5-minute exponential retry capped at 24h; success resets; one failure does not stop later feeds.
- **Failure:** unexpected errors are isolated and traceback-logged; exactly-due feeds run.
- **Mobile / accessibility:** N/A service; browser summary is messages.
- **Test evidence:** `test_feed_refresh.py` covers progression, saturation, restart persistence, per-feed timing and continuation.
- **Known gaps / expected failures:** overlapping refresh callers have no claim/lease; stale failure can overwrite concurrent success.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### ING-008 — Browser refresh

- **Actor / status / owner:** authenticated user; **fact**; refresh browser view/service/base JS.
- **Entry / validated input:** `POST /refresh/`, CSRF, optional `next`; all eligible active feeds.
- **Output / presentation:** disabled “Refreshing…” button, then success/warning flash with attempted/succeeded/failed/skipped/new-article counts and safely displayed failed feed names.
- **State / side effects:** synchronous serial global refresh and all ING-005/007 effects.
- **Failure:** partial failure still redirects with warning; a hung feed blocks request; unsafe `next` is WEB-018.
- **Mobile / accessibility:** native form and messages; pending button state; no mobile refresh/browser recovery test.
- **Test evidence:** `test_feed_refresh.py` feedback tests.
- **Known gaps / expected failures:** no request-level watchdog or authorization finer than login.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`base.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/base.html).

### ING-009 — Refresh management command

- **Actor / status / owner:** shell operator/Compose worker; **fact**; management command.
- **Entry / validated input:** `python manage.py refresh_feeds`; active feed catalog/settings.
- **Output / presentation:** colored per-feed results and aggregate checked/succeeded/failed/skipped summary with sanitized bounded titles.
- **State / side effects:** global refresh service effects.
- **Failure:** classified feed failures do not stop other feeds and currently do not make the command nonzero (defect ING-010).
- **Mobile / accessibility:** N/A CLI.
- **Test evidence:** two command-output tests in `test_feed_refresh.py`.
- **Known gaps / expected failures:** no command options for feed, dry-run, timeout or machine-readable output.
- **Source:** [`refresh_feeds.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/management/commands/refresh_feeds.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### ING-010 — Refresh command failure exit status

- **Actor / status / owner:** process supervisor/operator; **known-defect**; refresh command/worker supervision.
- **Entry / validated input:** a refresh cycle with one or more failed feeds.
- **Output / presentation:** command prints a warning but exits successfully.
- **State / side effects:** DB failures/backoff persist; shell loop continues to sleep.
- **Failure:** supervisor cannot infer degraded ingestion from exit status.
- **Mobile / accessibility:** N/A operational contract.
- **Test evidence:** command summary tests pin output but do not expect nonzero; architecture classifies this gap.
- **Known gaps / expected failures:** not one of the eight `expectedFailure` tests; no implemented replacement semantics are implied.
- **Source:** [`refresh_feeds.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/management/commands/refresh_feeds.py), [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml).

### ING-011 — OPML import

- **Actor / status / owner:** authenticated user; **fact**; OPML form/view/service.
- **Entry / validated input:** permissive `GET/POST* /opml/import/`; multipart uploaded file, read fully; XML outline recursion with `xmlUrl`, title/text, `htmlUrl`, parent category.
- **Output / presentation:** import form or created/updated/skipped message then Feeds redirect.
- **State / side effects:** collision-safe category creation; Feed update-or-create by URL; updates title/site/category and reactivates.
- **Failure:** valid XML processing is incremental/non-atomic; malformed handling defect is ING-012.
- **Mobile / accessibility:** labeled native file input/heading; no mobile/a11y test.
- **Test evidence:** `test_opml.py` covers parent categories and idempotent update.
- **Known gaps / expected failures:** no explicit upload-size limit, transaction or per-outline feedback.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`opml_import.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/opml_import.html).

### ING-012 — Malformed OPML feedback

- **Actor / status / owner:** authenticated importing user; **known-defect**; OPML view/service.
- **Entry / validated input:** malformed uploaded XML.
- **Output / presentation:** expected inline “Upload a valid OPML file.” with no writes; current parse exception reaches server error.
- **State / side effects:** malformed XML parses before walking, so the characterized case writes nothing.
- **Failure:** HTTP 500 rather than form feedback.
- **Mobile / accessibility:** expected labeled inline error is absent; viewport-independent.
- **Test evidence:** expected failure `test_malformed_opml_returns_form_feedback_without_writes`.
- **Known gaps / expected failures:** later failure during valid processing can still leave partial writes.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`test_known_correctness_failures.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_known_correctness_failures.py).

### ING-013 — OPML export

- **Actor / status / owner:** authenticated user; **fact**; OPML view/service.
- **Entry / validated input:** permissive `GET* /opml/export/`; all active Feeds.
- **Output / presentation:** `text/x-opml` attachment `daily-firehose-feeds.opml`, OPML 2.0 flat outlines.
- **State / side effects:** read-only.
- **Failure:** serializer exceptions are unhandled.
- **Mobile / accessibility:** ordinary download link; N/A document body.
- **Test evidence:** no dedicated export test in current suite.
- **Known gaps / expected failures:** categories/descriptions/inactive feeds are omitted, so import/export round-trip is lossy.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

## Newsletters and saving

### NEWS-001 — Postmark newsletter ingestion domain

- **Actor / status / owner:** authenticated Postmark adapter; **fact**; newsletter service/models.
- **Entry / validated input:** parsed Postmark object with nonblank MessageID and sender/recipient/subject/body/date fields; base URL supplied by adapter.
- **Output / presentation:** result with NewsletterIssue and created flag; public archive URL becomes Article URL.
- **State / side effects:** gets/creates synthetic “Email Newsletters” Feed inactive when newly created; existing feed active state is preserved; creates Article then one-to-one NewsletterIssue; MessageID dedupes.
- **Failure:** validation/integrity can occur between writes; non-atomic defect is NEWS-002.
- **Mobile / accessibility:** N/A ingestion service.
- **Test evidence:** `test_newsletters.py` creation/dedupe; API validation tests cover adapter errors.
- **Known gaps / expected failures:** no retention/purge and base URL is request-derived.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### NEWS-002 — Newsletter ingestion atomicity

- **Actor / status / owner:** Postmark ingestion service; **known-defect**; newsletter transaction/idempotency boundary.
- **Entry / validated input:** valid new payload where NewsletterIssue creation fails after Article creation.
- **Output / presentation:** expected full rollback; current orphan Article remains and retry may hit uniqueness.
- **State / side effects:** partially committed synthetic Feed/Article state.
- **Failure:** integrity error; adapter maps expected integrity safely but cannot promise rollback.
- **Mobile / accessibility:** N/A ingestion integrity.
- **Test evidence:** expected failure `test_postmark_issue_failure_rolls_back_article`.
- **Known gaps / expected failures:** concurrent MessageID race idempotency is not covered.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`test_known_correctness_failures.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_known_correctness_failures.py).

### NEWS-003 — Public newsletter archive

- **Actor / status / owner:** anonymous or authenticated reader; **fact**; newsletter detail view/template.
- **Entry / validated input:** permissive `GET* /newsletters/<UUID>/`; existing public ID.
- **Output / presentation:** public subject/sender/date and sanitized HTML, escaped text fallback, or empty-body state; meta and header `noindex`. Authenticated users get app chrome/read control.
- **State / side effects:** GET records no “open”; preference may be created for authenticated rendering.
- **Failure:** unknown UUID 404.
- **Mobile / accessibility:** labeled article/body and headings; no newsletter-specific 320/390 or screen-reader test.
- **Test evidence:** `test_newsletters.py` public/noindex/sanitize/auth markup; policy tests preserve read/open semantics.
- **Known gaps / expected failures:** UUID URL is public indefinitely, not confidential; remote images can contact trackers.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`newsletter_detail.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/newsletter_detail.html).

### NEWS-004 — Newsletter sanitization

- **Actor / status / owner:** public archive renderer; **fact**; sanitizer service/template escaping.
- **Entry / validated input:** raw stored HTML or text; Bleach allowlist of tags/attributes/protocols.
- **Output / presentation:** scripts/styles/disallowed content removed; links open `_blank` with `noopener noreferrer`; HTTP(S) images remain allowed; text fallback is escaped.
- **State / side effects:** sanitizes on every render; raw source remains stored unchanged.
- **Failure:** sanitizer/render exception reaches normal error handling.
- **Mobile / accessibility:** sanitizer preserves basic semantic content; no responsive-email or contrast guarantee.
- **Test evidence:** newsletter tests and deterministic sanitizer attack fixture tests.
- **Known gaps / expected failures:** no CSP/image proxy/click-to-load; retained raw content has no expiry.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`test_newsletters.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_newsletters.py).

### NEWS-005 — Newsletter read/open/save semantics

- **Actor / status / owner:** reader and all save adapters; **fact**; article capability/domain policy.
- **Entry / validated input:** persisted NewsletterIssue relationship, not stale prefetched capability.
- **Output / presentation:** “open” means navigation to public detail only; no event/counter exists. Read/unread remains available. Save capability is denied and UI omits Save.
- **State / side effects:** read writes normal ArticleReadState; save rejection performs no local/Linkding mutation.
- **Failure:** adapters return their safe semantic rejection forms; direct ORM remains outside domain enforcement.
- **Mobile / accessibility:** “Read newsletter” gives link purpose; no save affordance; authenticated detail shortcuts only partially match global help.
- **Test evidence:** entire `test_newsletter_save_policy.py`, plus newsletter card tests.
- **Known gaps / expected failures:** no open analytics should be inferred; policy is not a DB constraint.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`article_card.html`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/templates/feeds/includes/article_card.html).

### SAVE-001 — Local saved-article snapshot

- **Actor / status / owner:** authenticated session/bearer/signed actor; **fact**; save service/SavedArticle model.
- **Entry / validated input:** server-resolved ordinary Article and user; persisted newsletter capability recheck.
- **Output / presentation:** unique local user/Article row snapshots URL/title/feed/category, Linkding state, notes/score and timestamps.
- **State / side effects:** update-or-create refreshes snapshots; initial `saved_at` remains recency key; Article/user deletion cascades, Feed/Category deletion nulls snapshots.
- **Failure:** newsletter rejection before I/O; model/integrity errors caller-mapped.
- **Mobile / accessibility:** N/A storage; WEB-005/012 present it.
- **Test evidence:** article actions, state propagation and newsletter policy suites.
- **Known gaps / expected failures:** no retention/history; direct ORM can violate policy/score bounds.
- **Source:** [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### SAVE-002 — Linkding bookmark integration

- **Actor / status / owner:** save service; **fact**; Linkding integration/settings.
- **Entry / validated input:** local SavedArticle snapshot, configured URL/token; POST JSON includes exact Article URL and `toread` tag, optional meaningful summary.
- **Output / presentation:** exact returned URL confirms `linkding_saved=true`; error text otherwise persists with false. Local save survives all remote failures.
- **State / side effects:** synchronous 15-second Requests POST then local remote-status update.
- **Failure:** missing token, request, HTTP, JSON or URL mismatch is caught/persisted; re-save retries. Unsave never deletes remote bookmark.
- **Mobile / accessibility:** browser warning retains card; confirmed save removes target; Playwright covers 390px save removal.
- **Test evidence:** `test_article_actions.py`, state propagation and policy suites.
- **Known gaps / expected failures:** HTTPS is not enforced; no idempotency key/retry/outbox/circuit breaker; raw exception detail is stored/displayed.
- **Source:** [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py), [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py).

### SAVE-003 — Browser save action

- **Actor / status / owner:** authenticated reader; **fact**; save browser view/service/JS.
- **Entry / validated input:** `POST /articles/<id>/save/`, CSRF; if echoed article ID or URL is supplied, each must exactly match the server Article, but either or both may be omitted; optional AJAX/`next`.
- **Output / presentation:** JSON/flash; confirmed remote save removes card, warning/rejection keeps it; non-JS redirects.
- **State / side effects:** SAVE-001 then SAVE-002; newsletter denied.
- **Failure:** identity mismatch 400 actionable JSON/non-AJAX; policy rejection safe but session AJAX uses HTTP 200; redirect gap WEB-018.
- **Mobile / accessibility:** progressive native form; live status/alert; 390px target-only removal tested.
- **Test evidence:** `test_article_actions.py`, state propagation, policy and mobile suites.
- **Known gaps / expected failures:** remote save blocks request; no browser unsave.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`article-actions.js`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/js/article-actions.js).

### SAVE-004 — API unsave and metadata semantics

- **Actor / status / owner:** bearer client; **fact**; saved-state API/model.
- **Entry / validated input:** save body notes and nullable finite score 0..5; false/DELETE unsave; state alias contract is API-009.
- **Output / presentation:** representation reports independent read/save state and, for saves, Linkding result plus local metadata.
- **State / side effects:** metadata updates after save service in a second phase; unsave deletes local row only.
- **Failure:** metadata is forbidden when unsaving; metadata failure can occur after local/external save.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API validation, known-correctness native-false/unsave, article state propagation.
- **Known gaps / expected failures:** no atomicity across remote/local/metadata phases.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

## Machine adapters: legacy digest, bearer API, webhook, and signed actions

### API-001 — Legacy session digest

- **Actor / status / owner:** authenticated browser/session client; **fact**; browser views.
- **Entry / validated input:** permissive `/api/digest/today.json`; method/query/body are ignored rather than strictly validated.
- **Output / presentation:** JSON `title`, local date and Today unread/unsaved articles with feed/category strings, timestamps, summary and state flags.
- **State / side effects:** reads state; preference may lazy-create through card helper.
- **Failure:** anonymous request redirects to login, not JSON 401; errors do not use v1 envelope.
- **Mobile / accessibility:** N/A JSON; mobile hard-reload test compares its Article IDs.
- **Test evidence:** digest JSON tests and mobile parity test.
- **Known gaps / expected failures:** legacy method/input/error permissiveness is not a v1 promise.
- **Source:** [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py), [`urls.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/urls.py).

### API-002 — Bearer token storage and lifecycle

- **Actor / status / owner:** shell operator and API client; **fact**; ApiToken model/command.
- **Entry / validated input:** `create_api_token USER --name NAME`; existing user; API auth accepts generated raw key.
- **Output / presentation:** 32-byte URL-safe raw token printed once; DB stores SHA-256 hash, 12-char prefix, name, timestamps and active flag.
- **State / side effects:** same user/name is deleted then replaced; successful API authentication updates `last_used_at` before endpoint validation/work.
- **Failure:** missing user raises CommandError; create failure after delete can revoke old token; inactive token/user rejects.
- **Mobile / accessibility:** N/A CLI/JSON.
- **Test evidence:** basic API authentication/envelope tests; no command/token-lifecycle focused module.
- **Known gaps / expected failures:** no expiry, scopes, per-token capability, rotation grace or use-success audit.
- **Source:** [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py), [`create_api_token.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/management/commands/create_api_token.py).

### API-003 — Bearer authentication, methods, and error envelope

- **Actor / status / owner:** active token for active user; **fact**; API decorator/auth boundary.
- **Entry / validated input:** `Authorization` case-insensitive `Bearer` or compatibility `Token` plus nonempty key; endpoint method checked before auth.
- **Output / presentation:** expected errors `{error:{code,message,fields?}}`; 401 includes `WWW-Authenticate: Bearer`; stable 400/401/403/404/405/409/422/503 classes.
- **State / side effects:** valid authentication updates token `last_used_at`, even if later endpoint validation fails.
- **Failure:** missing/malformed/inactive credentials 401; unsupported method 405 before auth; unanticipated process/DB/network failures may be Django 500.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** `test_api_validation.py` method/auth/envelope/resource matrices.
- **Known gaps / expected failures:** Token alias/casing/inactive/last-used details lack focused tests; every active token has global mutation authority.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`api_validation.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api_validation.py).

### API-004 — Strict request input contract

- **Actor / status / owner:** bearer, signed, or webhook client as applicable; **fact**; API parser/validation.
- **Entry / validated input:** nonempty body must be UTF-8 JSON object with `application/json` or `application/*+json`; rejects duplicate/unknown fields, NaN/Infinity, malformed/oversized/nonobject JSON, repeated/unknown/excess query fields, wrong primitive types. Bodyless operations accept empty body plus legacy zero-field multipart boundary.
- **Output / presentation:** normalized 400 or semantic 422; dates canonical ISO/ordered, IDs positive signed-64-bit, URLs credential-free HTTP(S), score finite 0..5.
- **State / side effects:** validation precedes endpoint writes except auth timestamp.
- **Failure:** stable safe envelope; fields included for model validation.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** extensive `test_api_validation.py` request matrices and known-correctness boolean regressions.
- **Known gaps / expected failures:** default Django upload limits govern requests; programming failures remain outside envelope.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`api_validation.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api_validation.py).

### API-005 — Article/feed/category representations and capabilities

- **Actor / status / owner:** bearer client; **fact**; API serializers/save policy.
- **Entry / validated input:** ORM objects and authenticated user state.
- **Output / presentation:** feed identity/metadata/category/activity/last-fetch; category ID/name/slug; Article identity/content/timestamps, nested feed, read/save flags, per-article `capabilities` and `actions`. Newsletter omits save action and reports `save_not_allowed`.
- **State / side effects:** serializer queries read/save/capability state; no mutation.
- **Failure:** caller maps missing resources; serialization exceptions may 500.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API happy paths and newsletter capability/query-count tests.
- **Known gaps / expected failures:** exact full payload/order is only partially snapshot-asserted.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### API-006 — Morning briefing

- **Actor / status / owner:** bearer client; **fact**; briefing API.
- **Entry / validated input:** `GET /api/v1/briefing/morning/`; no query/body.
- **Output / presentation:** Today unread/unsaved Articles, title/date, per-article capabilities/actions plus legacy top-level mark/save URL templates.
- **State / side effects:** read-only except token timestamp; preference/card queries.
- **Failure:** shared API errors; newsletter generic top-level save template remains but capability denies it.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** `test_api.py` briefing and newsletter capability/query-count tests.
- **Known gaps / expected failures:** clients must not apply generic template without per-article capability.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md).

### API-007 — Article collection

- **Actor / status / owner:** bearer client; **fact**; article-list API/query helpers.
- **Entry / validated input:** `GET /api/v1/articles/`; `period=today|week|month` default today or paired ordered `start/end`; positive `feed_id`; lowercase boolean `include_read/include_saved`; no body/other query.
- **Output / presentation:** `period,start,end,articles`; explicit dates override window though period field remains.
- **State / side effects:** read-only except token timestamp; independently filters effective read/local saved.
- **Failure:** missing Feed 404; incomplete/wrong query 400; invalid period/date ordering 422.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API validation query matrix; state propagation exercises output across mutations.
- **Known gaps / expected failures:** unpaginated arbitrary date windows have no configured cap; success combinations lightly covered.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py).

### API-008 — Article read resource

- **Actor / status / owner:** bearer client; **fact**; read-state API.
- **Entry / validated input:** `POST|PATCH /api/v1/articles/<id>/read/`; optional strict boolean `is_read`, omitted defaults true.
- **Output / presentation:** current Article representation including independent saved state.
- **State / side effects:** upserts user/Article explicit read state.
- **Failure:** unknown/wrong/null input 400; missing 404; model 422; integrity race 409.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API happy path, validation, known boolean regression, state propagation.
- **Known gaps / expected failures:** no explicit transaction; PATCH/default-false/race coverage is limited.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`test_api_validation.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_api_validation.py).

### API-009 — Article saved resource

- **Actor / status / owner:** bearer client; **fact**; saved-state API/save service.
- **Entry / validated input:** `POST|PATCH /api/v1/articles/<id>/saved/`; `is_saved` or alias `saved` (not both), omitted true; optional notes and nullable finite score 0..5. `DELETE` requires no body.
- **Output / presentation:** Article plus local/Linkding saved result; false/DELETE returns unsaved Article preserving read state.
- **State / side effects:** SAVE-001/002/004; delete is local only.
- **Failure:** newsletter 422 `save_not_allowed`; bad fields 400/422; missing 404; integrity 409; Linkding failure remains HTTP 200 local success and must be inspected.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API happy/validation, policy, correctness and propagation suites.
- **Known gaps / expected failures:** external outage response at bearer boundary and multi-phase atomicity are not deeply asserted.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### API-010 — Period mark-read resource

- **Actor / status / owner:** bearer client; **fact**; API/read command.
- **Entry / validated input:** `POST /api/v1/mark-period-read/`; scope day default/week/month; optional paired ordered canonical dates.
- **Output / presentation:** marked scope/start/end summary.
- **State / side effects:** in one transaction materializes read states through cutoff and upserts period marker.
- **Failure:** 400/422 invalid input; duplicate/integrity 409 with rollback.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API validation and rollback tests; state propagation.
- **Known gaps / expected failures:** explicit-window happy path/cutoff concurrency are limited; DB accepts invalid direct marker shapes (WEB-019).
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`views.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/views.py).

### API-011 — Feed collection

- **Actor / status / owner:** bearer client; **fact**; feed collection API/discovery service.
- **Entry / validated input:** `GET|POST /api/v1/feeds/`; GET no input; POST required credential-free HTTP(S) `feed_url`, optional title/site/description/category/activity; blank title discovers.
- **Output / presentation:** ordered feed list; POST 201 created or 200 updated with `created` flag.
- **State / side effects:** creates/updates global Feed by exact URL; may fetch metadata synchronously.
- **Failure:** strict 400/404/422/409 and classified discovery 400 with no write.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API feed tests and validation/no-write matrices.
- **Known gaps / expected failures:** omitted optional POST fields may overwrite existing metadata/category/activity defaults.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### API-012 — Feed detail/update/deactivate

- **Actor / status / owner:** bearer client; **fact**; feed-detail API/model.
- **Entry / validated input:** `GET|PATCH|DELETE /api/v1/feeds/<id>/`; PATCH allows partial feed fields; GET/DELETE bodyless.
- **Output / presentation:** feed representation.
- **State / side effects:** PATCH validates then updates; DELETE soft-deactivates and preserves Articles.
- **Failure:** missing 404; URL/category/model 400/422; duplicate URL 409. Empty PATCH is accepted and saves.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** API validation/conflict/resource tests.
- **Known gaps / expected failures:** success/DELETE payloads are sparsely asserted.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### API-013 — Feed mark-read resource

- **Actor / status / owner:** bearer client; **fact**; API/read command.
- **Entry / validated input:** `POST /api/v1/feeds/<id>/mark-read/`; no query/body; existing Feed.
- **Output / presentation:** marked result with nested Feed.
- **State / side effects:** atomic explicit read materialization through cutoff and feed marker upsert, user/feed scoped.
- **Failure:** missing 404; duplicate/integrity 409 rollback.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** validation rollback and state-propagation isolation tests.
- **Known gaps / expected failures:** direct happy payload assertion is limited; WEB-019 affects database direct writes.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`test_article_state_propagation.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_article_state_propagation.py).

### API-014 — Category collection

- **Actor / status / owner:** bearer client; **fact**; category API/model.
- **Entry / validated input:** `GET|POST /api/v1/categories/`; POST requires nonblank strings `name`,`slug` and model slug/length validity.
- **Output / presentation:** ordered list; 201 create; exact same slug+name idempotent 200/created false.
- **State / side effects:** globally creates Category.
- **Failure:** same slug/different name or same name conflict 409; bad type 400; semantic/model 422.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** category validation/conflict tests.
- **Known gaps / expected failures:** list ordering/success payload lightly tested; no per-user authorization.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### API-015 — Preferences resource

- **Actor / status / owner:** bearer client; **fact**; preferences API/model.
- **Entry / validated input:** `GET|PATCH /api/v1/preferences/`; PATCH optional valid theme and strict compact/focus booleans.
- **Output / presentation:** all three preference fields.
- **State / side effects:** GET lazily creates defaults; PATCH validates all inputs before get/create/save, preventing an invalid request from creating row.
- **Failure:** bad type 400; unknown theme 422; no partial mutation.
- **Mobile / accessibility:** N/A JSON; corresponding browser presentation is WEB-016.
- **Test evidence:** API happy path, validation and native-false known-correctness tests.
- **Known gaps / expected failures:** lazy-create GET is lightly documented externally.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`models.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/models.py).

### API-016 — Refresh resource

- **Actor / status / owner:** bearer client; **fact**; refresh API/service.
- **Entry / validated input:** `POST /api/v1/refresh/`; no query/body.
- **Output / presentation:** aggregate checked/attempted/succeeded/failed/skipped/new-feed/created/updated plus each Feed status/count/duration/error/retry.
- **State / side effects:** synchronous global refresh.
- **Failure:** per-feed failure remains HTTP 200 result; only unexpected infrastructure failure may 500.
- **Mobile / accessibility:** N/A JSON.
- **Test evidence:** `test_api.py` partial failure/backoff aggregate; strict no-input tests.
- **Known gaps / expected failures:** token scopes do not restrict expensive refresh; clients must inspect result rather than HTTP alone.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### API-017 — Postmark webhook adapter

- **Actor / status / owner:** Postmark holding path secret; **fact**; webhook API/newsletter service.
- **Entry / validated input:** `POST /api/postmark/inbound/<secret>/`; method first, configured secret constant-time match before no-query/strict JSON object validation.
- **Output / presentation:** `{id,created}`, 201 new or 200 MessageID dedupe; expected errors use API envelope.
- **State / side effects:** NEWS-001 domain writes.
- **Failure:** absent/bad secret 403; method 405; malformed/service input 400; model 422; integrity 409. Secret is path-embedded.
- **Mobile / accessibility:** N/A webhook.
- **Test evidence:** newsletter webhook tests and API validation/auth/error tests.
- **Known gaps / expected failures:** NEWS-002; no provider signature/source/rate/replay control; path may be logged upstream.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### API-018 — Signed save-and-go and period-and-go

- **Actor / status / owner:** caller possessing URL; mutation executes as configured active username; **fact**; signed API adapters.
- **Entry / validated input:** GET-only `/api/v1/articles/<id>/save-and-go/?sig=…` HMAC over `save-and-go:<id>`; `/api/v1/mark-period-read-and-go/?scope=…&sig=…` HMAC over raw/default day/week/month. Signature is checked before full query parsing.
- **Output / presentation:** successful save redirects external Article URL; period redirects Today. Missing resource 404, bad/missing sig 403, invalid signed scope 422, absent/inactive actor 503, conflict 409.
- **State / side effects:** save service or atomic current-period mark under configured user.
- **Failure:** newsletter save 422; malformed/excess valid-signature input is strictly rejected.
- **Mobile / accessibility:** redirect link usable by any client; N/A JSON errors.
- **Test evidence:** signed happy paths, strict method/query/body/error tests and newsletter-policy tests.
- **Known gaps / expected failures:** replay/mutating-GET defect is API-019.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`test_api_validation.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_api_validation.py).

### API-019 — Replayable mutating signed GETs

- **Actor / status / owner:** anyone with a signed URL; **known-defect**; signed capability/security boundary.
- **Entry / validated input:** deterministic global HMAC and configured username; no expiry, nonce, user binding in signature, record or one-use state.
- **Output / presentation:** scanners, previews or repeated callers can replay mutation; period URL targets changing current windows over time.
- **State / side effects:** repeated saves/marks execute as shared actor; no capability-use audit/revocation.
- **Failure:** secrecy of URL is the only replay barrier; valid leaked links remain valid while secret/config persists.
- **Mobile / accessibility:** N/A security semantics.
- **Test evidence:** signature correctness/error tests; no expected-failure because current replay behavior is directly implemented/documented.
- **Known gaps / expected failures:** no implemented expiring POST replacement is implied.
- **Source:** [`api.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/api.py), [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md).

## Configuration, static/build, deployment, and operations

### OPS-001 — Development/production mode and fail-closed identity

- **Actor / status / owner:** developer/operator/process bootstrap; **fact**; Django settings.
- **Entry / validated input:** `DJANGO_ENV` exactly development/production; strict `DJANGO_DEBUG`; in production strong nondevelopment secret, public multi-label DNS hosts and exact matching HTTPS CSRF origins.
- **Output / presentation:** development defaults debug/local hosts/SQLite; production transport/security and database validation.
- **State / side effects:** settings import only.
- **Failure:** invalid/missing production values abort with variable names but not values; Compose defaults intentionally fail closed until explicit safe values.
- **Mobile / accessibility:** N/A configuration.
- **Test evidence:** exhaustive subprocess matrix in `test_production_settings.py`.
- **Known gaps / expected failures:** validation cannot establish DNS ownership/Funnel policy.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`.env.example`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.env.example).

### OPS-002 — Database configuration and persistence

- **Actor / status / owner:** developer/operator; **fact**; settings/PostgreSQL/Compose.
- **Entry / validated input:** `DATABASE_URL` precedence for direct deployment or complete discrete `POSTGRES_DB/USER/PASSWORD/HOST/PORT`; production only PostgreSQL and nondevelopment password; port 1..65535.
- **Output / presentation:** Django DB config with `CONN_MAX_AGE=600`; development fallback SQLite; Compose PostgreSQL 17 Alpine named volume.
- **State / side effects:** all durable application models reside in configured DB.
- **Failure:** partial/blank/bad URL/scheme/port abort; changing `.env` does not rotate existing volume role.
- **Mobile / accessibility:** N/A.
- **Test evidence:** settings DB matrix and Compose tests.
- **Known gaps / expected failures:** SQLite tests do not prove PostgreSQL null uniqueness/locks/concurrency; named volume is not a backup.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml).

### OPS-003 — Proxy, HTTPS, headers, and cookies

- **Actor / status / owner:** Funnel/proxy/operator and web client; **fact**; Django security settings/host topology.
- **Entry / validated input:** production request; trusts only `X-Forwarded-Proto=https`, exact allowed Host/origin.
- **Output / presentation:** direct HTTP 301; trusted HTTPS reaches normal anonymous 302; secure cookies; nosniff, frame deny, same-origin referrer/opener controls.
- **State / side effects:** request/cookie behavior.
- **Failure:** wrong/missing proxy scheme redirects; invalid host rejected.
- **Mobile / accessibility:** transport is viewport-independent.
- **Test evidence:** production settings request/header/cookie tests and documented smoke commands.
- **Known gaps / expected failures:** host Funnel/ACL/firewall/cert/rate-limit configuration is outside repository evidence.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`AGENTS.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/AGENTS.md).

### OPS-004 — HSTS policy

- **Actor / status / owner:** operator/security boundary; **deferred**; Django settings/operator documentation.
- **Entry / validated input:** production security settings.
- **Output / presentation:** `SECURE_HSTS_SECONDS=0`; only deploy warning `security.W004` is intentionally silenced.
- **State / side effects:** browsers receive no project HSTS policy.
- **Failure:** downgrade resistance depends on current entry path; enabling without recovery validation could lock out paths.
- **Mobile / accessibility:** N/A.
- **Test evidence:** deploy check and settings assertions.
- **Known gaps / expected failures:** staged rollout is documented as future operator choice; it does not exist.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md).

### OPS-005 — Integration and fetch configuration surface

- **Actor / status / owner:** operator; **fact**; settings/Compose.
- **Entry / validated input:** `LINKDING_URL/TOKEN`, `AGENT_LINK_SECRET/USERNAME`, `POSTMARK_INBOUND_SECRET/EMAIL`, `FEED_REFRESH_SECONDS`, five `FEED_FETCH_*`, `FEED_REFRESH_LOG_LEVEL`.
- **Output / presentation:** supplies SAVE/NEWS/API/ING boundaries; the five `FEED_FETCH_*` values are parsed by Django settings and semantically checked at fetch use, while shell-only `FEED_REFRESH_SECONDS` defaults to 3600 without Django validation.
- **State / side effects:** environment only; secrets are not stored in repository.
- **Failure:** absent integration credentials defer failure to endpoint; invalid fetch numeric syntax aborts and invalid semantic fetch policy yields `invalid_policy`. Invalid `FEED_REFRESH_SECONDS` makes `sleep` fail inside an unconditional loop, causing an immediate tight refresh loop rather than requiring a container restart.
- **Mobile / accessibility:** N/A.
- **Test evidence:** fetch policy, settings and Compose tests; endpoint tests override integrations.
- **Known gaps / expected failures:** Linkding URL has no startup/HTTPS validation; log level is neither example-documented nor Compose-passed.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml), [`.env.example`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.env.example).

### OPS-006 — Static assets and frontend build contract

- **Actor / status / owner:** image builder/Django/WhiteNoise; **fact**; static settings/assets.
- **Entry / validated input:** tracked `static/` CSS/JS/SVG and template `{% static %}` references; image build settings use development defaults.
- **Output / presentation:** `collectstatic` generates ignored `staticfiles/`; compressed manifest WhiteNoise storage serves `static/` URL assets.
- **State / side effects:** generated build output only.
- **Failure:** missing manifest reference/collectstatic error fails build or rendering.
- **Mobile / accessibility:** CSS owns responsive/focus/theme presentation; SVG has descriptive template alt.
- **Test evidence:** static-file test base, browser rendering and deploy check; no dedicated production static HTTP smoke.
- **Known gaps / expected failures:** generated assets are not source; no frontend bundler/minifier beyond WhiteNoise compression.
- **Source:** [`Dockerfile`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/Dockerfile), [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`site.css`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/css/site.css), [`firehose-masthead.svg`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/static/img/firehose-masthead.svg).

### OPS-007 — Locked application-dependency image and repository build controls

- **Actor / status / owner:** image builder/operator/developer; **fact**; Dockerfile, uv lock, build/ignore/version and pre-commit configuration.
- **Entry / validated input:** mutable-tag Python 3.12 Bookworm-slim base, `pyproject.toml`, `uv.lock`, and source context excluding secrets/VCS/SQLite/caches/generated static/tests. `.python-version` selects 3.12 locally; `.gitignore` excludes local secret/generated/session artifacts; pre-commit runs YAML validity, EOF, and trailing-whitespace checks.
- **Output / presentation:** frozen no-dev application dependency environment under `/app/.venv`, source, collected static, exposed 8000, and Gunicorn WSGI default. Locked Python dependencies improve repeatability, but the complete image is not reproducible while the base tag is unpinned.
- **State / side effects:** immutable image layers.
- **Failure:** lock/dependency/copy/collectstatic errors fail build.
- **Mobile / accessibility:** N/A image; includes shared frontend assets.
- **Test evidence:** production settings checks; no dedicated image-build/container test.
- **Known gaps / expected failures:** no base-image digest pin, SBOM/scan, non-root USER, resource/read-only settings or multi-arch publish contract; pre-commit is not a full test/type/Markdown gate.
- **Source:** [`Dockerfile`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/Dockerfile), [`.dockerignore`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.dockerignore), [`.gitignore`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.gitignore), [`.python-version`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.python-version), [`.pre-commit-config.yaml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.pre-commit-config.yaml), [`pyproject.toml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/pyproject.toml), [`uv.lock`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/uv.lock).

### OPS-008 — Compose topology, startup, and health

- **Actor / status / owner:** Docker Compose/operator; **fact**; Compose runtime.
- **Entry / validated input:** `.env` interpolation; DB environment/volume; web and worker production settings.
- **Output / presentation:** PostgreSQL health → web migrate+Gunicorn → TCP health → refresh loop; all restart unless-stopped; web published only on host loopback.
- **State / side effects:** web auto-applies migrations; worker refreshes then sleeps; DB alone uses named durable volume.
- **Failure:** DB/migration/listen failure blocks dependents; TCP health proves only socket accept; shell exit restarts worker.
- **Mobile / accessibility:** N/A topology.
- **Test evidence:** Compose text/render/order tests in `test_production_settings.py`.
- **Known gaps / expected failures:** no container integration test, resource limits, explicit networks/egress, migration backup gate or semantic readiness.
- **Source:** [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml), [`test_production_settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_production_settings.py).

### OPS-009 — Canonical deployment preflight

- **Actor / status / owner:** operator on/over SSH to `daily-firehose`; **fact**; operator procedure.
- **Entry / validated input:** canonical `/home/ubuntu/daily-firehose`, fast-forward pull, preserved production `.env`/DB credential; start DB, fresh image `check --deploy --fail-level WARNING`, real DB connection.
- **Output / presentation:** only after both probes pass, `docker compose up -d --build` recreates changed app services and preserves DB/volume.
- **State / side effects:** pull/build/check/connect/recreate; no volume deletion.
- **Failure:** SSH/preflight/connection failure stops; credential recovery is OPS-012.
- **Mobile / accessibility:** N/A operations.
- **Test evidence:** deploy-check settings test; procedure itself is manual.
- **Known gaps / expected failures:** no CI/CD or host provisioning-as-code.
- **Source:** [`AGENTS.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/AGENTS.md), [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md).

### OPS-010 — Post-deploy verification

- **Actor / status / owner:** operator; **fact**; operator procedure/proxy contract.
- **Entry / validated input:** `docker compose ps/logs`, direct loopback Host HEAD, trusted proxy-header HEAD, public HTTPS HEAD.
- **Output / presentation:** db/web/worker Up; Gunicorn/migration logs; direct 301; proxy/public anonymous 302 to login.
- **State / side effects:** observational requests/log reads only.
- **Failure:** unexpected state/status/log indicates deployment problem; no automated rollback.
- **Mobile / accessibility:** N/A smoke; does not render authenticated UI.
- **Test evidence:** proxy statuses mirror `test_production_settings.py`; public smoke is manual.
- **Known gaps / expected failures:** no authenticated/static/feed freshness/Linkding/Postmark semantic smoke.
- **Source:** [`AGENTS.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/AGENTS.md), [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md).

### OPS-011 — Refresh logs and observability

- **Actor / status / owner:** operator/process supervisor; **fact**; services logger/command/Compose stdout.
- **Entry / validated input:** refresh completion and unexpected errors; logger level defaults INFO.
- **Output / presentation:** plain console safe single-line feed ID/title/status/duration/counts/error/failure/retry; unexpected traceback; command summary.
- **State / side effects:** per-feed DB health timestamps/backoff supplement logs.
- **Failure:** invalid log level can break logging; canonical Compose cannot pass `.env` log-level variable; no retention configured.
- **Mobile / accessibility:** N/A operations.
- **Test evidence:** refresh log content/traceback/command sanitization tests.
- **Known gaps / expected failures:** no metrics/tracing/error collector/access log/dashboard/alert.
- **Source:** [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`services.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/services.py).

### OPS-012 — Database credential recovery

- **Actor / status / owner:** production operator/PostgreSQL; **fact**; README/AGENTS procedure.
- **Entry / validated input:** stopped app/full restart, running DB/volume, existing `.env`; restore prior password or interactive `psql \password` then update `.env`.
- **Output / presentation:** real Django connectivity probe succeeds before app restart.
- **State / side effects:** optionally rotates DB role credential without shell argument/history; preserves data volume.
- **Failure:** mismatch keeps application stopped; never delete volume or print/pass password in command.
- **Mobile / accessibility:** N/A operations.
- **Test evidence:** no automated recovery drill; settings detect mismatch only at real connection.
- **Known gaps / expected failures:** `.env` permission/rotation history not evidenced.
- **Source:** [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md), [`AGENTS.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/AGENTS.md).

### OPS-013 — Application rollback

- **Actor / status / owner:** production operator; **fact**; operator procedure.
- **Entry / validated input:** known-good code revision and preserved `.env`/volumes.
- **Output / presentation:** rebuild/recreate application services at older code.
- **State / side effects:** database schema/data remains as-is unless separately reversed/restored.
- **Failure:** code checkout alone cannot undo migration; require migration-specific verified reversal or real known-good backup.
- **Mobile / accessibility:** N/A operations.
- **Test evidence:** no automated rollback drill.
- **Known gaps / expected failures:** automatic migration has no backup gate or migration-specific playbook.
- **Source:** [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md), [`AGENTS.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/AGENTS.md).

### OPS-014 — Backup and worker freshness coverage

- **Actor / status / owner:** infrastructure operator; **known-defect**; operational storage/worker-health boundary.
- **Entry / validated input:** local `postgres-data` volume and long-running refresh container.
- **Output / presentation:** persistence and restart state exist, but no backup creation/schedule/off-host encrypted copy/integrity test/restore drill/RPO/RTO; no worker heartbeat, semantic healthcheck, staleness threshold or alert.
- **State / side effects:** host/volume loss may lose all data; worker can remain Up while stale/hung.
- **Failure:** Compose health/status cannot detect these conditions; historical incident demonstrates stale ingestion class.
- **Mobile / accessibility:** N/A operations.
- **Test evidence:** none for backup/heartbeat; architecture and incident explicitly bound the gaps.
- **Known gaps / expected failures:** external backup/monitoring may exist but is not evidenced; this record does not claim a deferred implementation.
- **Source:** [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml), [`README.md`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/README.md), [`incident`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docs/incidents/2026-08-11-mobile-today-empty.md).

### OPS-015 — Production-external controls and authorization intent

- **Actor / status / owner:** operator/product boundary outside tracked code; **unknown**; Funnel/host/network/organizational policy.
- **Entry / validated input:** host provisioning, Funnel/ACL/firewall, egress policy, log redaction, Docker log policy, backups/monitoring, and whether accounts/tokens are mutually trusted.
- **Output / presentation:** repository evidence cannot establish these controls or the intended single-owner versus multi-user authorization contract.
- **State / side effects:** unknown external configuration may strengthen or weaken in-repo boundaries.
- **Failure:** do not infer protection, backup, monitoring, webhook redaction or least privilege from absence/presence of app code.
- **Mobile / accessibility:** N/A.
- **Test evidence:** none possible from current repository suite; architecture labels these unknown.
- **Known gaps / expected failures:** every bearer token can currently mutate global Feeds/Categories and refresh; whether that is acceptable is unknown.
- **Source:** [`current-state architecture`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docs/architecture/current-state.md), [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml).

### Complete configuration-name matrix

No values are reproduced. This matrix verifies reachability as well as settings parsing; a variable present in `.env` does not reach a container unless Compose passes it.

| Names | Owning catalog ID | Reachability and validation summary |
| --- | --- | --- |
| `DJANGO_ENV`, `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | OPS-001, OPS-003 | web/worker/direct; strict fail-closed production identity and transport inputs. |
| `DATABASE_URL` | OPS-002 | direct/non-Compose only; takes precedence and must be complete PostgreSQL in production. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | OPS-002 | DB/app services as applicable; discrete all-or-none validation. |
| `WEB_PORT` | OPS-008 | Compose host publication only; Django does not read it. |
| `LINKDING_URL`, `LINKDING_TOKEN` | OPS-005, SAVE-002 | web/worker settings; missing token is local-save/remote-failure, URL not HTTPS-validated. |
| `AGENT_LINK_SECRET`, `AGENT_LINK_USERNAME` | OPS-005, API-018–API-019 | web/worker; absent configuration disables signed actions safely. |
| `POSTMARK_INBOUND_SECRET`, `POSTMARK_INBOUND_EMAIL` | OPS-005, API-017 | web only in canonical Compose; secret gates webhook and address is displayed to authenticated users. |
| `FEED_REFRESH_SECONDS` | OPS-005, OPS-008 | worker shell only; default 3600 and not settings-validated. |
| `FEED_FETCH_CONNECT_TIMEOUT_SECONDS`, `FEED_FETCH_READ_TIMEOUT_SECONDS`, `FEED_FETCH_TOTAL_TIMEOUT_SECONDS`, `FEED_FETCH_MAX_BYTES`, `FEED_FETCH_MAX_REDIRECTS` | OPS-005, ING-004 | web/worker/direct; syntax at settings import and semantic policy at fetch use. |
| `FEED_REFRESH_LOG_LEVEL` | OPS-005, OPS-011 | read by Django settings, but absent from `.env.example` and canonical Compose pass-through. |
| `DJANGO_SETTINGS_MODULE` | OPS-001 | fixed by manage.py, WSGI and ASGI process entry points, not normal `.env` configuration. |
| `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`, `PATH` | OPS-007 | image-fixed process environment, not operator `.env` settings. |

Source: [`settings.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/daily_firehose/settings.py), [`docker-compose.yml`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/docker-compose.yml), [`Dockerfile`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/Dockerfile), [`.env.example`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/.env.example).

## Expected-failure traceability

Exactly eight tests at the snapshot use `unittest.expectedFailure`; all are current defects, not passing implementations.

| Expected-failure test | Catalog ID | Current contradiction |
| --- | --- | --- |
| `test_refresh_reconciles_changed_guid_for_same_url` | ING-006 | GUID upsert conflicts with stable URL uniqueness. |
| `test_malformed_opml_returns_form_feedback_without_writes` | ING-012 | malformed XML returns server error, not form feedback. |
| `test_mark_article_rejects_external_next_redirect` | WEB-018 | external `next` is redirected to. |
| `test_postmark_issue_failure_rolls_back_article` | NEWS-002 | Article remains after issue failure. |
| `test_database_rejects_feed_marker_without_feed` | WEB-019 | invalid feed marker is accepted. |
| `test_database_rejects_period_marker_without_dates` | WEB-019 | invalid period marker is accepted. |
| `test_database_rejects_period_marker_with_feed` | WEB-019 | invalid mixed marker is accepted. |
| `test_database_rejects_reversed_period_marker` | WEB-019 | reversed dates are accepted. |

Source: [`test_known_correctness_failures.py`](https://github.com/feoh/daily-firehose/blob/03965d98aa51522a98266df28aa2ba45e80c03e7/feeds/tests/test_known_correctness_failures.py).

## Test-module traceability

All 15 `test_*.py` modules are mapped. Shared `feeds/tests/support/` builders, HTTP doubles and fixtures support all mapped modules but are not executable test modules.

| Test module | Primary catalog IDs |
| --- | --- |
| `test_api.py` | API-002–API-003, API-006, API-008–API-009, API-015–API-016, API-018, ING-003 |
| `test_api_validation.py` | API-003–API-004, API-007–API-019, SAVE-004, NEWS-001 |
| `test_article_actions.py` | WEB-007–WEB-008, WEB-012, SAVE-002–SAVE-003 |
| `test_article_state_propagation.py` | WEB-004–WEB-006, WEB-008–WEB-011, SAVE-001–SAVE-004, API-008–API-010, API-013 |
| `test_builders_and_fixtures.py` | ING-002, ING-004–ING-005, NEWS-001, NEWS-004, SAVE-001; test-support contracts |
| `test_digest_views.py` | WEB-001–WEB-005, WEB-008–WEB-011, WEB-016–WEB-017, API-001 |
| `test_feed_fetch.py` | ING-003–ING-005, OPS-005 |
| `test_feed_refresh.py` | ING-005, ING-007–ING-010, OPS-011 |
| `test_feed_views.py` | WEB-015, ING-001, ING-003 |
| `test_known_correctness_failures.py` | API-004, API-008–API-009, API-015, ING-006, ING-012, WEB-018–WEB-019, NEWS-002, NEWS-005 |
| `test_mobile_today_browser.py` | AUTH-001, WEB-001–WEB-002, WEB-007–WEB-008, WEB-012, WEB-016, WEB-020–WEB-021, SAVE-003, API-001 |
| `test_newsletter_save_policy.py` | AUTH-005, NEWS-005, SAVE-001–SAVE-004, API-005–API-006, API-009, API-018 |
| `test_newsletters.py` | NEWS-001, NEWS-003–NEWS-005, API-017, WEB-007 |
| `test_opml.py` | ING-011 |
| `test_production_settings.py` | AUTH-003, OPS-001–OPS-003, OPS-005, OPS-008–OPS-010 |

## Coverage summary

Snapshot inventory counts:

- **82** stable detailed feature/contract IDs: **72 fact**, **8 known-defect**, **1 deferred**, **1 unknown**.
- **31** first-party routes in `feeds/urls.py`, plus framework-mounted login, logout and admin: every route is named by an entry above.
- **15/15** executable test modules mapped; **191** `def test_...` methods at the snapshot.
- **8/8** expected failures mapped to stable IDs; none is represented as fixed.
- **9/9** app models and **2/2** management commands covered.
- Configuration covers every application/operator variable in the architecture inventory: Django mode/security/hosts/CSRF; URL/discrete database; web port; Linkding; signed actions; Postmark; refresh schedule/fetch/logging; process bootstrap and image-fixed Python variables.

Mechanical review from repository root:

```bash
python - <<'PY'
from collections import Counter
from pathlib import Path
import ast
import re
p = Path("docs/features/catalog.md").read_text()
matches = list(re.finditer(
    r"^### ((?:AUTH|WEB|ING|NEWS|SAVE|API|OPS)-\d{3}) —", p, re.M))
records = [match.group(1) for match in matches]
assert len(records) == len(set(records)) == 82
blocks = [p[match.start():matches[index + 1].start()]
          if index + 1 < len(matches) else p[match.start():]
          for index, match in enumerate(matches)]
required = ["Actor / status / owner", "Entry / validated input", "Output / presentation",
            "State / side effects", "Failure", "Mobile / accessibility",
            "Test evidence", "Known gaps / expected failures", "Source"]
assert all(all(label in block for label in required) for block in blocks)
statuses = Counter(re.search(r"\*\*(fact|known-defect|deferred|unknown)\*\*", b).group(1)
                   for b in blocks)
assert statuses == Counter({"fact": 72, "known-defect": 8,
                            "deferred": 1, "unknown": 1})
tests = {x.name for x in Path("feeds/tests").glob("test_*.py")}
mapped = set(re.findall(r"\| `(test_[^`]+\.py)` \|", p))
assert tests == mapped and len(tests) == 15
methods = sum(1 for path in Path("feeds/tests").glob("test_*.py")
              for node in ast.walk(ast.parse(path.read_text()))
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name.startswith("test_"))
assert methods == 191
assert Path("feeds/urls.py").read_text().count("path(") == 31
assert len([p for p in Path("feeds/management/commands").glob("*.py")
            if p.name != "__init__.py"]) == 2
assert Path("feeds/admin.py").read_text().count("@admin.register(") == 9
source = Path("feeds/tests/test_known_correctness_failures.py").read_text()
tree = ast.parse(source)
expected = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
            and any(isinstance(d, ast.Name) and d.id == "expectedFailure"
                    for d in node.decorator_list)}
expected_table = p.split("## Expected-failure traceability", 1)[1].split(
    "## Test-module traceability", 1)[0]
mapped_expected = set(re.findall(r"\| `(test_[^`]+)` \| (?:ING|WEB|NEWS)-",
                                 expected_table))
assert len(expected) == 8 and expected == mapped_expected
print(len(records), statuses, len(tests), methods, len(expected))
PY
```

The [current-state architecture](../architecture/current-state.md) remains the component/data-flow/trust/deployment companion to this behavioral catalog.
