#!/usr/bin/env python3
"""Regression test for a Windows-only crash: save_store()/load_store() used
Path.write_text()/read_text() with no explicit encoding, which falls back to
locale.getpreferredencoding() — cp1252 on most Windows installs. The moment
credentials.json holds a non-Latin-1 character (e.g. a Chinese
challenge_prompt containing "月", 月) that raised UnicodeEncodeError.

Real-world impact: the crash happened in save_store(), called AFTER
cmd_register already got a 200 back from the API (registration succeeded
server-side, agent_id + client_secret + challenge_prompt all in `resp`). So
the agent existed on the backend with no local credentials.json entry, and
the client_secret needed to authenticate as it was never persisted anywhere
the CLI could recover it from.

Stdlib-only (unittest + unittest.mock). Run: python3 scripts/test_ha_windows_encoding.py
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


def _resolve_like_windows_locale(encoding):
    """Stand-in for io.text_encoding(): when a caller passes no explicit
    encoding (None), real Python resolves it via locale.getpreferredencoding()
    — cp1252 on most Windows installs. When a caller DOES pass one (e.g.
    "utf-8"), it's returned unchanged. This lets a test force the "no
    explicit encoding given" path to behave like Windows without needing an
    actual cp1252-locale environment (which this container isn't)."""
    return "cp1252" if encoding is None else encoding


class WindowsEncodingTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._patch = mock.patch.object(ha, "CRED_FILE", Path(self._tmpdir.name) / "credentials.json")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_save_and_load_round_trips_non_latin1_text_without_locale_dependence(self):
        """Simulates the Windows failure mode directly: Path.write_text()/
        read_text() resolve a missing encoding= via io.text_encoding(), which
        this test forces to behave like a cp1252-locale Windows box. Only
        save_store/load_store passing an explicit encoding="utf-8" survives —
        exactly the contract this fix must uphold."""
        challenge_prompt = "本月CPI预测挑战"  # contains 月 ("月") — the reported crash trigger
        store = {
            "https://headlinearena.com": {
                "_default_agent": "agt_test",
                "_agents": {"agt_test": {
                    "agent_id": "agt_test",
                    "client_secret": "shh",
                    "challenge": {"challenge_prompt": challenge_prompt},
                }},
            }
        }
        with mock.patch("io.text_encoding", side_effect=_resolve_like_windows_locale):
            ha.save_store(store)  # must not raise UnicodeEncodeError
            loaded = ha.load_store()
        self.assertEqual(
            loaded["https://headlinearena.com"]["_agents"]["agt_test"]["challenge"]["challenge_prompt"],
            challenge_prompt,
        )

    def test_save_store_chmod_failure_is_non_fatal(self):
        """chmod's POSIX bits are a best-effort no-op on Windows — must not
        crash the CLI if the platform raises on it."""
        with mock.patch.object(Path, "chmod", side_effect=NotImplementedError):
            ha.save_store({"https://x": {"_default_agent": None, "_agents": {}}})  # must not raise


if __name__ == "__main__":
    unittest.main()
