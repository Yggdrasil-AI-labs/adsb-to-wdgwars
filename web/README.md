# Muninn — web frontend

A static, single-page version of Muninn that runs entirely in the browser.
The Python parsers from `../muninn.py` execute client-side via
[Pyodide](https://pyodide.org/), so a dropped capture file never leaves the
user's machine until they click **Upload**.

**Headless / CLI users:** ignore this directory. You don't need it. The
CLI in the repo root (`muninn.py`) is completely independent — no shared
runtime, no shared deps, no display required.

## What's here

- `index.html` — the page itself
- `app.js` — drop-zone wiring + Pyodide bootstrap + HMAC upload
- `muninn.py` — build-time copy of the root `muninn.py` (the parsers run as-is)

## Local preview (read-only — parse / download only)

Pyodide and ES modules require an HTTP server — opening `index.html`
directly via `file://` will not work.

```bash
cd web
python3 -m http.server 8000
# then visit http://localhost:8000
```

`http.server` only serves static files, so the in-browser **Direct
upload** button will still fail with CORS — the upload UI shows up
because the page is no longer on `*.github.io`, but the actual POST
to `wdgwars.pl` is blocked. For local preview / debugging this is fine.

## Self-hosted with direct upload (`serve.py`)

For self-hosters who want the in-browser upload button to actually
work, the repo ships a small stdlib-only server that serves the static
files **and proxies `/api/upload/` to `wdgwars.pl`**. Because the
browser sees a same-origin POST, CORS doesn't apply; the server-to-
server forward inherits no such restriction.

```bash
cd web
python3 serve.py          # binds 127.0.0.1:8765 by default
# python3 serve.py --port 8000 --host 0.0.0.0   # bind everywhere
```

Open the page, then in **Settings → Endpoint** change the value from
`https://wdgwars.pl/api/upload/` to the relative path:

```
/api/upload/
```

Now Direct upload routes through the local proxy and reaches WDG. Your
API key never leaves your machine in the page — it travels to `serve.py`
over loopback and then to `wdgwars.pl` server-to-server.

## Public deploy: parse-only

When the site is served from `*.github.io`, the upload UI is hidden
entirely (button, dry-run toggle, uplink config). The page becomes a
pure converter: drop → preview → download. Players take the downloaded
JSON to wdgwars.pl's normal upload form.

This is detected at runtime in `app.js`:

```js
const IS_PUBLIC_DEPLOY = /\.github\.io$/i.test(location.hostname);
```

## Keeping `web/muninn.py` in sync

The web bundle ships a copy of the root parser. Before deploying:

```bash
cp muninn.py web/muninn.py
```

Or (cleaner) wire this into a GitHub Action that copies on every release tag.

## Deployment

This directory is designed to be served as static files. Any static host
works — GitHub Pages, Netlify, Cloudflare Pages, an `nginx` block on
your own box. There is no server-side component.
