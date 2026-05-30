## Context

The project is a personal information-flow app named **Daily Firehose**. Personal projects generally live in `~/src/personal` and use GitHub repositories under the `feoh` GitHub user, so the likely local path is `~/src/personal/daily-firehose` and the likely repository is `feoh/daily-firehose`.

Daily Firehose will start as a Django-based RSS reader web app. It should fetch feeds, show new articles for the current day, and eventually support broader daily/weekly/monthly reading workflows. The initial output target is an accessible web page, with additional agent-friendly outputs such as structured JSON or Markdown planned. The UI should prioritize readability because WCAG AA accessibility is critical. The preferred initial visual direction is a spacious reading-card layout, with user-selectable themes including Catppuccin Mocha.

## Goals / Non-goals

### Goals

- Create a Django web app for reading RSS feeds.
- Use built-in Django authentication.
- Store feeds, articles, and read state locally, likely with SQLite initially.
- Allow manually adding feed URLs and importing/exporting feed subscriptions as OPML files.
- Provide views for Today, week, month, and individual feed detail pages.
- Allow marking a day, week, month, feed, or individual article as read.
- Provide a clear **Save to Linkding** action for articles, integrating with the Linkding instance at `https://linkding.reedfish-regulus.ts.net`.
- Store and track saved articles, including article URL, title, feed, category, and timestamps, so future versions can highlight articles likely to be interesting.
- Keep the implementation simple and maintainable.
- Use a highly readable, WCAG AA-oriented UI with semantic HTML, large readable typography, visible focus states, high contrast, and clear labels.
- Include user-selectable light and dark themes, including Catppuccin Mocha as a preferred dark theme.
- Provide agent-ingestible outputs later, such as JSON and/or Markdown digest endpoints.

### Non-goals

- Do not build a complex background-worker system for the first version.
- Do not prioritize whimsical action labels over clarity; controls should use direct labels such as **Mark read**, **Mark feed read**, **Mark today read**, **Save to Linkding**, and **Open article**.
- Do not make security the primary design driver beyond using Django auth and reasonable configuration practices.
- Do not assume the first version needs multi-user product polish beyond what Django auth naturally supports.

## Files to change

- `~/src/personal/daily-firehose/` — create the new personal project directory.
- Django project package, likely `daily_firehose/` — project settings, URL routing, and shared configuration.
- Django app package, likely for feeds/readers — models, views, forms, services, and URLs for feed and article workflows.
- Templates — accessible server-rendered HTML for Today’s Firehose, week/month views, feed detail pages, OPML import/export, login/logout, and article actions.
- Static CSS — theme variables, Catppuccin Mocha support, accessible typography, spacing, focus states, and card layout styles.
- Management command — scheduled feed refresh command intended for cron or systemd timer.
- Linkding integration module/service — backend code to save article links to Linkding using configurable URL/token values.
- API or renderer endpoints — later JSON and/or Markdown digest outputs for pi or other agents to ingest.
- Project metadata and docs — dependency configuration, README, and basic setup/run instructions.

## Ordered steps

1. Create the local project at `~/src/personal/daily-firehose` and initialize it as a Git repository intended for `feoh/daily-firehose`.
2. Start a Django project using package name `daily_firehose`.
3. Add a Django app for feed/article functionality.
4. Configure built-in Django authentication, SQLite for initial storage, templates, static files, and environment-based configuration.
5. Define initial data models for feeds, articles, saved articles, categories, and read-state markers that can support individual article read state plus day/week/month/feed-level mark-read behavior.
6. Add Django admin support for managing feeds and inspecting articles.
7. Implement manual feed URL management plus OPML import/export for subscriptions.
8. Implement feed fetching/parsing as a simple service plus a Django management command suitable for cron or systemd timer usage.
9. Build the first web views:
   - Today’s Firehose
   - week view
   - month view
   - feed detail view
   - OPML import/export
   - login/logout
10. Implement article and bulk read actions:
   - mark article read/unread
   - mark feed read
   - mark day read
   - mark week read
   - mark month read
11. Implement the spacious reading-card layout as the default UI.
12. Add accessible theme support with CSS custom properties, including user-selectable light/dark themes and Catppuccin Mocha.
13. Add Linkding configuration and a **Save to Linkding** backend action for article cards.
14. Add initial agent-friendly digest output endpoints or renderers, likely JSON first and Markdown later.
15. Write setup and usage documentation, including how to configure feeds, OPML import/export, Linkding, auth, and scheduled refresh.

## Validation

- Run Django checks and migrations.
- Verify login/logout works using Django auth.
- Verify feeds can be added through Django admin and the web UI.
- Verify OPML import creates feed subscriptions and OPML export returns the configured subscriptions.
- Verify the feed refresh management command fetches and stores articles.
- Verify Today, week, month, and feed detail pages render correctly.
- Verify marking individual articles, feeds, days, weeks, and months as read updates what appears as unread.
- Verify **Save to Linkding** sends the correct article URL/title/metadata using configured Linkding settings and records the save locally with feed/category metadata.
- Validate accessibility manually against WCAG AA expectations:
  - readable font sizing and line height
  - sufficient color contrast
  - semantic headings and landmarks
  - keyboard navigation
  - visible focus states
  - clear button/link labels
- Check Catppuccin Mocha and light theme readability, overriding palette choices where needed for contrast.
- Verify JSON and/or Markdown digest output is easy for pi or other agents to ingest once implemented.

## Risks & unknowns

- Exact data model for efficient day/week/month/feed mark-read behavior needs to be finalized during implementation.
- Exact agent-friendly output format is not yet fully specified; JSON is likely, with Markdown possible later.
- Linkding API details and authentication method need to be confirmed against the deployed Linkding instance.
- Theme palette choices may need adjustment to meet WCAG AA contrast requirements.
- Feed refresh scheduling mechanism should stay simple, but the exact deployment environment for cron or systemd has not been specified.
- The app name and repository path are agreed in principle, but the repository may not yet exist.
