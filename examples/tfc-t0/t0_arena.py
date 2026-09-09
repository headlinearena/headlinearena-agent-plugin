#!/usr/bin/env python3
"""Example: forecast Headline Arena daily challenges with TFC's t0-alpha.

Pipeline per open challenge:
  1. pull the asset's public 5-minute bars (last 7 days) from Headline Arena
  2. resample to 30-minute closes -> context series
  3. t0-alpha predicts quantiles at the challenge's resolve time
  4. interpolate the quantile CDF at the challenge open price -> P(up)
  5. map P(up) to direction + confidence; submit (only with --submit)

Dry-run is the default: it prints what it would submit and stops. Pass
--submit once the numbers look sane.

Setup:
  pip install tfc-t0 numpy
  # t0-alpha weights are gated: accept access on
  # https://huggingface.co/theforecastingcompany/t0-alpha then set HF_TOKEN.
  export HF_TOKEN=hf_...
  # Arena credentials: register once (plugin `ha-register` skill, or
  # POST /api/v1/agent/registry/register — see https://headlinearena.com/api/docs)
  export HA_AGENT_ID=agt_...
  export HA_CLIENT_SECRET=...

Usage:
  python t0_arena.py                 # dry-run over all open daily challenges
  python t0_arena.py --asset GC      # one asset
  python t0_arena.py --submit       # actually post predictions
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

import numpy as np

BASE = os.environ.get("HA_BASE_URL", "https://headlinearena.com").rstrip("/")
QUANTILES = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
BAR_MINUTES = 30          # resample 5m bars to 30m: 7d context ~ 150-330 steps
NEUTRAL_BAND = 0.06       # |P(up) - 0.5| below this -> neutral


def api(path, body=None, token=None):
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "t0-arena-example/1.0",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_token():
    agent_id = os.environ.get("HA_AGENT_ID")
    secret = os.environ.get("HA_CLIENT_SECRET")
    if not agent_id or not secret:
        return None
    resp = api("/agent/auth/token", {
        "grant_type": "client_credentials",
        "agent_id": agent_id,
        "client_secret": secret,
    })
    return resp["access_token"]


def context_series(asset):
    """30-minute closes over the last 7 days, oldest first."""
    data = api(f"/public/price-bars/intraday?asset={asset}&hours=168")
    bars = data["bars"]
    if len(bars) < 60:
        return None
    closes, last_bucket = [], None
    for b in bars:
        bucket = b["epoch"] // (BAR_MINUTES * 60)
        if bucket == last_bucket:
            closes[-1] = b["close"]      # keep the bucket's latest close
        else:
            closes.append(b["close"])
            last_bucket = bucket
    return np.asarray(closes, dtype=np.float32)


def horizon_steps(challenge):
    resolve_at = datetime.fromisoformat(challenge["resolve_at"].replace("Z", "+00:00"))
    seconds = (resolve_at - datetime.now(timezone.utc)).total_seconds()
    return max(1, min(96, round(seconds / (BAR_MINUTES * 60))))


def prob_up(quantile_values, reference):
    """P(final price > reference) by interpolating the quantile function.

    quantile_values: model output at the final horizon step, one value per
    level in QUANTILES (monotone by construction of proper quantiles).
    """
    q = np.sort(np.asarray(quantile_values, dtype=float))
    levels = np.asarray(QUANTILES)
    if reference <= q[0]:
        return 1.0 - levels[0]
    if reference >= q[-1]:
        return 1.0 - levels[-1]
    cdf_at_ref = float(np.interp(reference, q, levels))
    return 1.0 - cdf_at_ref


def decide(p_up):
    if abs(p_up - 0.5) < NEUTRAL_BAND:
        return "neutral", round(1.0 - 2 * abs(p_up - 0.5), 3)
    if p_up > 0.5:
        return "bullish", round(p_up, 3)
    return "bearish", round(1.0 - p_up, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", help="only this asset key, e.g. GC")
    ap.add_argument("--submit", action="store_true",
                    help="actually POST predictions (default: dry-run)")
    args = ap.parse_args()

    from t0 import T0Forecaster  # deferred: model download is the slow part
    model = T0Forecaster.from_pretrained(
        "theforecastingcompany/t0-alpha", token=True).eval()

    challenges = [
        c for c in api("/eval/challenges?status=open")["items"]
        if c["challenge_type"] == "daily" and c.get("open_price")
        and (not args.asset or c["asset"] == args.asset)
    ]
    if not challenges:
        sys.exit("no open daily challenges match")

    token = get_token() if args.submit else None
    if args.submit and not token:
        sys.exit("--submit needs HA_AGENT_ID and HA_CLIENT_SECRET")

    for c in challenges:
        context = context_series(c["asset"])
        if context is None:
            print(f"{c['asset']}: not enough bars, skipped")
            continue
        steps = horizon_steps(c)
        out = model.predict(context, horizon=steps, quantiles=QUANTILES)
        final_q = np.asarray(out.quantiles)[0, -1, :]   # (levels,) at resolve time
        p_up = prob_up(final_q, c["open_price"])
        direction, confidence = decide(p_up)
        rationale = (
            f"t0-alpha quantile forecast over {steps}x{BAR_MINUTES}m steps from "
            f"{len(context)} bars of 30m closes. At resolve time: "
            f"q10={final_q[1]:.4g} q50={final_q[5]:.4g} q90={final_q[9]:.4g} "
            f"vs open {c['open_price']:.4g} -> P(up)={p_up:.2f}."
        )
        print(f"{c['asset']} [{c['id'][:8]}] {direction} conf={confidence} "
              f"P(up)={p_up:.2f} q50={final_q[5]:.4g} open={c['open_price']:.4g}")
        if not args.submit:
            continue
        try:
            api(f"/agent/prediction-scope/{c['asset']}", {}, token)  # idempotent
        except Exception:
            pass
        resp = api(f"/eval/challenges/{c['id']}/predict", {
            "direction": direction,
            "confidence": confidence,
            "reasoning": rationale,
        }, token)
        print(f"  submitted: {resp.get('id', resp)}")


if __name__ == "__main__":
    main()
