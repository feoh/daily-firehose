---
version: 1
slug: "templates-base-html"
primary_target: "templates/base.html"
related_targets: ["static/css/site.css","templates/feeds/digest.html","templates/feeds/feed_detail.html","templates/feeds/feed_list.html","templates/feeds/includes/article_card.html","templates/feeds/newsletter_detail.html","templates/feeds/opml_import.html","templates/feeds/preferences.html","templates/registration/login.html"]
---

# Daily Firehose application interface

## Scope

Operate mode. Replace the visual system across the authenticated application shell, Today and period queues, recommendations, archives, saved links, feeds, preferences, OPML, login, newsletter reading, messages, dialogs, and shared controls. The Today queue is the proving surface; all other screens inherit the same world.

## Audience, job, and constraints

The single owner-reader is partially blind and frequently triages from an iPhone 16 in portrait orientation. The first task is scanning Today, understanding source and state, then opening, saving, or marking articles without hidden actions. Preserve all existing content, routes, behavior, progressive enhancement, semantic structure, keyboard support, touch support, theme preferences, and responsive contracts. WCAG AA is the minimum; primary text, controls, focus, and status should reach AAA contrast wherever practical, and color must never carry meaning alone.

## Direction contract

**THESIS:** The daily queue is one compact information packet that unfolds into linked, readable decisions. Replace generic rounded cards and dashboard chrome with a Miura-fold field while refusing folded decoration that bends text, hides actions, or sacrifices density.

**OWN-WORLD:** Near-white sheet stock carries near-black ink; a matte gold face marks the active route or primary action; mountain gray and valley blue appear only as high-contrast structural scores and state companions. Parallelogram edges, clipped fold corners, crease diagrams, terse IDs, and squared controls form the component language, while all text and touch targets remain level, conventional, and plainly labeled.

**STORY:** The owner sees what arrived, understands each article’s source and state, and acts immediately. Today proves the packet-to-field mechanism; period views, recommendations, feed management, preferences, forms, messages, and dialogs reuse its folds as stable hierarchy rather than metaphorical copy.

**FIRST VIEWPORT:** At iPhone 16 portrait width, a compact sticky masthead and explicit navigation control sit above Today’s date and queue count. The first full article sheet shows source/state, a large readable title, essential time metadata, and an always-visible OPEN · SAVE · READ action row; the next sheet remains visibly discoverable below. The signature interaction is linked deployment: selection or state change travels along one crease axis in a short, non-opacity motion, with no overshoot and a complete reduced-motion fallback.

**FORM:** Miura Fold Inbox, selected from the bolder challenger hand on re-roll 1; seed key `12af39b7`. Its complete crease graph becomes responsive hierarchy: small screens step the deployment and remove secondary detail before shrinking type or targets.

**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved decisions

Exact font files and final token values are resolved during implementation and contrast testing. The existing brand name, masthead, and tagline are not protected identity assets.
