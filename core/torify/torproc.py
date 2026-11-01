"""The private Tor that apps use while Entropy Shield is not connected.

Entropy Shield's own Tor only exists for the length of a system-wide session.
"Run this one app through Tor" has to work without one, so the per-user
service starts a Tor of its own: as the user, no root, with its SocksPort
and ControlPort on Unix sockets in the user's runtime directory (reachable
from inside an app's namespace by path, unreachable from other accounts).

It takes the user's bridge and exit-node settings from Entropy Shield, so a
network that blocks Tor is handled the same way here, and it remembers its
guards between runs in ~/.local/share like any long-lived Tor client should.
`__OwningControllerProcess` ties its life to the service: if the service dies,
Tor exits on its own instead of lingering.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from core.torify import paths

_FP_RE = re.compile(r"\$([0-9A-Fa-f]{40})")
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class TorError(RuntimeError):
    pass


# ── control port ─────────────────────────────────────────────────────────────

class Control:
    """A minimal Tor control-port client over a Unix socket (cookie auth)."""

    def __init__(self, sock_path: str, cookie_path: str, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(sock_path)
        self._buf = b""
        with open(cookie_path, "rb") as f:
            cookie = f.read().hex()
        code, lines = self.command(f"AUTHENTICATE {cookie}")
        if code != "250":
            raise TorError("control port authentication failed")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "Control":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _line(self) -> str:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise TorError("control connection closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line.decode("utf-8", "replace")

    def command(self, cmd: str) -> tuple[str, list[str]]:
        """Send one command; return (status code, reply lines without codes)."""
        if "\r" in cmd or "\n" in cmd:
            raise ValueError("control command must be one line")
        self._sock.sendall(cmd.encode() + b"\r\n")
        out: list[str] = []
        while True:
            line = self._line()
            code, sep, rest = line[:3], line[3:4], line[4:]
            if sep == "+":                       # data block until "."
                out.append(rest)
                while True:
                    data = self._line()
                    if data == ".":
                        break
                    out.append(data[1:] if data.startswith("..") else data)
            else:
                out.append(rest)
            if sep == " ":
                return code, out

    def getinfo(self, key: str) -> str:
        code, lines = self.command(f"GETINFO {key}")
        if code != "250":
            return ""
        text = "\n".join(lines)
        prefix = f"{key}="
        text = text[len(prefix):] if text.startswith(prefix) else text
        # Drop the trailing "OK".
        return text.rsplit("\nOK", 1)[0].strip()


def parse_bootstrap(info: str) -> tuple[int, str]:
    """(percent, summary) from GETINFO status/bootstrap-phase."""
    pct = re.search(r"PROGRESS=(\d+)", info)
    summary = re.search(r'SUMMARY="([^"]*)"', info)
    return (int(pct.group(1)) if pct else 0, summary.group(1) if summary else "")


def exit_for_username(circuit_status: str, username: str) -> str | None:
    """The exit relay fingerprint of a BUILT circuit carrying `username`'s
    streams (Tor reports SOCKS_USERNAME on isolated circuits)."""
    needle = f'SOCKS_USERNAME="{username}"'
    for line in circuit_status.splitlines():
        # Tor 0.4.8+ carries user streams on Conflux legs (CONFLUX_LINKED);
        # both legs of a set end at the same exit.
        if (" BUILT " in f" {line} " and needle in line
                and ("PURPOSE=GENERAL" in line or "PURPOSE=CONFLUX_LINKED" in line)):
            parts = line.split()
            if len(parts) >= 3:
                fps = _FP_RE.findall(parts[2])
                if fps:
                    return fps[-1]
    return None


def relay_ip(ns_info: str) -> str | None:
    for line in ns_info.splitlines():
        if line.startswith("r "):
            for tok in line.split()[2:]:
                if _IPV4_RE.match(tok):
                    return tok
    return None


def exit_info(ctrl: Control, username: str) -> dict:
    """{"country": "DE", "ip": "...", "nickname": "..."} or {} if unknown."""
    fp = exit_for_username(ctrl.getinfo("circuit-status"), username)
    if not fp:
        return {}
    ns = ctrl.getinfo(f"ns/id/{fp}")
    ip = relay_ip(ns)
    nick = ns.split()[1] if ns.startswith("r ") and len(ns.split()) > 1 else ""
    cc = ctrl.getinfo(f"ip-to-country/{ip}").upper() if ip else ""
    if len(cc) != 2 or not cc.isalpha() or cc == "??":
        cc = ""
    return {"country": cc, "ip": ip or "", "nickname": nick}


# ── the process ──────────────────────────────────────────────────────────────

def torrc(owner_pid: int) -> str:
    run = paths.run_dir()
    lines = [
        "# Entropy Shield — private Tor for apps run through Tor. Generated.",
        f"DataDirectory {paths.tor_data_dir()}",
        f"SocksPort unix:{run / 'socks.sock'} IsolateSOCKSAuth",
        f"ControlPort unix:{run / 'control.sock'}",
        "CookieAuthentication 1",
        f"CookieAuthFile {run / 'control.cookie'}",
        f"__OwningControllerProcess {owner_pid}",
        "AvoidDiskWrites 1",
        "Log notice stderr",
        "ClientOnly 1",
    ]
    lines += _user_tor_settings()
    return "\n".join(lines) + "\n"


def _user_tor_settings() -> list[str]:
    """Bridges and exit nodes from the user's Entropy Shield settings.

    Reuses TorManager's builder, which already confines every bridge line to
    one `Bridge` directive. Any failure means "no extras", never "no Tor".
    """
    out: list[str] = []
    try:
        from core.config import cfg
        from core.tor import TorManager, _torrc_safe
        section = TorManager(lambda _m: None)._build_bridge_section()
        if section:
            out += section.splitlines()
        exits = _torrc_safe(str(cfg().get("tor", "exit_nodes") or "")).strip()
        if exits:
            out.append(f"ExitNodes {exits}")
            if cfg().get("tor", "strict_nodes"):
                out.append("StrictNodes 1")
    except Exception:  # noqa: BLE001
        pass
    return out


class PrivateTor:
    def __init__(self, owner_pid: int | None = None):
        self.owner_pid = owner_pid or os.getpid()
        self.proc: subprocess.Popen | None = None
        self.log_tail: list[str] = []

    @property
    def socks_path(self) -> str:
        return str(paths.run_dir() / "socks.sock")

    @property
    def control_path(self) -> str:
        return str(paths.run_dir() / "control.sock")

    @property
    def cookie_path(self) -> str:
        return str(paths.run_dir() / "control.cookie")

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        tor = shutil.which("tor")
        if not tor:
            raise TorError("tor is not installed")
        run = paths.run_dir(create=True)
        data = paths.tor_data_dir(create=True)
        for stale in ("socks.sock", "control.sock", "control.cookie"):
            try:
                (run / stale).unlink()
            except FileNotFoundError:
                pass
        conf = run / "torrc"
        conf.write_text(torrc(self.owner_pid))
        os.chmod(conf, 0o600)
        log = open(run / "tor.log", "wb")
        self.proc = subprocess.Popen([tor, "-f", str(conf), "--RunAsDaemon", "0"],
                                     stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, cwd=str(data))
        log.close()
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise TorError(f"Tor exited: {self.tail()}")
            if os.path.exists(self.control_path) and os.path.exists(self.cookie_path):
                return
            time.sleep(0.1)
        self.stop()
        raise TorError("Tor did not open its control port")

    def tail(self, n: int = 3) -> str:
        try:
            lines = (paths.run_dir() / "tor.log").read_text(errors="replace").splitlines()
        except OSError:
            return ""
        return " | ".join(lines[-n:])

    def control(self) -> Control:
        return Control(self.control_path, self.cookie_path)

    def bootstrap(self) -> tuple[int, str]:
        with self.control() as c:
            return parse_bootstrap(c.getinfo("status/bootstrap-phase"))

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
