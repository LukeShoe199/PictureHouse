# Making a PictureHouse theme

A theme changes how PictureHouse looks: colours, fonts, corner rounding, where
the navigation sits, and anything else CSS can style. Themes apply to the web
console everywhere it appears: browsers, the Windows and Linux apps, and the
Android app. The LG TV interface follows a theme's colours and fonts.

PictureHouse comes with two themes:

- **PictureHouse** (`picturehouse`). The original look: a dark cinema with
  amber highlights, condensed poster titles and a side rail. It sets nothing,
  because it *is* the built-in look.
- **iStream** (`istream`). Inspired by Apple TV: a frosted top bar, the system
  font in sentence case, white highlights, and artwork that lifts when chosen.
  It's the reference example: [`src/picturehouse/themes/builtin/istream`](../src/picturehouse/themes/builtin/istream).

## Using themes

- **On one device:** *Library → Appearance → Use on this device*. The choice is
  stored in that browser or app only.
- **For every device** (admin): *Library → Appearance → Make default for all
  devices*. Devices without their own choice use it from the next time they
  open or reload PictureHouse (the device you change it on, straight away).
- **From the command line:**
  ```bash
  picturehouse theme list                  # * marks the default
  picturehouse theme check ./my-theme      # check a theme folder, install nothing
  picturehouse theme install ./my-theme    # check it, then copy it into the data folder
  picturehouse theme install ./my-theme --replace   # update it
  picturehouse theme default my-theme      # everyone's default (applies after a restart)
  picturehouse theme remove my-theme
  ```
  On ZimaOS or Docker, run these inside the container, e.g.
  `sudo docker exec picturehouse picturehouse theme list`.

Installed themes live in the data folder, under `themes/<id>/`. Edit a theme
there and reload the page: PictureHouse re-reads a theme whenever its files
change.

## What a theme is

A folder whose name is the theme's id:

```
my-theme/
  theme.json      required: the theme language (below)
  theme.css       optional: restyle anything else
  assets/         optional: images and fonts that theme.css uses
```

### theme.json

```json
{
  "schema": 1,
  "id": "my-theme",
  "name": "My Theme",
  "version": "1.0.0",
  "author": "Your name",
  "description": "One or two sentences shown in Library → Appearance.",
  "scheme": "dark",
  "layout": { "nav": "top" },
  "tokens": {
    "color.background": "#101418",
    "color.accent": "#4fd1c5",
    "color.on-accent": "#00201c",
    "radius.card": "10px"
  },
  "tv": { "tokens": { "color.accent": "#81e6d9" } },
  "stylesheet": "theme.css"
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | Always `1` (the version of this theme language). |
| `id` | yes | 2 to 40 lowercase letters, digits or `-`; must match the folder name. Can't reuse a built-in theme's id. |
| `name` | yes | Shown to people, up to 40 characters. |
| `version`, `author`, `description` | no | Shown in Library → Appearance (up to 20, 60 and 300 characters). |
| `scheme` | no | `"dark"` (default) or `"light"`: tells browsers which way to draw scrollbars and form controls. |
| `layout` | no | Layout options, see below. |
| `tokens` | no | Design tokens, see below. Anything you leave out keeps the PictureHouse value. |
| `tv` | no | `{"tokens": {...}}`: different values for the TV interface (only tokens marked *TV* below). |
| `stylesheet` | no | `"theme.css"` if the theme has one. |

Unknown fields, tokens or values are refused with a message saying what's
wrong; run `picturehouse theme check` while you work.

### Tokens

Tokens are the theme language's vocabulary: named design decisions that the
whole interface uses. Changing `color.accent` recolours every focus ring,
progress bar, primary button and active tab at once.

| Token | Type | Used for | PictureHouse value | TV |
|---|---|---|---|---|
| `color.background` | color | Page background | `#0a0d11` | yes |
| `color.navigation` | color | Navigation rail / top bar | `#080a0d` |  |
| `color.surface` | color | Cards, panels, inputs | `#121821` | yes |
| `color.surface-raised` | color | Menus, dialogs, raised panels | `#19212c` | yes |
| `color.line` | color | Borders and dividers | `#243041` | yes |
| `color.text` | color | Main text | `#eef1f4` | yes |
| `color.text-muted` | color | Secondary text | `#93a0b0` | yes |
| `color.text-faint` | color | Hints, table headings | `#5f6b7a` |  |
| `color.accent` | color | Highlights: focus, progress, primary buttons, active tab | `#f2b33d` | yes |
| `color.on-accent` | color | Text on the accent colour | `#1b1305` | yes |
| `color.good` | color | OK / online / done | `#5fc48b` |  |
| `color.info` | color | Informational (e.g. converting) | `#7fb2e5` |  |
| `color.warning` | color | Warnings, offline | `#ec8a4c` |  |
| `color.error` | color | Errors | `#e5605c` | yes |
| `font.display` | font | Headings and titles | `"Archivo", "Arial Narrow", "Helvetica Neue", Arial, sans-serif` | yes |
| `font.body` | font | Everything else | `"Figtree", "Segoe UI", system-ui, -apple-system, sans-serif` | yes |
| `font.mono` | font | Paths, codes, technical details | `"JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace` |  |
| `size.gutter` | length | Page side margins | `clamp(16px, 3vw, 40px)` |  |
| `size.navigation` | length | Width of the side rail (or height of the top bar) | `88px` |  |
| `radius.card` | length | Artwork corners | `7px` |  |
| `radius.button` | length | Button corners | `8px` |  |
| `radius.panel` | length | Panel, menu and dialog corners | `12px` |  |
| `radius.hero` | length | Big banner (hero) corners | `14px` |  |

Value types:

- **color**: `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb()`/`rgba()`, `hsl()`/`hsla()`,
  or a colour name.
- **font**: a CSS font list, such as `"My Font", Georgia, serif`. Use fonts
  installed on the device, or ship your own with `@font-face` in theme.css.
- **length**: e.g. `12px`, `0.75rem`, `clamp(12px, 2vw, 20px)`.

In theme.css, each token is a CSS variable: `color.accent` is `var(--accent)`,
`color.text` is `var(--fg)`, `radius.card` is `var(--r-card)`, and so on. The
built-in themes' CSS is the best guide.

### Layout options

| Option | Values | Effect |
|---|---|---|
| `nav` | `"rail"` (default), `"top"` | Where the main navigation sits on tablets and computers. `"top"` puts it in a bar across the top with centred tabs; `size.navigation` is then the bar's height (iStream uses `64px`). Phones always use the bottom tab bar. |

### theme.css

Anything tokens can't express goes here: glass effects, typography details,
hover effects, different shadows. It's added after PictureHouse's own styles,
so plain selectors win. These class names are kept stable for themes:

| Area | Classes |
|---|---|
| Navigation | `.rail` (rail or top bar), `.logo`, `.navbtn`, `.navbtn.on` (current tab) |
| Page | `.main`, `.top` (search row), `.search`, `.devbtn`, `.devmenu`, `.page-head h1`, `.sec`, `.sec-head h2` |
| Banner | `.hero`, `.hero-img`, `.hero-shade`, `.hero-title`, `.hero-syn`, `.eyebrow`, `.meta` |
| Artwork | `.card`, `.card .art`, `.card .cap`, `.poster`, `.poster .ptitle` (title on generated posters), `.land`, `.square`, `.row`, `.grid` |
| Controls | `.btn`, `.btn.primary`, `.btn.small`, `.chip`, `.chip.on`, `.link`, `.pill`, `.field` |
| Details | `.d-back`, `.d-poster`, `.d-title`, `.d-syn`, `.badge`, `.ep` (episode), `.trk` (track) |
| Panels | `.panel`, `.stat`, `.sheet`, `.sheet-box`, `.mini` (music bar), `.toast` |
| Player | `.player`, `.ptop`, `.pctrl`, `.pbtn`, `.pbtn.big`, `.scrub` |

**What theme.css can't do.** These rules keep themes safe to share:

- no `@import`, scripts, `expression()` or backslash escapes;
- no web addresses: every `url()` must point at a file in the theme's own
  folder, such as `url(assets/backdrop.jpg)`. Allowed file types are PNG,
  JPEG, WebP, GIF, AVIF, WOFF, WOFF2, TTF and OTF (no SVG: it can carry
  scripts);
- at most 256 KB.

PictureHouse checks this when it loads the theme. A theme that breaks a rule
isn't used, and Library → Appearance shows the admin why.

### Light themes

Set `"scheme": "light"` and light colour tokens. A few details (some text on
artwork, the player) are drawn light-on-dark whatever the theme, which is
deliberate: they sit on pictures. If something else looks wrong on a light
background, adjust it in theme.css.

## A minimal theme

`teal/theme.json`:

```json
{
  "schema": 1,
  "id": "teal",
  "name": "Teal",
  "tokens": {
    "color.accent": "#2dd4bf",
    "color.on-accent": "#042f2e",
    "radius.card": "12px"
  }
}
```

```bash
picturehouse theme install ./teal
picturehouse theme default teal
```
