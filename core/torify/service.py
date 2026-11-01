"""The per-user service behind "run through Tor".

One per user, started on demand by the first launch and gone a short while
after the last torified app closes. It owns the Tor that the apps use and
the list of running apps, and answers JSON lines on a Unix socket in the
user's runtime directory (0700, so only the user can talk to it).

Which Tor:
  * Entropy Shield connected (system-wide) → its own Tor, through the Unix
    SocksPort the daemon opens. Starting a private Tor then would send Tor's
    traffic into the system-wide Tor: Tor over Tor, slower and no safer.
  * otherwise → a private Tor as the user (torproc.py).

Requests:
  {"op": "prepare", "app": "Firefox"}   → progress lines, then the session
  {"op": "end", "session": id}
  {"op": "list"}                          → sessions, with exit country
  {"op": "new_identity", "session": id}  → new circuits for that app
  {"op": "status"}
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import secrets
import socket
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from core.torify import paths  # noqa: E402
from core.torify.torproc import PrivateTor, TorError, exit_info  # noqa: E402

IDLE_EXIT = 60            # seconds without sessions before Tor and the service stop
BOOTSTRAP_TIMEOUT = 300   # a bridge on a slow network can take minutes
_MAX_LINE = 64 * 1024


def system_tor_available(path=None) -> bool:
    p = str(path or paths.SYSTEM_SOCKS)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(p)
        return True
    except OSError:
        return False


class Session:
    def __init__(self, app: str, socks: str, via: str):
        self.id = secrets.token_hex(6)
        self.app = app[:120]
        self.socks = socks
        self.via = via
        self.started = time.time()
        self.token_path = paths.sessions_dir(create=True) / f"{self.id}.token"
        self.username = ""
        self.renew()

    def renew(self) -> None:
        """A fresh SOCKS username: Tor builds new circuits for it."""
        self.username = f"es-{self.id}-{secrets.token_hex(6)}"
        tmp = self.token_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.username)
        os.replace(tmp, self.token_path)

    def remove(self) -> None:
        try:
            self.token_path.unlink()
        except FileNotFoundError:
            pass

    def public(self) -> dict:
        return {"session": self.id, "app": self.app, "via": self.via,
                "started": self.started}


class Service:
    def __init__(self):
        self.tor = PrivateTor()
        self.sessions: dict[str, Session] = {}
        self._tor_lock = asyncio.Lock()
        self._idle_since = time.time()
        self._server = None

    # ── Tor ───────────────────────────────────────────────────────────────
    async def _ensure_tor(self, progress) -> tuple[str, str]:
        """(socks path, via) once Tor is ready to carry traffic."""
        if system_tor_available():
            return str(paths.SYSTEM_SOCKS), "system"
        async with self._tor_lock:
            if not self.tor.running():
                await asyncio.to_thread(self.tor.start)
            deadline = time.time() + BOOTSTRAP_TIMEOUT
            last = -1
            while True:
                if not self.tor.running():
                    raise TorError(f"Tor stopped: {self.tor.tail()}")
                try:
                    pct, summary = await asyncio.to_thread(self.tor.bootstrap)
                except (OSError, TorError):
                    pct, summary = 0, ""
                if pct != last:
                    await progress(pct, summary)
                    last = pct
                if pct >= 100:
                    return self.tor.socks_path, "private"
                if time.time() > deadline:
                    raise TorError("Tor could not connect — the network may be "
                                   "blocking it; try bridges in Settings → Tor")
                await asyncio.sleep(0.5)

    # ── requests ──────────────────────────────────────────────────────────
    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        async def send(obj: dict) -> None:
            writer.write((json.dumps(obj) + "\n").encode())
            await writer.drain()
        try:
            line = await reader.readline()
            if not line or len(line) > _MAX_LINE:
                return
            req = json.loads(line)
            op = req.get("op") if isinstance(req, dict) else None
            if op == "prepare":
                async def progress(pct: int, summary: str) -> None:
                    await send({"progress": pct, "summary": summary})
                socks, via = await self._ensure_tor(progress)
                s = Session(str(req.get("app") or "app"), socks, via)
                self.sessions[s.id] = s
                await send({"ok": True, "session": s.id, "socks": socks,
                            "token": str(s.token_path), "via": via})
                # The launcher keeps this connection open for the app's life;
                # its closing (app gone, launcher killed) ends the session.
                await reader.read()
                self._end(s.id)
            elif op == "end":
                self._end(str(req.get("session")))
                await send({"ok": True})
            elif op == "new_identity":
                s = self.sessions.get(str(req.get("session")))
                if s is None:
                    await send({"ok": False, "error": "no such session"})
                else:
                    s.renew()
                    await send({"ok": True})
            elif op == "list":
                await send({"ok": True, "sessions": await self._list()})
            elif op == "status":
                await send({"ok": True, "tor": "running" if self.tor.running() else "stopped",
                            "system": system_tor_available(),
                            "sessions": len(self.sessions)})
            else:
                await send({"ok": False, "error": "unknown op"})
        except TorError as e:
            try:
                await send({"ok": False, "error": str(e)})
            except (ConnectionError, OSError):
                pass
        except (ValueError, ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _list(self) -> list[dict]:
        out = [s.public() for s in self.sessions.values()]
        if not self.tor.running():
            return out

        def lookup() -> dict[str, dict]:
            info = {}
            try:
                with self.tor.control() as c:
                    for s in self.sessions.values():
                        if s.via == "private":
                            info[s.id] = exit_info(c, s.username)
            except (OSError, TorError):
                pass
            return info
        info = await asyncio.to_thread(lookup)
        for item in out:
            item["exit"] = info.get(item["session"], {})
        return out

    def _end(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        if s is not None:
            s.remove()
        if not self.sessions:
            self._idle_since = time.time()

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def run(self) -> None:
        sock = paths.service_socket()
        paths.run_dir(create=True)
        # One service per user. Two launches at the same moment may both
        # start one; the second finds the lock taken and leaves.
        lock = open(paths.run_dir() / "service.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return
        try:
            sock.unlink()
        except FileNotFoundError:
            pass
        old = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(self.handle, str(sock),
                                                           limit=_MAX_LINE)
        finally:
            os.umask(old)
        try:
            while True:
                await asyncio.sleep(2)
                if not self.sessions and time.time() - self._idle_since > IDLE_EXIT:
                    break
        finally:
            self._server.close()
            try:
                sock.unlink()
            except FileNotFoundError:
                pass
            await asyncio.to_thread(self.tor.stop)
            for s in list(self.sessions.values()):
                s.remove()


def main() -> int:
    asyncio.run(Service().run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
