"""Tests for running one app through Tor (core/torify).

The end-to-end cases build a real network namespace (unprivileged user
namespaces, no root) and put a stand-in for Tor behind the Unix socket: a
small SOCKS5 server that records what it was asked for. That is enough to
prove the properties that matter — names reach "Tor" unresolved, nothing
else gets out — without a network or a real Tor. They are skipped where
user namespaces are not allowed.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.torify import dnsfake, launcher, paths, torproc  # noqa: E402
from core.torify.service import Session  # noqa: E402


def _query(name: str, qtype: int = 1, qid: int = 0x1234) -> bytes:
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\x00"
    return struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack("!HH", qtype, 1)


class DnsTestCase(unittest.TestCase):
    def test_a_query_gets_a_stable_fake_address(self):
        pool = dnsfake.FakeIPPool()
        r1 = dnsfake.answer(_query("example.com"), pool)
        r2 = dnsfake.answer(_query("EXAMPLE.com"), pool)
        ip1 = socket.inet_ntoa(r1[-4:])
        self.assertTrue(pool.owns(ip1))
        self.assertEqual(r1[-4:], r2[-4:], "same name, same address")
        self.assertEqual(pool.name_for(ip1), "example.com")
        self.assertEqual(struct.unpack("!H", r1[:2])[0], 0x1234)

    def test_onion_names_work_like_any_other(self):
        pool = dnsfake.FakeIPPool()
        name = "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
        ip = socket.inet_ntoa(dnsfake.answer(_query(name), pool)[-4:])
        self.assertEqual(pool.name_for(ip), name)

    def test_aaaa_and_others_are_empty_not_errors(self):
        pool = dnsfake.FakeIPPool()
        for qtype in (28, 65, 15, 16):
            r = dnsfake.answer(_query("example.com", qtype), pool)
            flags, _qd, an = struct.unpack("!HHH", r[2:8])
            self.assertEqual(flags & 0x000F, 0, "NOERROR")
            self.assertEqual(an, 0)

    def test_reverse_lookup_of_a_fake_address(self):
        pool = dnsfake.FakeIPPool()
        ip = pool.ip_for("example.org")
        rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
        r = dnsfake.answer(_query(rev, 12), pool)
        self.assertIn(b"example", r)

    def test_garbage_is_refused(self):
        pool = dnsfake.FakeIPPool()
        for bad in (b"", b"\x00" * 5, _query("x")[:-3],
                    struct.pack("!HHHHHH", 1, 0x8000, 1, 0, 0, 0) + b"\x00\x00\x01\x00\x01"):
            with self.assertRaises(dnsfake.DnsError):
                dnsfake.answer(bad, pool)

    def test_pool_wraps_instead_of_failing(self):
        import ipaddress
        pool = dnsfake.FakeIPPool(ipaddress.ip_network("10.192.0.0/30"))
        a, b = pool.ip_for("a"), pool.ip_for("b")
        c = pool.ip_for("c")
        self.assertIn(c, (a, b))
        self.assertEqual(pool.name_for(c), "c")


class CircuitTestCase(unittest.TestCase):
    STATUS = (
        '1 BUILT $' + "A" * 40 + '~g,$' + "B" * 40 + '~m,$' + "C" * 40 + '~x '
        'BUILD_FLAGS=NEED_CAPACITY PURPOSE=GENERAL TIME_CREATED=x\n'
        '2 BUILT $' + "A" * 40 + '~g,$' + "D" * 40 + '~m,$' + "E" * 40 + '~exit '
        'BUILD_FLAGS=NEED_CAPACITY PURPOSE=CONFLUX_LINKED '
        'SOCKS_USERNAME="es-abc-123" SOCKS_PASSWORD="x"\n'
        '3 EXTENDED $' + "F" * 40 + '~g PURPOSE=GENERAL SOCKS_USERNAME="es-other"\n'
    )

    def test_exit_of_the_apps_own_circuit_including_conflux(self):
        self.assertEqual(torproc.exit_for_username(self.STATUS, "es-abc-123"), "E" * 40)

    def test_unbuilt_or_foreign_circuits_are_not_ours(self):
        self.assertIsNone(torproc.exit_for_username(self.STATUS, "es-other"))
        self.assertIsNone(torproc.exit_for_username(self.STATUS, "es-nobody"))

    def test_relay_ip_and_bootstrap(self):
        ns = "r MixRelay AAAA BBBB 2026-10-31 12:00:00 45.9.156.238 9001 0\ns Fast Running"
        self.assertEqual(torproc.relay_ip(ns), "45.9.156.238")
        pct, summary = torproc.parse_bootstrap(
            'NOTICE BOOTSTRAP PROGRESS=85 TAG=ap_conn SUMMARY="Connecting to a relay"')
        self.assertEqual((pct, summary), (85, "Connecting to a relay"))

    def test_private_torrc(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            text = torproc.torrc(4242)
        self.assertIn("SocksPort unix:/run/user/1000/entropy-shield-torify/socks.sock", text)
        self.assertIn("__OwningControllerProcess 4242", text)
        self.assertNotIn("SocksPort 9050", text)


class LauncherTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="es-torify-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_desktop_file_parsing_drops_field_codes(self):
        d = self.tmp / "x.desktop"
        d.write_text("[Desktop Entry]\nName=Konversation\nName[tr]=Konv\n"
                     "Exec=konversation --opt \"a b\" %U\n[Desktop Action new]\nExec=other\n")
        name, argv = launcher.parse_desktop(str(d))
        self.assertEqual(name, "Konversation")
        self.assertEqual(argv, ["konversation", "--opt", "a b"])

    def test_browsers_get_a_fresh_profile(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.tmp)}):
            argv, prof = launcher.browser_profile(["/usr/bin/firefox", "https://x"], "s1")
            self.assertEqual(argv[1:4], ["--no-remote", "--profile", str(prof)])
            self.assertIn("media.peerconnection.enabled", (prof / "user.js").read_text())
            argv, prof2 = launcher.browser_profile(["chromium"], "s2")
            self.assertTrue(argv[1].startswith("--user-data-dir="))
            self.assertEqual(launcher.browser_profile(["konsole"], "s3"), (["konsole"], None))

    def test_new_identity_changes_the_socks_username(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.tmp)}):
            s = Session("Firefox", "/x", "private")
            first = s.token_path.read_text()
            s.renew()
            self.assertNotEqual(first, s.token_path.read_text())
            self.assertEqual(s.token_path.stat().st_mode & 0o777, 0o600)
            s.remove()
            self.assertFalse(s.token_path.exists())


# ── end to end, in a real namespace ──────────────────────────────────────────

def _userns_ok() -> bool:
    try:
        r = subprocess.run(["unshare", "--user", "--map-root-user", "--net", "true"],
                           capture_output=True, timeout=10)
        help_ = subprocess.run(["unshare", "--help"], capture_output=True, text=True).stdout
        return (r.returncode == 0 and "--map-user" in help_
                and shutil.which("nft") is not None and shutil.which("ip") is not None)
    except (OSError, subprocess.SubprocessError):
        return False


class _FakeTor(threading.Thread):
    """SOCKS5 on a Unix socket that records requests and answers HTTP."""

    def __init__(self, path: str):
        super().__init__(daemon=True)
        self.path = path
        self.requests: list[dict] = []
        self.ready = threading.Event()
        self.loop = asyncio.new_event_loop()

    async def _handle(self, r, w):
        try:
            _v, n = await r.readexactly(2)
            await r.readexactly(n)
            w.write(b"\x05\x02")
            ul = (await r.readexactly(2))[1]
            user = (await r.readexactly(ul)).decode()
            pl = (await r.readexactly(1))[0]
            await r.readexactly(pl)
            w.write(b"\x01\x00")
            _v, _c, _r, atyp = await r.readexactly(4)
            if atyp == 3:
                host = (await r.readexactly((await r.readexactly(1))[0])).decode()
            else:
                host = socket.inet_ntoa(await r.readexactly(4))
            (port,) = struct.unpack("!H", await r.readexactly(2))
            self.requests.append({"user": user, "host": host, "port": port})
            w.write(b"\x05\x00\x00\x01" + bytes(6))
            await r.read(4096)
            w.write(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(asyncio.start_unix_server(self._handle, self.path))
        self.ready.set()
        self.loop.run_forever()
        server.close()


@unittest.skipUnless(_userns_ok(), "unprivileged user namespaces or nft/ip not available")
class NamespaceTestCase(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self.tmp = Path(tempfile.mkdtemp(prefix="est-", dir=base))   # short: AF_UNIX limit
        self.tor = _FakeTor(str(self.tmp / "s.sock"))
        self.tor.start()
        self.tor.ready.wait(5)
        (self.tmp / "token").write_text("es-test-token")

    def tearDown(self):
        self.tor.loop.call_soon_threadsafe(self.tor.loop.stop)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_in_ns(self, script: str) -> tuple[int, str, list[dict]]:
        r_fd, w_fd = os.pipe()
        cfg = {"socks": str(self.tmp / "s.sock"), "token": str(self.tmp / "token"),
               "app_root": str(ROOT), "uid": os.getuid(), "gid": os.getgid(),
               "argv": ["bash", "-c", script], "env": {"TZ": "UTC"},
               "hide_system_bus": True, "status_fd": w_fd}
        (self.tmp / "cfg.json").write_text(json.dumps(cfg))
        proc = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--net", "--mount", "--pid", "--fork",
             "--mount-proc", "--kill-child", sys.executable, "-m", "core.torify.netns",
             str(self.tmp / "cfg.json")],
            pass_fds=(w_fd,), cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PYTHONPATH=str(ROOT)))
        os.close(w_fd)
        with os.fdopen(r_fd) as f:
            events = [json.loads(line) for line in f if line.strip()]
        return proc.returncode, proc.stdout, events

    def test_names_reach_tor_unresolved_and_nothing_else_gets_out(self):
        code, out, events = self.run_in_ns(
            "echo uid=$(id -u) tz=$TZ;"
            "curl -s -m 5 http://example.com/ && echo;"
            "curl -s -m 5 http://1.1.1.1/ >/dev/null && echo ip-via-tor;"
            "timeout 3 bash -c 'echo > /dev/tcp/[2606:4700::1111]/80' 2>/dev/null"
            "  && echo IPV6-LEAK || echo ipv6-blocked;"
            "getent hosts some.example | cut -d' ' -f1;"
            "ls /run/systemd/resolve 2>/dev/null | wc -l")
        self.assertEqual(code, 0, out)
        self.assertIn(f"uid={os.getuid()} tz=UTC", out)
        self.assertIn("ok", out)
        self.assertIn("ip-via-tor", out)
        self.assertIn("ipv6-blocked", out)
        self.assertIn("10.192.", out, "names resolve to fake addresses")
        self.assertTrue(out.strip().endswith("0"), "resolved's socket is hidden")
        hosts = [r["host"] for r in self.tor.requests]
        self.assertIn("example.com", hosts, "the NAME went to Tor, not an address")
        self.assertIn("1.1.1.1", hosts)
        self.assertTrue(all(r["user"] == "es-test-token" for r in self.tor.requests))
        self.assertEqual([e["event"] for e in events][:2], ["ready", "started"])

    def test_without_tor_the_app_has_no_network(self):
        os.unlink(self.tmp / "s.sock")      # Tor gone
        code, out, _ = self.run_in_ns(
            "curl -s -m 4 http://example.com/ && echo LEAK || echo cut-off;"
            "curl -s -m 4 http://1.1.1.1/ && echo LEAK || echo cut-off")
        self.assertNotIn("LEAK", out)
        self.assertEqual(out.split().count("cut-off"), 2)

    def test_background_children_keep_the_session_alive(self):
        # Longer than the hand-off window, so a run that lasted is told apart.
        code, out, events = self.run_in_ns("(sleep 5; echo child-done) & echo parent-exits")
        self.assertIn("child-done", out)
        exited = [e for e in events if e["event"] == "exited"][0]
        self.assertFalse(exited["handoff"], "a session that lasted is not a hand-off")


if __name__ == "__main__":
    unittest.main(verbosity=2)
