# Making it your own format

Everything the bot stamps onto a post — the account name, the colours,
the hashtags, the caption voice, and the **news-graphic design** — is
controlled by one optional file: `assets/branding.json`. No code, no
redeploy of logic. Change that file (and drop in your own images/fonts),
restart, done.

If `assets/branding.json` doesn't exist, you get the original Signal
Europe look. Copy the example to start:

```bash
cp assets/branding.example.json assets/branding.json
```

Then edit it. **Every field is optional** — keep only the ones you want
to change; the rest fall back to defaults.

```jsonc
{
  "brand_name": "EUROMAXXING",              // watermark + news brand mark + caption voice
  "accent_hex": "#FFD400",                  // your brand colour (badge, wordmark, accents)
  "hashtags": "#Euromaxxing #Europe #Tech", // appended to every caption
  "caption": {
    "niche": "European tech, startups, and culture", // what the account is about
    "angle": "European"                              // the slant the hook leans into ("" to drop it)
  },
  "news": { /* the NEWS graphic — see below */ }
}
```

- **`brand_name`** — shown as the watermark wordmark (`/format`), the news-graphic brand mark, and used by the AI caption writer when it refers to the account.
- **`accent_hex`** — your single brand colour. Drives the watermark text, the `BREAKING` badge, and the brand mark.
- **`hashtags`** — the exact hashtag line appended to every reposted/news caption.
- **`caption.niche` / `caption.angle`** — steer the AI captions. `niche` is what you cover; `angle` is the slant every hook tries to take (e.g. `"European"`). Set `angle` to `""` to let the hook lead with whatever fits.

## Your own news-graphic template (Figma / Canva)

The `NEWS` command builds a 1080×1920 (9:16) Reel graphic. You can
replace the whole look with a design **you** make in Figma or Canva.

### 1. Design a 1080×1920 background

In Figma or Canva, make a **1080 × 1920 px** frame. Put in everything
that stays the same on every post:

- your logo, brand colours, gradients, textures
- any fixed bands, borders, a footer with your handle
- a label like `BREAKING` **if** you'd rather bake it in than let the bot draw it

**Leave an empty area** (roughly the middle of the frame) where the bot
will draw the **headline** and **description** text. Don't put anything
there you don't want text on top of.

Export it as a **PNG** and save it to `assets/template/news_bg.png`.

### 2. Tell the bot where the text goes

In `assets/branding.json`, the `news` block positions the dynamic text
over your background. Coordinates are pixels from the top-left of the
1080×1920 canvas.

```jsonc
"news": {
  "background_image": "assets/template/news_bg.png",
  "text_color": "#FFFFFF",        // headline colour
  "muted_color": "#AAB0C0",       // description colour

  "badge": { "enabled": true, "text": "BREAKING", "x": 84, "y": 84 },
  // set "enabled": false if your background already has a label

  "headline": {
    "x": 84, "y": 560,            // top-left of the headline block
    "width": 912,                 // wrap width; font auto-shrinks to fit
    "max_lines": 4,
    "size_max": 110, "size_min": 58
  },
  "description": {
    "x": 84, "width": 912,        // y is automatic (sits under the headline)
    "size": 44, "line_height": 60, "gap": 60
  },

  "show_bottom_brand": false,     // bot draws your brand_name at the bottom; off if your bg has it
  "show_top_logo": false          // bot draws a logo top-right; off if your bg has it
}
```

**Typical setup for a custom template:** turn `show_bottom_brand` and
`show_top_logo` **off** (your design already carries your branding),
disable the `badge` if you drew your own, and tune `headline.y` /
`width` so the text lands in your empty area. Send a `NEWS` message and
the bot replies with a preview — nudge the numbers until it sits right.

### Fonts & logo (optional)

- Drop `assets/fonts/Bold.ttf` and `assets/fonts/Regular.ttf` to use your brand fonts everywhere (they must cover any accents you use — €, á, ü…).
- Drop `assets/logo/logo.png` (transparent PNG) to use a real logo for the watermark and the news top-right mark instead of a text wordmark.

## Reposting a post "in your format"

Two ways, depending on what you want:

- **`/format <link>`** — downloads the original media and stamps *your*
  watermark (brand_name + accent, bottom-right) onto every slide/frame,
  keeping the original shape. The post keeps its original imagery but
  now carries your mark.
- **`NEWS` + headline + description** — ignores the original imagery and
  **rebuilds** the announcement from scratch on *your* news template.
  This is the "redo it as a clean [your brand] card" path: you read a
  post somewhere, type the story in your own words, and the bot renders
  it in your design.

In both cases the caption is written in your voice (`brand_name`,
`niche`, `angle`) with your hashtags.

> On Railway (deploy-from-GitHub), the bot runs from **your repo**, so
> your branding files have to be committed to your fork to take effect:
> `assets/branding.json`, your `assets/template/news_bg.png`, and any
> `assets/logo/logo.png`. They're intentionally not gitignored. Commit
> them to your own (private) fork and Railway picks them up on the next
> deploy.
