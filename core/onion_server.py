from __future__ import annotations
import os
import pwd
import time
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Callable

from .config import cfg

_HS_DIR       = "/var/lib/tor/entropy-shield-hs"
_MARKER_BEGIN = "# --- entropy-shield-hs-begin ---"
_MARKER_END   = "# --- entropy-shield-hs-end ---"


def _real_user_home() -> str:
    """Return the home directory of the invoking user (before sudo/pkexec)."""
    for var in ("PKEXEC_UID", "SUDO_UID"):
        val = os.environ.get(var)
        if val and val.isdigit():
            try:
                return pwd.getpwuid(int(val)).pw_dir
            except KeyError:
                pass
    return os.path.expanduser("~")


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without request logs or keep-alive."""
    # Per-connection inactivity timeout so shutdown() never waits more
    # than this many seconds for an idle client to close.
    timeout = 5

    def log_message(self, *_args) -> None:
        pass

    def handle_one_request(self) -> None:
        super().handle_one_request()
        # Force-close after each request so shutdown() doesn't block
        # waiting for a browser's persistent keep-alive connection.
        self.close_connection = True


class OnionServerManager:
    def __init__(self, log: Callable[[str], None]):
        self._log        = log
        self._httpd:     HTTPServer | None  = None
        self._thread:    threading.Thread | None = None

    # ── torrc config ──────────────────────────────────────────

    def configure(self, torrc_path: str) -> None:
        local_port = cfg().get("onion_server", "local_port")
        hs_port    = cfg().get("onion_server", "hs_port")

        block = (
            f"\n{_MARKER_BEGIN}\n"
            f"HiddenServiceDir {_HS_DIR}\n"
            f"HiddenServicePort {hs_port} 127.0.0.1:{local_port}\n"
            f"{_MARKER_END}\n"
        )

        with open(torrc_path, "r") as f:
            content = f.read()
        content = self._strip_block(content)
        with open(torrc_path, "w") as f:
            f.write(content)
            f.write(block)

        self._log(
            f"[ONION] Hidden service configured: "
            f"onion port {hs_port} → 127.0.0.1:{local_port}"
        )

    def remove_config(self, torrc_path: str) -> None:
        if not os.path.exists(torrc_path):
            return
        with open(torrc_path, "r") as f:
            content = f.read()
        stripped = self._strip_block(content)
        if stripped != content:
            with open(torrc_path, "w") as f:
                f.write(stripped)

    # ── HTTP file server ──────────────────────────────────────

    def start(self) -> None:
        local_port = cfg().get("onion_server", "local_port")
        serve_dir  = cfg().get("onion_server", "serve_dir").strip()
        if not serve_dir:
            serve_dir = _real_user_home()

        if not os.path.isdir(serve_dir):
            raise RuntimeError(
                f"Serve directory does not exist: {serve_dir}")

        import functools
        handler = functools.partial(_QuietHandler, directory=serve_dir)

        try:
            self._httpd = HTTPServer(("127.0.0.1", local_port), handler)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot bind HTTP server on port {local_port}: {exc}") from exc

        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._log(
            f"[ONION] HTTP server started on 127.0.0.1:{local_port}"
            f" — serving: {serve_dir}"
        )

    def stop(self) -> None:
        httpd,         self._httpd  = self._httpd,  None
        thread,        self._thread = self._thread, None

        if httpd is not None:
            # shutdown() blocks until serve_forever() exits.  Run it in a
            # background thread so the caller (the Qt disconnect worker)
            # is not frozen while an open browser connection drains.
            def _bg():
                try:
                    httpd.shutdown()
                    httpd.server_close()
                except Exception:
                    pass
            threading.Thread(target=_bg, daemon=True).start()

        self._log("[ONION] HTTP server stopped.")

    # ── .onion address ────────────────────────────────────────

    def onion_address(self, wait: int = 10) -> str | None:
        hostname = os.path.join(_HS_DIR, "hostname")
        for _ in range(wait):
            if os.path.exists(hostname):
                try:
                    with open(hostname) as f:
                        addr = f.read().strip()
                    if addr:
                        return addr
                except Exception:
                    pass
            time.sleep(1)
        return None

    # ── helpers ───────────────────────────────────────────────

    def _strip_block(self, content: str) -> str:
        lines, out, inside = content.splitlines(keepends=True), [], False
        for line in lines:
            if line.strip() == _MARKER_BEGIN:
                inside = True
                continue
            if line.strip() == _MARKER_END:
                inside = False
                continue
            if not inside:
                out.append(line)
        return "".join(out)
