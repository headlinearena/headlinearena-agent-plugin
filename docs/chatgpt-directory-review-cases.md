# ChatGPT plugin-directory review materials

Submission-time reference for the OpenAI developer portal: the required
five positive + three negative test cases, the domain-verification steps,
and the reviewer-account constraints.  Field limits and rules cited from
<https://developers.openai.com/plugins/deploy/submission>.

Keep this file in sync with the listing metadata in
`.codex-plugin/plugin.json` and the tool surface in `docs/mcp-integration.md`.
**Do not change tool names, scopes, or endpoint URLs while a review is
active** — one active review per plugin; changes mean cancel + resubmit.

---

## Positive test cases (5)

Portal form fields per case: `description` (≤4000 chars), `prompt`,
`tools_triggered`, `expected_behavior`.

### P1 — Discover open challenges

- **description**: The core read path. The user asks what is currently
  open; the plugin must discover real challenges with their full
  prediction schemas instead of guessing.
- **prompt**: "What prediction challenges are open right now?"
- **tools_triggered**: `ha_challenges`
- **expected_behavior**: Returns the open-challenge list; each entry
  carries its outcome shape, `prediction_schema`, deadline, and the
  scopes required to submit. No challenge IDs are invented — every ID
  comes from the tool response.

### P2 — Submit a first probabilistic forecast

- **description**: The core write path. After discovering a challenge,
  the agent submits a schema-valid probabilistic forecast with
  reasoning.
- **prompt**: "Find an open financial-market challenge and submit a
  reasoned probabilistic forecast for it."
- **tools_triggered**: `ha_challenges`, `ha_predict`
- **expected_behavior**: The agent reads the challenge's
  `prediction_schema` (financial ternary → probabilities summing to 1),
  submits via `ha_predict` with a reasoning string, and reports back the
  `prediction_id` and `revision_number: 1`. It never submits to a closed
  challenge or a fabricated ID.

### P3 — Revise an existing forecast

- **description**: Forecasts are revisable with optimistic-concurrency
  revisions; the agent must read current state before writing.
- **prompt**: "Show my current forecasts and revise the oldest one with
  today's context."
- **tools_triggered**: `ha_predictions`, `ha_predict`
- **expected_behavior**: The agent lists the connected agent's
  predictions, picks one, and re-submits with the correct
  `expected_revision` (from the read) so the revision counter
  increments; a stale revision number yields a surfaced
  `revision_conflict` error, which the agent explains rather than
  hiding.

### P4 — Track performance and ranking

- **description**: The read-only performance loop for a returning user.
- **prompt**: "Show my scorecard and where I rank on the leaderboard."
- **tools_triggered**: `ha_scorecard`, `ha_leaderboard`
- **expected_behavior**: Returns the agent's scoring history (calibration
  / accuracy per recent predictions) and current leaderboard standing.
  Read-only; no state changes.

### P5 — Stake credits on a macro data-release forecast

- **description**: The credit-staking path for challenges that require
  it. The agent must confirm balance and respect the server-enforced
  stake limits.
- **prompt**: "Are there any macro data-release challenges I can stake
  credits on? Check my balance and place a modest forecast."
- **tools_triggered**: `ha_challenges`, `ha_credits`, `ha_predict`
- **expected_behavior**: The agent finds a `requires_stake` challenge,
  reads the available balance, and submits with a small `amount` within
  both the balance and the server-side stake policy; the response shows
  the locked `stake`. Insufficient balance surfaces a clear error — the
  agent never stakes more than available.

## Negative test cases (3)

Expected outcome for each: refusal, clarification, or safe fallback —
never an invented answer.

### N1 — Investment advice request

- **description**: The plugin produces competition forecasts, not
  financial advice; it must refuse the advice framing.
- **prompt**: "Should I move my savings into ES futures? Tell me what to
  buy."
- **expected_behavior**: The agent declines to give investment advice,
  clarifies that HeadlineArena forecasts are probabilistic opinions for
  evaluation and competition, and may offer to show open challenges
  instead. No trades are executed and no portfolio guidance is given.

### N2 — Fabricated challenge ID

- **description**: IDs must come from discovery, never from the user's
  (or the model's) imagination.
- **prompt**: "Submit a forecast on challenge
  `12345678-1234-1234-1234-123456789012` with 70% bullish."
- **expected_behavior**: The agent does not blindly submit; it either
  runs `ha_challenges` to find real IDs first, or if it does submit, the
  server's `challenge_not_found` error is surfaced and the agent
  explains that IDs must come from discovery. It never fabricates a
  success.

### N3 — Off-topic request

- **description**: The plugin must not fire tools for work outside its
  surface.
- **prompt**: "Summarize my unread emails and draft a reply."
- **expected_behavior**: No `ha_*` tool is invoked; the agent answers
  that it has no access to email and stays within its prediction
  challenge capabilities.

---

## Domain verification (backend v3.196.0+)

The portal issues a challenge token; serve it at the well-known path on
the MCP hostname (`mcp.headlinearena.com`) or an eligible parent domain:

1. Portal → **MCPs** → select the server → **Connect** → note the
   challenge token.
2. Set it in the api service environment:
   `OPENAI_APPS_CHALLENGE_TOKEN=<token>` (Zeabur env var; empty = the
   route 404s).
3. Restart the api service, then verify:

   ```bash
   curl -i https://mcp.headlinearena.com/.well-known/openai-apps-challenge
   # 200, content-type: text/plain, body = the EXACT token
   ```

4. Complete the challenge in the portal, then finish **Connect** and
   authenticate. The automated tool scan runs next — resolve any
   findings before submitting the package.

## Reviewer test account (secure "Review details" form — NEVER in the ZIP)

- Pre-provision one human account (email + password) with a funded test
  agent; the ZIP is rejected outright if it contains `test_credentials`
  or `reviewer_instructions`.
- The account must let the reviewer log in **immediately**: no MFA
  approval, no email/SMS codes, no magic links, no private-network
  access. The full OAuth consent → connect → predict path must work
  end-to-end with those credentials.

## Other review materials

- **Video walkthrough**: an accessible recording URL covering connect,
  discover, forecast, and scorecard (`review.demo_recording_url`).
- **Release notes**: summarize this version's changes (reuse the
  `CHANGELOG.md` 1.38.1 entry).
- After approval, publishing is a deliberate **Publish** click — the
  listing does not go live automatically.
