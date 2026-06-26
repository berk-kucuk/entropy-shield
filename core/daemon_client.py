#!/usr/bin/env python3
"""
Entropy Shield — Daemon Client
==============================

Thin client used by the GUI (running as the unprivileged desktop user) to talk
to the root :mod:`core.daemon` over its Unix-domain socket.

The GUI's worker threads were written against a ``subprocess.Popen`` whose
``stdin``/``stdout`` pipes carried the line protocol.  To keep that code
unchanged, :class:`DaemonClient` exposes the same small surface:

    client.stdin.write(b"connect ...\\n");  client.stdin.flush()
    line = client.stdout.readline()
    client.poll()        # None while connected, exit-ish code once closed
    client.wait(timeout) # no-op for a socket, kept for API symmetry
    client.close()       # tear down the connection (replaces proc.kill())

A connection corresponds to one privileged session: the daemon rolls back any
state when the socket closes, so closing the client is a safe disconnect.
"""
from __future__ import annotations
import socket

# Must match core/daemon.py
SOCKET_PATH = "/run/entropy-shield/daemon.sock"


class DaemonError(RuntimeError):
    """Raised when the daemon socket cannot be reached."""


class DaemonClient:
    """Popen-compatible wrapper around the daemon's Unix socket connection."""

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self.stdin  = sock.makefile("wb")
        self.stdout = sock.makefile("rb")
        self._closed = False

    # ── construction ──────────────────────────────────────────────────────────
    @classmethod
    def connect(cls, path: str = SOCKET_PATH, timeout: float = 10.0) -> "DaemonClient":
        """Open a connection to the daemon, raising :class:`DaemonError` on failure."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(path)
        except FileNotFoundError:
            sock.close()
            raise DaemonError(
                "Entropy Shield daemon is not running. "
                "Start it with:  sudo systemctl start entropy-shield"
            )
        except PermissionError:
            sock.close()
            raise DaemonError(
                "Permission denied connecting to the Entropy Shield daemon. "
                "Your user must be in the 'entropy-shield' group "
                "(log out and back in after installing)."
            )
        except OSError as exc:
            sock.close()
            raise DaemonError(f"Cannot reach Entropy Shield daemon: {exc}")
        # Blocking I/O for the rest of the session (the GUI runs this on a thread).
        sock.settimeout(None)
        return cls(sock)

    # ── Popen-compatible API ──────────────────────────────────────────────────
    def poll(self):
        """Return None while the connection is open, 0 once it has been closed."""
        return 0 if self._closed else None

    def wait(self, timeout=None):  # noqa: ARG002 - signature parity with Popen
        """No child process to reap; present only for API symmetry."""
        return 0 if self._closed else None

    def kill(self) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for f in (self.stdin, self.stdout):
            try:
                f.close()
            except Exception:
                pass
        try:
            self._sock.close()
        except Exception:
            pass


def daemon_available(path: str = SOCKET_PATH) -> bool:
    """Best-effort check: can we currently open a session to the daemon?"""
    try:
        client = DaemonClient.connect(path, timeout=2.0)
    except DaemonError:
        return False
    client.close()
    return True
