# Plan: Postmark inbound newsletters for Daily Firehose

## Context

Daily Firehose currently ingests RSS/Atom feeds and lets articles be saved to Linkding. The goal is to add newsletter ingestion without running mail infrastructure. Daily Firehose is already reachable on the public internet via Tailscale Funnel over HTTPS, so a hosted inbound email provider can POST to the app without exposing SMTP. We agreed to use Postmark’s generated inbound email address, archive newsletter issues in Daily Firehose, render HTML newsletter bodies publicly, hide the existing generic “Save to Linkding” action for newsletter items, and avoid private-content assumptions.

## Goals / Non-goals

- Goals:
  - Receive inbound newsletter email from Postmark via an HTTPS webhook.
  - Store inbound newsletters as a distinct archived newsletter issue linked to a normal `Article` so they appear in Today/Week/Month/feed views.
  - Use a synthetic “Email Newsletters” feed for newsletter-backed articles.
  - Expose public, unguessable newsletter archive pages and mark them `noindex`.
  - Render sanitized HTML email content, with text fallback.
  - Hide the normal “Save to Linkding” button for newsletter-backed article cards.
  - Use Postmark’s generated inbound address to avoid DNS/MX setup.
- Non-goals:
  - Running or exposing an SMTP server.
  - Making newsletter pages login-required.
  - Saving newsletter archive pages to Linkding in the initial implementation.
  - Implementing per-link extraction/save-to-Linkding behavior in the initial implementation.

## Files to change

- `pyproject.toml` / `uv.lock` — add an HTML sanitization dependency such as `bleach`.
- `daily_firehose/settings.py` — add `POSTMARK_INBOUND_SECRET` and any newsletter archive/base URL settings needed.
- `.env.example` — document `POSTMARK_INBOUND_SECRET`.
- `feeds/models.py` — add a newsletter/archive model linked one-to-one to `Article`, with UUID slug and Postmark/message metadata plus HTML/text bodies.
- `feeds/migrations/` — add migrations for the newsletter model and related constraints/indexes.
- `feeds/services.py` — add helpers to create/get the synthetic newsletter feed, parse Postmark payloads, create/update newsletter-backed articles, sanitize newsletter HTML, and de-dupe by message id.
- `feeds/views.py` — add a public newsletter detail view that renders sanitized HTML/text and returns `noindex`; adjust card context if needed.
- `feeds/api.py` — add a CSRF-exempt Postmark inbound webhook endpoint authenticated by the shared secret path segment.
- `feeds/urls.py` — add routes for the Postmark webhook and public newsletter archive page.
- `templates/feeds/includes/article_card.html` — detect newsletter-backed articles, label the open action as “Read newsletter,” and hide “Save to Linkding.”
- `templates/feeds/newsletter_detail.html` — render the public newsletter archive page with sanitized HTML or text fallback and `noindex` metadata.
- `feeds/admin.py` — optionally expose newsletter issues for inspection/debugging.
- `feeds/tests.py` — add tests for webhook authentication, de-dupe, article creation, newsletter page rendering, sanitization, and hidden Linkding action.

## Ordered steps

1. Add the HTML sanitization dependency using the project’s `uv` workflow and update lockfile.
2. Add `POSTMARK_INBOUND_SECRET` to settings and `.env.example`.
3. Add the newsletter issue model with:
   - one-to-one link to `Article`
   - UUID public slug
   - unique Postmark/message id for de-duping
   - sender/recipient/subject metadata
   - raw HTML body and text body fields
   - timestamps
4. Create and apply the Django migration.
5. Implement service helpers to:
   - get or create the synthetic “Email Newsletters” feed
   - parse the relevant Postmark inbound payload fields
   - create/update the linked `Article`
   - build the public newsletter archive URL for `Article.url`
   - sanitize stored HTML for rendering
6. Add the CSRF-exempt Postmark inbound webhook endpoint at a secret-bearing URL such as `/api/postmark/inbound/<secret>/`.
7. Add the public newsletter detail route, preferably using the UUID slug, and return `X-Robots-Tag: noindex` plus a template-level robots meta tag.
8. Update article card rendering so newsletter-backed articles open as “Read newsletter” and do not show the generic “Save to Linkding” form.
9. Add tests for successful Postmark ingestion, rejected bad secrets, message-id de-dupe, newsletter detail HTML rendering/sanitization, noindex behavior, and hidden Linkding action.
10. Configure Postmark’s generated inbound address to POST to the deployed HTTPS webhook URL using the configured secret.
11. Deploy with the existing Docker Compose process after validation passes.

## Validation

- Run Django tests, including the new newsletter tests:
  - `uv run python manage.py test feeds`
- Run project validation before declaring done:
  - `uv run pre-commit run --all-files`
  - `uv run mypy .`
- Run Django checks:
  - `uv run python manage.py check`
- Manual checks:
  - POST a representative Postmark inbound payload locally and confirm a newsletter issue and article are created.
  - Visit Today/Week views and confirm the newsletter appears as an article card.
  - Confirm the newsletter card says “Read newsletter” and does not show “Save to Linkding.”
  - Visit the newsletter archive URL without logging in and confirm sanitized HTML renders and `noindex` is present.
  - Send a test email through Postmark’s generated inbound address after deployment and confirm it appears in Daily Firehose.

## Risks & unknowns

- Exact Postmark inbound payload field names and any available signature/auth features should be verified against Postmark docs while implementing.
- Rendering HTML email safely requires a careful sanitizer allowlist; too strict may break newsletter formatting, too loose may create XSS risk.
- Remote images in newsletters may load tracking pixels; we agreed to render HTML, but image proxying/blocking is not part of this initial plan.
- Public newsletter archives are unguessable and `noindex`, but not private; anyone with a link can view them.
- Some newsletters may not have useful HTML or may rely heavily on CSS that sanitization removes.
