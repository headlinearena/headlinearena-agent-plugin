"""Shared failure/note primitives (Phase 2, Step 1).

HAFailure / fail / note moved verbatim from scripts/ha.py. ha.py imports
them back under the same names, so `ha.fail`, `ha.note`, and `ha.HAFailure`
(including the Hermes adapter's `except ha.HAFailure` and every
`mock.patch.object(ha, "note")` in the test suite) still refer to the exact
same objects they always did.
"""

import sys


class HAFailure(Exception):
    """Raised by fail() instead of exiting the process directly, so library
    consumers (e.g. the Hermes plugin adapter) can catch it instead of losing
    their whole host process to sys.exit. The CLI entry point (main()) is the
    only place that still turns this into the historical print+exit(1)."""

    def __init__(self, detail, status=None):
        super().__init__(str(detail))
        self.detail = detail
        self.status = status


def fail(detail, status=None):
    raise HAFailure(detail, status)


def note(msg):
    # flush=True matters here: when ha.py runs through a subprocess/tool pipe
    # (the normal way a coding agent invokes it) rather than an interactive
    # tty, Python block-buffers stderr — without an explicit flush, --wait's
    # per-poll "still waiting" messages would all sit in the buffer and only
    # appear at once when the process exits, making the live polling status
    # invisible to whoever is watching in real time.
    print(f"→ {msg}", file=sys.stderr, flush=True)
