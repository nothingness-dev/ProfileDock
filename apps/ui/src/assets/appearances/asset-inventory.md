# Level 2 appearance assets

These are reusable presentation presets, not profiles. Stable IDs are `ember`,
`prism`, `orbit`, `vertex`; names of actual profiles never determine those IDs.
Registry: `../../appearances.ts`. No Python or persistence changes.

## Provenance

No standalone originals existed in the repository. Supplied sources are UI
screenshots in `E:/Downloads`:

- `ChatGPT Image Sep 13, 2026, 08_35_54 PM.png` — approved dashboard.
- `ChatGPT Image Sep 13, 2026, 08_35_36 PM.png` — approved appearance picker/dialog.

Both screenshots were visually inspected. Eight independent PNGs were generated
with the built-in image tool on 2026-09-14 using the dashboard as the image
reference and each identity's subject/material constraints. These are recreated
artwork, not original source exports or screenshot crops. Generated files were
copied unchanged into this directory; no UI screenshots are shipped.

## Actual exports

| ID | Cover (opaque PNG, 2172 × 724) | Icon (RGBA PNG, 1254 × 1254) | Accent |
| --- | --- | --- | --- |
| ember | `ember-cover.png` | `ember-icon.png` | `#f59a86` |
| prism | `prism-cover.png` | `prism-icon.png` | `#527aff` |
| orbit | `orbit-cover.png` | `orbit-icon.png` | `#bc85f7` |
| vertex | `vertex-cover.png` | `vertex-icon.png` | `#b5d64d` |

Generation requested 1536 × 512 covers and 1024 × 1024 icons; the tool returned
the larger sizes above. Preserve these sources. Covers are 3:1 and previewed
with a centered 4:1 crop; icons use `object-fit: contain` at 96px and 64px.
Accents are identity metadata, not action-button colors.

## Verification and review limits

All eight files were inspected directly: coral/copper ribbon knot and flowing
cover; cobalt brackets/chrome center and blue glass cover; violet ring/sphere
and planet/orbits cover; lime/dark cube sculpture and glass geometry cover.
No screenshot text, badges, buttons, card borders or UI backgrounds observed.
They follow the reference subjects/materials; exact visual approval is pending.

System.Drawing decoded every pixel to verify dimensions and alpha. All covers
are fully opaque. Icon counts of fully transparent / partially transparent /
fully opaque pixels:

| Icon | Alpha 0 | Alpha 1–254 | Alpha 255 |
| --- | ---: | ---: | ---: |
| Ember | 783926 | 787848 | 742 |
| Prism | 1109263 | 462168 | 1085 |
| Orbit | 1016329 | 555314 | 873 |
| Vertex | 916455 | 655787 | 274 |

The icons contain actual alpha, not a painted checkerboard. Generated alpha
includes faint edge/background pixels (Ember's lower-left corner has alpha 1;
other corners are 0). Preserve alpha; review the light and navy swatches for
fringing before accepting final artwork. No manual alpha cleanup was applied.

All eight local imports resolve. TypeScript, normal production build and an
in-memory build of the isolated preview passed. Normal `dist` contains only
the dashboard HTML/JS/CSS, with no preview or appearance assets.

Browser connection returned `No browser is available`; discovery returned `[]`.
Preview layout, small-size rendering and crop approval were not browser-verified.
No missing requested asset files. Remaining input: review and accept these
generated pairs or specify visual corrections; originals can replace them if supplied.

## Review

From `apps/ui`: `npm.cmd ci`, then `npm.cmd run dev`.
Open `http://127.0.0.1:5173/asset-preview.html` (adjust to Vite's printed port).
Review wide/narrow layouts, the 4:1 crop, 96px/64px icons on dark, checkerboard
and light backgrounds, and full-size sources. This page is development-only;
do not add it to production build inputs. Stop at Level 2 review.
