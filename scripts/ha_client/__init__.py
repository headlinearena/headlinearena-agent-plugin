"""HeadlineArena shared client internals — scripts/ha_client/.

Incrementally extracted from scripts/ha.py per Phase 2 of the WorkBuddy
baseline (docs/plans/workbuddy-connector-v3.md §27–28). ha.py stays the
stable entrypoint (`python3 scripts/ha.py ...`) and re-binds what it
imports to the historical names, so every caller, test, and host adapter
(including the Hermes plugin's `except ha.HAFailure`) keeps working.

Layout target — transport/auth land in later steps:

  errors.py      HAFailure / fail / note
  prediction.py  prediction validation + payload construction
"""
