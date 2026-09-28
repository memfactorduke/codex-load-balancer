# codexpool website

The marketing site for codexpool: one static page, no build step, no frameworks and no external requests
(system fonts, inline SVG). This folder is the whole site; deploy it as it is.

```
site/
  index.html            the page
  404.html              self-contained "page not found" (inline styles, so it works at any depth)
  robots.txt
  assets/style.css      all styles; light and dark follow prefers-color-scheme
  assets/site.js        copy buttons, phone menu, diagram pause, ?theme=; the page works without it
  assets/img/           favicon.svg, apple-touch-icon.png, og.png (1200 × 630) and the screenshots
```

## Preview locally

```sh
python3 -m http.server 8000 --directory site
```

Then open <http://localhost:8000/>. Opening `site/index.html` straight from disk works too.

Add `?theme=light` or `?theme=dark` to the URL to force an appearance, for example when taking screenshots:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --virtual-time-budget=3000 --window-size=1440,11000 --screenshot=site-dark.png \
  "file://$PWD/site/index.html?theme=dark"
```

The whole page is about 10,900 px tall at 1440 px wide. The diagram animates, so Chrome can keep running after
it has written the screenshot; stop it with Ctrl-C once the file is there. To look at a tall screenshot in
pieces, crop it with `sips -c 1200 1440 --cropOffset Y 0`, but start the top piece at `Y` = 1: sips ignores
`--cropOffset 0 0` and crops from the middle of the page instead, which looks as if the page starts at a later
section.

Headless Chrome won't lay a page out narrower than 500 px (a narrower `--window-size` only crops the screenshot);
for phone widths use the device toolbar in Chrome's DevTools (390 × 844, for example), or screenshot a local page
that shows `index.html?theme=light` in a 390 px wide `<iframe>` (tall enough for the page, about 17,000 px) and crop
the result to 390 px.

## Deploy

### GitHub Pages

[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) deploys this folder whenever it is run by hand
(Actions → Pages → Run workflow), and on pushes to `main` that change `site/` once the repository variable
`PAGES_ENABLED` is `true` (until then those pushes skip the job instead of failing it). Going live is two one-time
steps: repository **Settings → Pages → Build and deployment → Source: GitHub Actions**, then
`gh variable set PAGES_ENABLED --body true` (or Settings → Secrets and variables → Actions → Variables). The site
then lives at <https://memfactorduke.github.io/codex-load-balancer/>.

### Netlify

New site from Git, pick the repository, leave the build command empty and set the publish directory to `site`.
Or drag the `site` folder onto <https://app.netlify.com/drop>. Netlify serves `404.html` for missing pages by
itself.

### Vercel

Import the repository, choose the framework preset **Other**, set the root directory to `site` and leave the build
and output settings empty. Vercel serves `404.html` for missing pages by itself.

### Any other static host

Copy the contents of `site/` to the web root, for example `rsync -av site/ user@host:/var/www/codexpool/`, and
point the host's "not found" page at `/404.html` if it doesn't pick it up on its own.

## Custom domain

1. **Add the domain at the host.** GitHub Pages: Settings → Pages → Custom domain (with a workflow deployment no
   `CNAME` file is needed), then tick **Enforce HTTPS** once the certificate is issued. Netlify and Vercel: add
   it under the project's domains.
2. **Point DNS at it.** For a subdomain such as `codexpool.example.com`, add a `CNAME` record to
   `memfactorduke.github.io` (or the target Netlify or Vercel shows you). For an apex domain on GitHub Pages, add
   `A` records for `185.199.108.153`, `185.199.109.153`, `185.199.110.153` and `185.199.111.153`
   (and `AAAA` records for `2606:50c0:8000::153` through `2606:50c0:8003::153` for IPv6).
3. **Update the absolute URLs.** The canonical link, `og:url`, `og:image` and `twitter:image` in `index.html` and
   the "Back to codexpool" link in `404.html` use the GitHub Pages address. Replace it in both pages (and only
   there, so this README keeps its instructions):

   ```sh
   sed -i '' 's#https://memfactorduke.github.io/codex-load-balancer/#https://codexpool.example.com/#g' \
     site/index.html site/404.html
   ```

   (On Linux, use `sed -i` without the `''`.)

## Images

- `popover-light.webp`, `popover-dark.webp` and `popover-reserve-light.webp` are `docs/images/popover-light.png`,
  `popover-dark.png` and `popover-reserve-light.png` cropped to the popover card (from 56,56 to the outer edge of
  its border: 680 × 1566 px, 1570 px for the reserve one), with the corners rounded at a 30 px radius and
  encoded with `cwebp -q 90`. They are 2× images shown 340 px wide, so each `<img>` has `width="340"` and half
  the pixel height; update the height when a new crop is taller.
- The window screenshots are 2× renders from the synthetic data in `docs/images/demo/`, encoded as lossless
  WebP (`cwebp -lossless -z 9`), so the text stays pixel-exact at a third of the PNG's size. The page expects
  four files, and shows an empty window in place of any that is missing:

  | File | Source |
  |---|---|
  | `settings-overview-light.webp` (1776 × 1512) | `docs/images/settings-overview-light.png` |
  | `settings-overview-dark.webp` (1776 × 1512) | `docs/images/settings-overview-dark.png` |
  | `setup-signin-light.webp` (1416 × 1346) | `docs/images/setup-signin-light.png` |
  | `setup-signin-dark.webp` (1416 × 1346) | the same Setup assistant step in dark, rendered below |

  ```sh
  python menubar/codexpool_settings.py --snapshot setup-signin-dark.png --pane setup-signin \
    --appearance dark --status docs/images/demo/status-regular.json --doctor docs/images/demo/doctor.json \
    --lanes docs/images/demo/lanes.json --history docs/images/demo/history-regular.jsonl \
    --now 2026-09-24T16:41:00Z
  cwebp -lossless -z 9 setup-signin-dark.png -o site/assets/img/setup-signin-dark.webp
  ```

  (Use a Python 3.11+ with PyObjC, such as the menu bar app's venv. Snapshot mode runs no command. These are
  the arguments `docs/images/demo/render.py` uses, so the light render comes out byte for byte the same as the
  one in `docs/images/`.) If a screenshot's size changes, update the `width` and `height` on its `<img>` and
  the column ratio in `.shots-windows` in `style.css`, which is each screenshot's width divided by its height.
- `og.png` is the social preview (1200 × 630): the hero's wordmark, headline and dark popover on the dark
  wallpaper, captured with headless Chrome.
- `favicon.svg` and `apple-touch-icon.png` are the capsule mark: two stacked rounded bars, green on dark.

Every screenshot comes from made-up seats. Never put a real account, email or seat name on the site.

## Budget and checks

- Keep the page under 300 KB without the screenshots: `index.html`, `style.css`, `site.js` and the SVGs are
  about 90 KB together, uncompressed.
- Check both appearances, a 390 px phone and a 1440 px desktop, keyboard focus (Tab through the page) and
  reduced motion (the diagram then shows its resting state: Seat 1 out, Seat 2 serving, and no pause button).
- The disclaimers in the footer and the FAQ stay: codexpool is independent of OpenAI, and people should use it
  only with accounts they own, following the terms that apply to them.
