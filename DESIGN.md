# Design

<!-- impeccable:design-schema 1 -->

## World

**The drafting table.** OzemPDF is a precision instrument that sits on a document workstation. The interface borrows the material language of a drafting surface: warm paper, hairline rules, a faint measured grid, tabular figures, and a single ink-blue mark that shows where the tool is cutting. Nothing is decorated; everything is measured. The queue and the numbers are the composition; the chrome recedes into hairlines.

Mode: **Operate**. Every screen serves a task in progress. Familiar desktop-document conventions (three-step stepper, table with sticky head, footer action dock) earn their place; the point of view lives in the typography, the measured grid, and the restraint of one accent.

## Palette

Tokens are defined once in `styles.css` (`:root` and `[data-theme='dark']`) and consumed by both themes.

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--paper` | `#F2F1EC` | `#111210` | app canvas |
| `--surface` | `#FAFAF6` | `#171815` | content surface |
| `--raised` | `#FFFFFF` | `#1F211D` | rows, controls, floating layers |
| `--raised-2` | `#F5F4EF` | `#24261F` | recessed tracks, table head |
| `--ink` | `#1B1B17` | `#ECEBE4` | primary text |
| `--ink-2` | `#54554D` | `#B3B4AA` | secondary text |
| `--ink-3` | `#6B6C63` | `#96978D` | muted text, labels (AA on its surface) |
| `--rule` | `#DBD9D0` | `#30322B` | hairlines |
| `--rule-strong` | `#B8B6AB` | `#4A4C42` | dashed drop border, separators |
| `--accent` | `#1B4A80` | `#63B3ED` | primary action, active step, divider |
| `--accent-hover` | `#143A67` | `#86C7F2` | pressed/hover accent |
| `--accent-ink` | `#FFFFFF` | `#0A1622` | text on an accent fill |
| `--accent-soft` | `#E6EDF7` | `#16283A` | accent tint |
| `--success` | `#1C6742` | `#5EC191` | savings, completed |
| `--success-soft` | `#E3EFE6` | `#1C2E24` | completion tint |
| `--warning` | `#7E5310` | `#E3AE55` | partial runs |
| `--warning-soft` | `#F5ECD8` | `#322A17` | warning tint |
| `--danger` | `#9E2A1E` | `#F08170` | errors, destructive |
| `--danger-soft` | `#F5E2DE` | `#38211C` | error tint |

The accent is **ink blue**, used only for: the primary action, the current step marker, keyboard
focus, the wipe divider, slider thumbs and the active segmented pill. Savings are `--success`;
errors `--danger`; partial runs `--warning`. No other colour appears. Measured contrast: accent on
surface 8.6:1 light / 7.8:1 dark, `--accent-ink` on accent 9.0:1 / 8.0:1, accent on its tint 7.6:1 /
6.3:1 — all clear of the 4.5:1 AA floor.

## Typography

- **IBM Plex Sans** (variable, bundled) for all text — a technical grotesque with enough character to
  belong to an instrument rather than a generic dashboard. Fixed rem scale, ratio ≈1.15: `11px`
  uppercase labels (tracking `0.08em`, weight 600), `12.5px` table body, `13px` default UI, `15px`
  section titles, `20px` step headings, `30px` the one display numeral (step 3 saved percentage).
- **JetBrains Mono** (bundled) for every measurement: byte sizes, percentages, page counts, versions, paths, the wipe readout. `font-variant-numeric: tabular-nums` on all numerals.
- Prose measure capped at 62ch; the drop-zone subtitle at 46ch. No eyebrow kickers, no gradient text.
- Headings use `text-wrap: balance`; long file names elide with the full value in `title`.

## Space and surfaces

- 4px base. Content max width `1180px`; page padding `24px` (32px at ≥1280px).
- Surfaces are separated by 1px hairlines, not shadows. Shadows exist only for genuinely floating layers: the inspector dialog, the output popover, toasts. Shadow: `0 1px 2px rgb(0 0 0 / .06), 0 12px 32px -12px rgb(0 0 0 / .28)`.
- Radius: `8px` surfaces, `6px` controls, `4px` pills and thumbnail frames. Nothing pill-shaped beyond the status/savings chips.
- The measured grid (24px, `repeating-linear-gradient` at 3% ink) appears only in the drop mat and empty states — it is the world's only texture.

## Layout

`body` is a three-row grid: 56px header, scrollable main, footer dock. Header: brand · stepper · controls`(language, theme, help)`. The footer dock is always present and holds destination + primary CTA; in step 3 it becomes `Start new batch` + `Open output`.

Step 1 centres a single drop mat. Step 2 stacks: queue panel → compression card → advanced drawer. Step 3: results band → processed queue → savings breakdown. The queue table is the spine of the app at every step, with columns `Document | Pages | Original | Status | Savings | Action` and 60px rows.

Responsive: below 1080px, hide `Pages` and `Original`; below 820px, rows collapse action labels to icons, the drawer becomes one column, and the footer dock wraps into two rows. No horizontal page scroll at 360px.

## Motion

- 160ms `cubic-bezier(.2,.8,.3,1)` for hover/press feedback, 220ms for drawers and dialogs, 360ms for the savings bars and bars' width changes.
- One authored moment: on step 3 the four category bars draw from 0 to their measured fraction, once, after the results band appears.
- The advanced drawer animates `grid-template-rows 0fr → 1fr`; the inspector wipe follows the pointer with no easing.
- All motion is disabled under `prefers-reduced-motion: reduce` (the web equivalent of defect D-13's dead Qt probe).

## Components

- **Buttons**: primary (accent fill, ink-on-accent contrast ≥4.5:1), secondary (raised surface + hairline), ghost (text only), destructive (danger text, safe default elsewhere). One shape, one radius, 32px/36px heights.
- **Segmented presets**: a three-cell control on a recessed track; active cell is raised with an accent top rule? No — active uses `--accent-soft` fill and accent text, plus `aria-pressed`. Each has a tooltip stating the real trade-off.
- **Sliders**: custom-tracked range inputs with mono value readouts that state the effective meaning (`Max 150 DPI · trigger > 218 DPI`).
- **Status vocabulary**: `Queued` (muted), `Slimming…` (accent, indeterminate 3-bar shimmer), `Slimmed → size` (success), `Already optimal` (muted, with explanation), `Error: …` (danger, full text on hover/focus).
- **Savings chip**: mono `−71.2%` on `--success-soft`; `Pending` neutral; `Failed` danger tint; `—` when unknown.
- **Inspector**: modal `<dialog>`, dark canvas, paper page with shadow, wipe divider (2px accent + circular handle), corner badges in mono, zoom pills, page control, footer slider and `Open file`.
- **File picker**: modal browser over the server filesystem (the browser cannot read absolute paths), breadcrumb + roots + list; folders navigate on double click, PDFs multi-select.

## States

Every interactive component ships default, hover, focus-visible (`2px accent`, 2px offset), active, disabled (40% opacity, no pointer), and busy. Empty queue teaches the two ways to add files. Loading uses a skeleton row rather than a centred spinner. Errors state the problem and the recovery in the product's voice (`Password required — this file is locked. Unlock and retry.`).

## Browser surfaces

Selection is tinted with `--accent-soft`; the caret and focus ring are accent; scrollbars are thin and themed in both palettes; links use `--accent` with underline offset 3px. Table numerals are tabular everywhere; dates/sizes never shift width between states.

## Accessibility

- WCAG 2.1 AA: body ≥4.5:1, large numerals ≥3:1, verified per theme.
- Keyboard: `Ctrl/Cmd+O` add files, `Ctrl/Cmd+Shift+O` add folder, `Enter` run (not while a text field is focused), `Esc` closes dialogs, inspector arrows page, `+`/`-` zoom, `R` reset, `Ctrl/Cmd+I` inspect first result.
- Icon-only controls have `aria-label` and a `title`; the stepper uses `aria-current="step"`; queue rows expose their status as text, never colour alone; live regions announce run completion, skips and errors.
- Reduced motion honoured; the wipe and bars remain usable without it.
