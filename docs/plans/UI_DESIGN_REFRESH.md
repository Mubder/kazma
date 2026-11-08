# UI design refresh: one surface system, the reference's shapes, Kazma's colours

Asked for by the owner on 2026-11-09, in two parts:

1. "There is a different kind of colours in some pages. The card colours
   aren't all in sync with the wall." Four audits measured it (below).
2. A reference the owner likes, to follow in light and dark with Kazma's
   own colours: <https://tailwindadmin-reactjs-dark.netlify.app/>. "Elements,
   menu and etc. all should look like this theme." Then the same design on
   the website, kazma.ai.

We take the reference's design language (surfaces, shapes, spacing, menu and
control styles), measured from the live demo. We do not copy its code,
images or illustrations, and Kazma keeps its own colours, font, logo and
right-to-left layout.

---

## What the audits found (2026-11-09)

Reports in the session's scratchpad (`ui-audit/`): stylesheets, templates,
page scripts, and the rendered pages in both themes.

- **No card token.** Card fills in dark were five different navies; in light
  three greys. The "elevated" surfaces were lighter than the wall in dark and
  darker than it in light, so the same box looked raised in one theme and
  sunken in the other.
- **Pages paint their own cards.** The Dashboard's style block made its cards
  the wall colour in dark while every other page's cards were a lighter navy;
  Settings used 8 tints for one "info" note; the Memory console and Swarm were
  written for dark only (white-alpha fills that vanish in light, black-alpha
  wells that turn grey).
- **Eight colour tokens used but never defined** (`--primary`, `--text`,
  `--warn`, `--ok`, `--err`, `--surface-2`, `--bg-secondary`, `--card-bg`...):
  stripes and pills silently lost their colour.
- **The accent.** Settings brought back the retired indigo `#5e6ad2` and kept
  it on every page after a sidebar navigation; saving Appearance made it
  permanent; the accent code set 4 of about 12 accent tokens.
- About 230 literal colours in page scripts, 418 in templates, 136 in the
  stylesheets; four palettes for status colours.

## The reference, measured

| Element | Light | Dark |
|---|---|---|
| Wall | white | the theme's navy (reference `#1c2536`; Kazma keeps `#0e1626`) |
| Card | same as the wall, radius 12px, padding 24px, shadow `0 0 2px rgba(145,158,171,.30), 0 12px 24px -4px rgba(145,158,171,.02)`; a card inside a page section adds a 1px border | same |
| Hairline | `#e5e5e5` | `#333f55` on its navy |
| Sidebar | wall colour, 270px, 1px hairline on its inner edge; section labels 12px bold upper case | same |
| Menu item | 45px tall, padding 12px, radius 6px, 20px icon + 14px label, muted ink; hover moves 4px along the reading direction and takes the accent | same |
| Active menu item | filled accent pill, white ink | same |
| Header | transparent over the wall, 72px; text links; round icon buttons with a 40px tinted circle on hover | same |
| Dropdown / menu | wall colour + the card shadow, radius 8px; rows: 40px tinted icon tile, 14px semibold title, 12px muted line | same |
| Input / select | transparent, 1px hairline, radius 8px, height 40px | same |
| Label | 14px semibold | same |
| Button | radius 6px, padding 8px 16px, 14px medium; primary filled with white ink; a "soft" variant: 12% tint + the colour's ink | same |
| Tabs | pills, radius 4px; active filled accent with a tiny shadow | same |
| Badge | full pill, padding 2px 10px, 600 weight, 12% tint + the colour's ink | same |
| Icon tile | 40px, radius 6px, 12% tint | same |
| Stat tiles | radius 12px, 12% tint of a status colour, coloured value | same |
| Page header | 12% tint of the accent, radius 12px, title + breadcrumb | same |
| Table | 1px row hairlines, no fills; head 14px semibold | same |
| Type | 14px base; card title 18px/600; subtitle 14px muted; big numbers 24px/600 | same |

## What stays Kazma

- Colours: the royal-blue accent (and the owner's accent setting), Kazma's
  navy dark wall, the darker green, amber and red status colours, readable
  at 4.5:1 for text in both themes.
- Font: IBM Plex Sans with IBM Plex Sans Arabic (vendored).
- Right-to-left: every rule in logical properties; the hover nudge follows
  the reading direction.
- The page structure, routes and controls: this is a restyle, not a new UI.

## Stages

| Stage | What | State |
|---|---|---|
| 1 | Foundation: one surface scale (card = wall, sunken, overlay), status, categorical, on-fill and accent-family tokens; every undefined token defined; one card rule; the accent fixed end to end; gates | in progress |
| 2 | Shell and components: sidebar, header, menu items, dropdowns, buttons, inputs, tabs, badges, tables, page header, icon tiles, stat tiles, toasts, modals | after 1 |
| 3 | Every page onto the tokens and components (Settings, Memory, Workspace, Swarm, Dashboard, then the rest), no literal colours left | after 1, alongside 2 by disjoint files |
| 4 | Verification: every page by the sidebar, light and dark, English and Arabic, measured (card = wall, contrast) and screenshotted; the gates | after 2-3 |
| 5 | The website, kazma.ai: the same surfaces and components | after 4 |

## Gates

- Every `var(--name)` used in the stylesheets, templates and scripts is
  defined; every surface and status token has both themes.
- Card = wall in each theme, and the card rule carries the hairline and the
  shadow; status text meets 4.5:1 on the card in each theme.
- No literal colour in templates or page scripts outside a declared list
  (charts read the tokens); a ratchet for the stylesheets.
- The accent: one call sets it, every accent token follows, nothing wipes it,
  Settings never applies a default the owner did not choose.
- A browser tour reaches every page by the sidebar in both themes and both
  languages, with no card whose fill differs from the wall.
