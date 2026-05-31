from __future__ import annotations
import os
import re
import subprocess
import shutil
import threading
import time
from typing import Callable

from .config import cfg
from .platform import is_nixos

_RUN_DIR  = "/run/entropy-shield"
_TORRC_PATH = "/run/entropy-shield/torrc"
_DATA_DIR = "/run/entropy-shield/tor-data"

# Exported so onion_server.py can append HiddenService config to our torrc.
TORRC = _TORRC_PATH

_TORRC_TEMPLATE = """\
# entropy-shield — auto-generated, do not edit
DataDirectory {data_dir}
Log notice stderr
{user_line}

VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
AutomapHostsSuffixes .onion,.exit

TransPort   127.0.0.1:{trans_port}
DNSPort     127.0.0.1:{dns_port}
SocksPort   127.0.0.1:{socks_port} IsolateDestAddr IsolateDestPort
ControlPort 127.0.0.1:{control_port}
CookieAuthentication 1
CookieAuthFile {data_dir}/control_auth_cookie
{exit_line}
{strict_line}
{bridge_section}
"""


class TorManager:
    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self._tor_proc: subprocess.Popen | None = None
        self._bootstrap_done: threading.Event | None = None
        self._bootstrap_error: str = ""
        self._system_tor_was_active: bool = False

    def is_installed(self) -> bool:
        return bool(shutil.which("tor"))

    def is_running(self) -> bool:
        """Return True if our managed Tor subprocess is alive."""
        return self._tor_proc is not None and self._tor_proc.poll() is None

    def configure(self) -> None:
        self._log("[TOR] Writing custom torrc…")

        # Reset flag so stale state from a previous run can't cause a
        # spurious system-tor restart if this configure() is called again.
        self._system_tor_was_active = False

        # If system Tor is occupying our ports, stop it temporarily.
        if self._service_active("tor"):
            self._log("[TOR] System tor service found — stopping it temporarily.")
            subprocess.run(["systemctl", "stop", "tor"], capture_output=True)
            self._system_tor_was_active = True

        os.makedirs(_RUN_DIR, mode=0o755, exist_ok=True)
        os.makedirs(_DATA_DIR, mode=0o700, exist_ok=True)

        # ── kill orphaned Tor from a previous force-killed session ────────────
        # When the runner is killed (SIGKILL / process killed), its Tor child
        # becomes an orphan but keeps running.  The lock file it holds would
        # prevent a new Tor from starting.  Detect and terminate it first.
        self._kill_orphaned_tor()

        tor_user = self._find_tor_user()
        if tor_user:
            r_uid = subprocess.run(["id", "-u", tor_user], capture_output=True, text=True)
            r_gid = subprocess.run(["id", "-g", tor_user], capture_output=True, text=True)
            if r_uid.returncode == 0 and r_gid.returncode == 0:
                os.chown(_DATA_DIR, int(r_uid.stdout.strip()), int(r_gid.stdout.strip()))

        trans   = cfg().get("tor", "trans_port")
        dns     = cfg().get("tor", "dns_port")
        socks   = cfg().get("tor", "socks_port")
        ctrl    = cfg().get("tor", "control_port")
        exits   = cfg().get("tor", "exit_nodes").strip()
        strict  = cfg().get("tor", "strict_nodes")

        # ── bridge / pluggable transport ─────────────────────────
        bridge_section = self._build_bridge_section()

        content = _TORRC_TEMPLATE.format(
            data_dir       = _DATA_DIR,
            user_line      = f"User {tor_user}" if tor_user else "",
            trans_port     = trans,
            dns_port       = dns,
            socks_port     = socks,
            control_port   = ctrl,
            exit_line      = f"ExitNodes {{{exits}}}" if exits else "",
            strict_line    = "StrictNodes 1" if strict else "",
            bridge_section = bridge_section,
        )

        with open(_TORRC_PATH, "w") as f:
            f.write(content)
        os.chmod(_TORRC_PATH, 0o644)
        self._log(f"[TOR] Custom torrc → {_TORRC_PATH}")

    def start(self) -> None:
        self._log("[TOR] Starting Tor…")
        self._bootstrap_done = threading.Event()
        self._bootstrap_error = ""

        self._tor_proc = subprocess.Popen(
            ["tor", "-f", _TORRC_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        threading.Thread(target=self._stderr_reader, daemon=True).start()

        if not self._bootstrap_done.wait(timeout=90):
            if self._tor_proc and self._tor_proc.poll() is None:
                self._tor_proc.terminate()
            self._tor_proc = None
            raise RuntimeError("Tor bootstrap timed out (90 s). Check your network.")

        if self._bootstrap_error:
            err = self._bootstrap_error
            self._tor_proc = None
            raise RuntimeError(f"Tor failed to start: {err}")

        self._log("[TOR] Tor is running.")

    def stop(self) -> None:
        self._log("[TOR] Stopping Tor…")

        if self._tor_proc:
            if self._tor_proc.poll() is None:
                self._tor_proc.terminate()
                try:
                    self._tor_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._tor_proc.kill()
                    self._tor_proc.wait()
            self._tor_proc = None

        try:
            os.unlink(_TORRC_PATH)
        except FileNotFoundError:
            pass
        # DataDirectory kept intentionally — caches consensus for faster reconnect.

        if self._system_tor_was_active:
            subprocess.run(["systemctl", "start", "tor"], capture_output=True)
            self._system_tor_was_active = False

        self._log("[TOR] Tor stopped.")

    # ── bridge / pluggable transport ──────────────────────────────

    def _build_bridge_section(self) -> str:
        """Return torrc lines for bridge/pluggable-transport support."""
        from .config import cfg as _cfg
        bridges_cfg = _cfg().get("bridges")
        if not bridges_cfg.get("enabled"):
            return ""

        transport = bridges_cfg.get("transport", "obfs4")
        lines_raw  = bridges_cfg.get("lines", [])

        lines = ["UseBridges 1"]

        # Add ClientTransportPlugin for known transports
        pt_cmds = {
            "obfs4":      "obfs4 exec /usr/bin/obfs4proxy",
            "meek-azure": "meek_lite exec /usr/bin/obfs4proxy",
            "snowflake":  "snowflake exec /usr/bin/snowflake-client "
                          "-url https://snowflake-broker.torproject.net/ "
                          "-front cdn.sstatic.net "
                          "-ice stun:stun.l.google.com:19302",
        }
        if transport in pt_cmds:
            lines.append(f"ClientTransportPlugin {pt_cmds[transport]}")

        for bl in lines_raw:
            bl = bl.strip()
            if bl and not bl.startswith("#"):
                if not bl.lower().startswith("bridge"):
                    bl = "Bridge " + bl
                lines.append(bl)

        if not lines_raw:
            self._log(
                "[TOR] Bridges enabled but no bridge lines configured. "
                "Add bridge lines in Settings → TOR → BRIDGES."
            )

        return "\n".join(lines)

    def new_circuit(self) -> None:
        """Request a new Tor identity via the ControlPort (SIGNAL NEWNYM)."""
        import socket as _socket
        ctrl = cfg().get("tor", "control_port")
        cookie_path = os.path.join(_DATA_DIR, "control_auth_cookie")

        try:
            with open(cookie_path, "rb") as f:
                cookie_hex = f.read().hex()
        except FileNotFoundError:
            raise RuntimeError(
                f"Control auth cookie not found at {cookie_path}. "
                "Is Tor running?"
            )

        with _socket.socket() as s:
            s.settimeout(5)
            s.connect(("127.0.0.1", ctrl))
            # Tor's control port sends NO banner — send AUTHENTICATE immediately.
            s.sendall(f"AUTHENTICATE {cookie_hex}\r\n".encode())
            resp = s.recv(1024).decode(errors="replace")
            if not resp.startswith("250"):
                raise RuntimeError(f"Control auth failed: {resp.strip()}")
            s.sendall(b"SIGNAL NEWNYM\r\n")
            resp = s.recv(1024).decode(errors="replace")
            if not resp.startswith("250"):
                raise RuntimeError(f"NEWNYM failed: {resp.strip()}")

    def query_control(self, *commands: str) -> dict[str, str]:
        """Send one or more GETINFO commands to the control port.

        Returns a dict mapping keyword → value.
        Returns an empty dict on any error (Tor not running, etc.).
        """
        import socket as _socket
        ctrl = cfg().get("tor", "control_port")
        cookie_path = os.path.join(_DATA_DIR, "control_auth_cookie")
        result: dict[str, str] = {}
        try:
            with open(cookie_path, "rb") as f:
                cookie_hex = f.read().hex()
            with _socket.socket() as s:
                s.settimeout(4)
                s.connect(("127.0.0.1", ctrl))
                # Tor sends no banner — authenticate immediately.
                s.sendall(f"AUTHENTICATE {cookie_hex}\r\n".encode())
                s.recv(1024)  # auth response
                for cmd in commands:
                    s.sendall(f"GETINFO {cmd}\r\n".encode())
                    resp = b""
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                        if b"250 OK" in resp or b"515 " in resp:
                            break
                    for line in resp.decode(errors="replace").splitlines():
                        if line.startswith("250-") or line.startswith("250 "):
                            kv = line[4:]
                            if "=" in kv:
                                k, _, v = kv.partition("=")
                                result[k.strip()] = v.strip()
        except Exception:
            pass
        return result

    def get_circuit_info(self) -> dict:
        """Return a summary dict with circuit count and exit country."""
        data = self.query_control("circuit-status", "traffic/read", "traffic/written")
        circuits = [l for l in data.get("circuit-status", "").splitlines()
                    if "BUILT" in l]
        exit_country = ""
        for c in circuits:
            # Last node in path is exit; country code follows ~{cc}
            import re
            m = re.search(r"~\{([A-Z]{2})\}", c.split(",")[-1] if "," in c else c)
            if m:
                exit_country = m.group(1)
                break
        return {
            "circuit_count": len(circuits),
            "exit_country":  exit_country,
            "bytes_read":    int(data.get("traffic/read", 0) or 0),
            "bytes_written": int(data.get("traffic/written", 0) or 0),
        }

    # ── orphan / stale-state cleanup ─────────────────────────────

    def _kill_orphaned_tor(self) -> None:
        """Kill any Tor process left running from a previous force-killed session.

        When the runner is SIGKILL'd, the Tor subprocess it owns becomes an
        orphan adopted by PID 1.  The orphan still holds the data-directory
        lock file, which prevents a new Tor instance from starting.

        Strategy:
          1. Read the PID from the lock file (Tor writes its PID there).
          2. If the PID exists and the process name contains 'tor', send SIGTERM.
          3. Remove the lock file so the new instance can start cleanly.
        """
        import signal as _signal
        lock_file = os.path.join(_DATA_DIR, "lock")
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())
            # Verify the PID is actually a Tor process (not a recycled PID).
            try:
                with open(f"/proc/{old_pid}/comm") as f:
                    comm = f.read().strip().lower()
                if "tor" in comm:
                    self._log(f"[TOR] Killing orphaned Tor process (PID {old_pid})…")
                    try:
                        os.kill(old_pid, _signal.SIGTERM)
                        time.sleep(0.5)
                        os.kill(old_pid, _signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # Already exited after SIGTERM
            except (FileNotFoundError, ValueError):
                pass  # PID no longer running or /proc entry gone
        except (FileNotFoundError, ValueError):
            pass  # Lock file absent or unreadable — nothing to do
        # Always remove the lock file so Tor can start cleanly.
        try:
            os.unlink(lock_file)
        except FileNotFoundError:
            pass

    # ── stderr reader (daemon thread) ─────────────────────────────

    # Patterns that indicate a fatal or near-fatal startup failure.
    # Tor uses [warn] for many failures, not just [err], so we capture both.
    _FATAL_PATTERNS = (
        "[err]", "problem bootstrapping",
        "failed to bind", "address already in use",
        "could not bind",
        "another tor process",
        "unable to open",
        "permission denied",
    )

    def _stderr_reader(self) -> None:
        bootstrap_re = re.compile(r"Bootstrapped (\d+)%")
        proc = self._tor_proc  # local reference — safe if tor_proc is reset later
        last_pct = -1
        last_err = ""
        try:
            for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                low = line.lower()
                if any(p in low for p in self._FATAL_PATTERNS):
                    last_err = line
                m = bootstrap_re.search(line)
                if m:
                    pct = int(m.group(1))
                    if pct != last_pct:
                        last_pct = pct
                        self._log(f"[TOR] Bootstrap: {pct}%")
                    if pct >= 100:
                        self._log("[TOR] Bootstrap complete.")
                        self._bootstrap_done.set()
        except Exception:
            pass

        # Pipe closed — tor exited or was stopped.
        if self._bootstrap_done and not self._bootstrap_done.is_set():
            self._bootstrap_error = last_err or "Tor process ended unexpectedly."
            self._bootstrap_done.set()

    # ── DNS routing via resolvectl (systemd-resolved systems) ─────

    def nixos_redirect_dns(self) -> None:
        self._redirect_resolved_dns()

    def nixos_restore_dns(self) -> None:
        self._restore_resolved_dns()

    _DROPIN_DIR  = "/run/systemd/resolved.conf.d"
    _DROPIN_FILE = "/run/systemd/resolved.conf.d/entropy-shield.conf"

    def _redirect_resolved_dns(self) -> None:
        dns_port = cfg().get("tor", "dns_port")
        dns_addr = f"127.0.0.1:{dns_port}"

        if not self._resolved_running():
            self._log(
                f"[TOR] systemd-resolved not active — "
                f"nftables handles DNS redirect to {dns_addr}."
            )
            return

        os.makedirs(self._DROPIN_DIR, exist_ok=True)
        with open(self._DROPIN_FILE, "w") as f:
            f.write("[Resolve]\n")
            f.write(f"DNS={dns_addr}\n")
            f.write("Domains=~.\n")

        for iface in self._ifaces():
            subprocess.run(["resolvectl", "dns",    iface, dns_addr], capture_output=True)
            subprocess.run(["resolvectl", "domain", iface, "~."],     capture_output=True)

        subprocess.run(["systemctl", "reload", "systemd-resolved"], capture_output=True)
        self._log(f"[TOR] System DNS → Tor DNSPort ({dns_addr}).")

    def _restore_resolved_dns(self) -> None:
        if not self._resolved_running():
            return

        if os.path.exists(self._DROPIN_FILE):
            os.unlink(self._DROPIN_FILE)

        for iface in self._ifaces():
            subprocess.run(["resolvectl", "revert", iface], capture_output=True)

        subprocess.run(["systemctl", "reload", "systemd-resolved"], capture_output=True)
        self._log("[TOR] DNS settings reverted to system defaults.")

    # ── helpers ───────────────────────────────────────────────────

    def _find_tor_user(self) -> str | None:
        for name in ("debian-tor", "tor", "_tor", "toranon"):
            r = subprocess.run(["id", "-u", name], capture_output=True, text=True)
            if r.returncode == 0:
                return name
        return None

    def _service_active(self, name: str) -> bool:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True)
        return r.stdout.strip() == "active"

    def _resolved_running(self) -> bool:
        return self._service_active("systemd-resolved")

    def _ifaces(self) -> list[str]:
        r = subprocess.run(["ip", "-o", "link", "show", "up"],
                           capture_output=True, text=True)
        ifaces = []
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2:
                iface = parts[1].strip().split("@")[0].strip()
                if iface and iface != "lo":
                    ifaces.append(iface)
        return ifaces
