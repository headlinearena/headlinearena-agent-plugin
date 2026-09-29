"""HeadlineArena shared client internals — scripts/ha_client/.

Incrementally extracted from scripts/ha.py per Phase 2 of the WorkBuddy
baseline (docs/plans/workbuddy-connector-v3.md §27–28). ha.py stays the
stable entrypoint (`python3 scripts/ha.py ...`) and re-binds what it
imports to the historical names, so every caller, test, and host adapter
(including the Hermes plugin's `except ha.HAFailure`) keeps working.

Layout:

  errors.py      HAFailure / fail / note
  prediction.py  prediction validation + payload construction
  contracts.py   prediction-contract-v2 parsing + Civic/legacy projections
  transport.py   HTTP primitives (urllib core, headers, origin, expect)
  auth.py        token freshness / request-response helpers, claim messages
  legacy.py      deprecated-macro compat routing (predicates, messages,
                 CN-endpoint guard, 404-fallback body)

The orchestrators (http/authed/get_token, the credential store) deliberately
stay in ha.py: tests and the Hermes adapter intercept them by patching ha's
names, which only works while they resolve collaborators through ha's module
globals at call time. ha_client modules must stay leaf-only — never call back
into ha or each other's stateful layers.
"""
