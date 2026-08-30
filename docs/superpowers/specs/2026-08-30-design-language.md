# MOPAN Design Language

This document REPLACES the design rule in `2026-08-28-vertical-slice-1-design.md` line 206
("과도한 gradient/glow/glassmorphism 지양, 평평한 테두리 기반"). That rule was written by
the assistant, not by the product owner, and it was then handed to three reviewers as a
pass/fail bar — which is why every Slice 1 screen is flat, borderless-grey and reads as
unfinished. **It is revoked.** Reviewers check against THIS document.

The reference is the Google Gemini web app. What follows is grounded in the published
Material 3 role structure and shape scale; the concrete hex values are this project's
instantiation of those roles, not values copied from Google.

---

## 1. Principle

**Surfaces, not borders.** Hierarchy comes from tonal steps between container surfaces.
A 1px grey rule around every box is the thing being replaced. Borders survive only where
they carry meaning: a focus ring, a text field's resting outline, a table's row separator.

**Generous.** Slice 1 sized everything to fit. This language spends space: 24px page
padding, 16-24px inside containers, a 768px reading column for the chat transcript.

**Quiet colour, one accent.** Blue is the single accent. Everything else is a tonal step
of the neutral surface. Red is reserved for destructive and error. There is no second
brand colour competing for attention.

**Motion is feedback, not decoration.** 150-250ms, one easing curve, and only on things
the user caused: a dialog opening, a hover, a message arriving. Nothing loops, nothing
pulses, nothing animates on page load. `prefers-reduced-motion: reduce` disables all of it.

---

## 2. Colour tokens

Defined as CSS custom properties on `:root`, re-declared under
`@media (prefers-color-scheme: dark)` and under `[data-theme="dark"]` so an explicit
toggle wins in both directions. Every colour in the app resolves through a token; a raw
hex or a Tailwind palette class (`bg-gray-50`, `text-gray-500`) in a component is a defect.

### Light

| Token | Value | Use |
|---|---|---|
| `--surface` | `#FFFFFF` | Page background |
| `--surface-container-lowest` | `#FFFFFF` | — |
| `--surface-container-low` | `#F8FAFD` | Sidebar, resting cards |
| `--surface-container` | `#F0F4F9` | Composer, user message bubble, chips |
| `--surface-container-high` | `#E9EEF6` | Hover on a container, selected nav item |
| `--surface-container-highest` | `#DDE3EA` | Pressed state, table header |
| `--on-surface` | `#1F1F1F` | Primary text |
| `--on-surface-variant` | `#444746` | Secondary text, icons, labels |
| `--outline` | `#747775` | Text field resting outline |
| `--outline-variant` | `#C4C7C5` | Row separators, dividers |
| `--primary` | `#0B57D0` | Accent: links, active nav, primary button fill |
| `--on-primary` | `#FFFFFF` | Text on primary |
| `--primary-container` | `#D3E3FD` | Selected chip, active nav background |
| `--on-primary-container` | `#041E49` | Text on primary-container |
| `--error` | `#B3261E` | Destructive action, error text |
| `--on-error` | `#FFFFFF` | Text on error |
| `--error-container` | `#F9DEDC` | Error banner background |
| `--on-error-container` | `#410E0B` | Error banner text |

### Dark

| Token | Value |
|---|---|
| `--surface` | `#131314` |
| `--surface-container-lowest` | `#0E0E0E` |
| `--surface-container-low` | `#1B1B1B` |
| `--surface-container` | `#1E1F20` |
| `--surface-container-high` | `#282A2C` |
| `--surface-container-highest` | `#333537` |
| `--on-surface` | `#E3E3E3` |
| `--on-surface-variant` | `#C4C7C5` |
| `--outline` | `#8E918F` |
| `--outline-variant` | `#444746` |
| `--primary` | `#A8C7FA` |
| `--on-primary` | `#062E6F` |
| `--primary-container` | `#0842A0` |
| `--on-primary-container` | `#D3E3FD` |
| `--error` | `#F2B8B5` |
| `--on-error` | `#601410` |
| `--error-container` | `#8C1D18` |
| `--on-error-container` | `#F9DEDC` |

Contrast: every `on-*` / surface pairing above must reach WCAG AA (4.5:1 for body text,
3:1 for large text and UI boundaries). Verify with a computed-style measurement, not by eye.

### The Gemini gradient

Blue → violet → magenta, used **only** on: the wordmark, the assistant avatar/sparkle, and
the streaming indicator. Never on a surface, never behind text, never on a button.
`linear-gradient(74deg, #4285F4 0%, #9B72CB 45%, #D96570 100%)`.

---

## 3. Shape

| Token | Radius | Use |
|---|---|---|
| `--radius-xs` | 4px | Chips-in-text, tags |
| `--radius-sm` | 8px | Buttons, inputs, table containers |
| `--radius-md` | 12px | Cards, menus, message bubbles |
| `--radius-lg` | 16px | Dialogs, panels |
| `--radius-xl` | 28px | The chat composer |
| `--radius-full` | 9999px | Icon buttons, avatars, suggestion chips |

The 4px-everywhere of Slice 1 is gone. A 28px composer against 12px cards is the contrast
that reads as designed rather than defaulted.

---

## 4. Elevation

M3 expresses depth with surface tone, not shadow. Containers step through
`surface-container-low` → `container` → `high` → `highest`. **No `box-shadow` on any
resting container.**

Two exceptions, both true overlays that float above the page:
- Menu / popover: `0 2px 6px rgba(0,0,0,.10), 0 1px 2px rgba(0,0,0,.06)`
- Dialog: `0 8px 24px rgba(0,0,0,.16), 0 2px 6px rgba(0,0,0,.08)`

In dark mode both drop to half opacity — shadow reads as dirt on a dark surface.

---

## 5. Typography

Latin **Inter**, Korean **Noto Sans KR**, loaded through `next/font/google` so they are
self-hosted at build time (no runtime CDN, no layout shift). Fallback stack:
`system-ui, -apple-system, "Segoe UI", Roboto, "Malgun Gothic", sans-serif`.

| Role | Size / line-height | Weight | Use |
|---|---|---|---|
| `display` | 36 / 44 | 400 | The chat empty-state greeting |
| `headline` | 24 / 32 | 400 | Page titles |
| `title` | 18 / 24 | 500 | Section headings, dialog titles |
| `body-lg` | 16 / 26 | 400 | Chat messages, long-form reading |
| `body` | 14 / 21 | 400 | Default UI text, table cells |
| `label` | 13 / 18 | 500 | Buttons, nav items, table headers |
| `caption` | 12 / 16 | 400 | Timestamps, helper text, counts |

Korean needs more leading than Latin at the same size — the line-heights above are already
set for it. Do not tighten them.

---

## 6. Spacing

4px base: `4, 8, 12, 16, 24, 32, 48, 64`. Page gutter 24px (16px under 640px).
Chat transcript column `max-width: 768px`, centred. Sidebar 280px (was 256px).

---

## 7. Motion

One curve, `cubic-bezier(0.2, 0, 0, 1)`. Durations: 150ms for hover/press feedback, 200ms
for a panel or dialog, 250ms for a drawer. Nothing longer, nothing looping except the
streaming indicator.

Wrap every transition in `@media (prefers-reduced-motion: no-preference)`. Under `reduce`
the app must be fully usable with zero animation.

---

## 8. Component notes

**Chat transcript.** The user's message sits in a `surface-container` bubble with
`--radius-md`, right-aligned, max 75% width. **The assistant's answer is NOT bubbled** —
it renders flat on `--surface` at `body-lg` across the full column, with the gradient
sparkle at its head. This is the single biggest visual difference from a generic chat UI
and it is what makes the answer feel like a document rather than a text message.

**Composer.** One `surface-container` block, `--radius-xl`, no border at rest, a
`--primary` 2px outline on focus-within. Inside: a `+` icon button (left), an
auto-growing textarea (1 to 8 rows), and a send button (right) that is `--primary` filled
when the field has content and `surface-container-high` when empty. Attachment thumbnails
render in a row above the textarea, inside the same block.

**Empty state.** Centred, `display` size, the greeting in the Gemini gradient, followed by
3-4 suggestion chips (`--radius-full`, `surface-container`) that fill the composer when
clicked.

**Buttons.** Filled (`--primary` / `--on-primary`), tonal (`--surface-container-high`),
and text (`--primary` on transparent). All `--radius-sm`, 40px tall, 16px horizontal
padding. Icon buttons 40×40, `--radius-full`.

**Tables.** No outer border. Header row `label` on `--surface-container-low`, body rows
separated by a 1px `--outline-variant` bottom border, row hover `--surface-container-low`.

**Focus.** `outline: 2px solid var(--primary); outline-offset: 2px` on `:focus-visible`,
globally. Already present; keep it, retarget it to the token.

---

## 9. What reviewers check

1. No raw hex and no Tailwind default palette class in any component — every colour is a token.
2. No `box-shadow` outside menus and dialogs.
3. Both themes render correctly; measure computed contrast for `on-surface`,
   `on-surface-variant` and `primary` against their backgrounds in both.
4. `prefers-reduced-motion: reduce` leaves the app fully usable with no animation.
5. The assistant answer is not in a bubble.
6. Korean text is not clipped and its line-height is not tightened below §5.
7. Every interactive element is keyboard reachable and shows the focus ring.
8. No horizontal page scroll at 375px.
