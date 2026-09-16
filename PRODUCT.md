# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Daily Firehose is for one owner-reader managing their own information flow in a private, authenticated deployment. The owner is partially blind and frequently uses the web application from an iPhone 16 as well as from a keyboard-equipped computer. The primary job is to process a high-volume stream quickly, decide what deserves attention, and preserve worthwhile material without sacrificing readability, touch usability, or keyboard access.

## Product Purpose

Daily Firehose brings RSS, Atom, and email newsletters into one reading workflow. It helps the owner move through current material, mark it read, save valuable links to Linkding, and discover unread articles that match the taste expressed by prior saves.

Success means the owner can keep up with daily information efficiently and accessibly while retaining control over subscriptions, reading state, and saved material. No quantitative success target has been established.

## Positioning

Daily Firehose combines an accessibility-first, keyboard-driven reading queue with private recommendations learned from the owner's saved-link history. Recommendation relevance is based on that personal taste profile rather than article recency.

## Operating Context

- The owner signs in and works through Today, Week, Month, recommendation, archived, saved-link, and individual-feed views.
- Frequent iPhone 16 use makes the mobile web experience a primary operating context, not a secondary adaptation.
- Read, unread, save, bulk-read, refresh, touch, and keyboard actions support fast triage.
- Linkding is the durable destination for selected links.
- RSS and Atom feeds are managed directly or moved through OPML import and export.
- Email newsletters enter through Postmark and are read in the same product.
- Session JSON, capability-scoped bearer APIs, and signed actions let agents and other clients access supported workflows.
- The production application is privately operated behind Tailscale Funnel rather than positioned as a public reader service.

## Capabilities and Constraints

- Preserve readable, WCAG AA-oriented presentation, touch-friendly mobile operation, and keyboard-friendly desktop operation.
- Treat iPhone 16 portrait use as a first-class target for navigation, article triage, forms, dialogs, and feedback; avoid horizontal overflow and desktop-only interaction assumptions.
- Preserve Linkding saves, OPML portability, agent-friendly JSON/API access, and newsletter ingestion as product capabilities.
- Keep per-user read state, saved state, preferences, and recommendations private behind authentication, except for deliberately public newsletter archive links and operational health endpoints.
- Continue to support progressive enhancement: core browser actions must remain usable without JavaScript.
- The existing product is a server-rendered Django application with PostgreSQL in production and a supervised feed-refresh worker.
- Feed ingestion must remain bounded and defensive around remote content; newsletter content must remain sanitized.

## Brand Commitments

The current Daily Firehose name, firehose masthead, and “Readable daily feed flow” tagline are provisional. Future work may change them; they are not durable brand constraints.

## Evidence on Hand

- The current product implementation, routes, tests, and behavioral documentation are repository evidence for existing capabilities.
- `docs/images/article-reading-view.png` captures the current reading interface.
- `static/img/firehose-masthead.svg` is the current masthead asset, but it is not a required future identity asset.
- Existing feed, newsletter, reading-state, and saved-link data can demonstrate real workflows in the running application.
- No testimonials, customer logos, public usage claims, performance benchmarks, or other marketing proof have been established and must not be fabricated.

## Product Principles

1. **Accessible speed over decorative friction.** Reading and triage should be fast by touch on an iPhone and by keyboard on a computer, understandable with assistive technology, and comfortable over long sessions.
2. **Personal taste over generic popularity.** Discovery should reflect what the owner has actually valued, without using freshness as a proxy for relevance.
3. **Owner control over lock-in.** Subscriptions and selected links remain portable through OPML and Linkding, with clear local state.
4. **One workflow across sources.** Feeds and newsletters should feel coherent even when their ingestion paths differ.
5. **Automation complements the reader.** APIs and agent-facing output should extend the same truthful product state rather than create a separate workflow.

## Accessibility & Inclusion

WCAG AA is a floor, not a target: because the owner is partially blind, primary text, controls, focus indicators, status cues, and action labels should meet WCAG AAA contrast wherever practical, and no meaning may depend on color alone. Strong readability, touch-friendly controls, keyboard operation, visible focus, semantic controls, responsive behavior, text zoom, and high-contrast theme support are durable requirements. The mobile web experience must be fully usable on an iPhone 16 in portrait orientation, including navigation and every core reading or mutation workflow. Core workflows must not rely on hover or hardware-keyboard shortcuts, and must retain non-JavaScript fallbacks where currently supported.
