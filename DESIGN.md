---
name: Daily Firehose
description: An accessible Miura-fold reading queue for fast, explicit article triage.
colors:
  sheet-field: "#f7f7f5"
  sheet-surface: "#fefefb"
  sheet-raised: "#eeeeea"
  ink-near-black: "#0e0e0e"
  ink-muted: "#454545"
  valley-link-blue: "#173f5c"
  active-matte-gold: "#d4af37"
  active-gold-deep: "#916f08"
  border-hairline: "#a6a6a0"
  border-strong: "#0e0e0e"
  focus-blue: "#1f5f8f"
  success-green: "#1c5a3b"
  warning-umber: "#704b00"
  danger-red: "#8a2121"
  mountain-gray: "#a6a6a0"
  valley-score-blue: "#7ca5c7"
  crease-ink: "#2f5470"
typography:
  display:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(2.4rem, 5vw, 4.5rem)"
    fontWeight: 600
    lineHeight: 1.06
    letterSpacing: "0.035em"
  headline:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(1.4rem, 2.4vw, 1.9rem)"
    fontWeight: 600
    lineHeight: 1.06
    letterSpacing: "0.005em"
  title:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 600
    lineHeight: 1.06
    letterSpacing: "0.05em"
  headline-compact:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "1.45rem"
    fontWeight: 600
    lineHeight: 1.08
  body:
    fontFamily: "Atkinson Hyperlegible Next, Verdana, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.5
  interface:
    fontFamily: "Atkinson Hyperlegible Next, Verdana, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  control:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "0.85rem"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.1em"
  label:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "0.78rem"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.12em"
  micro:
    fontFamily: "Saira Semi Condensed, Arial Narrow, sans-serif"
    fontSize: "0.7rem"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.16em"
rounded:
  none: "0"
spacing:
  hairline: "0.0625rem"
  tight: "0.35rem"
  control: "0.55rem"
  unit: "1rem"
  panel: "1.5rem"
  section: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.active-matte-gold}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
  button-secondary:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
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
    padding: "0.45rem 0.6rem"
    height: "2.65rem"
  article-card:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "clamp(0.9rem, 2vw, 1.25rem)"
  state-marker:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink-near-black}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.05rem 0.45rem"
---

# Design System: Daily Firehose

## Overview

**Creative North Star: "Miura Fold Inbox"**

Daily Firehose is a compact information packet that unfolds into linked, readable decisions. Near-white matte sheet stock, near-black ink, gold active faces, and restrained mountain/valley scores make the interface feel engineered and tactile without turning reading into spectacle.

The drawing is hairline, not heavy. Every rule is one pixel, every corner is square or cut on a fold angle, and the paper carries the weight instead of thick borders. Text remains level, conventional, and highly legible while clipped corners, parallelogram controls, crease scores, and terse blue coordinates carry the folding metaphor. Accessibility is structural: explicit labels, adequate targets, visible focus, semantic state, and AAA contrast take priority over ornament.

There are exactly two palettes — Miura Paper and Miura Night — plus a device-following mode. A reading tool for one owner does not need a theme gallery; it needs one world that works in daylight and one that works at night.

**Key Characteristics:**
- Matte paper and gold-foil material cues rather than glossy effects.
- Hairline rules throughout; weight comes from type and gold, never from thick borders.
- Uppercase letterspaced condensed display type over hyperlegible body copy.
- Explicit actions and written states; color is always supplementary.
- Mobile hierarchy removes secondary detail before shrinking type or targets.

## Colors

The canonical Miura Paper palette resembles an annotated deployment sheet. Miura Night remaps the same semantic roles for low ambient light without changing hierarchy or meaning; no other palettes are supported.

### Primary
- **Active Matte Gold** (`active-matte-gold`): marks the current route, unread state, primary action, and clipped active face. Its rarity gives it authority.
- **Active Gold Deep** (`active-gold-deep`): provides stronger structural emphasis and scrollbar affordance.

### Secondary
- **Valley Link Blue** (`valley-link-blue`): identifies links and article titles with an underline.
- **Valley Score Blue** (`valley-score-blue`): draws restrained structural crease lines only, never text.
- **Crease Ink** (`crease-ink`): the readable blue of terse sheet coordinates, dark enough for AAA at small sizes.
- **Mountain Gray** (`mountain-gray`): pairs with valley blue to describe fold structure and hairline frames.

### Tertiary
- **Success Green**, **Warning Umber**, and **Danger Red**: accompany written success, warning, and error labels. They never communicate status alone.
- **Focus Blue** (`focus-blue`): creates the high-contrast focus ring and selected-sheet outline.

### Neutral
- **Sheet Field** (`sheet-field`): the page ground and repeating crease field.
- **Sheet Surface** (`sheet-surface`): the primary reading and control face.
- **Sheet Raised** (`sheet-raised`): quiet differentiation for read items and supporting blocks.
- **Near-Black Ink** (`ink-near-black`): primary text and active-face ink.
- **Muted Ink** (`ink-muted`): metadata that remains legible, never faint.
- **Hairline and Strong Borders** (`border-hairline`, `border-strong`): the one-pixel fold frame, dividers, and control outlines.

**The Hairline Rule.** Every border is `0.0625rem`. A heavier rule is a costume for structure the paper and type already provide.

**The Gold Face Rule.** Reserve gold for current route, primary action, and explicit state markers; do not wash whole reading surfaces in accent color.

**The Written State Rule.** Every success, warning, error, read, saved, or selected state needs text or structure in addition to color.

## Typography

**Display Font:** Saira Semi Condensed (with Arial Narrow and sans-serif fallback)
**Body Font:** Atkinson Hyperlegible Next (with Verdana and sans-serif fallback)
**Label Font:** Saira Semi Condensed

**Character:** Saira supplies the compact, engineered voice of routing labels and fold coordinates. Atkinson Hyperlegible Next keeps article metadata, summaries, forms, and long reading comfortable for a partially sighted owner.

### Hierarchy
- **Display** (600, fluid 2.4–4.5rem, 1.06, `0.035em`, uppercase): page titles and queue identity; keep to roughly 22 characters per line. Narrow screens and focus mode cap it at 2.4rem.
- **Headline** (600, fluid 1.4–1.9rem, 1.06): article titles and major section headings.
- **Headline Compact** (600, 1.45rem): article titles in compact mode and at mobile widths.
- **Title** (600, 1.15rem, uppercase, tracked): subsection, panel, and empty-state headings.
- **Body** (400, 1.0625rem, 1.5): default reading copy, generally capped near 70 characters.
- **Interface** (600, 1rem): brand name, queue count, and form help text.
- **Control** (600, 0.85rem, `0.1em`, uppercase): buttons and the keyboard hint.
- **Label** (600, 0.78rem, `0.12em`, uppercase): navigation, states, contextual labels, and mobile action cells.
- **Micro** (600, 0.7rem, `0.16em`): crease coordinates, timestamps, tagline, and mobile metadata.

**The Nine-Step Rule.** These nine roles are the whole ramp. A literal font-size that is not one of them is drift, not a decision.

**The Tracked Caps Rule.** Structural type — display, labels, nav, buttons, coordinates — is uppercase and letterspaced. Sentence-case is reserved for content the owner actually reads: article titles, summaries, and prose.

**The Level Type Rule.** Fold geometry may angle edges and scores, but never rotate, skew, or distort readable text.

**The Detail-Before-Type Rule.** On narrow screens, hide summaries and category markers before reducing essential title, state, time, or action text.

## Layout

The content rail is centered at a maximum width of 78rem with 1.5-rem page gutters; focus mode contracts it to 60rem. Desktop uses a single-row masthead — brand block, full route strip, refresh and sign-out — above a one-dimensional reading queue, because scan speed matters more than dashboard density.

At 68rem the masthead stacks. At 42rem it becomes a 3.5-rem sticky mobile header whose entire navigation and account actions collapse behind one `MENU` disclosure that opens a bounded sheet. The tagline and article summaries drop out, and the three article actions become a single row of three equal 44px cells labeled OPEN · SAVE · READ.

Triage density is the point on mobile: at 393px the header is 56px, an article sheet is about 183px, and three complete sheets fit in the first viewport without horizontal or in-card overflow. Touch controls stay at or above the 44px comfortable target for article actions and 24px for secondary controls. Core navigation and mutations remain conventional links, buttons, and forms so progressive enhancement and keyboard access survive the visual system.

## Elevation & Depth

The system is flat by default. Depth comes from clipped silhouettes, doubled fold lines, matte textures, tonal faces, and directional state movement rather than ambient card shadows. Shadows are reserved for genuinely overlaid or focused modes: the mobile navigation sheet, keyboard-help dialog, and distraction-light focus mode.

### Shadow Vocabulary
- **Overlay Low** (`0 1rem 2rem var(--shadow-color)`): mobile navigation only.
- **Focus Surface** (`0 1rem 2.5rem var(--shadow-color)`): article cards in focus mode.
- **Modal High** (`0 1.25rem 3rem var(--shadow-color)`): keyboard-help dialog.
- **Inset Selection** (`inset 0 0 0 0.0625rem var(--focus)`): selected article state without changing layout.

**The Flat Sheet Rule.** Resting content surfaces do not float; establish hierarchy with paper faces, borders, scores, and clipped geometry.

## Shapes

Corners are square by default. Buttons use a shallow parallelogram clip; panels, article cards, and feed rows use asymmetric clipped polygons that imply a deployed fold without bending their contents. A small gold corner face identifies the upper-right fold, while light mountain and valley lines cross the paper at controlled angles.

Borders are uniformly one pixel. Terse blue IDs such as `M-01 · V-0004` sit at the top-right of every sheet and make article and feed rows feel indexed rather than decorated.

**The Complete Crease Rule.** Use the clipped edge, corner face, score line, and coordinate as one coherent grammar; avoid arbitrary triangles or isolated diagonal decoration.

## Components

### Buttons
- **Shape:** squared parallelogram with opposing clipped corners and a one-pixel frame.
- **Primary:** matte gold face, near-black ink, 2.75rem height, uppercase tracked verb label.
- **Hover / Focus:** hover turns the full face gold; focus uses a four-pixel blue outline with offset; press moves downward by two pixels.
- **Secondary:** paper face with a faint valley fold; never ghosted or low contrast.
- **Responsive labels:** on narrow screens the full verb stays in the accessibility tree while a short synonym (OPEN, SAVE, READ) is shown; never drop the action or hide it behind a gesture.

### State Markers
- **Style:** compact uppercase Saira label with a thick gold left bar on the paper face; read state swaps the bar and text to muted ink.
- **State:** wording such as “Unread,” “Read,” or “Saved” is mandatory; color is reinforcement only.

### Cards / Containers
- **Corner Style:** asymmetric clipped fold; no rounded corners.
- **Background:** paper surface with subtle procedural grain and restrained crossing score lines.
- **Shadow Strategy:** none at rest; use inset focus or focus-mode elevation only.
- **Border:** one-pixel hairline frame.
- **Internal Padding:** fluid 0.9–1.25rem on desktop and 0.7rem on mobile.
- **Signature Behavior:** selection advances along one crease axis; removal folds vertically in 180ms with a complete reduced-motion fallback.

### Inputs / Fields
- **Style:** level paper field, one-pixel outline, square corners, and at least 3rem height.
- **Focus:** global four-pixel focus outline with visible offset.
- **Checkboxes / Radios:** 2.75rem square targets with strongly colored native state.
- **Error / Disabled:** retain readable labels and explicit status; never rely on tint or reduced opacity alone.

### Navigation
- **Style:** a ruled strip of compact uppercase Saira labels on one row. The active route uses a gold face, written label, `aria-current`, and an inset dark baseline.
- **Mobile:** one explicit `MENU` disclosure opens a bounded sheet holding the two-column route grid plus refresh and sign-out, so the sticky header stays 3.5rem tall.

### Article Sheet

Source, written state, title, time, and crease coordinate follow a stable scan path. `Open article`, `Save to Linkding`, and `Mark read` stay exposed as conventional controls. Summaries are supporting content on wider screens, not prerequisites for mobile triage.

## Do's and Don'ts

### Do:
- **Do** keep every primary action, state, and focus indicator explicit and high contrast.
- **Do** use level text inside clipped, scored paper geometry.
- **Do** preserve 44px article-action targets and at least three complete Today sheets in the iPhone 16 portrait first viewport.
- **Do** keep every border at one pixel.
- **Do** remove secondary detail before reducing essential type or controls.
- **Do** carry the same semantic palette roles through every supported theme.
- **Do** provide reduced-motion behavior for every fold or deployment transition.

### Don't:
- **Don't** use rounded cards, pill controls, glossy gradients, or generic dashboard chrome.
- **Don't** hide open, save, read, refresh, or navigation actions behind gestures or unlabeled icons.
- **Don't** use color, texture, diagonal lines, or coordinates as the sole carrier of meaning.
- **Don't** rotate or skew readable text to match a fold edge.
- **Don't** add shadows to resting cards merely to make them feel clickable.
- **Don't** add palettes beyond Miura Paper, Miura Night, and the device-following mode.
- **Don't** let a mobile article sheet grow tall enough to push the next sheet out of the first viewport.
- **Don't** let decorative materiality compromise contrast, text zoom, keyboard operation, or progressive enhancement.
