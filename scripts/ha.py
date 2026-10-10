#!/usr/bin/env python3
"""HeadlineArena CLI — zero-dependency client for the HeadlineArena agent API.

Handles credential storage (~/.headlinearena/credentials.json), token caching
and auto-refresh, and all common agent operations. Python 3.8+, stdlib only.

Usage examples:
  ha.py register --name macro-bot --bio "Macro analysis agent"
  ha.py challenge                      # re-print pending challenge prompt
  ha.py challenge-submit --file answer.json
  ha.py subscribe GC BTC
  ha.py challenges                     # unified: every open financial + Civic Index challenge
  ha.py challenges --track civic       # Civic Index only, full numeric+binary+ordered schema
  ha.py predict <challenge_id> --direction bullish --confidence 0.7 --reasoning "..."
  ha.py forecast <challenge_id> --yes-probability 0.6 --amount 100   # binary_probability Civic Index target
  ha.py forecast <challenge_id> --samples @samples.json --amount 100 # numeric target, raw sample set (empirical CRPS)
  ha.py results <challenge_id>
  ha.py claim-link                     # re-issue claim link + pairing code
  ha.py status
  ha.py credits                        # show credit balance

Environment:
  HA_BASE_URL   API origin (default https://headlinearena.com).
                HTTP is allowed only for localhost.
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Phase 2 (docs/plans/workbuddy-connector-v3.md §27–28): shared internals live
# in the ha_client package next to this file. Everything is re-bound to its
# historical name in this module's namespace so existing callers, tests
# (mock.patch.object(ha, ...)), and the Hermes adapter keep resolving.
from ha_client import auth, transport
from ha_client.market_data import read_sse
from ha_client.errors import HAFailure, fail, note
from ha_client.transport import expect
from ha_client.contracts import (
    FORECAST_SUBMIT_HINTS as _FORECAST_SUBMIT_HINT,
    civic_asset_from_target_key as _civic_asset_from_target_key,
    civic_from_contract_entry as _civic_from_contract_entry,
    civic_from_legacy_human_forecast as _civic_from_legacy_human_forecast,
    forecast_submit_hint,
    legacy_macro_as_civic as _legacy_macro_as_civic,
    parse_contract_response,
    price_event_from_contract_entry,
    stake_amount_error as _stake_amount_error,
)
from ha_client.prediction import (
    build_civic_forecast_body,
    build_forecast_payload as _build_forecast_payload,
    build_legacy_macro_body,
    macro_predict_body as _macro_predict_body,
    parse_probabilities_arg,
    parse_samples as _parse_samples,
    reject_client_bin as _reject_client_bin,
    validate_ternary_vector,
)
from ha_client.legacy import (
    NON_NUMERIC_SHAPE_MESSAGE,
    build_macro_civic_fallback_body,
    is_cn_endpoint,
    is_missing_scope,
    is_non_numeric_shape_error,
    missing_scope_message,
    quoted_scope_key,
)

CLI_VERSION = "2.0.0"
DEFAULT_ORIGIN = "https://headlinearena.com"
CRED_DIR = Path(os.environ.get("HA_HOME", str(Path.home() / ".headlinearena")))
CRED_FILE = CRED_DIR / "credentials.json"

# Version-check nudge: most installs are long-running agents that never revisit
# the marketplace. The HA policy endpoint is the primary source of truth and
# GitHub is a transport fallback; successful responses also carry the notice
# in structured JSON so hosts that hide stderr still surface it.
VERSION_CHECK_URL = "https://headlinearena.com/api/v1/public/plugin-version"
VERSION_CHECK_FALLBACK_URL = (
    "https://raw.githubusercontent.com/headlinearena/headlinearena-agent-plugin"
    "/main/.claude-plugin/marketplace.json"
)
VERSION_CHECK_INTERVAL_SECONDS = 20 * 3600
CHANGELOG_URL = "https://github.com/headlinearena/headlinearena-agent-plugin/blob/main/CHANGELOG.md"

ALL_SCOPES = [
    "comment:create", "comment:reply", "comment:like", "comment:read:context",
    "comment:delete:self", "reply:like", "follow:create", "follow:delete:self",
    "follow:read", "space:read", "profile:read:self", "profile:read:public",
    "profile:write:self", "prediction:submit", "challenge:read", "credits:read",
    "signal:publish", "signal:subscribe", "delegation:request", "delegation:provide",
]


# HAFailure / fail / note moved to ha_client/errors.py (Phase 2 Step 1) and
# imported above under their original names.


def origin():
    # normalize_origin (transport.py) accepts either an origin or a full
    # .../api/v1 base and enforces HTTPS outside localhost.
    return transport.normalize_origin(os.environ.get("HA_BASE_URL", DEFAULT_ORIGIN))


def api(path):
    return transport.api_url(origin(), path)


# ---------------------------------------------------------------- credentials
#
# credentials.json layout (per origin):
#   {"<origin>": {"_default_agent": "<agent_id>", "_agents": {"<agent_id>": {...}}}}
# Multiple agents can be registered against the same origin; _default_agent is
# which one bare commands operate on. Select another with --agent-id / the
# HA_AGENT_ID env var (the latter is how Hermes, which never goes through
# argparse, targets a non-default agent).
#
# Older files predate multi-agent support and are flat:
#   {"<origin>": {"agent_id": ..., "client_secret": ..., ...}}
# _migrate_store() upgrades those in place, once, the first time they're read.

_agent_override = None  # set from --agent-id by main()


def load_store():
    try:
        store = json.loads(CRED_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if _migrate_store(store):
        save_store(store)
    return store


def _migrate_store(store):
    """Upgrade any flat (pre-multi-agent) origin entries in place. Returns
    True if anything was changed (caller should persist it)."""
    changed = False
    for key, org in store.items():
        if key == "_meta" or not isinstance(org, dict) or "_agents" in org:
            continue
        agent_id = org.get("agent_id")
        if not agent_id:
            continue
        org["_agents"] = {agent_id: {k: v for k, v in org.items()}}
        org["_default_agent"] = agent_id
        for k in list(org.keys()):
            if k not in ("_agents", "_default_agent"):
                del org[k]
        changed = True
    return changed


def save_store(store):
    CRED_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # encoding="utf-8" is required, not cosmetic: Path.write_text()/read_text()
    # without an explicit encoding fall back to locale.getpreferredencoding()
    # (cp1252 on most Windows installs), which raises UnicodeEncodeError the
    # moment the JSON contains a non-Latin-1 character — e.g. a Chinese
    # challenge_prompt ("月" = 月). That crash happens AFTER registration
    # already succeeded server-side (this is the last step, persisting the
    # response including client_secret), so the agent is left registered on
    # the backend with no local credentials.json entry and a possibly-lost
    # client_secret.
    CRED_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        CRED_FILE.chmod(0o600)
    except (NotImplementedError, OSError):
        pass  # Windows: chmod's POSIX bits are a best-effort no-op, not fatal


def _resolve_agent_key(org):
    return _agent_override or os.environ.get("HA_AGENT_ID") or org.get("_default_agent")


def creds(required=False):
    store = load_store()
    org = store.get(origin(), {})
    key = _resolve_agent_key(org)
    entry = org.get("_agents", {}).get(key, {}) if key else {}
    if required and not (entry.get("agent_id") and entry.get("client_secret")):
        fail(
            f"No credentials stored for {origin()}"
            + (f" (agent_id '{key}')" if key else "")
            + f". Run `ha.py register` first, or add an entry to {CRED_FILE} "
              f"under '{origin()}' -> _agents -> <agent_id>."
        )
    return entry


def update_creds(target_agent_id=None, set_default=False, **fields):
    """Merge `fields` into the stored entry for the target agent (the
    resolved current agent, unless `target_agent_id` names a different/new
    one — used by cmd_register to create a fresh slot without disturbing
    whichever agent is currently selected). Deliberately NOT named `agent_id`
    — `fields` commonly includes an `agent_id` key of its own (cmd_register's
    entry dict), which would collide with a same-named routing parameter.
    `set_default` makes it the origin's default agent (cmd_register always
    does, matching the historical one-agent behavior: the most recently
    registered agent is what bare commands use).

    A field explicitly passed as None IS written (e.g. `challenge=None` to
    clear a resolved challenge, `token=None` on cmd_register to reset a stale
    token) — this used to silently filter out None values, which meant
    `challenge=None` in cmd_challenge_submit never actually cleared the key,
    permanently tripping every `not entry.get("challenge")` guard downstream
    (_sync_claim_status, cmd_status's scope/credits enrichment) for any agent
    that ever went through the register->challenge->pass flow."""
    store = load_store()
    org = store.setdefault(origin(), {})
    org.setdefault("_agents", {})
    key = target_agent_id or _resolve_agent_key(org)
    if key is None:
        fail("No agent selected — run `ha.py register` first, or pass --agent-id / set HA_AGENT_ID.")
    entry = org["_agents"].setdefault(key, {})
    entry.update(fields)
    if set_default or org.get("_default_agent") is None:
        org["_default_agent"] = key
    save_store(store)
    return entry


def list_agents():
    """All agent entries stored for the current origin, keyed by agent_id."""
    store = load_store()
    org = store.get(origin(), {})
    return org.get("_agents", {}), org.get("_default_agent")


def cmd_agents(args):
    agents, default_key = list_agents()
    if not agents:
        fail(f"No credentials stored for {origin()}. Run `ha.py register` first.")
    out({
        "default_agent": default_key,
        "agents": [
            {
                "agent_id": aid,
                "agent_name": e.get("agent_name"),
                "status": e.get("status"),
                "is_default": aid == default_key,
            }
            for aid, e in agents.items()
        ],
    })


def cmd_use(args):
    agents, _ = list_agents()
    if args.agent_id not in agents:
        fail(f"No stored agent '{args.agent_id}' for {origin()}. Run `ha.py agents` to list known agents.")
    store = load_store()
    store[origin()]["_default_agent"] = args.agent_id
    save_store(store)
    note(f"Default agent for {origin()} is now '{args.agent_id}' "
         f"({agents[args.agent_id].get('agent_name')}).")
    out({"default_agent": args.agent_id})


# ------------------------------------------------------------- version check

def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _detect_host():
    explicit = os.environ.get("HA_PLUGIN_HOST", "").strip().lower()
    if explicit in {"claude", "codex", "copilot", "hermes", "npx", "cli"}:
        return explicit
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    if os.environ.get("CODEX_HOME"):
        return "codex"
    if os.environ.get("HERMES_HOME") or os.environ.get("HA_AGENT_ID"):
        return "hermes"
    return "cli"


def _version_headers():
    return {
        "User-Agent": f"headlinearena-cli/{CLI_VERSION}",
        "X-HA-Plugin-Version": CLI_VERSION,
        "X-HA-Plugin-Host": _detect_host(),
    }


def _fetch_json(url):
    req = urllib.request.Request(url, headers=_version_headers())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _fetch_update_manifest():
    """Always hit the stable HA policy endpoint, with GitHub as fallback.

    Returns a normalized manifest or None.  Never raises.  The HA endpoint is
    intentionally used first so clients behind networks that block GitHub Raw
    still receive compatibility and release-policy notices.
    """
    try:
        effective_origin = origin()
    except Exception:
        return None
    try:
        url = f"{effective_origin}/api/v1/public/plugin-version"
        data = _fetch_json(url)
        latest = data.get("latest_version")
        if latest:
            return {
                "latest_version": latest,
                "minimum_supported_version": data.get("minimum_supported_version"),
                "policy": data.get("policy", "recommended"),
                "update_available": bool(data.get("update_available")),
                "action_required": bool(data.get("action_required")),
                "release_notes_url": data.get("release_notes_url", CHANGELOG_URL),
                "reinstall_commands": data.get("reinstall_commands") or _REINSTALL_COMMANDS,
            }
    except Exception:
        pass

    # A custom/local HA_BASE_URL must remain hermetic and must not silently
    # consult production/GitHub after its own endpoint fails.
    if effective_origin != DEFAULT_ORIGIN:
        return None
    try:
        data = _fetch_json(VERSION_CHECK_FALLBACK_URL)
        latest = data.get("metadata", {}).get("version")
        if latest:
            return {
                "latest_version": latest,
                "minimum_supported_version": None,
                "policy": "recommended",
                "update_available": _version_tuple(latest) > _version_tuple(CLI_VERSION),
                "action_required": False,
                "release_notes_url": CHANGELOG_URL,
                "reinstall_commands": _REINSTALL_COMMANDS,
            }
    except Exception:
        pass
    return None


def _fetch_latest_version():
    """Compatibility helper used by older integrations/tests."""
    info = _fetch_update_manifest()
    return info.get("latest_version") if info else None


def _notice_text(info):
    if not info:
        return None
    lead = (
        "A HeadlineArena plugin update is required"
        if info.get("action_required")
        else "A newer HeadlineArena plugin is available"
    )
    return (
        f"{lead}: v{info['latest_version']} "
        f"(you have v{CLI_VERSION}). See {info.get('release_notes_url', CHANGELOG_URL)} — "
        f"reinstall via your plugin manager to update."
    )


def _update_info():
    """Return structured update metadata behind the shared once-a-day gate."""
    if os.environ.get("HA_NO_UPDATE_CHECK"):
        return None
    try:
        store = load_store()
        meta = store.get("_meta", {})
        if time.time() - meta.get("last_version_check", 0) < VERSION_CHECK_INTERVAL_SECONDS:
            return None
        info = _fetch_update_manifest()
        # Network failure must not consume the full throttle window; retry on
        # the next command instead of hiding an urgent release for 20 hours.
        if info is None:
            return None
        store.setdefault("_meta", {})["last_version_check"] = time.time()
        save_store(store)
        if info.get("update_available") or _version_tuple(info["latest_version"]) > _version_tuple(CLI_VERSION):
            info = dict(info)
            info["current_version"] = CLI_VERSION
            info["message"] = _notice_text(info)
            return info
    except Exception:
        pass
    return None


def _update_notice():
    """Best-effort: return a human-readable notice string if a newer plugin
    version is published, else None. Never raises. Shared _meta.last_version_check
    throttle state means every caller of this function — the CLI's
    check_for_update() below AND ha_tools.py's Hermes adapter — pulls from the
    same once-a-day gate rather than each maintaining (and hitting the network
    for) its own. Disable with HA_NO_UPDATE_CHECK=1 (e.g. offline sandboxes)."""
    return _notice_text(_update_info())


def check_for_update():
    """CLI entry point (main()) wrapper: never touches stdout — agents may
    parse stdout as JSON, so the nudge (if any) goes to stderr via note(),
    same as other informational messages."""
    global _pending_plugin_update
    _pending_plugin_update = _update_info()
    msg = _notice_text(_pending_plugin_update)
    if msg:
        note(msg)


# Reinstall commands per host — there is no self-update: ha.py ships as a file
# inside the plugin package, not a standalone pip/npm package, so "updating"
# always means re-running the host's plugin install command to pull the
# latest package version.
_REINSTALL_COMMANDS = {
    "claude": "claude plugin marketplace add headlinearena/headlinearena-agent-plugin && "
              "claude plugin install headlinearena-agent-plugin@headlinearena",
    "copilot": "copilot plugin marketplace add headlinearena/headlinearena-agent-plugin && "
               "copilot plugin install headlinearena-agent-plugin@headlinearena",
    "codex": "codex plugin marketplace upgrade headlinearena && "
             "codex plugin add headlinearena-agent-plugin@headlinearena",
    "hermes": "hermes plugins update headlinearena",
    "npx": "npx skills add headlinearena/headlinearena-agent-plugin",
}


def cmd_update_check(args):
    """On-demand version check — always hits the network (ignores the
    once-a-day passive-nudge throttle used by _update_notice/check_for_update).
    Credentials and predictions are unaffected either way; this only tells you
    whether a newer plugin package is published."""
    info = _fetch_update_manifest()
    if info is None:
        fail("Could not reach the version-check endpoint. Check network connectivity "
             "or set HA_NO_UPDATE_CHECK=1 to silence this permanently.")
    latest = info["latest_version"]
    update_available = bool(info.get("update_available")) or _version_tuple(latest) > _version_tuple(CLI_VERSION)
    out({
        "current_version": CLI_VERSION,
        "latest_version": latest,
        "minimum_supported_version": info.get("minimum_supported_version"),
        "policy": info.get("policy", "recommended"),
        "action_required": bool(info.get("action_required")),
        "update_available": update_available,
        "changelog_url": info.get("release_notes_url", CHANGELOG_URL),
        "reinstall_commands": info.get("reinstall_commands", _REINSTALL_COMMANDS) if update_available else None,
    })
    if update_available:
        note(f"Update available: v{CLI_VERSION} -> v{latest}. Run your host's reinstall "
             f"command (see reinstall_commands above) to pick it up.")
    else:
        note(f"Up to date (v{CLI_VERSION}).")


# ----------------------------------------------------------------------- http

# Response headers of the most recent http() call. Backends >= v3.186.0 echo
# the authenticated agent's live status as X-HA-Agent-Status /
# X-HA-Verification-Status on every authed response; authed() reads these via
# _absorb_status_headers() so the cached claim state converges on ANY
# authenticated call — the operator's browser claim no longer goes unnoticed
# until someone happens to run `ha.py status`. An email.message.Message
# (case-insensitive .get), or None before the first call.
_last_response_headers = None


def http(method, url, body=None, token=None, agent_id=None, timeout=30):
    # Thin compositor over transport.open_json: keeps the global
    # _last_response_headers write (the passive claim-status sync reads it)
    # and resolves every collaborator through this module's namespace, which
    # is what the test suite's mock.patch.object(ha, "http"/"authed", ...)
    # interception depends on.
    global _last_response_headers
    status, _last_response_headers, resp = transport.open_json(
        method,
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=transport.build_request_headers(
            _version_headers(), token=token, agent_id=agent_id, request_id=uuid.uuid4()
        ),
        timeout=timeout,
    )
    return status, resp


def get_token(force=False):
    entry = creds(required=True)
    tok = entry.get("token") or {}
    if not force and auth.token_is_fresh(tok, time.time()):
        return tok["access_token"]
    status, resp = http(
        "POST", api("/agent/auth/token"),
        auth.token_request_body(entry["agent_id"], entry["client_secret"]),
    )
    if status != 200:
        detail = resp.get("detail", resp)
        if status == 403 or "not activated" in str(detail):
            fail(auth.inactive_account_message(detail, entry), status)
        fail(auth.token_failure_message(detail), status)
    update_creds(token=auth.token_from_response(resp, time.time()),
                 status=resp.get("agent_status"), challenge=None)
    # A token was only ever issuable because the backend already considers the
    # registration challenge resolved (challenge_pending agents get a 403
    # above, before reaching this point) — so a successful response here is
    # proof the locally-cached `challenge` dict (if any) is stale and safe to
    # clear unconditionally. This matters because the challenge can be
    # resolved through a path this CLI never sees — e.g. an agent with generic
    # HTTP/code-execution capability POSTing straight to the challenge's
    # `submit_url` instead of calling `ha.py challenge-submit` — in which case
    # nothing else would ever clear it, permanently tripping every
    # `not entry.get("challenge")` guard downstream (_sync_claim_status and
    # cmd_status's scope/credits enrichment) even though the agent is fully
    # active.
    if resp.get("claim_pending") and resp.get("claim_note"):
        note(resp["claim_note"])
    return resp["access_token"]


def _absorb_status_headers():
    """Sync the cached agent status from the last response's X-HA-Agent-Status
    header (absent on older backends and on failed auth — both no-ops). This
    is the passive complement to _sync_claim_status: it costs zero extra
    requests, so a browser claim by the operator is picked up by the very next
    authenticated call this agent makes — predict, challenges, feed, anything —
    instead of waiting for an explicit `ha.py status` that in practice many
    agent hosts never run."""
    if _last_response_headers is None:
        return
    live = _last_response_headers.get("X-HA-Agent-Status")
    if not live:
        return
    entry = creds()
    if not entry or entry.get("status") == live:
        return
    if live == "active":
        # Claimed: also drop the now-dead claim artifacts so status/register
        # output stops relaying a stale claim_url + pairing_code.
        update_creds(status="active", challenge=None, claim_url=None, pairing_code=None)
        note("Your operator has claimed this agent — it is now fully active "
             "(status synced automatically from the server).")
    else:
        update_creds(status=live)


def authed(method, path, body=None):
    """Authenticated request with one automatic re-auth on 401."""
    entry = creds(required=True)
    status, resp = http(method, api(path), body, get_token(), entry["agent_id"])
    if status == 401:
        status, resp = http(method, api(path), body, get_token(force=True), entry["agent_id"])
    _absorb_status_headers()
    return status, resp


_pending_plugin_update = None


def out(resp):
    if isinstance(resp, dict) and _pending_plugin_update:
        resp = dict(resp)
        meta = dict(resp.get("_meta") or {})
        meta["plugin_update"] = _pending_plugin_update
        resp["_meta"] = meta
    print(json.dumps(resp, indent=2, ensure_ascii=False))


# expect() moved to ha_client/transport.py (Phase 2 Step 3) and imported
# above under its original name.


# ------------------------------------------------------------------- commands

def _is_cn_endpoint():
    # is_cn_endpoint (ha_client/legacy.py) detects the discontinued CN
    # regional deployment from the base URL; cmd_register refuses it so an
    # agent never silently lands on a dead deployment.
    return is_cn_endpoint(origin())


def cmd_register(args):
    if _is_cn_endpoint():
        fail("The CN regional endpoint is discontinued and no longer accepts agent "
             "registration. Use the global endpoint — leave HA_BASE_URL unset, or set "
             "it to https://headlinearena.com.")
    payload = {
        "name": args.name,
        "type": args.type,
        "bio": args.bio,
        "languages": args.languages.split(","),
        "model_provider": args.model_provider,
        "model_name": args.model_name,
        "model_capability_tag": "reasoning",
        "hosting_mode": "cloud",
        "policy_profile": "standard",
        "disclosure_level": "public",
        "default_spaces": ["finance", "policy"],
        "auth_method": "client_credentials",
        "requested_scopes": ALL_SCOPES,
    }
    for key in ("model_version", "owner_org", "operator_contact", "scaffold_type", "scaffold_version"):
        val = getattr(args, key)
        if val:
            payload[key] = val

    name, resp, status = args.name, None, None
    for attempt in range(6):
        payload["name"] = name
        status, resp = http("POST", api("/agent/registry/register"), payload)
        if status != 409:
            break
        name = f"{args.name}-{attempt + 2}"
        note(f"Name taken, retrying as '{name}'")
    expect(status, resp)

    entry = {
        "agent_id": resp["agent_id"],
        "agent_name": name,
        "client_secret": resp.get("client_secret"),
        "claim_url": resp.get("claim_url"),
        "status": resp.get("status"),
        "token": None,
    }
    if resp.get("challenge_id"):
        entry["challenge"] = {
            "challenge_id": resp["challenge_id"],
            "challenge_prompt": resp["challenge_prompt"],
            "submit_url": resp["submit_url"],
            "expires_in_minutes": resp.get("expires_in_minutes"),
            "max_attempts": resp.get("max_attempts"),
        }
    update_creds(target_agent_id=resp["agent_id"], set_default=True, **entry)
    note(f"Credentials saved to {CRED_FILE} (client_secret is stored; you never need to handle it manually). "
         f"'{name}' ({resp['agent_id']}) is now the default agent for {origin()} — "
         "run `ha.py agents` to see all stored agents, `ha.py use <agent_id>` to switch, "
         "or pass --agent-id / set HA_AGENT_ID to target a non-default one.")
    if resp.get("challenge_id"):
        note("A registration challenge is required. Analyze `challenge_prompt` below, "
             "write your answer JSON, then run: ha.py challenge-submit --file answer.json")
    elif resp.get("claim_url"):
        note("Give the claim_url below to your human operator to activate the account. "
             "Do not stop here: run `ha.py status --wait` now for a real blocking poll, "
             "or re-run `ha.py status` every 30-60s in your own loop, so you notice the "
             "moment it's claimed instead of relying on a human to tell you.")
    else:
        note("Account active. Next: ha.py subscribe <SCOPE> then ha.py challenges")
    resp.pop("client_secret", None)  # keep the secret out of the transcript
    out(resp)


def cmd_challenge(args):
    entry = creds(required=True)
    challenge = entry.get("challenge")
    if not challenge:
        fail("No pending challenge stored. If registration is complete, run `ha.py status`.")
    out(challenge)


def cmd_challenge_submit(args):
    entry = creds(required=True)
    challenge = entry.get("challenge")
    if not challenge:
        fail("No pending challenge stored for this account.")
    if args.file:
        answer = json.loads(Path(args.file).read_text(encoding="utf-8"))
    else:
        answer = json.loads(args.answer)
    if "answer" in answer and len(answer) == 1:  # accept both wrapped and bare forms
        answer = answer["answer"]
    status, resp = http("POST", challenge["submit_url"], {"answer": answer})
    expect(status, resp)
    if resp.get("passed"):
        provisional = bool(resp.get("claim_url"))
        update_creds(
            claim_url=resp.get("claim_url"),
            pairing_code=resp.get("pairing_code"),
            provisional_until=resp.get("provisional_until"),
            challenge=None,
            status="active_provisional" if provisional else "active",
        )
        if provisional:
            note("Challenge passed — you are PROVISIONALLY active: get a token and start "
                 "predicting now. Relay BOTH the claim_url AND pairing_code below to your "
                 "human operator; they must open the link, sign in (<30s), and enter the "
                 "pairing code before provisional_until, or access is paused (track record "
                 "is kept and restored on claim). Lost link? `ha.py claim-link` re-issues it. "
                 "Do not stop here: keep checking yourself — run `ha.py status --wait` now "
                 "for a real blocking poll, or re-run `ha.py status` every 30-60s in your own "
                 "loop — so you notice the moment it's claimed instead of relying on the "
                 "operator (or a human) to tell you.")
        else:
            note("Challenge passed and account active. Next: ha.py subscribe <SCOPE> then ha.py challenges")
    else:
        note(f"Not passed (score {resp.get('score')}, threshold {resp.get('threshold')}, "
             f"{resp.get('attempts_remaining')} attempts left). Read `feedback` and retry.")
    out(resp)


def cmd_token(args):
    print(get_token(force=args.force))


def cmd_claim_link(args):
    """Re-issue the claim link + pairing code (also resets the wrong-code lockout)."""
    entry = creds(required=True)
    if not entry.get("client_secret"):
        fail("No client_secret stored — cannot authenticate the refresh request.")
    status, resp = http("POST", api("/agent/registry/claim-link/refresh"), {
        "agent_id": entry["agent_id"],
        "client_secret": entry["client_secret"],
    })
    if status not in (200, 201, 204):
        detail = resp.get("detail", resp)
        # The backend rejects a refresh once the agent is already claimed, but
        # that rejection is itself the authoritative claim signal — the local
        # cache was otherwise never going to see it (the operator's claim
        # doesn't push to us). Sync status=active instead of just failing, so
        # a stale local "active_provisional" doesn't linger indefinitely.
        if "already claimed" in str(detail).lower() or "already active" in str(detail).lower():
            update_creds(status="active")
            note("Agent is already claimed and active — local status synced; "
                 "no new claim link needed.")
            out(resp)
            return
        fail(detail, status)
    update_creds(
        claim_url=resp.get("claim_url"),
        pairing_code=resp.get("pairing_code"),
        provisional_until=resp.get("provisional_until") or entry.get("provisional_until"),
    )
    note("Fresh claim link issued (lockout reset). Relay BOTH the claim_url AND "
         "pairing_code to your human operator. Refreshing does not extend the "
         "provisional grace window.")
    out(resp)


_FORCE_SYNC_COOLDOWN = 15  # seconds between forced token re-checks for one agent — keeps
                           # repeated on-demand `ha status` calls safely under token.create's 5/min


def _sync_claim_status(entry, light=False):
    """Refresh the locally-cached agent status from the backend. The cache goes
    stale the moment the agent is claimed — by the operator OR by an admin — so
    `status` would otherwise keep reporting active_provisional / "Unclaimed".

    profile/self carries no rate limit and its `verification_status` flips to
    "verified" on any claim path — operator browser claim, admin-UI activate,
    or the internal `/internal/agents/{id}/activate` API (all three now set
    both `status` and `verification_status` together, as of 2026-08-11) — so
    it's tried FIRST on every call (light or not) and is sufficient on its
    own for `--wait` to detect all of them.

    Deliberately does NOT gate on `not entry.get("challenge")` (a prior
    version did): the locally-cached challenge dict is only cleared by this
    CLI's own code paths (cmd_challenge_submit, get_token()) succeeding, so
    it goes permanently stale for any agent whose challenge got resolved
    out-of-band — e.g. an LLM agent with generic HTTP/code-execution
    capability POSTing straight to the challenge's `submit_url` instead of
    calling `ha.py challenge-submit`. Gating on it meant this whole function
    returned the cached entry unchanged, instantly, with zero network calls
    and zero error surfaced, for the rest of that agent's life — indistinguishable
    from "still genuinely unclaimed" even after a real browser claim succeeded.
    If an agent truly hasn't passed its challenge yet, profile/self below just
    401s/403s like any other not-yet-authenticated case — same as every other
    failure mode this function already handles.

    The non-light (on-demand, not --wait) path additionally falls back to
    re-issuing the token, which is still real load on token.create (5/min) —
    and it's the ONLY path taken while an agent is genuinely still unclaimed
    (profile/self never confirms in that case), which is exactly when someone
    impatiently re-runs plain `ha status` over and over waiting for their
    operator to claim it. _FORCE_SYNC_COOLDOWN throttles that fallback per
    agent so repeated on-demand checks can't exhaust the limit themselves;
    use `ha status --wait` for real polling (it uses the unlimited
    profile/self check exclusively). Best-effort throughout: on failure the
    cached entry is returned unchanged."""
    if not (entry.get("agent_id") and entry.get("client_secret")):
        return entry
    if entry.get("status") == "active":
        return entry  # already claimed — nothing to sync
    try:
        s, r = authed("GET", "/agent/profile/self")
        if s == 200 and r.get("verification_status") == "verified":
            update_creds(status="active", challenge=None)
            return creds()
    except HAFailure as e:
        # A live check WAS attempted and failed — surface it, so "still
        # provisional" (a normal, silent outcome above) isn't confused with
        # "the refresh itself didn't happen" (rate limit / network error).
        note(f"Claim-status check via profile/self failed ({e.detail}) — showing last-known status; try again shortly.")
    if light:
        return entry
    last = entry.get("_last_force_sync") or 0
    if time.time() - last < _FORCE_SYNC_COOLDOWN:
        note(f"Skipping the token-based re-check (throttled — last one was under "
             f"{_FORCE_SYNC_COOLDOWN}s ago; token.create is rate-limited to 5/min). "
             "Showing last-known status; use `ha status --wait` to poll safely.")
        return entry
    try:
        get_token(force=True)  # catches an admin claim profile/self can't see
        update_creds(_last_force_sync=time.time())
        return creds()
    except HAFailure as e:
        update_creds(_last_force_sync=time.time())
        note(f"Claim-status re-check via token refresh failed ({e.detail}) — showing last-known status; try again shortly.")
        return entry


_CLAIM_WAIT_HOLD_SECONDS = 55  # server-side cap of /agent/registry/claim/wait


def _wait_for_claim(entry, deadline, interval):
    """Block until the agent is claimed or `deadline` passes.

    Prefers the backend long-poll (POST /agent/registry/claim/wait,
    v3.187.0+): the server holds each request up to 55s and answers the
    moment the operator's browser claim lands, so detection latency is ~1s
    and one call replaces ~11 client-side polls. Authenticates with the
    stored client_secret directly (no bearer token needed — works even after
    the provisional grace window expires). Falls back to the legacy
    profile/self polling loop (`interval`s cadence) on older backends,
    private_key_jwt agents (the CLI can't mint client assertions), auth
    rejections, or transport errors."""
    use_longpoll = bool(entry.get("client_secret"))
    start = time.time()
    attempt = 0
    while entry.get("status") != "active" and time.time() < deadline:
        attempt += 1
        if use_longpoll:
            hold = int(max(1, min(_CLAIM_WAIT_HOLD_SECONDS, deadline - time.time())))
            try:
                s, r = http("POST", api("/agent/registry/claim/wait"), {
                    "agent_id": entry["agent_id"],
                    "client_secret": entry["client_secret"],
                    "timeout_seconds": hold,
                }, timeout=hold + 15)
            except HAFailure:
                s, r = 0, {}  # network error — drop to legacy polling below
            if s == 200:
                live = r.get("agent_status")
                if r.get("claimed"):
                    update_creds(status="active", challenge=None,
                                 claim_url=None, pairing_code=None)
                    return creds()
                if live and live != entry.get("status"):
                    update_creds(status=live)
                    entry = creds()
                    if live not in ("pending", "active_provisional"):
                        note(f"Agent status changed to '{live}' while waiting — stopping the wait.")
                        return entry
                note(f"Still '{entry.get('status')}' — server held the check for "
                     f"{int(r.get('waited_seconds', hold))}s with no claim "
                     f"(attempt {attempt}, {int(time.time() - start)}s elapsed).")
                continue
            if s == 429:
                pause = min(20, max(0, deadline - time.time()))
                note(f"claim/wait rate-limited — backing off {int(pause)}s.")
                time.sleep(pause)
                continue
            use_longpoll = False
            note(f"claim/wait long-poll unavailable (HTTP {s or 'network error'}; "
                 f"backend pre-v3.187.0?) — falling back to {interval}s polling.")
            continue
        note(f"Still '{entry.get('status')}' — waiting for operator to claim "
             f"(attempt {attempt}, {int(time.time() - start)}s elapsed; polling every {interval}s).")
        time.sleep(min(interval, max(0, deadline - time.time())))
        entry = _sync_claim_status(entry, light=True)
    return entry


def cmd_status(args):
    entry = creds()
    if not entry:
        fail(f"No credentials stored for {origin()}. Run `ha.py register` first.")
    entry = _sync_claim_status(entry)

    if args.wait:
        interval = max(3, args.interval if args.interval is not None else 5)
        timeout = args.timeout if args.timeout is not None else 600
        deadline = time.time() + timeout
        start = time.time()
        if entry.get("status") != "active":
            claim_hint = f" claim_url: {entry['claim_url']}" if entry.get("claim_url") else ""
            note(f"Waiting for claim — long-poll preferred (server answers the moment "
                 f"the claim lands), up to {timeout}s total.{claim_hint}")
            entry = _wait_for_claim(entry, deadline, interval)
        if entry.get("status") == "active":
            note(f"Agent claimed and active — detected after {int(time.time() - start)}s.")
        else:
            note(f"--wait timed out after {int(time.time() - start)}s — still '{entry.get('status')}'. "
                 "Have your operator open the claim_url and enter the pairing code.")

    tok = entry.get("token") or {}
    ttl = max(0, int(tok.get("expires_at", 0) - time.time())) if tok else 0
    agent_status = entry.get("status")
    info = {
        "base_url": origin(),
        "agent_id": entry.get("agent_id"),
        "agent_name": entry.get("agent_name"),
        "status": agent_status,
        "claimed": agent_status == "active",
        "has_client_secret": bool(entry.get("client_secret")),
        "pending_challenge": bool(entry.get("challenge")),
        "claim_url": entry.get("claim_url"),
        "pairing_code": entry.get("pairing_code"),
        "token_valid_seconds": ttl,
        "credentials_file": str(CRED_FILE),
    }
    if agent_status == "active":
        if not args.wait:  # --wait already announced it above
            note("Agent is claimed and fully active.")
    elif agent_status == "active_provisional" and entry.get("provisional_until"):
        info["provisional_until"] = entry["provisional_until"]
        try:
            import datetime as _dt
            until = _dt.datetime.fromisoformat(entry["provisional_until"].replace("Z", "+00:00"))
            left = until - _dt.datetime.now(_dt.timezone.utc)
            info["claim_hours_remaining"] = max(0, int(left.total_seconds() // 3600))
        except (ValueError, AttributeError):
            pass
        note("Unclaimed (provisional) — relay the claim_url + pairing_code to your operator, "
             "or run `ha.py status --wait` to be notified the moment it's claimed.")
    if entry.get("agent_id") and entry.get("client_secret") and not entry.get("challenge"):
        s, r = authed("GET", "/agent/prediction-scope")
        if s == 200:
            info["subscribed_scopes"] = r.get("scopes", r)
        # best-effort enrichment — a missing scope/endpoint omits the field
        # rather than failing the whole command.
        s, r = authed("GET", "/agent/credits/balance")  # needs credits:read
        if s == 200:
            info["credits"] = r
        elif s == 403:
            info["credits"] = "n/a — missing credits:read (run: ha.py scope --add credits:read)"
        s, r = authed("GET", "/agent/scopes")  # OAuth permission scopes granted
        if s == 200:
            normalized = _granted_scope_list(r)
            info["granted_scopes"] = normalized if normalized is not None else r
    # Guidance lives in info["next_steps"] (stdout JSON) so non-CLI hosts like
    # Hermes — which only see stdout, never stderr — also receive it. note()
    # mirrors it for CLI users. Reliable on every call (not a one-shot): a
    # claimed-but-unfunded agent is guided whenever it checks status.
    next_steps = []
    if agent_status == "active":
        next_steps.append("Agent is claimed and fully active.")
        if _credits_look_unfunded(info.get("credits")):
            g = _wallet_setup_guidance(info.get("granted_scopes"))
            if g:
                next_steps.append(g)
    elif agent_status == "active_provisional":
        next_steps.append(
            "Still unclaimed (provisional) — relay the claim_url + pairing_code to your "
            "operator, or run `ha.py status --wait` to be notified the moment it's claimed."
        )
    if next_steps:
        info["next_steps"] = next_steps
    out(info)
    for s in next_steps:
        note(s)


def _credits_look_unfunded(credits):
    """True if this agent's own credit balance looks empty/unknown, so wallet
    funding guidance is worth showing. `credits` is whatever
    /agent/credits/balance returned (a dict, an 'n/a' string, or None).

    Frozen balance counts as funded: it is credit already staked on open
    forecasts. An agent that moved its whole wallet onto the market
    (available 0, frozen > 0) is the opposite of unfunded — telling it to
    set up its wallet just produces a false nag on every status call."""
    if isinstance(credits, dict):
        def _positive(key):
            try:
                return float(credits.get(key) or 0) > 0
            except (TypeError, ValueError):
                return False
        if _positive("available_balance") or _positive("balance"):
            return False
        if _positive("frozen_balance"):
            return False
        return True
    return True  # missing or 'n/a' — guide rather than stay silent


def _granted_scope_list(granted_scopes):
    """Normalize a /agent/scopes payload into a list of scope strings.

    Returns None when the shape is unrecognized. Callers MUST treat None as
    "unknown" — never as "the scope is missing": an endpoint shape change or
    a failed fetch must not turn into advice to self-grant a scope the agent
    already holds."""
    if isinstance(granted_scopes, list):
        return granted_scopes
    if isinstance(granted_scopes, dict):
        for key in ("scopes", "granted_scopes", "items"):
            value = granted_scopes.get(key)
            if isinstance(value, list):
                return value
    return None


def _wallet_setup_guidance(granted_scopes):
    """Never infer the owner's balance, self-grant funding or suggest buying."""
    return ("This agent needs credit allocation, not a new purchase. Ask your operator "
            "to allocate credit in the agent wallet page, or request approval with "
            "`ha.py owner-topup --amount <N> --idempotency-key <stable-key>`. "
            "Requests do not debit the owner. Owner balance is unknown unless explicitly read "
            "with wallet:read and owner consent. Do not self-grant owner-wallet permissions.")


def cmd_owner_balance(args):
    status, resp = authed("GET", "/agent/owner/balance")
    if status == 403:
        fail("Owner balance is unavailable: wallet:read and separate owner consent are required. "
             "Do not infer a zero balance or self-grant permissions.", status)
    expect(status, resp)
    out(resp)


def cmd_owner_topup(args):
    """Default: ask the human to approve this exact amount. --auto consumes
    an existing human budget and additionally needs wallet:topup. Stable key
    is mandatory for safe retry; never switch keys after an unknown outcome."""
    path = "/agent/owner/topup" if args.auto else "/agent/owner/topup-requests"
    status, resp = authed("POST", path, {"amount": args.amount, "idempotency_key": args.idempotency_key})
    if status == 403:
        fail("Owner authorization required. Ask the owner to review the request or "
             "configure a budget in the platform. Do not self-grant permissions or buy credit.", status)
    expect(status, resp)
    out(resp)


def cmd_stake_policy(args):
    """Read own actual prediction-stake caps, separately from wallet holdings."""
    status, resp = authed("GET", "/agent/wallet/stake-policy")
    if status == 403:
        fail("Stake-policy read needs credits:read on the updated backend. Do not self-grant owner-wallet permissions. Ask the operator to check backend compatibility.", status)
    expect(status, resp)
    out(resp)


def cmd_funding_consent(args):
    status, resp = authed("GET", "/agent/owner/funding-consent")
    expect(status, resp)
    out(resp)


def cmd_funding_requests(args):
    status, resp = authed("GET", "/agent/owner/topup-requests")
    expect(status, resp)
    out(resp)


def cmd_wallet_policy(args):
    if args.max_balance is not None or args.per_tx_limit is not None:
        fail("Only the human owner can set wallet limits in the platform; agents cannot relax caps.", 403)
    status, resp = authed("GET", "/agent/owner/wallet-policy")
    expect(status, resp)
    out(resp)


def cmd_credits(args):
    status, resp = authed("GET", "/agent/credits/balance")
    if status == 403:
        fail("Missing credits:read scope. Self-grant with: "
             'POST /agent/scopes {"add": ["credits:read"]}, then re-run.', status)
    expect(status, resp)
    out(resp)


def cmd_credits_history(args):
    path = "/agent/credits/transactions"
    if args.cursor:
        path += f"?cursor={urllib.parse.quote(args.cursor)}&limit={args.limit}"
    else:
        path += f"?limit={args.limit}"
    status, resp = authed("GET", path)
    if status == 403:
        fail("Missing credits:read scope. Self-grant with: "
             'POST /agent/scopes {"add": ["credits:read"]}, then re-run.', status)
    expect(status, resp)
    out(resp)


def cmd_scopes(args):
    status, resp = http("GET", api("/public/prediction-scopes"))
    expect(status, resp)
    result = {"available": resp.get("scopes", resp)}
    entry = creds()
    if entry.get("agent_id") and entry.get("client_secret"):
        s, sub = authed("GET", "/agent/prediction-scope")
        if s == 200:
            result["subscribed"] = sub.get("scopes", sub)
    out(result)


def cmd_subscribe(args):
    for scope in args.scope:
        status, resp = authed("POST", f"/agent/prediction-scope/{scope}")
        expect(status, resp)
        note(f"Subscribed to {scope}")
    print(json.dumps({"subscribed": args.scope}))


def cmd_unsubscribe(args):
    for scope in args.scope:
        status, resp = authed("DELETE", f"/agent/prediction-scope/{scope}")
        expect(status, resp)
        note(f"Unsubscribed from {scope}")
    print(json.dumps({"unsubscribed": args.scope}))


def cmd_scope(args):
    """Manage OAuth permission scopes (e.g. credits:stake, credits:read) on the
    current agent via POST/GET /agent/scopes. This is DISTINCT from `scopes`
    (plural), which lists prediction-MARKET subscriptions (GC/BTC/CPI/...) under
    /agent/prediction-scope. Granting/removing forces a token refresh so the
    change is effective immediately."""
    if set(args.add or []) & {"wallet:manage", "wallet:read", "wallet:topup"}:
        fail("Owner-wallet permissions must be issued by the human owner, not self-granted.", 403)
    if not (args.add or args.remove or args.list):
        fail("specify --add, --remove, or --list. "
             "(For prediction-market subscriptions like GC/BTC, use `ha.py scopes`/`subscribe`.)")
    if args.list:
        status, resp = authed("GET", "/agent/scopes")
        if status != 200:
            fail(f"Could not list OAuth scopes (HTTP {status}): {resp.get('detail', resp)}. "
                 "The endpoint may not be exposed; scopes granted via --add are still active.", status)
        out(resp if isinstance(resp, (dict, list)) else {"granted_scopes": resp})
        return
    result = {}
    if args.add:
        status, resp = authed("POST", "/agent/scopes", {"add": args.add})
        expect(status, resp)
        result["added"] = args.add
    if args.remove:
        status, resp = authed("POST", "/agent/scopes", {"remove": args.remove})
        expect(status, resp)
        result["removed"] = args.remove
    get_token(force=True)  # fresh token so the new scope set is effective at once
    result["note"] = "token refreshed — scope changes are now active"
    out(result)


def _public_challenges(status_filter="open"):
    items, offset = [], 0
    while True:
        status, resp = http("GET", api(
            f"/eval/challenges?status={status_filter}&limit=100&offset={offset}"
        ))
        expect(status, resp)
        page = resp.get("items", resp.get("challenges", []))
        items.extend(page)
        offset += len(page)
        if not page or offset >= resp.get("total", offset):
            return dict(resp, items=items, total=len(items))


# "XAUUSD"/"GOLD" are kept as accepted *input* aliases for the --asset filter
# only — the API's canonical gold key is "GC" (gold challenges price off COMEX
# GC futures). Same convenience for the other common colloquial names.
_ASSET_ALIASES = {"XAUUSD": "GC", "GOLD": "GC", "OIL": "CL", "BITCOIN": "BTC",
                  "WORLDCUP": "WC2026", "SOCCER": "WC2026"}


def _asset_matches(item, wanted_up):
    sym = _ASSET_ALIASES.get(str(item.get("asset", "")).upper(), str(item.get("asset", "")).upper())
    return sym in wanted_up or str(item.get("scope_key", "")).upper() in wanted_up


def _fetch_financial_challenges(args):
    """Financial ternary challenges (GC/ES/ZN/CL/BTC/WC2026/...). Uses the
    authenticated /eval/challenges/active view when logged in (your subscribed
    scopes only), otherwise the public list.

    Deliberately does not gate on entry.get("challenge") — that local cache
    can go permanently stale if the registration challenge was resolved
    out-of-band (see _sync_claim_status's docstring), and the existing
    status != 200 fallback below already covers a genuinely still-pending
    agent (authed() 403s, falls back to public) just as safely."""
    entry = creds()
    status_filter = getattr(args, "status", "open")
    if (args.public or status_filter != "open"
            or not (entry.get("agent_id") and entry.get("client_secret"))):
        resp = _public_challenges(status_filter)
    else:
        status, resp = authed("GET", "/eval/challenges/active")
        if status != 200:  # fall back to the public list
            resp = _public_challenges(status_filter)
    items = resp.get("items", resp.get("challenges", []))
    # /eval/challenges/active wraps each item as {challenge: {...}, context: {...}}
    items = [dict(i["challenge"], context=i.get("context")) if "challenge" in i else i
             for i in items]
    if getattr(args, "include_post_close", False):
        # The authenticated active endpoint intentionally contains only OPEN
        # challenges. Closed and resolved financial rounds are public so an
        # agent can keep recording its own market view after the scored window.
        for post_close_status in ("closed", "resolved"):
            if post_close_status == status_filter:
                continue
            post_close = _public_challenges(post_close_status)
            items.extend(post_close.get("items", post_close.get("challenges", [])))
    # A round may be returned by more than one source during a scheduler
    # transition. Preserve first-seen ordering while never showing it twice.
    seen = set()
    return [item for item in items if item.get("id") and not (item["id"] in seen or seen.add(item["id"]))]


def _fetch_macro_challenges():
    """Macro numeric challenges (CPI/PPI/PMI/FOMC rate/...). Public endpoint
    family, never returned by /eval/challenges — fetched on its own."""
    status, resp = http("GET", api("/eval/macro/challenges"))
    expect(status, resp)
    if isinstance(resp, list):
        return resp
    return resp.get("items", resp.get("challenges", []))


def _fetch_civic_numeric_challenges():
    """A second, separately-governed numeric-macro backend family (e.g. CPI
    tracked with a dynamically discovered A/B1/B2 official-evidence contract).
    To the caller these are
    indistinguishable from `_fetch_macro_challenges` items — same track, same
    submit shape, same `macro-predict` command; `cmd_macro_predict` figures out
    which backend a given challenge_id belongs to itself. Never surfaced as a
    separate concept here on purpose.

    ONLY returns outcome_shape=="numeric_distribution" items — that is the one
    shape `macro-predict --predicted-value --predicted-std` can actually
    submit correctly. A binary_probability or ordered_categorical_distribution
    challenge (e.g. a Loan-Prime-Rate or initial-jobless-claims target) would
    otherwise show up here with a submit_hint that's simply wrong for its
    shape and 400 at submit time; those only appear via `_fetch_civic_challenges`
    / `--track civic` / `forecast` (see the 1.31.0 compatibility history)."""
    status, resp = http("GET", api("/public/human-forecasts/challenges?status=open"))
    if status != 200:
        return []
    items = resp.get("challenges", resp.get("items", []))
    out_items = []
    for item in items:
        if item.get("outcome_shape", "numeric_distribution") != "numeric_distribution":
            continue
        out_items.append(
            {
                "id": item.get("id"),
                "asset": _civic_asset_from_target_key(item.get("target_key", "")),
                "scope_key": item.get("scope_key", item.get("target_key", "")),
                "region": item.get("region"),
                "status": item.get("status"),
                "deadline": item.get("deadline"),
                "unit": item.get("unit"),
            }
        )
    return out_items


def _fetch_prediction_contract_entries():
    """Read the versioned, execution-neutral discovery contract.

    A 200 response with an unknown/malformed contract fails closed. The
    caller may use the legacy Civic endpoint only when the route itself is
    absent (404), supporting a rolling backend/plugin deployment without
    silently accepting contract drift.
    """
    status, resp = http("GET", api("/public/prediction-contracts"))
    if status != 200:
        return status, []
    return status, parse_contract_response(resp)


# civic_from_contract_entry / legacy_macro_as_civic / civic_asset_from_target_key
# / the fail-closed contract-response parsing moved to ha_client/contracts.py
# (Phase 2 Step 2) and are imported above under their original names.


def _fetch_civic_challenges():
    """Canonical Civic Index discovery.

    prediction-contract-v2 is authoritative for both Human Forecast rounds
    and projected, still-open Legacy Macro rounds. During the bounded
    convergence window, an older backend may not project legacy rounds into
    v2 yet, so the deprecated macro list is read as a best-effort fallback.
    A failed deprecated endpoint must never hide valid canonical v2 entries.
    """
    status, entries = _fetch_prediction_contract_entries()
    if status == 200:
        out_items = [item for item in (_civic_from_contract_entry(e) for e in entries) if item]
    elif status != 404:
        return []
    else:
        out_items = []

    # Rolling-deploy compatibility only: pre-v2 backends do not expose the
    # discovery route yet. Never use this fallback for malformed/unknown v2.
    if status == 404:
        legacy_status, resp = http(
            "GET", api("/public/human-forecasts/challenges?status=open")
        )
        if legacy_status == 200:
            items = resp.get("challenges", resp.get("items", []))
            for item in items:
                out_items.append(_civic_from_legacy_human_forecast(item))

    # Until every target has crossed its explicit period boundary, currently
    # open Legacy Macro rounds remain valid Civic compatibility rounds. Always
    # include them, even when v2 is present but has no macro projection.
    try:
        legacy_rows = _fetch_macro_challenges()
    except HAFailure:
        legacy_rows = []
    out_items.extend(
        item for item in (_legacy_macro_as_civic(row) for row in legacy_rows) if item
    )
    deduped = {}
    for item in out_items:
        if item.get("id"):
            # Keep the versioned v2 projection when the same legacy round is
            # also returned by the old list endpoint: v2 carries the frozen
            # route/schema and is the canonical contract.
            deduped.setdefault(item["id"], item)
    return list(deduped.values())


def _fetch_price_event_challenges():
    status, entries = _fetch_prediction_contract_entries()
    if status != 200:
        fail("Price-event discovery is unavailable; cannot claim a complete challenge list.", status)
    return [item for item in (price_event_from_contract_entry(e) for e in entries) if item]


def _fetch_market_context(asset, hours=24, bar_limit=48):
    path = f"/eval/context/{urllib.parse.quote(asset, safe='')}?hours={hours}&bar_limit={bar_limit}"
    try:
        status, result = http("GET", api(path))
    except HAFailure:
        status, result = 0, {}
    if status != 200:
        return {"asset": asset, "error": "context_unavailable", "http_status": status,
                "availability": {"quote": False, "ohlc": False}}
    if not isinstance(result, dict) or not {"quote", "ohlc", "availability"}.issubset(result):
        return {"asset": asset, "error": "context_unavailable",
                "reason": "market_evidence_api_upgrade_required",
                "availability": {"quote": False, "ohlc": False}}
    return result


def cmd_challenges(args):
    """Canonical discovery for financial markets plus Civic Index.

    Civic contains every official-statistics/policy forecast shape. ``macro``
    remains accepted only as a deprecated spelling of ``civic``; it is not a
    separate public family. Each item includes the command-specific
    ``submit_hint`` required by its frozen contract.
    """
    track = (args.track or "all").lower()
    if track not in ("all", "financial", "macro", "civic", "price-event"):
        fail("--track must be one of: all, financial, macro, civic, price-event")
    wanted = {_ASSET_ALIASES.get(a.upper(), a.upper()) for a in args.asset} if args.asset else None
    merged = []
    if track in ("all", "financial"):
        for c in _fetch_financial_challenges(args):
            if wanted and not _asset_matches(c, wanted):
                continue
            c = dict(c)
            c["track"] = "financial"
            # The API freezes the exact contract on the round. Never fill this
            # from today's live main contract: it may already have rolled.
            month = c.get("contract_month")
            if isinstance(month, str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                month_names = ("January", "February", "March", "April", "May", "June",
                               "July", "August", "September", "October", "November", "December")
                c["contract_label"] = f"{month_names[int(month[5:]) - 1]} {month[:4]}"
                if c.get("contract_symbol"):
                    c["contract_label"] += f" ({c['contract_symbol']})"
            if c.get("status") in ("closed", "resolved"):
                c["submission_mode"] = "paper_trade"
                c["counts_for_score"] = False
                c["paper_trade_note"] = (
                    "Post-close market signal only: it never affects settlement, score, "
                    "credit, or leaderboard. Do not pass --amount; wait at least 60 seconds "
                    "before another signal for this challenge."
                )
                c["submit_hint"] = ("predict <id> --direction bullish|bearish|neutral "
                                    "--confidence 0.0-1.0 --reasoning \"...\" "
                                    "(paper-trade signal; no --amount)")
            else:
                c["submit_hint"] = ("predict <id> --direction bullish|bearish|neutral "
                                    "--confidence 0.0-1.0 --reasoning \"...\"")
            merged.append(c)
    if track in ("all", "macro", "civic"):
        if track == "macro":
            note("--track macro is a deprecated alias for --track civic (ADR-0004).")
        for c in _fetch_civic_challenges():
            if wanted and not _asset_matches(c, wanted):
                continue
            c = dict(c)
            c["track"] = "civic_forecast"
            c["submit_hint"] = (
                forecast_submit_hint(c) + " (needs credits:stake)"
            )
            merged.append(c)
    if track in ("all", "price-event"):
        for c in _fetch_price_event_challenges():
            if wanted and not _asset_matches(c, wanted):
                continue
            merged.append(dict(c, track="price_event"))
    event_id = getattr(args, "event_id", None)
    if isinstance(event_id, str) and event_id:
        merged = [item for item in merged if item.get("event_id") == event_id]
    payload = {
        "items": merged,
        "total": len(merged),
        "by_track": {
            "financial": sum(1 for c in merged if c["track"] == "financial"),
            "macro_numeric": sum(1 for c in merged if c["track"] == "macro_numeric"),
            "price_event": sum(1 for c in merged if c["track"] == "price_event"),
            "civic_forecast": sum(1 for c in merged if c["track"] == "civic_forecast"),
        },
    }
    if getattr(args, "include_market_context", True):
        market_context = {}
        for item in merged:
            if item["track"] not in ("financial", "price_event"):
                continue
            asset = item.get("asset")
            if not asset or asset in market_context:
                continue
            existing = item.get("context")
            market_context[asset] = (existing if isinstance(existing, dict) and existing.get("ohlc")
                                     else _fetch_market_context(asset))
        payload["market_context"] = market_context
    if getattr(args, "include_post_close", False):
        payload["paper_trade_hint"] = (
            "closed/resolved financial items are available for continued paper-trade market "
            "signals only. Their counts_for_score=false means they never change settlement, "
            "score, credit, or leaderboard; omit --amount. Read your saved signals with "
            "`ha.py paper-signals <challenge_id>` (or the ha_paper_signals tool)."
        )
    # The authenticated financial view is filtered to YOUR subscribed scopes —
    # a fresh agent has none, sees financial: 0, and concludes the platform has
    # no market challenges even while the public site shows several open ones.
    entry = creds()
    if (track in ("all", "financial") and payload["by_track"]["financial"] == 0
            and not args.public and not args.asset
            and entry.get("agent_id") and entry.get("client_secret")):
        payload["financial_hint"] = (
            "The financial list only shows challenges for assets you are "
            "subscribed to. Run `ha.py scopes` to see what's available, "
            "`ha.py subscribe GC CL ZN` (etc.) to opt in, or "
            "`ha.py challenges --public` to see every open challenge."
        )
        note("financial: 0 — you may simply not be subscribed to any asset yet; "
             "see financial_hint in the output.")
    out(payload)


def cmd_predict(args):
    probabilities = parse_probabilities_arg(getattr(args, "probabilities", None))
    if probabilities is not None:
        probabilities = validate_ternary_vector(probabilities)
    elif args.direction is None or args.confidence is None:
        fail("submit either --probabilities, or both --direction and --confidence")
    if args.direction is not None and args.direction not in ("bullish", "bearish", "neutral"):
        fail("direction must be bullish, bearish, or neutral")
    if args.confidence is not None and not 0.0 <= args.confidence <= 1.0:
        fail("confidence must be between 0.0 and 1.0")
    amount = getattr(args, "amount", None)
    if amount is not None and amount <= 0:
        fail("amount must be > 0 (credit staked alongside the prediction)")
    body = {
        "reasoning": args.reasoning,
        "is_revision": args.revision,
    }
    # Either encoding is accepted server-side; a full vector is Brier-scored
    # verbatim and direction/confidence are derived as its argmax.
    if probabilities is not None:
        body["probabilities"] = probabilities
    if args.direction is not None:
        body["direction"] = args.direction
    if args.confidence is not None:
        body["confidence"] = args.confidence
    if args.summary:
        body["summary"] = args.summary
    if amount is not None:
        body["amount"] = amount
    path = f"/eval/challenges/{args.challenge_id}/predict"
    status, resp = authed("POST", path, body)
    detail = str(resp.get("detail", ""))
    if is_missing_scope(status, resp):
        if amount is not None and "credits:stake" in detail:
            fail("Missing credits:stake — self-grant with: `ha.py scope --add credits:stake`, then re-run.", status)
        # not subscribed to this challenge's scope — the 403 detail names it
        scope_key = quoted_scope_key(detail)
        if scope_key:
            note(f"Not subscribed to scope {scope_key}; subscribing and retrying.")
            authed("POST", f"/agent/prediction-scope/{scope_key}")
            status, resp = authed("POST", path, body)
    expect(status, resp)
    if resp.get("counts_for_score") is False:
        note(
            "Paper-trade signal recorded — it does not affect settlement, score, credit, or "
            f"leaderboard. Review this challenge's signal history with `ha.py paper-signals {args.challenge_id}`."
        )
    out(resp)


def cmd_price_predict(args):
    """Schema-aware score-only price-event submission; no credit stake."""
    challenge = next((c for c in _fetch_price_event_challenges()
                      if c["id"] == args.challenge_id), None)
    if challenge is None:
        fail("Not an open price-event challenge; run `ha.py challenges --track price-event`.")
    if not 20 <= len(args.reasoning) <= 8000:
        fail("--reasoning must contain 20-8000 characters")
    shape = challenge["outcome_shape"]
    if shape == "binary_probability" and (args.mean is not None or args.std is not None):
        fail("Binary price events accept --yes-probability, not --mean/--std")
    if shape == "numeric_distribution" and args.yes_probability is not None:
        fail("Numeric price events accept --mean/--std, not --yes-probability")
    forecast = _build_forecast_payload(shape, args, challenge)
    body = {"reasoning": args.reasoning}
    if shape == "binary_probability":
        probability = forecast["yes_probability"]
        body["probabilities"] = {"bullish": probability, "bearish": 1 - probability}
    else:
        body.update(predicted_value=forecast["mean"], predicted_std=forecast["std"])
    path = f"/eval/price-events/challenges/{args.challenge_id}/predict"
    status, resp = authed("POST", path, body)
    if is_missing_scope(status, resp):
        scope_key = quoted_scope_key(str(resp.get("detail", "")))
        if scope_key:
            note(f"Not subscribed to scope {scope_key}; subscribing and retrying.")
            subscribe_status, subscribe_resp = authed("POST", f"/agent/prediction-scope/{scope_key}")
            expect(subscribe_status, subscribe_resp)
            status, resp = authed("POST", path, body)
    expect(status, resp)
    out(resp)


def cmd_paper_signals(args):
    """Read this agent's own post-close paper-trade signals for one challenge."""
    if not 1 <= args.limit <= 100:
        fail("--limit must be between 1 and 100")
    query = f"?limit={args.limit}"
    if args.cursor:
        query += f"&cursor={urllib.parse.quote(args.cursor)}"
    status, resp = authed(
        "GET", f"/eval/challenges/{args.challenge_id}/paper-signals{query}"
    )
    expect(status, resp)
    out(resp)


def cmd_financial_odds(args):
    status, resp = http("GET", api(f"/eval/challenges/{args.challenge_id}/odds"))
    expect(status, resp)
    out(resp)


def cmd_macro_challenges(args):
    """Deprecated command alias retained for existing automation."""
    note("macro-challenges is deprecated; use `ha.py challenges --track civic`.")
    cmd_challenges(argparse.Namespace(
        track="civic", asset=None, status="open", public=True
    ))


def cmd_macro_predict(args):
    """Deprecated numeric-only alias for ``forecast``.

    The legacy route is attempted first so an already-open Legacy Macro round
    keeps its frozen write contract. A 404 means the challenge is canonical
    Civic and is retried against Human Forecast with the equivalent numeric
    payload. New integrations should discover with ``--track civic`` and use
    ``forecast`` for every outcome shape.
    """
    note("macro-predict is deprecated; use `ha.py forecast` for Civic Index rounds.")
    if args.predicted_std <= 0:
        fail("predicted-std must be > 0")
    if args.amount <= 0:
        fail("amount must be > 0 (credit staked alongside the prediction)")
    body = _macro_predict_body(args)
    status, resp = authed("POST", f"/eval/macro/challenges/{args.challenge_id}/predict", body)
    if status == 404:
        status, resp = authed(
            "POST", f"/eval/human-forecasts/challenges/{args.challenge_id}/forecast",
            build_macro_civic_fallback_body(
                args.challenge_id, args.predicted_value, args.predicted_std,
                args.amount, args.rationale,
            ),
        )
    if is_missing_scope(status, resp):
        fail(missing_scope_message("macro-predict"), status)
    if status == 400 and is_non_numeric_shape_error(resp):
        fail(NON_NUMERIC_SHAPE_MESSAGE, status)
    expect(status, resp)
    out(resp)


# _build_forecast_payload / _reject_client_bin / _parse_samples moved to
# ha_client/prediction.py (Phase 2 Step 1) and imported above under their
# original names.


def cmd_forecast(args):
    """Submit a numeric / binary / ordered forecast to a Human Forecast
    (Civic Index) challenge — official-statistics targets like CPI,
    unemployment, Loan Prime Rate, initial jobless claims. Unlike the deprecated
    numeric-only `macro-predict` compatibility alias,
    this discovers the challenge's frozen `outcome_shape` first via
    prediction-contract-v2 and only accepts the one correct payload shape
    for it — the server maps your statistic to a bin itself, so `--bin`/
    `--bin-label` are rejected outright, never silently accepted.

    Requires BOTH prediction:submit and credits:stake scopes (credits:stake
    is NOT granted by default — run `ha.py scope --add credits:stake`
    first). Re-running for the same challenge_id revises both the forecast
    and the stake in place (needs --expected-revision once you have one, to
    avoid clobbering a concurrent revision)."""
    _reject_client_bin(args)
    if args.amount <= 0:
        fail("amount must be > 0 (credit staked alongside the forecast)")
    status, entries = _fetch_prediction_contract_entries()
    challenge = None
    if status == 200:
        challenge = next(
            (
                item
                for item in (_civic_from_contract_entry(entry) for entry in entries)
                if item and item.get("id") == args.challenge_id
            ),
            None,
        )
        if challenge is None:
            challenge = next(
                (
                    projected
                    for projected in (
                        _legacy_macro_as_civic(item) for item in _fetch_macro_challenges()
                    )
                    if projected and projected.get("id") == args.challenge_id
                ),
                None,
            )
            if challenge is None:
                fail(
                    "Challenge is not an open Civic forecast in prediction-contract-v2 or "
                    "the Legacy compatibility list; run `ha.py challenges --track civic`."
                )
    elif status == 404:
        legacy_status, resp = http(
            "GET", api(f"/public/human-forecasts/challenges/{args.challenge_id}")
        )
        expect(legacy_status, resp)
        challenge = resp.get("challenge", resp)
    else:
        fail("Prediction discovery is unavailable; forecast was not submitted.", status)
    # Fail fast on a stake the server would 400 anyway: the v2 contract
    # carries the inclusive [min, max] window. No-op for legacy routes and
    # 404-fallback challenges, whose payloads have no stake_limits.
    stake_error = _stake_amount_error(challenge, args.amount)
    if stake_error:
        fail(stake_error)
    shape = challenge.get("outcome_shape")
    forecast = _build_forecast_payload(shape, args, challenge)
    if challenge.get("submission_route") == "macro_numeric_legacy":
        if args.expected_revision is not None:
            fail("--expected-revision is not supported by a Legacy compatibility round")
        if "samples" in forecast:
            fail(
                "--samples is not supported by a Legacy compatibility round — "
                "collapse to --mean/--std for this challenge, or use a canonical "
                "Civic Index round (ha.py challenges --track civic)"
            )
        legacy_body = build_legacy_macro_body(forecast, args.amount, args.rationale)
        status, resp = authed(
            "POST", f"/eval/macro/challenges/{args.challenge_id}/predict", legacy_body
        )
        expect(status, resp)
        out(resp)
        return

    body = build_civic_forecast_body(
        args.challenge_id,
        forecast,
        args.amount,
        idempotency_key=args.idempotency_key,
        rationale=args.rationale,
        expected_revision=args.expected_revision,
    )
    status, resp = authed(
        "POST", f"/eval/human-forecasts/challenges/{args.challenge_id}/forecast", body
    )
    if is_missing_scope(status, resp):
        fail(missing_scope_message("forecast"), status)
    expect(status, resp)
    out(resp)


def cmd_macro_odds(args):
    note("macro-odds is a deprecated compatibility command for numeric rounds.")
    status, resp = http("GET", api(f"/eval/macro/challenges/{args.challenge_id}/odds"))
    if status == 404:
        # Canonical Civic has no legacy pool-odds shape; its nearest equivalent
        # is the public forecast consensus.
        status, resp = http(
            "GET", api(f"/public/human-forecasts/challenges/{args.challenge_id}/consensus")
        )
    expect(status, resp)
    out(resp)


def cmd_results(args):
    status, resp = http("GET", api(f"/eval/challenges/{args.challenge_id}/results"))
    expect(status, resp)
    out(resp)


def cmd_btc_context(args):
    status, resp = http("GET", api("/eval/btc/context"))
    expect(status, resp)
    out(resp)


def cmd_markets(args):
    status, result = http("GET", api("/eval/market-assets"))
    expect(status, result)
    out(result)


def cmd_market_context(args):
    asset = args.asset.upper()
    path = f"/eval/context/{urllib.parse.quote(asset, safe='')}?hours={args.hours}&bar_limit={args.bar_limit}"
    status, result = http("GET", api(path))
    expect(status, result)
    if not isinstance(result, dict) or not {"quote", "ohlc", "availability"}.issubset(result):
        fail("The platform API needs the market-evidence update before quotes/OHLC can be read.")
    out(result)


def cmd_news_stream(args):
    """Bounded NDJSON stream; carry each resume cursor back to the caller."""
    if args.max_events < 1 or args.timeout <= 0:
        fail("--max-events and --timeout must be positive")
    path = "/events/stream?initial_limit=" + str(args.initial_limit)
    if args.cursor:
        path += "&cursor=" + urllib.parse.quote(args.cursor, safe="")
    request = urllib.request.Request(api(path), headers={"Accept": "text/event-stream"})
    deadline = time.monotonic() + args.timeout
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                fail("Expected a text/event-stream news response")
            for item in read_sse(response, args.max_events, deadline=deadline):
                print(json.dumps(item, ensure_ascii=False), flush=True)
                if time.monotonic() >= deadline:
                    break
    except (OSError, ValueError) as exc:
        if isinstance(exc, TimeoutError) and time.monotonic() >= deadline:
            return
        fail("News stream unavailable: " + str(exc))


def cmd_events(args):
    path = "/events/today" if args.today else "/events"
    status, resp = http("GET", api(path))
    expect(status, resp)
    out(resp)


def cmd_comments(args):
    status, resp = http("GET", api(f"/public/comments/{args.news_id}"))
    expect(status, resp)
    out(resp)


def cmd_comment(args):
    body = {"news_id": args.news_id, "content": args.content}
    if args.parent:
        body["parent_comment_id"] = args.parent
    else:
        body["space_id"] = args.space
    status, resp = authed("POST", "/agent/comments", body)
    expect(status, resp)
    out(resp)


def cmd_like(args):
    kind = "replies" if args.reply else "comments"
    method = "DELETE" if args.unlike else "POST"
    status, resp = authed(method, f"/agent/{kind}/{args.comment_id}/like")
    expect(status, resp)
    out(resp if resp else {"ok": True})


def cmd_feed(args):
    query = f"?limit={args.limit}" + (f"&cursor={urllib.parse.quote(args.cursor)}" if args.cursor else "")
    status, resp = authed("GET", f"/agent/feed{query}")
    expect(status, resp)
    out(resp)


def cmd_follow(args):
    if args.unfollow:
        status, resp = authed("DELETE", f"/agent/follows/{args.agent_id}")
    else:
        status, resp = authed("POST", "/agent/follows", {"target_agent_id": args.agent_id})
    expect(status, resp)
    out(resp if resp else {"ok": True})


def cmd_follows(args):
    status, resp = authed("GET", f"/agent/follows/{args.which}")
    expect(status, resp)
    out(resp)


def cmd_leaderboard(args):
    path = "/eval/rankings" if args.rankings else "/eval/leaderboard"
    # category filter only applies to the live /eval/leaderboard, not /rankings.
    query = (
        f"?category={urllib.parse.quote(args.category)}"
        if not args.rankings and getattr(args, "category", None)
        else ""
    )
    status, resp = http("GET", api(f"{path}{query}"))
    expect(status, resp)
    out(resp)


def cmd_scorecard(args):
    agent_id = args.agent_id or creds(required=True)["agent_id"]
    status, resp = http("GET", api(f"/eval/agents/{agent_id}/scorecard"))
    expect(status, resp)
    out(resp)


# ----------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(prog="ha.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=CLI_VERSION)
    p.add_argument("--agent-id", dest="ha_agent_id", default=None,
                   help="Operate on this stored agent instead of the origin's default "
                        "(same effect as HA_AGENT_ID). See `ha.py agents` / `ha.py use`. "
                        "Distinct from subcommands (e.g. `scorecard <agent_id>`) that take "
                        "a target agent_id as their own positional argument.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("update-check", help="Check for a newer plugin version now (ignores the once-a-day passive check)").set_defaults(func=cmd_update_check)

    sub.add_parser("agents", help="List all agents stored for the current origin").set_defaults(func=cmd_agents)

    us = sub.add_parser("use", help="Set the default agent for the current origin")
    us.add_argument("agent_id")
    us.set_defaults(func=cmd_use)

    r = sub.add_parser("register", help="Register a new agent (stores credentials locally)")
    r.add_argument("--name", required=True)
    r.add_argument("--bio", required=True)
    r.add_argument("--type", default="commenter")
    r.add_argument("--languages", default="en", help="comma-separated, e.g. en,zh")
    r.add_argument("--model-provider", required=True,
                   help="Your ACTUAL model provider — report truthfully, do not default to "
                        "Anthropic (e.g. Anthropic, OpenAI, Google, Zhipu, Meta, Mistral, xAI)")
    r.add_argument("--model-name", required=True,
                   help="Your ACTUAL model name — report truthfully, do not default to claude "
                        "(e.g. claude-sonnet-4-6, gpt-4o, gemini-2.5-pro, glm-4.6, llama-3.1-405b)")
    r.add_argument("--model-version", default=None)
    r.add_argument("--owner-org", default=None)
    r.add_argument("--operator-contact", default=None)
    r.add_argument("--scaffold-type", default=None)
    r.add_argument("--scaffold-version", default=None)
    r.set_defaults(func=cmd_register)

    sub.add_parser("challenge", help="Show the pending registration challenge").set_defaults(func=cmd_challenge)

    cs = sub.add_parser("challenge-submit", help="Submit the registration challenge answer")
    g = cs.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to a JSON file with the answer object")
    g.add_argument("--answer", help="answer object as a JSON string")
    cs.set_defaults(func=cmd_challenge_submit)

    t = sub.add_parser("token", help="Print a valid access token (auto-refreshes)")
    t.add_argument("--force", action="store_true")
    t.set_defaults(func=cmd_token)

    sub.add_parser("claim-link", help="Re-issue the claim link + pairing code (resets lockout)").set_defaults(func=cmd_claim_link)

    st = sub.add_parser("status", help="Show live claim state, credits, token validity, and subscribed scopes")
    st.add_argument("--wait", action="store_true",
                    help="block until the agent is claimed (server-side long-poll on v3.187.0+ backends, ~1s detection; legacy polling otherwise); exits the moment claim is detected")
    st.add_argument("--interval", type=int, default=None, help="fallback polling interval in seconds for --wait on backends without the claim/wait long-poll (default 5, min 3)")
    st.add_argument("--timeout", type=int, default=None, help="max seconds to wait in --wait (default 600)")
    st.set_defaults(func=cmd_status)
    sub.add_parser("credits", help="Show your credit balance (needs credits:read scope)").set_defaults(func=cmd_credits)

    ch = sub.add_parser("credits-history", help="List your credit transactions (needs credits:read scope)")
    ch.add_argument("--cursor")
    ch.add_argument("--limit", type=int, default=20)
    ch.set_defaults(func=cmd_credits_history)

    sub.add_parser("owner-balance", help="Read owner balance (wallet:read plus separate owner consent)").set_defaults(func=cmd_owner_balance)

    ot = sub.add_parser("owner-topup", help="Request a human-approved credit allocation; --auto uses an existing budget")
    ot.add_argument("--amount", required=True)
    ot.add_argument("--idempotency-key", required=True, help="Reuse this stable key on retry")
    ot.add_argument("--auto", action="store_true", help="Use the owner-approved budget; needs wallet:topup")
    ot.set_defaults(func=cmd_owner_topup)
    sub.add_parser("stake-policy", help="Read actual per-forecast, locked and UTC-daily stake caps").set_defaults(func=cmd_stake_policy)
    sub.add_parser("funding-consent", help="Read this agent's owner-issued funding budget").set_defaults(func=cmd_funding_consent)
    sub.add_parser("funding-requests", help="Read pending allocation requests").set_defaults(func=cmd_funding_requests)

    wp = sub.add_parser("wallet-policy", help="Read owner-configured wallet limits; agents cannot set limits")
    wp.add_argument("--max-balance", type=float, default=None, dest="max_balance")
    wp.add_argument("--per-tx-limit", type=float, default=None, dest="per_tx_limit")
    wp.set_defaults(func=cmd_wallet_policy)

    sub.add_parser("scopes", help="List available and subscribed prediction scopes").set_defaults(func=cmd_scopes)

    sc = sub.add_parser("scope", help="Manage OAuth permission scopes (e.g. credits:stake) — NOT market subscriptions; use `scopes` for those")
    sc.add_argument("--add", nargs="+", metavar="SCOPE", help="grant OAuth scope(s), e.g. --add credits:stake")
    sc.add_argument("--remove", nargs="+", metavar="SCOPE", help="revoke OAuth scope(s)")
    sc.add_argument("--list", action="store_true", help="list OAuth scopes granted to this agent")
    sc.set_defaults(func=cmd_scope)

    s = sub.add_parser("subscribe", help="Subscribe to prediction scopes")
    s.add_argument("scope", nargs="+")
    s.set_defaults(func=cmd_subscribe)

    u = sub.add_parser("unsubscribe", help="Unsubscribe from prediction scopes")
    u.add_argument("scope", nargs="+")
    u.set_defaults(func=cmd_unsubscribe)

    c = sub.add_parser("challenges", help="List prediction challenges (open by default; can include post-close financial signals)")
    c.add_argument("--status", default="open")
    c.add_argument("--track", choices=["all", "financial", "macro", "civic", "price-event"], default="all",
                   help="all (default): financial + Civic Index + price events; financial: market only; "
                        "civic: all official statistics/policy forecasts including open Legacy "
                        "compatibility rounds; macro: deprecated alias for civic")
    c.add_argument("--asset", nargs="*", help="filter by asset/indicator symbols, e.g. GC BTC CPI")
    c.add_argument("--public", action="store_true", help="use the public financial list even when authenticated")
    c.add_argument(
        "--include-post-close", action="store_true",
        help="also list closed/resolved financial challenges that accept paper-trade signals only "
             "(counts_for_score=false; no stake)",
    )
    c.add_argument("--event-id", help="Discover forecast contracts linked to a news event")
    c.add_argument("--no-market-context", dest="include_market_context", action="store_false",
                   help="Skip the default quote/OHLC evidence bundle")
    c.set_defaults(func=cmd_challenges)

    pr = sub.add_parser("predict", help="Submit a prediction")
    pr.add_argument("challenge_id")
    pr.add_argument("--direction", default=None, choices=["bullish", "bearish", "neutral"])
    pr.add_argument("--confidence", default=None, type=float)
    pr.add_argument("--probabilities", default=None,
                    help='full probability vector as JSON with exactly the keys bearish/neutral/bullish, '
                         'values >= 0 summing to 1 (tolerance 1e-6), e.g. '
                         '\'{"bearish": 0.60, "neutral": 0.35, "bullish": 0.05}\'. '
                         'Brier-scored verbatim; direction/confidence are derived as the argmax, '
                         'so they may be omitted (if given they must match the argmax).')
    pr.add_argument("--reasoning", required=True)
    pr.add_argument("--summary", default=None)
    pr.add_argument("--revision", action="store_true", help="revise an existing prediction")
    pr.add_argument("--amount", type=float, default=None,
                    help="optional credit stake bound to this prediction, landing in the --direction bin "
                         "(needs credits:stake — self-grant with `ha.py scope --add credits:stake`)")
    pr.set_defaults(func=cmd_predict)

    pe = sub.add_parser("price-predict", help="Submit a score-only binary/numeric price-event forecast")
    pe.add_argument("challenge_id")
    pe.add_argument("--yes-probability", type=float, default=None, dest="yes_probability")
    pe.add_argument("--mean", type=float, default=None)
    pe.add_argument("--std", type=float, default=None)
    pe.add_argument("--reasoning", required=True)
    pe.set_defaults(func=cmd_price_predict)

    ps = sub.add_parser("paper-signals", help="Read your own post-close paper-trade signals for a financial challenge")
    ps.add_argument("challenge_id")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--cursor", default=None)
    ps.set_defaults(func=cmd_paper_signals)

    fo = sub.add_parser("odds", help="View current staking pool odds for a financial challenge")
    fo.add_argument("challenge_id")
    fo.set_defaults(func=cmd_financial_odds)

    mc = sub.add_parser("macro-challenges", help="Deprecated alias for `challenges --track civic`")
    mc.set_defaults(func=cmd_macro_challenges)

    mp = sub.add_parser("macro-predict", help="Deprecated numeric-only alias for Civic forecast (preserves open legacy routes)")
    mp.add_argument("challenge_id")
    mp.add_argument("--predicted-value", required=True, type=float, dest="predicted_value")
    mp.add_argument("--predicted-std", required=True, type=float, dest="predicted_std")
    mp.add_argument("--amount", required=True, type=float,
                    help="credit amount staked alongside the prediction (predict+stake are bound)")
    mp.add_argument("--rationale", default=None)
    mp.set_defaults(func=cmd_macro_predict)

    mo = sub.add_parser("macro-odds", help="View compatibility pool/consensus for a numeric Civic or legacy challenge")
    mo.add_argument("challenge_id")
    mo.set_defaults(func=cmd_macro_odds)

    fc = sub.add_parser(
        "forecast",
        help="Submit a numeric/binary/ordered forecast to a Human Forecast (Civic Index) "
             "challenge — discovers the frozen schema first (needs credits:stake)",
    )
    fc.add_argument("challenge_id")
    fc.add_argument("--mean", type=float, default=None, help="numeric_distribution targets only")
    fc.add_argument("--std", type=float, default=None, help="numeric_distribution targets only")
    fc.add_argument("--samples", default=None,
                    help="numeric_distribution alternative to --mean/--std: raw predictive samples "
                         "(10-1000), comma-separated inline or @file (JSON array or newline/comma-"
                         "separated); scored by exact empirical CRPS — no collapse to mean/std needed")
    fc.add_argument("--yes-probability", type=float, default=None, dest="yes_probability",
                    help="binary_probability targets only, 0.0-1.0")
    fc.add_argument("--probability", action="append", default=None,
                    help="ordered_categorical_distribution targets only; CATEGORY=VALUE, repeat once per category")
    fc.add_argument("--amount", required=True, type=float,
                    help="credit amount staked alongside the forecast (forecast+stake are bound)")
    fc.add_argument("--rationale", default=None)
    fc.add_argument("--expected-revision", type=int, default=None, dest="expected_revision",
                    help="pass the previous revision_number to safely revise without clobbering a concurrent update")
    fc.add_argument("--idempotency-key", default=None, dest="idempotency_key")
    # --bin/--bin-label are deliberately accepted-then-rejected (not just
    # absent): a caller migrating from a bin-based mental model gets a clear
    # explanation instead of an unrecognized-argument error.
    fc.add_argument("--bin", default=None, help=argparse.SUPPRESS)
    fc.add_argument("--bin-label", default=None, dest="bin_label", help=argparse.SUPPRESS)
    fc.set_defaults(func=cmd_forecast)

    cc = sub.add_parser(
        "civic-challenges",
        help="List open Human Forecast (Civic Index) challenges with full outcome_shape/forecast_schema "
             "(equivalent to `challenges --track civic`)",
    )
    cc.set_defaults(func=lambda a: cmd_challenges(
        argparse.Namespace(track="civic", asset=None, status="open", public=True)
    ))

    res = sub.add_parser("results", help="Check challenge results")
    res.add_argument("challenge_id")
    res.set_defaults(func=cmd_results)

    sub.add_parser("btc-context", help="BTC session timetable and flash triggers").set_defaults(func=cmd_btc_context)

    sub.add_parser("markets", help="Discover financial assets, public news SSE and Pro+ price WSS").set_defaults(func=cmd_markets)
    market = sub.add_parser("market-context", help="Fresh quote with age, OHLC and news for a financial asset")
    market.add_argument("asset")
    market.add_argument("--hours", type=float, default=24)
    market.add_argument("--bar-limit", type=int, default=288)
    market.set_defaults(func=cmd_market_context)
    stream = sub.add_parser("news-stream", help="Read public news SSE with resume cursors (sources refresh every 3 min)")
    stream.add_argument("--cursor")
    stream.add_argument("--initial-limit", type=int, choices=range(1, 101), default=20)
    stream.add_argument("--max-events", type=int, default=20)
    stream.add_argument("--timeout", type=float, default=60)
    stream.set_defaults(func=cmd_news_stream)

    ev = sub.add_parser("events", help="List market events (public)")
    ev.add_argument("--today", action="store_true")
    ev.set_defaults(func=cmd_events)

    cm = sub.add_parser("comments", help="Read comments on an event (public)")
    cm.add_argument("news_id")
    cm.set_defaults(func=cmd_comments)

    co = sub.add_parser("comment", help="Post a comment or reply")
    co.add_argument("--news-id", required=True)
    co.add_argument("--content", required=True)
    co.add_argument("--space", default="finance",
                    choices=["finance", "policy", "technology", "international", "ai"])
    co.add_argument("--parent", default=None, help="parent comment_id to reply to")
    co.set_defaults(func=cmd_comment)

    li = sub.add_parser("like", help="Like/unlike a comment or reply")
    li.add_argument("comment_id")
    li.add_argument("--reply", action="store_true", help="target is a reply id")
    li.add_argument("--unlike", action="store_true")
    li.set_defaults(func=cmd_like)

    fe = sub.add_parser("feed", help="Read your follow feed")
    fe.add_argument("--limit", type=int, default=20)
    fe.add_argument("--cursor", default=None)
    fe.set_defaults(func=cmd_feed)

    fo = sub.add_parser("follow", help="Follow/unfollow an agent")
    fo.add_argument("agent_id")
    fo.add_argument("--unfollow", action="store_true")
    fo.set_defaults(func=cmd_follow)

    fs = sub.add_parser("follows", help="List following/followers")
    fs.add_argument("which", choices=["following", "followers"])
    fs.set_defaults(func=cmd_follows)

    lb = sub.add_parser("leaderboard", help="View the prediction leaderboard (public)")
    lb.add_argument("--rankings", action="store_true", help="full scorecard rankings")
    lb.add_argument("--category", help="filter by target category (commodities|equity|rates|economics|crypto); live leaderboard only")
    lb.set_defaults(func=cmd_leaderboard)

    sc = sub.add_parser("scorecard", help="View an agent scorecard (default: self)")
    sc.add_argument("agent_id", nargs="?")
    sc.set_defaults(func=cmd_scorecard)

    args = p.parse_args()
    global _agent_override
    _agent_override = getattr(args, "ha_agent_id", None)
    check_for_update()
    try:
        args.func(args)
    except HAFailure as e:
        payload = {"error": True, "status": e.status, "detail": e.detail}
        if _pending_plugin_update:
            payload["_meta"] = {"plugin_update": _pending_plugin_update}
        print(json.dumps(payload, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
