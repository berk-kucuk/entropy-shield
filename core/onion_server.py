from __future__ import annotations
import os
import sys
import pwd
import time
import subprocess
import threading
from typing import Callable

from .config import cfg, cfg_port

_HS_DIR       = "/var/lib/tor/entropy-shield-hs"
_MARKER_BEGIN = "# --- entropy-shield-hs-begin ---"
_MARKER_END   = "# --- entropy-shield-hs-end ---"


def _invoking_uid() -> int | None:
    """Return the uid of the desktop user that asked for this connection.

    The privileged daemon exposes it via SUDO_UID (set from the socket peer
    credentials); the legacy pkexec path used PKEXEC_UID.
    """
    for var in ("PKEXEC_UID", "SUDO_UID"):
        val = os.environ.get(var)
        if val and val.isdigit():
            return int(val)
    return None


def _real_user_home() -> str:
    """Return the home directory of the invoking user (before sudo/pkexec)."""
    uid = _invoking_uid()
    if uid is not None:
        try:
            return pwd.getpwuid(uid).pw_dir
        except KeyError:
            pass
    return os.path.expanduser("~")


def _public_share_dir(home: str) -> str:
    """The user's XDG "Public" folder — the directory meant for sharing.

    Read from ~/.config/user-dirs.dirs (XDG_PUBLICSHARE_DIR, which xdg-user-dirs
    writes and may localise, e.g. "$HOME/Genel"); falls back to ~/Public.
    """
    try:
        with open(os.path.join(home, ".config", "user-dirs.dirs")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("XDG_PUBLICSHARE_DIR="):
                    val = line.split("=", 1)[1].strip().strip('"')
                    val = val.replace("$HOME", home)
                    if os.path.isabs(val):
                        return val
    except OSError:
        pass
    return os.path.join(home, "Public")


class OnionServerManager:
    def __init__(self, log: Callable[[str], None]):
        self._log   = log
        self._proc: "subprocess.Popen | None" = None

    # ── torrc config ──────────────────────────────────────────

    def configure(self, torrc_path: str) -> None:
        local_port = cfg_port("onion_server", "local_port")
        hs_port    = cfg_port("onion_server", "hs_port")

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
        local_port = cfg_port("onion_server", "local_port")
        serve_dir  = str(cfg().get("onion_server", "serve_dir")).strip()
        home = _real_user_home()
        # The default used to be the WHOLE home directory. http.server lists
        # directories, so anyone holding the .onion address could browse and
        # download ~/.ssh, ~/.gnupg, browser profiles, password stores and
        # wallets — dropping to the user's uid (below) protects root's files,
        # not the user's own. Default to the folder that exists for sharing.
        if not serve_dir:
            serve_dir = _public_share_dir(home)
            if not os.path.isdir(serve_dir):
                raise RuntimeError(
                    f"No folder to publish: {serve_dir} does not exist. "
                    "Create it and put in it only what you want to share, "
                    "or choose a folder in Settings → Onion Server.")

        if not os.path.isdir(serve_dir):
            raise RuntimeError(
                f"Serve directory does not exist: {serve_dir}")

        # Refuse the home directory itself (or anything above it) even when
        # chosen explicitly: it exposes every private file the user has.
        real_dir, real_home = os.path.realpath(serve_dir), os.path.realpath(home)
        if real_home == real_dir or real_home.startswith(real_dir.rstrip("/") + "/"):
            raise RuntimeError(
                f"Refusing to publish {serve_dir}: it contains your whole home "
                "directory (SSH keys, browser profiles, passwords). Choose a "
                "folder that holds only what you want to share.")

        # SECURITY: the file server is run as the *invoking desktop user*, never
        # as root.  Otherwise a user could point serve_dir at /root, /etc, … and
        # read root-only files (e.g. /etc/shadow) over 127.0.0.1 — a privilege
        # escalation.  Running it dropped to their uid means the server can only
        # read what that user can already read.
        cmd = [
            sys.executable, "-m", "http.server", str(local_port),
            "--bind", "127.0.0.1", "--directory", serve_dir,
        ]

        popen_kwargs: dict = dict(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        uid = _invoking_uid()
        dropped = False
        if os.geteuid() == 0:
            if uid is not None and uid > 0:
                try:
                    gid = pwd.getpwuid(uid).pw_gid
                except KeyError:
                    raise RuntimeError(
                        f"Cannot resolve invoking user (uid {uid}).")
                # user=/group= require Python 3.9+ (project targets 3.10+).
                popen_kwargs["user"]  = uid
                popen_kwargs["group"] = gid
                # extra_groups is what makes the drop complete. user=/group=
                # call setreuid/setregid only — Popen never calls setgroups
                # unless this is given, so without it the child KEEPS the
                # parent's supplementary groups. Started under sudo rather than
                # systemd that parent is root with group 0 in its list, and the
                # child would carry it: /etc/sudoers is 0440 root:root, so
                # pointing serve_dir at /etc would have served a file the
                # invoking user cannot read — the exact thing the comment above
                # says cannot happen. Passing the user's own group list also
                # fixes the other direction, which was silently missing too.
                try:
                    popen_kwargs["extra_groups"] = os.getgrouplist(
                        pwd.getpwuid(uid).pw_name, gid)
                except (KeyError, OSError):
                    # Cannot resolve them → grant none rather than inherit
                    # root's. An empty list is the safe answer here.
                    popen_kwargs["extra_groups"] = []
                dropped = True
            else:
                # No identifiable desktop user — refuse to expose files as root.
                self._log(
                    "[ONION] WARNING: could not identify the invoking user; "
                    "refusing to serve files as root.")
                raise RuntimeError(
                    "Onion server: cannot drop privileges (no invoking user).")

        try:
            self._proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot start onion HTTP server: {exc}") from exc

        # Give it a moment to fail fast (e.g. port in use, privileged port the
        # dropped user cannot bind) and surface a clear error.
        time.sleep(0.3)
        if self._proc.poll() is not None:
            err = ""
            try:
                err = (self._proc.stderr.read() or b"").decode(errors="replace").strip()
            except Exception:
                pass
            self._proc = None
            raise RuntimeError(
                f"Onion HTTP server failed to bind 127.0.0.1:{local_port}"
                + (f" — {err.splitlines()[-1]}" if err else "")
                + (". Use a port ≥ 1024." if local_port < 1024 else ""))

        who = f"as uid {uid}" if dropped else "as current user"
        self._log(
            f"[ONION] HTTP server started on 127.0.0.1:{local_port} ({who})"
            f" — serving: {serve_dir}"
        )

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return

        def _bg():
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
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
