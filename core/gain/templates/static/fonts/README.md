# Fonts, vendored

The two faces the GRR's generated pages use. `grr_manage repo-info` /
`repo-index` publish them into every repository they index, under
`.static/fonts/` beside `index.html`, and each page declares them with
`@font-face` blocks whose `url()` is relative to the page
(`roboto_font.jinja`, `icon_font.jinja`). The repository therefore
renders on an intranet or behind an air gap exactly as on the web: no
Google Fonts at view time (gain#1400).

Why this matters more for the icons than for the typeface: Material
Symbols draws by *ligature* -- an element's text is the glyph's name,
and the font substitutes the glyph. With the font unreachable, Roboto
degrades to the system font, but every icon renders as its name: the
words `unfold_more` beside each column title, `folder` before each tree
row.

## Contents

| File                                   | What it is                                                                 |
| -------------------------------------- | -------------------------------------------------------------------------- |
| `roboto-v51-latin.woff2`               | Roboto, Google Fonts release `v51` (font version 3.015), the **latin** subset, variable across `wght` 100–900 (43 kB). One file covers every weight the pages set |
| `material-symbols-outlined-v371.woff2` | Material Symbols Outlined, Google Fonts release `v371` (font version 2.969), subsetted to the **eight glyphs** the pages draw (6 kB, against ~3.8 MB for the whole family). Variable across `FILL`, `GRAD`, `opsz` and `wght` |
| `LICENSE-Roboto.txt`                   | SIL Open Font License 1.1, with the Roboto copyright line, as the Roboto repository ships it |
| `LICENSE-Material-Symbols.txt`         | Apache License 2.0                                                          |

There is no `version.txt`, unlike `../sqlite-wasm/`: the published name
of each file carries a digest of its bytes
(`roboto-v51-latin.<sha256[:8]>.woff2`), computed by
`gain.templates.static_assets` at import. Nothing but the page beside a
font binds to its name, and the page is republished on every run, so a
refreshed file changes its own url with no version to bump by hand --
which is what keeps a browser holding yesterday's icon subset from
rendering a glyph added today in words.

## Which glyphs

`core/tests/small/templates/test_grr_page_icon_font.py` is the authority:
its `EXPECTED_GLYPHS` is the hand-written set the browse page can draw,
it scans the rendered page to hold that set honest, and it decodes this
file's `GSUB` ligature table to hold the file to the same set. A glyph
drawn on a page but missing here fails that test rather than rendering
as a word in a browser. Google's subsetter emits every ligature name a
glyph answers to, so the file also carries `clear` (an alias of the
`close` glyph); the test lists such aliases.

The sorter on the resource pages draws three of the eight
(`test_resource_page_sorter.py` holds the file to those too), so one file
serves every page.

## Provenance

Both files are exactly what Google Fonts serves a current Chromium for
the stylesheets the pages linked before gain#1400, unmodified:

- Roboto: <https://fonts.google.com/specimen/Roboto>, licence
  [OFL-1.1](https://openfontlicense.org) (the font's own `name` table
  carries the copyright and the licence url). Source repository
  <https://github.com/googlefonts/roboto-3-classic>.
- Material Symbols: <https://fonts.google.com/icons>, licence
  Apache-2.0. Source repository
  <https://github.com/google/material-design-icons>.

SHA-256 at vendoring (2026-09-14):

- `roboto-v51-latin.woff2`
  `1404ca348bd75ef836f4dd8b6f2cc719458642d1237c368296b2fc652dca47dc`
- `material-symbols-outlined-v371.woff2`
  `68a25c8772830b271da8e900b000c0755d36dad6e2b8be9ad913aa6c2ee1fb28`

## Refreshing, or adding a glyph

Google Fonts hands out a `woff2` only to a browser that accepts one, so
the stylesheet has to be fetched with a browser's `User-Agent`; the
`@font-face` blocks it returns name the file urls on `fonts.gstatic.com`.

```bash
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

# Roboto: one variable file per unicode-range block; take the `latin` one.
curl -sS -A "$UA" 'https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;800&display=swap'

# Material Symbols: `icon_names` is the subset.  The list must equal
# EXPECTED_GLYPHS in test_grr_page_icon_font.py -- add the new glyph to
# both, in one change.
curl -sS -A "$UA" 'https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&icon_names=arrow_downward,arrow_upward,check,close,content_copy,description,folder,unfold_more&display=block'

# Then, for each, the url() the stylesheet names:
curl -sS -o <file>.woff2 '<url from the stylesheet>'
sha256sum *.woff2     # update the digests above
```

If the Google Fonts release number in the url changed (`v51`, `v371`),
rename the file to match and update the name in the three places that
spell it: `_ROBOTO` / `_MATERIAL_SYMBOLS` in
`gain/templates/static_assets.py`, the table and digests above, and the
served-file stems in `info_pages_e2e/tests/index-page.spec.ts`. The
`unicode-range` in `roboto_font.jinja` is the one the `latin` block
declares and should be checked against it. The next `repo-info`
publishes the new file under its new digest; the old one is left in
place -- and, in a GRR kept in git, stays committed -- until deleted by
hand.
