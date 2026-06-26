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


def _torrc_safe(value: str) -> str:
    """Strip newlines/CRs from a user-supplied value before it enters torrc.

    SECURITY: the privileged daemon reads these values from the *desktop user's*
    config file (which they fully control) and writes them to a torrc that Tor —
    potentially running as root — then parses.  An embedded newline would let a
    user inject an arbitrary torrc directive such as
    ``ClientTransportPlugin x exec /tmp/evil``, which Tor would execute.  Keeping
    every value on a single line confines it to the directive it belongs to; a
    malformed value simply fails ``tor --verify-config`` and never runs code.
    """
    return value.replace("\r", " ").replace("\n", " ")

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
        exits   = _torrc_safe(cfg().get("tor", "exit_nodes").strip())
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
        tor_bin = shutil.which("tor") or "tor"
        self._log(f"[TOR] Starting Tor… ({tor_bin})")

        # Validate torrc and surface early diagnostics.
        try:
            chk = subprocess.run(
                [tor_bin, "--verify-config", "-f", _TORRC_PATH],
                capture_output=True, text=True, timeout=15,
            )
            vc_out = (chk.stdout + chk.stderr).strip()
            if vc_out:
                for ln in vc_out.splitlines():
                    if ln.strip():
                        self._log(f"[TOR] {ln.strip()}")
            if chk.returncode != 0:
                raise RuntimeError(
                    f"Tor config error (exit {chk.returncode}): "
                    f"{vc_out or 'verify-config failed'}"
                )
        except FileNotFoundError:
            raise RuntimeError(
                f"tor binary not found at '{tor_bin}'. Install the tor package.")
        except subprocess.TimeoutExpired:
            pass

        # Check that required ports are free before binding.
        self._check_ports()

        self._bootstrap_done = threading.Event()
        self._bootstrap_error = ""

        self._tor_proc = subprocess.Popen(
            [tor_bin, "-f", _TORRC_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        threading.Thread(target=self._stderr_reader, daemon=True).start()

        if not self._bootstrap_done.wait(timeout=90):
            if self._tor_proc and self._tor_proc.poll() is None:
                self._tor_proc.terminate()
                try:
                    self._tor_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._tor_proc.kill()
                    self._tor_proc.wait()
            self._tor_proc = None
            raise RuntimeError("Tor bootstrap timed out (90 s). Check your network.")

        if self._bootstrap_error:
            err = self._bootstrap_error
            proc, self._tor_proc = self._tor_proc, None
            exit_code = proc.poll() if proc else None
            if proc and exit_code is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            elif proc:
                proc.wait()  # reap zombie
            suffix = f" (exit code: {exit_code})" if exit_code is not None else ""
            raise RuntimeError(f"Tor failed to start{suffix}: {err}")

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
            # SECURITY: confine each bridge entry to a single line so a malicious
            # config cannot inject extra torrc directives (e.g. an exec plugin).
            bl = _torrc_safe(bl).strip()
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

    # ── control port helpers ───────────────────────────────────────────────────

    @staticmethod
    def _ctrl_recv(s) -> str:
        """Read a complete Tor control-port response and return the data value(s).

        Handles both single-line (250-key=value) and multi-line (250+key=\\n...\\n.)
        response formats.  Returns the raw value string (after the '=').
        """
        resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
            decoded = resp.decode(errors="replace")
            # Response is complete when it contains "250 OK" (success) or an
            # error code on a standalone line (513/515/552/554).
            # We check for the error codes as line prefixes (after any newline)
            # so partial first-chunk matches don't cause false positives.
            if "250 OK" in decoded:
                break
            for ec in ("513 ", "515 ", "552 ", "554 "):
                if f"\n{ec}" in decoded or decoded.startswith(ec):
                    break
            else:
                continue
            break
        text = resp.decode(errors="replace")
        data_lines: list[str] = []
        in_block = False
        for line in text.splitlines():
            if line.startswith("250+") and "=" in line:
                in_block = True
                val = line[4:].partition("=")[2]
                if val:
                    data_lines.append(val)
            elif line == ".":
                in_block = False
            elif in_block and not line.startswith("250"):
                data_lines.append(line)
            elif (line.startswith("250-") or line.startswith("250 ")) and "=" in line:
                data_lines.append(line[4:].partition("=")[2].strip())
        return "\n".join(data_lines)

    def _ctrl_getinfo(self, s, key: str) -> str:
        """Send GETINFO <key> over an already-authenticated socket and return the value."""
        s.sendall(f"GETINFO {key}\r\n".encode())
        return self._ctrl_recv(s)

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
                    lines_resp = resp.decode(errors="replace").splitlines()
                    idx = 0
                    while idx < len(lines_resp):
                        ln = lines_resp[idx]
                        if ln.startswith("250+") and "=" in ln:
                            # Multi-line reply block (e.g. circuit-status)
                            k, _, v = ln[4:].partition("=")
                            data: list[str] = [v] if v.strip() else []
                            idx += 1
                            while idx < len(lines_resp):
                                data_ln = lines_resp[idx]
                                if data_ln in (".", "") or data_ln.startswith("250"):
                                    break
                                data.append(data_ln)
                                idx += 1
                            result[k.strip()] = "\n".join(data)
                        elif (ln.startswith("250-") or ln.startswith("250 ")) and "=" in ln:
                            k, _, v = ln[4:].partition("=")
                            result[k.strip()] = v.strip()
                        idx += 1
        except Exception:
            pass
        return result

    def get_circuit_info(self) -> dict:
        """Return a summary dict with circuit count, exit country, and traffic.

        Opens a single authenticated control-port session and chains the
        needed GETINFO calls:
          1. circuit-status          → count BUILT circuits
          2. ns/id/<fingerprint>     → get exit relay's IP (from 'r' line)
          3. ip-to-country/<ip>      → 2-char country code
          4. traffic/read + written  → cumulative byte counters
        """
        import socket as _socket
        ctrl        = cfg().get("tor", "control_port")
        cookie_path = os.path.join(_DATA_DIR, "control_auth_cookie")

        empty = {"circuit_count": 0, "exit_country": "",
                 "bytes_read": 0, "bytes_written": 0}
        try:
            with open(cookie_path, "rb") as f:
                cookie_hex = f.read().hex()
        except FileNotFoundError:
            return empty

        try:
            with _socket.socket() as s:
                s.settimeout(5)
                s.connect(("127.0.0.1", ctrl))
                s.sendall(f"AUTHENTICATE {cookie_hex}\r\n".encode())
                self._ctrl_recv(s)  # consume auth response

                # ── 1. circuit list ───────────────────────────────
                circuits_raw = self._ctrl_getinfo(s, "circuit-status")
                all_built     = [l for l in circuits_raw.splitlines() if "BUILT" in l]
                general       = [l for l in all_built if "PURPOSE=GENERAL" in l]

                # ── 2. exit country from first general BUILT circuit ──
                exit_country = ""
                for circ in general:
                    parts = circ.split()
                    if len(parts) < 3 or not parts[2].startswith("$"):
                        continue
                    relays = parts[2].split(",")
                    fp_m   = re.match(r'\$([0-9A-Fa-f]{40})', relays[-1])
                    if not fp_m:
                        continue
                    fp = fp_m.group(1)

                    # ── 3. relay network-status → IP ─────────────
                    ns_raw = self._ctrl_getinfo(s, f"ns/id/{fp}")
                    ip = None
                    for ns_line in ns_raw.splitlines():
                        if ns_line.startswith("r "):
                            ns_parts = ns_line.split()
                            if len(ns_parts) >= 7:
                                ip = ns_parts[6]
                            break

                    if ip:
                        # ── 4. IP → country code ─────────────────
                        cc = self._ctrl_getinfo(s, f"ip-to-country/{ip}").strip()
                        if len(cc) == 2 and cc.isalpha() and cc not in ("ZZ",):
                            exit_country = cc.upper()
                    break  # one exit is enough

                # ── 5. traffic counters ───────────────────────────
                read_raw    = self._ctrl_getinfo(s, "traffic/read").strip()
                written_raw = self._ctrl_getinfo(s, "traffic/written").strip()

                return {
                    "circuit_count": len(all_built),
                    "exit_country":  exit_country,
                    "bytes_read":    int(read_raw    or 0),
                    "bytes_written": int(written_raw or 0),
                }
        except Exception:
            return empty

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
        killed = False
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())
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
                        pass
                    killed = True
            except (FileNotFoundError, ValueError):
                pass
        except (FileNotFoundError, ValueError):
            pass
        try:
            os.unlink(lock_file)
        except FileNotFoundError:
            pass

        if not killed:
            # Fallback: lock file may have been removed already but the process
            # is still alive and holding our ports.  Find it via ss.
            self._kill_tor_by_port(cfg().get("tor", "socks_port"), _signal)

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
        from collections import deque
        bootstrap_re = re.compile(r"Bootstrapped (\d+)%")
        proc = self._tor_proc  # local reference — safe if tor_proc is reset later
        last_pct = -1
        last_err = ""
        recent: deque = deque(maxlen=40)  # keep last 40 lines for failure diagnosis
        try:
            for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                recent.append(line)
                low = line.lower()
                if any(p in low for p in self._FATAL_PATTERNS):
                    last_err = line
                    self._log(f"[TOR] {line}")  # surface fatal lines immediately
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
            # Log all collected output so the user can see the actual error.
            for ln in recent:
                self._log(f"[TOR] {ln}")
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

    def _kill_tor_by_port(self, port: int, _signal_mod) -> None:
        """Kill any 'tor' process that is listening on *port* (ss-based fallback)."""
        import re as _re
        try:
            r = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port}"],
                capture_output=True, text=True, timeout=3,
            )
            for line in r.stdout.splitlines():
                if "tor" not in line.lower():
                    continue
                m = _re.search(r"pid=(\d+)", line)
                if not m:
                    continue
                pid = int(m.group(1))
                self._log(f"[TOR] Killing orphaned Tor by port :{port} (PID {pid})…")
                try:
                    os.kill(pid, _signal_mod.SIGTERM)
                    time.sleep(0.5)
                    os.kill(pid, _signal_mod.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception:
            pass

    # ── port / data-dir diagnostics ──────────────────────────────

    def _check_ports(self) -> None:
        """Raise RuntimeError listing any Tor ports that are already in use."""
        import socket as _sock
        ports = {
            "TransPort":   cfg().get("tor", "trans_port"),
            "DNSPort":     cfg().get("tor", "dns_port"),
            "SocksPort":   cfg().get("tor", "socks_port"),
            "ControlPort": cfg().get("tor", "control_port"),
        }
        busy = []
        for name, port in ports.items():
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", int(port)))
            except OSError:
                busy.append(f"{name} :{port}")
            finally:
                s.close()
        if busy:
            raise RuntimeError(
                f"Port(s) already in use — cannot start Tor: {', '.join(busy)}. "
                "Run 'sudo ss -tlnp' to see which process is holding them.")

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
