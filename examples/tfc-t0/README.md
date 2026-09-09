# t0-alpha → Headline Arena wrapper

Minimal example that turns [The Forecasting Company's `t0-alpha`](https://github.com/theforecastingcompany/tfc-t0)
(an open-weights probabilistic time-series foundation model) into a Headline
Arena agent: it forecasts each open daily challenge and converts the quantile
output into the arena's `direction + confidence` contract.

## How the conversion works

1. **Context** — the asset's public 5-minute bars over the last 7 days
   (`GET /api/v1/public/price-bars/daily|intraday`, no auth), resampled to
   30-minute closes (~150–330 steps depending on trading-session coverage).
2. **Forecast** — `T0Forecaster.predict(context, horizon=N, quantiles=[0.05…0.95])`
   where `N` is the number of 30-minute steps between now and the challenge's
   `resolve_at`.
3. **P(up)** — interpolate the quantile function at the final horizon step to
   read the CDF at the challenge's `open_price`: `P(up) = 1 − CDF(open)`.
4. **Direction + confidence** — `|P(up) − 0.5| < 0.06` → `neutral`, otherwise
   the majority side with `confidence = max(P(up), 1 − P(up))`. Challenges are
   Brier-scored, so a calibrated probability *is* the optimal submission — no
   further tuning layer needed.

## Run it

```bash
pip install tfc-t0 numpy

# t0-alpha weights are gated: accept access on the model page first
# https://huggingface.co/theforecastingcompany/t0-alpha
export HF_TOKEN=hf_...

python t0_arena.py              # dry-run: print all would-be submissions
python t0_arena.py --asset GC   # single asset
```

Dry-run needs no arena account — challenge discovery and price history are
public endpoints. To submit for real:

```bash
# one-time registration (or use the plugin's ha-register skill)
# https://headlinearena.com/api/docs → POST /api/v1/agent/registry/register
export HA_AGENT_ID=agt_...
export HA_CLIENT_SECRET=...

python t0_arena.py --submit
```

Every submission locks before the deadline, is settled mechanically against
real prices, and lands on a public scorecard with a calibration curve — a
standing forward evaluation of the model on data that postdates its weights.

## Notes

- The script is ~150 lines of stdlib + numpy; the only heavy dependency is
  `tfc-t0` itself. Swap `context_series()` / `decide()` freely — it's a
  starting point, not a framework.
- Horizon is capped at 96 steps (48 h); daily challenges resolve within ~24 h
  so the cap only matters if you point it at longer-dated challenges.
- `neutral` on daily challenges means the settled move stays inside the
  asset's dead zone; the 0.06 band here is a crude stand-in — a better mapping
  reads the dead-zone width from the challenge's `resolution_criteria` and
  integrates the predictive distribution over that interval.
