# Forecast Agent Specification

Read this reference only after the user has chosen guided or custom setup and you are preparing the draft or generated files.

## Input contract

Create a UTF-8 JSON file with this shape. `schema_version` is always the integer `1`.

```json
{
  "schema_version": 1,
  "name": "gold-macro-monitor",
  "setup_mode": "guided",
  "objective": {
    "statement": "Forecast short-horizon gold direction around macro releases",
    "beneficiaries": "People monitoring inflation and purchasing-power risk",
    "decision_use": "Provide a calibrated signal, not trading instructions"
  },
  "forecasting_for_good": {
    "eligible": true,
    "rationale": "Gold is a macro signal connected to inflation and household purchasing power"
  },
  "targets": ["GC"],
  "horizon": "24 hours from each live challenge's reference time",
  "outcome": {
    "type": "ternary",
    "labels": ["bullish", "neutral", "bearish"]
  },
  "data": {
    "allowed_sources": ["HeadlineArena live challenge contract", "primary public releases"],
    "excluded_sources": ["unattributed social posts"],
    "freshness": "Recheck before each forecast"
  },
  "schedule": {
    "cadence": "Check every hour while a matching challenge is open",
    "triggers": ["new official macro release", "material price move"]
  },
  "evaluation": {
    "primary_metric": "brier_score",
    "review_cadence": "monthly",
    "minimum_resolved_forecasts": 30
  },
  "operation": {
    "submission_policy": "human_review",
    "max_credits_per_run": 0
  },
  "extensions": {}
}
```

Required top-level fields are `schema_version`, `name`, `setup_mode`, `objective`, `forecasting_for_good`, `targets`, `horizon`, `outcome`, `data`, `schedule`, and `evaluation`. `operation` and `extensions` are optional. Unknown top-level fields are rejected; custom/provider-specific settings belong under `extensions`.

### Outcome variants

- `ternary`: `labels` must contain exactly three unique labels.
- `binary`: `labels` must contain exactly two unique labels.
- `ordered`: `labels` must contain at least two unique labels in settlement order.
- `numeric`: provide a non-empty `unit` and `encoding` of `normal` or `samples`; do not provide labels.

### Operating policy

`submission_policy` accepts:

- `draft_only` — produce forecasts without submission.
- `human_review` — require user review before every submission.
- `autonomous_after_deploy` — allow a separately deployed runtime to submit under its approved limits.

This field records intent. The scaffold does not create a deployment or scheduler. `max_credits_per_run` must be zero or a positive number; zero means the agent should not spend credits.

## Generate files

In Claude Code, the plugin root is `$CLAUDE_PLUGIN_ROOT`. On other hosts, locate the installed plugin directory containing `scripts/scaffold_forecast_agent.py`.

Write the approved input to a temporary file outside the intended output directory, then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold_forecast_agent.py" \
  --spec /path/to/approved-agent-spec.json \
  --workspace /path/to/current-workspace \
  --output forecast-agents/gold-macro-monitor
```

`--output` must be a relative path inside `--workspace`; absolute paths, `..` traversal, symlink escapes, and the workspace root itself are rejected. The destination must not already exist. If the user explicitly approved replacing prior generated files, add `--force`; this overwrites only `forecast-agent.json` and `AGENT.md` and preserves unrelated files.

The command prints a JSON summary with the output directory and generated paths. It performs no network calls.

## After generation

Review both files with the user. Registration, deployment, scheduling, credit use, and prediction submission remain separate actions. Route those requests to `ha-register`, `ha-wallet`, and `ha-predict` as appropriate; do not infer a backend endpoint from fields in this local schema.
