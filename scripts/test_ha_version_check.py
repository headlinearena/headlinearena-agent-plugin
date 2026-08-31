#!/usr/bin/env python3
"""Unit tests for ha.py's update-check nudge (scripts/ha.py: check_for_update).

Stdlib-only (unittest + unittest.mock), consistent with the plugin's
zero-dependency philosophy. Run: python3 scripts/test_ha_version_check.py
"""
import contextlib
import copy
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDisk:
    """Mimics ha.py's file-backed store: load_store()/save_store() must
    round-trip through something that isn't the same mutable object, or a
    setdefault-then-save sequence silently loses data (a real footgun this
    test exists to catch)."""

    def __init__(self):
        self._data = {}

    def load(self):
        return copy.deepcopy(self._data)

    def save(self, store):
        self._data = copy.deepcopy(store)


class CheckForUpdateTests(unittest.TestCase):
    def setUp(self):
        self.disk = FakeDisk()
        ha._pending_plugin_update = None
        self._patches = [
            mock.patch.object(ha, "load_store", self.disk.load),
            mock.patch.object(ha, "save_store", self.disk.save),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        os.environ.pop("HA_NO_UPDATE_CHECK", None)

    def _run_capturing_stderr(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ha.check_for_update()
        return buf.getvalue()

    def test_nudges_when_remote_is_newer(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": "9.9.9"}})):
            output = self._run_capturing_stderr()
        self.assertIn("9.9.9", output)
        self.assertIn(ha.CLI_VERSION, output)
        self.assertIn(ha.CHANGELOG_URL, output)

    def test_records_check_timestamp(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": "9.9.9"}})):
            self._run_capturing_stderr()
        self.assertGreater(self.disk.load()["_meta"]["last_version_check"], 0)

    def test_skips_network_within_cache_window(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": "9.9.9"}})):
            self._run_capturing_stderr()
        with mock.patch("urllib.request.urlopen") as mocked:
            ha.check_for_update()
            mocked.assert_not_called()

    def test_no_nudge_when_already_current(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": ha.CLI_VERSION}})):
            output = self._run_capturing_stderr()
        self.assertEqual(output, "")

    def test_disabled_via_env_var_skips_network_entirely(self):
        os.environ["HA_NO_UPDATE_CHECK"] = "1"
        try:
            with mock.patch("urllib.request.urlopen") as mocked:
                ha.check_for_update()
                mocked.assert_not_called()
        finally:
            del os.environ["HA_NO_UPDATE_CHECK"]

    def test_network_failure_is_silent_and_never_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            output = self._run_capturing_stderr()  # must not raise
        self.assertEqual(output, "")
        self.assertNotIn("last_version_check", self.disk.load().get("_meta", {}))

    def test_malformed_response_is_silent_and_never_raises(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse({"unexpected": "shape"})):
            output = self._run_capturing_stderr()  # .get() chain -> None, no crash
        self.assertEqual(output, "")

    def test_cli_json_contains_visible_structured_update_metadata(self):
        payload = {
            "latest_version": "9.9.9",
            "minimum_supported_version": "8.0.0",
            "policy": "required",
            "update_available": True,
            "action_required": True,
            "release_notes_url": "https://example.test/changes",
            "reinstall_commands": {"codex": "codex plugin marketplace upgrade headlinearena"},
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                ha.check_for_update()
                ha.out({"ok": True})
        rendered = json.loads(stdout.getvalue())
        update = rendered["_meta"]["plugin_update"]
        self.assertTrue(update["action_required"])
        self.assertEqual(update["current_version"], ha.CLI_VERSION)
        self.assertIn("marketplace upgrade", update["reinstall_commands"]["codex"])
        self.assertIn("required", stderr.getvalue())

    def test_codex_reinstall_command_refreshes_then_reinstalls(self):
        command = ha._REINSTALL_COMMANDS["codex"]
        self.assertIn("plugin marketplace upgrade headlinearena", command)
        self.assertIn("plugin add headlinearena-agent-plugin@headlinearena", command)


class UpdateNoticeTests(unittest.TestCase):
    """_update_notice() is the shared primitive both check_for_update() (CLI,
    note()/stderr) and ha_tools.py's _run() (Hermes, JSON field) call — it
    must return the message as a plain string rather than only printing it,
    since Hermes never sees stderr."""

    def setUp(self):
        self.disk = FakeDisk()
        ha._pending_plugin_update = None
        self._patches = [
            mock.patch.object(ha, "load_store", self.disk.load),
            mock.patch.object(ha, "save_store", self.disk.save),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        os.environ.pop("HA_NO_UPDATE_CHECK", None)

    def test_returns_message_string_when_newer(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": "9.9.9"}})):
            msg = ha._update_notice()
        self.assertIsInstance(msg, str)
        self.assertIn("9.9.9", msg)

    def test_returns_none_when_current(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": ha.CLI_VERSION}})):
            self.assertIsNone(ha._update_notice())

    def test_shares_throttle_with_check_for_update(self):
        """A check_for_update() call (as __init__.py's register() used to
        fire) must not starve a subsequent _update_notice() caller (ha_tools's
        _run()) of its own network attempt for the rest of the day — they
        share the same _meta.last_version_check gate by design, so whichever
        one runs first legitimately consumes the window for both."""
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResponse({"metadata": {"version": "9.9.9"}})):
            ha.check_for_update()
        with mock.patch("urllib.request.urlopen") as mocked:
            self.assertIsNone(ha._update_notice())
            mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
