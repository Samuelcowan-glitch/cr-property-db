# Mustica Pro

The CRM is set entirely in Mustica Pro. The licensed font files are **not** in
this repository — Mustica Pro is a commercial typeface and the licence is not
ours to publish, particularly while this repository is public.

Drop the files you have licensed into this folder using these exact names:

| Weight | Used for                                                      | Filename                   |
| -----: | ------------------------------------------------------------- | -------------------------- |
|    400 | body text, field values, inputs, dropdowns, notes, table content | `MusticaPro-Regular.woff2`  |
|    500 | labels, navigation, secondary buttons                          | `MusticaPro-Medium.woff2`   |
|    600 | page titles, section and card headings, primary buttons        | `MusticaPro-SemiBold.woff2` |
|    700 | totals, financial figures, strong emphasis                     | `MusticaPro-Bold.woff2`     |

`.woff` and `.otf` work too — the stylesheet lists all three formats for each
weight and the browser takes the first it can read. `.woff2` is much the
smallest, so convert to it if you have the choice.

Until the files are here the CRM falls back to the system sans, which is what
it was doing before: the `Inter` it named was loaded from Google Fonts and has
now been removed, so nothing is fetched from a font service and no page view
leaves the office.

Only add the weights you are licensed for. `font-synthesis` is switched off on
purpose, so a weight with no file falls back rather than being faked — a gap
shows up as a plain fallback instead of a smeared bold.

Nothing else needs changing: `static/css/style.css` declares the faces and the
whole CRM reads one `--font` token.
