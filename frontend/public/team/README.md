# Team portraits

The About page (`src/pages/AboutPage.tsx`) reads two files from here:

| File | Person |
|---|---|
| `maziyar.jpg` | Maziyar Azami |
| `bardia.jpg` | Bardia Azami |

Neither is required. A missing file falls back to the person's initial in a
tinted circle of the same size — the same bargain `Logo` makes with
`/brand.png` — so the page never shows a broken image.

**What to drop in:** a square image, 400×400 or larger, JPEG or PNG (keep the
`.jpg` name either way, or change the `photo` field in `TEAM`). It is drawn in
a 112px circle with `object-fit: cover` and the crop pulled up to `50% 22%`,
which puts the eyes near the optical centre of a normally-framed portrait. If
a photo is framed unusually tight or wide, adjust `object-position` on
`.rm-about-photo` in `src/styles.css` rather than re-cropping the file.

These are served as static assets by Vite from `public/`, so no import and no
rebuild of the bundle is needed — the file is at `/team/<name>.jpg`.
