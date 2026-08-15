# Team portraits

The About page (`src/pages/AboutPage.tsx`) looks for one file per person:

| Basename | Person | The photo |
|---|---|---|
| `maziyar` | Maziyar Azami | smiling, grey sweater, blurred office behind |
| `bardia` | Bardia Azami | black crew-neck, plain grey studio background |

Any of `.jpg`, `.jpeg`, `.png`, `.webp` — they are tried in that order, so the
file goes in as it is and nothing needs editing to match it. If none of them
exists the card shows the person's initial in a tinted circle, the same
fallback `Logo` uses for `/brand.png`, so the page never shows a broken image.

**What to drop in:** a square image, 400×400 or larger. It is drawn in a 128px
circle with `object-fit: cover` and the crop pulled up to `50% 22%`, which puts
the eyes near the optical centre of a normally-framed portrait. If a photo is
framed unusually tight or wide, adjust `object-position` on `.rm-about-photo`
in `src/styles.css` rather than re-cropping the file.

These are static assets served by Vite straight from `public/`, so no import
and no rebuild — the file lands at `/team/<basename>.<ext>` and a reload picks
it up.
