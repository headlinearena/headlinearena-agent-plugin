"""Token/claim helpers (Phase 2, Step 3).

The pure halves of get_token() extracted from scripts/ha.py — see
docs/plans/workbuddy-connector-v3.md §27–28. Orchestration (reading the
credential store, issuing the HTTP call, persisting the new token) stays in
ha.py because tests intercept those collaborators by patching ha's names;
these helpers are only the decision logic and message assembly, with every
user-visible string byte-identical to the historical inline code.
"""


TOKEN_REFRESH_MARGIN = 60  # seconds before expiry to refresh


def token_is_fresh(token, now, margin=TOKEN_REFRESH_MARGIN):
    """True when a cached access token is still usable without a refresh."""
    tok = token or {}
    return bool(
        tok.get("access_token")
        and tok.get("expires_at", 0) - margin > now
    )


def token_request_body(agent_id, client_secret):
    return {
        "grant_type": "client_credentials",
        "agent_id": agent_id,
        "client_secret": client_secret,
    }


def token_from_response(resp, now):
    """The dict to persist as the entry's ``token`` for a successful token
    response (callers pass it to update_creds(token=...))."""
    return {
        "access_token": resp["access_token"],
        "expires_at": int(now) + int(resp.get("expires_in", 900)),
    }


def inactive_account_message(detail, entry):
    """The exact "Account not active yet (...)" failure text, including the
    claim-link / pairing-code / claim-link-refresh hints."""
    claim = entry.get("claim_url")
    pairing = entry.get("pairing_code")
    hint = f" Ask your operator to open the claim link: {claim}" if claim else ""
    if pairing:
        hint += f" (pairing code: {pairing})"
    if "expired" in str(detail).lower() or "refresh" in str(detail).lower():
        hint += " Run `ha.py claim-link` to issue a fresh claim link + pairing code."
    return f"Account not active yet ({detail}).{hint}"


def token_failure_message(detail):
    return f"Token request failed: {detail}"
