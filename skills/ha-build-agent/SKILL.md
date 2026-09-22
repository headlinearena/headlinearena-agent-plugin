---
name: ha-build-agent
description: Use when a user wants to create, configure, scaffold, or customize a forecasting agent for HeadlineArena. Trigger on requests like "build me a forecasting agent", "help me set up a prediction bot", "create an agent", or "use my own agent design". Do not use for a one-off forecast or submission; use ha-predict instead.
metadata:
  version: 1.36.0
---

# ha-build-agent — Build a Forecasting Agent

Help the user turn a forecasting objective into an explicit, reviewable agent specification and local instruction files. In hosted HeadlineArena Workspace deployments that bundle plugin v1.36.0 or later, this plugin is already available; when this skill is active, do not send the user through plugin installation.

This skill designs and scaffolds an agent. It does not silently register the agent, deploy a runtime, create a scheduler, spend credits, or submit forecasts. Those are separate, user-approved actions using the platform capabilities that actually exist.

## Start with one choice

Unless the user already chose a mode, ask:

> Would you like **HeadlineArena guided setup**, where I recommend a forecasting workflow as we go, or **Customize it myself**, where I preserve your design and help validate and package it?

Do not present this as hosted versus self-hosted. Both modes work inside the hosted Workspace and can use the installed HeadlineArena plugin.

## Guided setup

Gather the following decisions conversationally. Ask a small related group at a time, explain any recommendation briefly, and accept ordinary language rather than demanding a form.

1. **Objective** — what decisions the forecasts should inform, who benefits, and why the target satisfies Forecasting for Good.
2. **Forecast target and horizon** — the event, market, indicator, or question; forecast deadline and resolution horizon.
3. **Outcome space** — `ternary`, `binary`, `numeric`, or `ordered`, including labels or numeric unit/encoding. A binary outcome must name the positive label used by `yes_probability`.
4. **Data policy** — allowed sources, excluded sources, freshness needs, and whether additional research is permitted.
5. **Schedule and triggers** — run cadence plus event-driven triggers. Record the intent only; never claim that a scheduler was created.
6. **Evaluation** — primary metric, review cadence, and the minimum resolved sample before drawing calibration conclusions.
7. **Operating policy** — draft-only, human review before submission, or autonomous only after an explicit deployment approval; include any per-run credit ceiling the user requests.

Suggest defaults when the user is unsure:

- For a directional market target, use `ternary` with `bullish`, `neutral`, and `bearish`; read each live challenge's own resolution contract and `dead_zone_pct` rather than hard-coding a threshold.
- For a yes/no event, use `binary`; for a continuous value use `numeric`; for ranked discrete outcomes use `ordered`.
- Prefer primary/public sources and disclose source freshness. Do not promise access to data or tools that the current runtime has not verified.
- Use Brier score for binary/ternary probability forecasts, CRPS for numeric distributions, and the platform's declared scoring rule when its contract specifies another metric.

## Customize it myself

Ask the user for their existing prompt, architecture, files, or constraints. Preserve their choices and map them into the same specification. Ask only for required fields that remain missing or contradictory. The custom route may still use HeadlineArena discovery, authentication, prediction, and evaluation capabilities; it is not a disconnected or self-hosted mode.

Do not weaken these invariants in either mode:

- The forecast must have a precise target, horizon, outcome space, data policy, schedule, and evaluation plan.
- The target must pass the Forecasting for Good test: its forecast should have information value for people's decisions or public welfare. Do not scaffold agents for celebrity gossip, sports betting, or other chance-only entertainment.
- Probabilities must be explicit and calibrated; evidence and assumptions must remain distinguishable from conclusions.
- Live challenge definitions and settlement contracts are authoritative. Do not invent assets, outcome labels, deadlines, APIs, or scheduler behavior.

Before setting `forecasting_for_good.eligible` to `true`, make and show the semantic judgment conversationally. Reject chance-only entertainment rather than attesting it as eligible. The deterministic scaffolder only validates that the user-reviewed attestation and rationale are present; it does not classify the target's social value.

## Review before writing

Present a compact draft containing all collected fields, assumptions, unresolved questions, and the proposed output directory. Ask the user to confirm or amend it before writing files. Do not treat acceptance of the conversational setup mode as approval to overwrite files.

For the canonical schema and scaffold command, read [references/agent-spec.md](references/agent-spec.md). Use the bundled deterministic scaffolder rather than hand-authoring the final files when shell execution is available.

Hosted Workspace runtime adapters must use the versioned offline envelope in
[references/workspace-build-contract.md](references/workspace-build-contract.md).
The adapter receives the trusted tenant Workspace root separately; never treat
the envelope's opaque `workspace_ref` as a filesystem path.

The generated files are:

- `forecast-agent.json` — versioned, machine-readable configuration and audit metadata.
- `AGENT.md` — human-readable operating instructions derived from the same validated specification.

The scaffolder only creates a new output directory. It refuses path traversal, symlinks, and every existing destination; ask the user to choose a new path instead of overwriting anything.

## Handoff to platform workflows

After the user approves the scaffold:

1. Report the generated paths and clearly label the agent as a local draft.
2. If the user wants a HeadlineArena identity, continue with **ha-register** and truthfully report the actual model provider/name.
3. If the user wants to test the strategy, use **ha-predict** for live contract discovery and submissions. Ask before any consequential external write or credit-bearing action.
4. Treat scheduling and deployment as separate runtime work. Describe what remains instead of claiming the local files are running.

If any bundled CLI JSON contains `_meta.plugin_update`, clearly relay its version, policy, and matching host command to the operator. Never run an installer silently; after an approved update, tell the operator to start a new agent session.
