"""HTTP transport primitives (Phase 2, Step 3).

The urllib mechanics, header assembly, and URL normalization extracted from
scripts/ha.py — see docs/plans/workbuddy-connector-v3.md §27–28. Everything
here is a leaf primitive: no function in this module calls back into ha or
holds credentials. That is deliberate — the test suite (and the Hermes
adapter) intercept I/O by patching ha's own names (``mock.patch.object(ha,
"http", ...)``), which only works while the *orchestrating* functions
(``http``/``authed``/``get_token``/credential store) resolve their
collaborators through ha's module globals at call time. So those stay in
ha.py as thin compositors over these primitives, and the primitives
themselves carry no cross-references.

Decode behavior is preserved byte-for-byte from the historical http(): a
successful response that isn't JSON becomes ``{"raw": text}``; an HTTPError
body that isn't JSON becomes ``{"detail": text}``; an unreachble host raises
HAFailure with the exact "Cannot reach ..." message.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from ha_client.errors import fail


def normalize_origin(raw):
    """Accept either an origin or a full .../api/v1 base; enforce HTTPS
    outside localhost. Returns the bare origin string."""
    raw = raw.rstrip("/")
    if raw.endswith("/api/v1"):
        raw = raw[: -len("/api/v1")]
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        fail(f"Insecure HA_BASE_URL '{raw}': HTTPS is required except for localhost")
    return raw


def api_url(origin_str, path):
    return f"{origin_str}/api/v1{path}"


def build_request_headers(version_headers, token=None, agent_id=None, request_id=None):
    """Assemble request headers exactly as the CLI always has: JSON content
    type, the plugin-identification trio, then optional auth headers."""
    headers = {
        "Content-Type": "application/json",
        **(version_headers or {}),
    }
    if request_id is not None:
        headers["X-Request-Id"] = str(request_id)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if agent_id:
        headers["X-Agent-Id"] = agent_id
    return headers


def _decode(raw, error_key):
    raw = raw or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {error_key: raw}


def open_json(method, url, data=None, headers=None, timeout=30):
    """Send one request and decode the response.

    Returns ``(status, response_headers, body)``. HTTP error responses are
    returned, never raised — the caller decides via expect(); only a
    transport-level failure (unreachable host) raises HAFailure.
    """
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, _decode(resp.read().decode(), "raw")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, _decode(e.read().decode(), "detail")
    except urllib.error.URLError as e:
        fail(f"Cannot reach {url}: {e.reason}")


def expect(status, resp, ok=(200, 201, 204)):
    """Turn a non-2xx response into HAFailure, passing the backend's detail."""
    if status not in ok:
        fail(resp.get("detail", resp), status)
    return resp
