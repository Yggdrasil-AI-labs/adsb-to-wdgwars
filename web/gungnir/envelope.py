"""HMAC-SHA256 envelope for the wdgwars.pl signed-JSON endpoint.

Envelope shape (canonical, as accepted by https://wdgwars.pl/api/upload/):

    {
        "data":  base64(json(payload)),
        "nonce": hex(8 random bytes),         # 16 hex chars
        "sig":   hex(hmac_sha256(key, nonce + data_b64))
    }

The payload is whatever combination of `networks`, `aircraft`, and
`meshcore_nodes` keys the caller provides — slots not supplied default to
empty lists. The server tolerates any combination; tools fill only the
slot relevant to their input.

Extracted from muninn.py v1.11.1 upload() (lines 1267-1283). Byte-identical
output for the same (payload, key, nonce) tuple — preserved deliberately so
tests can compare gungnir output against the muninn-1.x golden vectors.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets


def build_payload(
    *,
    networks: list[dict] | None = None,
    aircraft: list[dict] | None = None,
    meshcore_nodes: list[dict] | None = None,
) -> dict:
    """Assemble the inner payload dict from optional per-slot record lists.

    Unfilled slots default to empty lists — the server rejects payloads
    missing any of the three known keys with a generic 400, so we always
    include all three.
    """
    return {
        "networks": list(networks) if networks else [],
        "aircraft": list(aircraft) if aircraft else [],
        "meshcore_nodes": list(meshcore_nodes) if meshcore_nodes else [],
    }


def build_envelope(payload: dict, api_key: str, *, nonce: str | None = None) -> dict:
    """Wrap `payload` in the HMAC envelope. `nonce` is exposed for tests
    that need deterministic output; production callers should omit it.

    Returns the dict ready to `json.dumps()` and POST as the request body.
    """
    body_json = json.dumps(payload, separators=(",", ":"))
    data_b64 = base64.b64encode(body_json.encode()).decode()
    if nonce is None:
        nonce = secrets.token_hex(8)
    sig = hmac.new(
        api_key.encode(),
        (nonce + data_b64).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"data": data_b64, "nonce": nonce, "sig": sig}
