"""One user's settings must not reach other users (audit ES1, ES2)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import daemon, firewall  # noqa: E402


class _Cfg:
    def __init__(self, rules):
        self._rules = rules

    def get(self, key):
        assert key == "per_app_routing"
        return {"enabled": True, "rules": self._rules}


RULES = [{"uid_or_user": "0", "action": "direct"},       # root
         {"uid_or_user": "1000", "action": "direct"},    # the user themself
         {"uid_or_user": "1001", "action": "direct"},    # someone else (Ghost?)
         {"uid_or_user": "1002", "action": "block"}]


class PerAppRoutingTests(unittest.TestCase):
    def _rules_as(self, sudo_uid):
        env = {k: v for k, v in os.environ.items() if k not in ("SUDO_UID", "PKEXEC_UID")}
        if sudo_uid is not None:
            env["SUDO_UID"] = sudo_uid
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("core.config.cfg", lambda: _Cfg(RULES)), \
             mock.patch.object(firewall, "_real_user",
                               lambda: (int(sudo_uid), None) if sudo_uid else None):
            return firewall._per_app_rules()

    def test_a_user_only_decides_for_themself(self):
        """ES1: exempting root or another account from the kill switch."""
        self.assertEqual(self._rules_as("1000"), (["1000"], []))

    def test_root_config_may_name_anyone(self):
        self.assertEqual(self._rules_as(None), (["0", "1000", "1001"], ["1002"]))


class SessionIdentityTests(unittest.TestCase):
    def test_identity_is_bound_inside_and_cleared_after(self):
        """ES2: the per-user context exists only while the session runs."""
        calls = []

        class Cfg:
            @staticmethod
            def use_user_config(uid):
                calls.append(uid)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUDO_UID", None)
            with daemon._session_identity(Cfg, 1000):
                self.assertEqual(os.environ.get("SUDO_UID"), "1000")
            self.assertNotIn("SUDO_UID", os.environ)
        self.assertEqual(calls, [1000, 0])

    def test_cleared_even_when_the_session_fails(self):
        class Cfg:
            @staticmethod
            def use_user_config(uid):
                pass
        with self.assertRaises(RuntimeError):
            with daemon._session_identity(Cfg, 1000):
                raise RuntimeError("boom")
        self.assertNotIn("SUDO_UID", os.environ)

    def test_bound_only_after_the_lock(self):
        src = (Path(__file__).resolve().parents[1] / "core" / "daemon.py").read_text()
        handler = src[src.index("def _handle_client"):src.index("def _force_remove_firewall")]
        self.assertIn("with _session_lock, _session_identity(", handler)
        self.assertNotIn('os.environ["SUDO_UID"]', handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)
