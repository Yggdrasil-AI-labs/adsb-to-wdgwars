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

## Local preview

Pyodide and ES modules require an HTTP server — opening `index.html`
directly via `file://` will not work.

```bash
cd web
python3 -m http.server 8000
# then visit http://localhost:8000
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
