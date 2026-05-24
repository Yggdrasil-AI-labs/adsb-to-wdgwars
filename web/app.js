// Muninn — web frontend. Loads Pyodide, runs the muninn.py parsers
// client-side against a dropped file, then offers download or upload.

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const resultsEl = $("results");
const summaryEl = $("summary");
const previewBody = $("preview").querySelector("tbody");
const dropEl = $("drop");
const fileInput = $("file");
const downloadBtn = $("download");
const uploadBtn = $("upload");
const uploadStatusEl = $("upload-status");
const dryrunEl = $("dryrun");
const dryOutputEl = $("dry-output");

// Public GitHub Pages deploys can't direct-upload (CORS), so the upload UI
// (button, dry-run toggle, uplink config, player hint) is hidden when we're
// on *.github.io. Everywhere else (localhost, self-hosted, custom domain)
// the upload path is available — the self-hosted serve.py proxies it via
// same-origin so CORS doesn't apply.
// The class is already on <html> from the inline head script (so first paint
// doesn't flash the upload UI). Keep this in sync for any code path that
// reads the flag later.
const IS_PUBLIC_DEPLOY = document.documentElement.classList.contains("public-deploy");
const apikeyEl = $("apikey");
const apiurlEl = $("apiurl");
const versionPill = $("version-pill");

// Restore stored API key / URL.
apikeyEl.value = localStorage.getItem("muninn.apikey") || "";
apiurlEl.value = localStorage.getItem("muninn.apiurl") || "https://wdgwars.pl/api/upload/";
apikeyEl.addEventListener("change", () => localStorage.setItem("muninn.apikey", apikeyEl.value));
apiurlEl.addEventListener("change", () => localStorage.setItem("muninn.apiurl", apiurlEl.value));

let pyodide = null;
let lastPayload = null;        // { records: [...], filename: str, format: str }
let muninnVersion = "?";

function setStatus(msg, cls = "") {
  statusEl.className = "status " + cls;
  statusEl.textContent = msg;
}

function setUploadStatus(msg, cls = "") {
  uploadStatusEl.className = "status " + cls;
  uploadStatusEl.textContent = msg;
}

async function bootPyodide() {
  setStatus("Loading Pyodide runtime (one-time, ~10 MB)...", "");
  pyodide = await loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/",
  });

  // Pyodide unvendors `ssl` from the stdlib; muninn.py imports it for the
  // CLI's urllib uploads. The web frontend uses fetch() directly so the ssl
  // code path is dead, but the top-level import has to resolve.
  setStatus("Loading runtime modules...", "");
  await pyodide.loadPackage(["micropip", "ssl"]);

  // pyModeS is required for AVR raw Mode-S frame decoding. Pure-Python wheel.
  setStatus("Installing pyModeS (required for raw AVR captures)...", "");
  await pyodide.runPythonAsync(`
import micropip
await micropip.install("pyModeS")
`);

  // Pull our muninn.py from the same directory and write into Pyodide's FS.
  const src = await fetch("./muninn.py").then(r => {
    if (!r.ok) throw new Error("could not fetch muninn.py (" + r.status + ")");
    return r.text();
  });
  pyodide.FS.writeFile("/muninn.py", src);
  try { pyodide.FS.mkdir("/tmp"); } catch (_) {}

  muninnVersion = pyodide.runPython("import sys; sys.path.insert(0, '/'); import muninn; muninn.__version__");
  versionPill.textContent = "muninn " + muninnVersion;
  setStatus("Ready. Drop an ADS-B file above.", "ok");
}

function makeRecord(r) {
  // Records returned from muninn are Python dicts converted via .toJs({dict_converter: Object.fromEntries})
  return {
    icao: r.icao || "",
    callsign: r.callsign || "",
    lat: r.lat,
    lon: r.lon,
    alt_ft: r.alt_ft ?? null,
    speed: r.speed ?? null,
  };
}

async function handleFile(file) {
  if (!pyodide) {
    setStatus("Runtime still loading, hold on a moment.", "warn");
    return;
  }
  resultsEl.classList.remove("show");
  setStatus(`Reading ${file.name}...`, "");
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "dropped.txt";
  const dst = "/tmp/" + safeName;
  pyodide.FS.writeFile(dst, bytes);
  setStatus(`Parsing ${file.name}...`, "");

  // Run muninn parsers. Use detect_format then dispatch to the right parser.
  // Returns { records: [...], format, warning } where records use dump1090-fa-shaped fields.
  let result;
  try {
    result = pyodide.runPython(`
import json
from pathlib import Path
import muninn

p = Path(${JSON.stringify(dst)})
fmt = muninn.detect_format(p)
if fmt in ("avr", "avr-tagged"):
    rows = muninn.parse_avr(p)
elif fmt == "sbs1":
    rows = muninn.parse_sbs1(p)
elif fmt == "json":
    rows = muninn.parse_json(p)
elif fmt == "mayhem":
    rows = muninn.parse_mayhem(p)
elif fmt == "gdl90":
    rows = muninn.parse_gdl90(p)
elif fmt == "beast":
    rows = muninn.parse_beast(p)
elif fmt == "csv":
    rows = muninn.parse_csv(p, fmt=None)
elif fmt == "empty":
    rows = {}
elif fmt and fmt.startswith("zigbee"):
    # v1.9.0: Zigbee/802.15.4 captures are CLI-only this release.
    raise ValueError("ZIGBEE_CLI_ONLY: " + fmt)
else:
    raise ValueError(f"unsupported / unknown format: {fmt}")

records = list(rows.values())

# Range sanity check — replicate _warn_range's logic, return data for UI.
warning = None
if len(records) >= 2:
    lats = sorted(r["lat"] for r in records)
    lons = sorted(r["lon"] for r in records)
    mid = len(lats) // 2
    clat, clon = lats[mid], lons[mid]
    outliers = [r for r in records
                if muninn._haversine_km(clat, clon, r["lat"], r["lon"]) > muninn._ADSB_MAX_REALISTIC_KM]
    if outliers:
        warning = (
            f"{len(outliers)} of {len(records)} aircraft "
            f"({100*len(outliers)/len(records):.0f}%) are >{muninn._ADSB_MAX_REALISTIC_KM} km "
            f"from the position centroid — possible mixed local + remote feed."
        )

json.dumps({"format": fmt, "records": records, "warning": warning})
`);
  } catch (e) {
    // Pyodide stringifies exceptions into a multi-line Python traceback. For
    // the user, surface the most relevant line (final exception type+msg)
    // and a friendly hint. Frame the rest in DevTools for anyone debugging.
    const raw = String(e.message || e);
    console.error("[muninn] parse error:", raw);
    const lines = raw.trim().split("\n").map(l => l.trim()).filter(Boolean);
    const last = lines[lines.length - 1] || raw;
    let friendly = `Couldn't parse ${file.name}. `;
    if (/UnicodeDecodeError|codec can't decode|invalid start byte/i.test(raw)) {
      friendly += "Looks like a binary file — Muninn needs a text capture (.txt / .csv / .json / .log) or a .gz of one.";
    } else if (/Could not detect CSV columns|First row:/i.test(raw)) {
      // CSV parser sys.exit() — file was treated as CSV (didn't match any
      // other known format) and the columns weren't recognisable. Almost
      // always means the user dropped something that isn't ADS-B at all.
      friendly += "This doesn't look like a recognised ADS-B capture. Supported: AVR (.txt), SBS-1 (.txt), dump1090 / readsb / VRS / tar1090 JSON, NDJSON, gzipped JSON, PortaPack Mayhem (.txt).";
    } else if (/JSONDecodeError|Expecting value/i.test(raw)) {
      friendly += "The file looks like JSON but doesn't parse. Check that it's a valid dump1090 / readsb aircraft.json or NDJSON.";
    } else if (/ZIGBEE_CLI_ONLY/i.test(raw)) {
      friendly = `\u{1F4E1} Zigbee / 802.15.4 capture detected. Web upload arrives in v1.9.1 \u2014 use the CLI for now:\n\n  python3 muninn.py ${file.name} --zigbee --lat YOUR_LAT --lon YOUR_LON --upload\n\nSee the README \"Zigbee / 802.15.4 (mesh channel)\" section for full instructions.`;
    } else if (/ValueError/i.test(raw) && /unsupported/i.test(raw)) {
      friendly += "Format not recognised. Supported: AVR (.txt), SBS-1 / BaseStation (.txt), dump1090 / readsb / VRS JSON, NDJSON, gzipped JSON, PortaPack Mayhem.";
    } else {
      friendly += `(${last})`;
    }
    setStatus(friendly, "err");
    return;
  }

  const parsed = JSON.parse(result);
  if (!parsed.records.length) {
    setStatus(`No aircraft with positions found in ${file.name} (format: ${parsed.format}).`, "warn");
    return;
  }

  lastPayload = {
    records: parsed.records,
    filename: file.name.replace(/\.[^.]+$/, "") + ".wdgwars.json",
    format: parsed.format,
  };

  // Build dump1090-fa-shaped web payload via muninn._to_dump1090_fa
  lastPayload.web = JSON.parse(pyodide.runPython(`
import json, muninn
json.dumps(muninn._to_dump1090_fa(${JSON.stringify(parsed.records)}))
`));

  renderResults(parsed, file.name);
}

function renderResults(parsed, filename) {
  const n = parsed.records.length;
  summaryEl.innerHTML =
    `<span class="pill ok">${n} aircraft</span>` +
    `<span class="pill">format: ${parsed.format}</span>` +
    `<span class="pill">${filename}</span>` +
    (parsed.warning ? `<div class="status warn" style="margin-top:8px">⚠ ${parsed.warning}</div>` : "");

  previewBody.innerHTML = "";
  for (const r of parsed.records.slice(0, 6)) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${r.icao || ""}</td>` +
      `<td>${r.callsign || ""}</td>` +
      `<td>${(r.lat ?? "").toString().slice(0,9)}</td>` +
      `<td>${(r.lon ?? "").toString().slice(0,9)}</td>` +
      `<td>${r.alt_ft ?? ""}</td>` +
      `<td>${r.speed ?? ""}</td>`;
    previewBody.appendChild(tr);
  }
  if (n > 6) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" style="color:var(--muted)">... and ${n - 6} more</td>`;
    previewBody.appendChild(tr);
  }
  resultsEl.classList.add("show");
  setStatus(`Parsed ${n} aircraft from ${filename}.`, "ok");
  setUploadStatus("");
}

downloadBtn.addEventListener("click", () => {
  if (!lastPayload) return;
  const blob = new Blob([JSON.stringify(lastPayload.web, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = lastPayload.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

uploadBtn.addEventListener("click", async () => {
  if (!lastPayload) return;
  const key = apikeyEl.value.trim();
  const url = apiurlEl.value.trim();
  if (!key) {
    setUploadStatus("Set an API key in Settings below first.", "warn");
    return;
  }
  if (!url) {
    setUploadStatus("Set an upload URL.", "warn");
    return;
  }

  // Match muninn.py upload(): batch in chunks of 1000, build the envelope
  // {data: base64, nonce, sig} for each chunk and POST with X-API-Key.
  const BATCH = 1000;
  const records = lastPayload.records;
  const chunks = Math.ceil(records.length / BATCH);
  const dryRun = dryrunEl.checked;
  uploadBtn.disabled = true;
  let totalImported = 0, totalSeen = 0;
  let dryLog = "";
  if (dryRun) {
    dryOutputEl.textContent = "";
    dryOutputEl.classList.remove("show");
  }
  try {
    for (let i = 0; i < records.length; i += BATCH) {
      const chunk = records.slice(i, i + BATCH);
      const idx = Math.floor(i / BATCH) + 1;
      setUploadStatus(
        (dryRun ? "[DRY] Building " : "Uploading ") +
        `chunk ${idx}/${chunks} (${chunk.length} aircraft)...`,
        dryRun ? "warn" : "",
      );

      // Let Pyodide build the envelope using the same Python code path as
      // muninn.py upload(). This guarantees byte-for-byte signature match
      // even with non-ASCII callsigns (Python's json.dumps escapes them via
      // ensure_ascii=True, JSON.stringify does not — divergent bytes would
      // break HMAC). One Python call per chunk; negligible cost.
      pyodide.globals.set("_chunk_records", chunk);
      pyodide.globals.set("_api_key", key);
      const envelope = pyodide.runPython(`
import json, base64, hmac, hashlib, secrets
_payload = {"networks": [], "aircraft": _chunk_records.to_py(), "meshcore_nodes": []}
_body_json = json.dumps(_payload, separators=(",", ":"))
_data_b64 = base64.b64encode(_body_json.encode()).decode()
_nonce = secrets.token_hex(8)
_sig = hmac.new(_api_key.encode(), (_nonce + _data_b64).encode(), hashlib.sha256).hexdigest()
json.dumps({"data": _data_b64, "nonce": _nonce, "sig": _sig})
`);

      if (dryRun) {
        // Show the exact request that *would* have gone out — verify HMAC,
        // headers, and envelope structure without touching the server.
        const env = JSON.parse(envelope);
        const keyMask = key.length > 8
          ? key.slice(0, 4) + "..." + key.slice(-4)
          : "***";
        dryLog +=
          `─── CHUNK ${idx}/${chunks} (${chunk.length} aircraft) ─────────────\n` +
          `POST ${url}\n` +
          `Content-Type: application/json\n` +
          `X-API-Key:    ${keyMask}\n` +
          `User-Agent:   muninn-web/${muninnVersion}\n` +
          `Accept:       application/json\n\n` +
          `body bytes:   ${envelope.length}\n` +
          `nonce:        ${env.nonce}\n` +
          `sig (sha256): ${env.sig}\n` +
          `data (b64, first 80): ${env.data.slice(0,80)}${env.data.length > 80 ? "..." : ""}\n\n`;
        continue;
      }

      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": key,
          "Accept": "application/json",
          "User-Agent": "muninn-web/" + muninnVersion,
        },
        body: envelope,
      });
      const txt = await resp.text();
      if (!resp.ok) {
        setUploadStatus(`Chunk ${idx} failed: HTTP ${resp.status} ${txt.slice(0,200)}`, "err");
        return;
      }
      try {
        const data = JSON.parse(txt);
        totalImported += data.aircraft_imported || 0;
        totalSeen += data.aircraft_already_seen || 0;
      } catch (_) { /* server returned non-JSON; ignore counters */ }
    }
    if (dryRun) {
      dryOutputEl.textContent = dryLog;
      dryOutputEl.classList.add("show");
      setUploadStatus(
        `[DRY] Built ${chunks} chunk(s) — ${records.length} aircraft. ` +
        `Nothing sent. Inspect the request below.`,
        "warn",
      );
    } else {
      setUploadStatus(
        `Done — ${records.length} aircraft sent, ${totalImported} imported, ${totalSeen} already-seen.`,
        "ok",
      );
    }
  } catch (e) {
    const msg = (e.message || String(e));
    // "Failed to fetch" on cross-origin POSTs is almost always CORS, since the
    // request is well-formed and the network is up (Pyodide just loaded).
    // Surface the player-friendly explanation instead of the raw browser error.
    const looksLikeCors =
      /failed to fetch/i.test(msg) ||
      /networkerror/i.test(msg) ||
      /load failed/i.test(msg);
    if (looksLikeCors && !apiurlEl.value.startsWith("/")) {
      setUploadStatus(
        "Direct upload blocked by the WDG server's CORS policy. " +
        "Click 'Download JSON' and upload via wdgwars.pl, or run Muninn " +
        "self-hosted (see docs for the local-proxy setup).",
        "warn",
      );
    } else {
      setUploadStatus("Upload error: " + msg, "err");
    }
  } finally {
    uploadBtn.disabled = false;
  }
});

async function hmacSha256Hex(keyStr, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(keyStr),
    { name: "HMAC", hash: "SHA-256" },
    false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("");
}

function randomHex(byteCount) {
  const buf = new Uint8Array(byteCount);
  crypto.getRandomValues(buf);
  return [...buf].map(b => b.toString(16).padStart(2, "0")).join("");
}

// Drag-and-drop wiring.
["dragenter", "dragover"].forEach(ev =>
  dropEl.addEventListener(ev, e => { e.preventDefault(); dropEl.classList.add("drag"); }));
["dragleave", "drop"].forEach(ev =>
  dropEl.addEventListener(ev, e => { e.preventDefault(); dropEl.classList.remove("drag"); }));
dropEl.addEventListener("drop", e => {
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
});
dropEl.addEventListener("click", () => fileInput.click());
dropEl.addEventListener("keydown", e => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

bootPyodide().catch(e => setStatus("Failed to load runtime: " + (e.message || e), "err"));
