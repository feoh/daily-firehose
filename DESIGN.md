---
name: Daily Firehose
description: An accessible Miura-fold reading queue for fast, explicit article triage.
colors:
  sheet-field: "#f3f3ef"
  sheet-surface: "#ffffff"
  sheet-raised: "#fafaf7"
  ink-near-black: "#111413"
  ink-muted: "#343b3f"
  valley-link-blue: "#004b82"
  active-matte-gold: "#d4af37"
  active-gold-deep: "#a77d00"
  border-graphite: "#545c60"
  border-strong: "#24292c"
  focus-rust: "#8a4600"
  success-green: "#145c38"
  warning-umber: "#6d4400"
  danger-red: "#922323"
  mountain-gray: "#767b7d"
  valley-score-blue: "#286c9e"
typography:
  display:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(2.25rem, 6vw, 4.8rem)"
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(1.45rem, 3vw, 2.1rem)"
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "1.3rem"
    fontWeight: 700
    lineHeight: 1.08
  body:
    fontFamily: "Atkinson Hyperlegible Next, Verdana, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "0.04em"
rounded:
  none: "0"
spacing:
  tight: "0.45rem"
  control: "0.65rem"
  unit: "1rem"
  panel: "1.5rem"
  section: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.active-matte-gold}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.55rem 1rem"
    height: "3rem"
  button-secondary:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.55rem 1rem"
    height: "3rem"
  input:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "0.55rem 0.75rem"
    height: "3rem"
  nav-item:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.45rem 0.7rem"
    height: "2.75rem"
  article-card:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "clamp(1.2rem, 3vw, 2rem)"
  state-marker:
    backgroundColor: "{colors.active-matte-gold}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.22rem 0.5rem"
---

# Design System: Daily Firehose

## Overview

**Creative North Star: "Miura Fold Inbox"**

Daily Firehose is a compact information packet that unfolds into linked, readable decisions. Near-white matte sheet stock, near-black ink, gold active faces, and restrained mountain/valley scores make the interface feel engineered and tactile without turning reading into spectacle.

The system is dense but never cramped. Text remains level, conventional, and highly legible while clipped corners, parallelogram controls, crease diagrams, and terse coordinates carry the folding metaphor. Accessibility is structural: explicit labels, large targets, visible focus, semantic state, and high contrast take priority over ornament.

**Key Characteristics:**
- Matte paper and gold-foil material cues rather than glossy effects.
- Squared, clipped geometry with visible mountain and valley crease axes.
- Large hyperlegible text paired with condensed, emphatic display labels.
- Explicit actions and written states; color is always supplementary.
- Mobile hierarchy removes secondary detail before shrinking type or targets.

## Colors

The canonical light palette resembles an annotated deployment sheet; alternate themes remap the same semantic roles without changing hierarchy or meaning.

### Primary
- **Active Matte Gold** (`active-matte-gold`): marks the current route, unread state, primary action, and clipped active face. Its rarity gives it authority.
- **Active Gold Deep** (`active-gold-deep`): provides stronger structural emphasis and scrollbar affordance.

### Secondary
- **Valley Link Blue** (`valley-link-blue`): identifies links with strong underline treatment.
- **Valley Score Blue** (`valley-score-blue`): draws restrained structural crease lines, never body copy.
- **Mountain Gray** (`mountain-gray`): pairs with valley blue to describe fold structure.

### Tertiary
- **Success Green**, **Warning Umber**, and **Danger Red**: accompany written success, warning, and error labels. They never communicate status alone.
- **Focus Rust** (`focus-rust`): creates the high-contrast focus and selected-item ring.

### Neutral
- **Sheet Field** (`sheet-field`): the page ground and repeating crease field.
- **Sheet Surface** (`sheet-surface`): the primary reading and control face.
- **Sheet Raised** (`sheet-raised`): quiet differentiation for read items and supporting blocks.
- **Near-Black Ink** (`ink-near-black`): primary text and active-face ink.
- **Muted Ink** (`ink-muted`): metadata that remains legible, never faint.
- **Graphite Borders** (`border-graphite`, `border-strong`): the fold frame, dividers, and control outlines.

**The Gold Face Rule.** Reserve gold for current route, primary action, and explicit state markers; do not wash whole reading surfaces in accent color.

**The Written State Rule.** Every success, warning, error, read, saved, or selected state needs text or structure in addition to color.

## Typography

**Display Font:** Saira Semi Condensed (with Arial Narrow and sans-serif fallback)
**Body Font:** Atkinson Hyperlegible Next (with Verdana and sans-serif fallback)
**Label Font:** Saira Semi Condensed

**Character:** Saira supplies the compact, engineered voice of routing labels and fold coordinates. Atkinson Hyperlegible Next keeps article metadata, summaries, forms, and long reading comfortable for a partially sighted owner.

### Hierarchy
- **Display** (700, fluid 2.25–4.8rem, 1.08): page titles and major queue identity; keep to roughly 22 characters per line.
- **Headline** (700, fluid 1.45–2.1rem, 1.08): article titles and major section headings; mobile article titles deliberately rise to 1.75rem.
- **Title** (700, 1.3rem, 1.08): subsection and form-panel headings.
- **Body** (400, 1.125rem, 1.55): default interface and reading copy, generally capped near 72 characters.
- **Label** (700, 0.95rem, tracked): navigation, buttons, states, coordinates, and uppercase contextual labels.

**The Level Type Rule.** Fold geometry may angle edges and scores, but never rotate, skew, or distort readable text.

**The Detail-Before-Type Rule.** On narrow screens, hide summaries and category markers before reducing essential title, state, time, or action text.

## Layout

The content rail is centered at a maximum width of 76rem with one-rem page gutters; focus mode contracts it to 60rem. Desktop uses a two-part masthead and one-dimensional reading queue, because scan speed matters more than dashboard density. Surfaces follow a one-rem rhythm, with larger panel spacing and fluid internal padding.

At 68rem the masthead stacks. At 42rem it becomes a compact sticky mobile header, the navigation becomes an explicit two-column disclosure, and the page gutter contracts to half a rem. Article actions form two equal columns with the final read action spanning the row. At 393px, the first complete Today article and the beginning of the next article remain visible without horizontal overflow.

Touch controls are at least 44px; primary buttons are normally 48px high. Mobile summaries and category labels disappear before title size or action targets shrink. Core navigation and mutations remain conventional links, buttons, and forms so progressive enhancement and keyboard access survive the visual system.

## Elevation & Depth

The system is flat by default. Depth comes from clipped silhouettes, doubled fold lines, matte textures, tonal faces, and directional state movement rather than ambient card shadows. Shadows are reserved for genuinely overlaid or focused modes: the mobile navigation sheet, keyboard-help dialog, and distraction-light focus mode.

### Shadow Vocabulary
- **Overlay Low** (`0 1rem 2rem var(--shadow-color)`): mobile navigation only.
- **Focus Surface** (`0 1rem 2.5rem var(--shadow-color)`): article cards in focus mode.
- **Modal High** (`0 1.25rem 3rem var(--shadow-color)`): keyboard-help dialog.
- **Inset Selection** (`inset 0 0 0 0.25rem var(--focus)`): selected article state without changing layout.

**The Flat Sheet Rule.** Resting content surfaces do not float; establish hierarchy with paper faces, borders, scores, and clipped geometry.

## Shapes

Corners are square by default. Buttons use a shallow parallelogram clip; panels, article cards, and feed rows use asymmetric clipped polygons that imply a deployed fold without bending their contents. A small gold corner face identifies the upper-right fold, while light mountain and valley lines cross the paper at controlled angles.

Borders are structural and deliberate: one-pixel scores for internal divisions and two-pixel graphite frames for controls and primary surfaces. Terse IDs such as `M-01 · V-0004` make article and feed sheets feel indexed rather than decorated.

**The Complete Crease Rule.** Use the clipped edge, corner face, score line, and coordinate as one coherent grammar; avoid arbitrary triangles or isolated diagonal decoration.

## Components

### Buttons
- **Shape:** squared parallelogram with opposing clipped corners and a strong two-pixel frame.
- **Primary:** matte gold face, near-black ink, minimum 3rem height, and explicit verb label.
- **Hover / Focus:** hover turns the full face gold; focus uses a substantial rust outline with offset; press moves downward by two pixels.
- **Secondary:** white paper face with a faint valley fold; never ghosted or low contrast.

### State Markers
- **Style:** compact uppercase Saira labels on a matte gold face for unread/current state, or a muted high-contrast face for read state.
- **State:** wording such as “Unread,” “Read,” or “Saved” is mandatory; color is reinforcement only.

### Cards / Containers
- **Corner Style:** asymmetric clipped fold; no rounded corners.
- **Background:** paper surface with subtle procedural grain and restrained crossing score lines.
- **Shadow Strategy:** none at rest; use inset focus or focus-mode elevation only.
- **Border:** strong two-pixel graphite frame.
- **Internal Padding:** fluid 1.2–2rem on desktop and 1rem on mobile.
- **Signature Behavior:** selection advances along one crease axis; removal folds vertically in 180ms with a complete reduced-motion fallback.

### Inputs / Fields
- **Style:** level white field, strong two-pixel outline, square corners, and at least 3rem height.
- **Focus:** global four-pixel focus outline with visible offset.
- **Checkboxes / Radios:** 2.75rem square targets with strongly colored native state.
- **Error / Disabled:** retain readable labels and explicit status; never rely on tint or reduced opacity alone.

### Navigation
- **Style:** a ruled strip of compact Saira labels. The active route uses a gold face, written label, `aria-current`, and an inset dark baseline.
- **Mobile:** a full-width “Sections” disclosure remains explicit and reveals a two-column sheet; refresh and sign-out stay visible beside it.

### Article Sheet

Source, written state, title, time, and crease coordinate follow a stable scan path. `Open article`, `Save to Linkding`, and `Mark read` stay exposed as conventional controls. Summaries are supporting content on wider screens, not prerequisites for mobile triage.

## Do's and Don'ts

### Do:
- **Do** keep every primary action, state, and focus indicator explicit and high contrast.
- **Do** use level text inside clipped, scored paper geometry.
- **Do** preserve 44px minimum touch targets and the complete first Today article at iPhone 16 portrait width.
- **Do** remove secondary detail before reducing essential type or controls.
- **Do** carry the same semantic palette roles through every supported theme.
- **Do** provide reduced-motion behavior for every fold or deployment transition.

### Don't:
- **Don't** use rounded cards, pill controls, glossy gradients, or generic dashboard chrome.
- **Don't** hide open, save, read, refresh, or navigation actions behind gestures or unlabeled icons.
- **Don't** use color, texture, diagonal lines, or coordinates as the sole carrier of meaning.
- **Don't** rotate or skew readable text to match a fold edge.
- **Don't** add shadows to resting cards merely to make them feel clickable.
- **Don't** let decorative materiality compromise contrast, text zoom, keyboard operation, or progressive enhancement.
