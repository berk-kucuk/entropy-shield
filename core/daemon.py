#!/usr/bin/env python3
"""
Entropy Shield — Privileged Daemon
===================================

Runs as root, started by systemd (see entropy-shield.service).  Listens on a
Unix-domain socket and accepts a small, fixed set of **validated** commands from
the unprivileged GUI client.

Why a daemon instead of pkexec/sudo?
------------------------------------
The old design ran ``core/privileged_runner.py`` as root via ``pkexec`` or a
passwordless ``sudo`` rule.  Granting NOPASSWD to run a *Python script* is a
privilege-escalation vector: whoever can modify that script — or any ``core/*``
module it imports — runs arbitrary code as root.  Here the root code lives in a
root-owned location the user cannot write, and the client may only send
whitelisted commands with validated arguments.  It can never inject code.

Access control
--------------
The socket is created ``root:entropy-shield`` mode ``0660``.  Only members of the
``entropy-shield`` group (the kernel enforces this on connect()) can talk to the
daemon.  ``install.sh`` adds the desktop user to that group.

Protocol (line based — identical to the legacy runner so the GUI parser is
unchanged):

  daemon → client : log lines, one per line; sentinels:
      [RUNNER] started                    (ready for commands)
      [OK] All selected layers are active.
      [OK] All layers disconnected.
      [ERR] <message>
      [CIRCUIT] <json>
  client → daemon : one command per line
      ping
      connect <json>      (json: {use_tor, use_dnscrypt, use_i2p, use_onion_server})
      disconnect
      new_circuit
      circuit_info
      panic
      status
"""
from __future__ import annotations
import os
import sys
import json
import socket
import struct
import signal
import threading

# ── NixOS PATH (root services may have a minimal PATH; ensure system tools
#    such as nft/tor/dnscrypt-proxy are reachable) ─────────────────────────────
for _p in ("/run/current-system/sw/bin", "/run/wrappers/bin",
           "/nix/var/nix/profiles/default/bin"):
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + ":" + os.environ.get("PATH", "")

# ── allow `from core.xxx import ...` regardless of cwd ────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── socket / group configuration ──────────────────────────────────────────────
SOCKET_GROUP = "entropy-shield"
RUNTIME_DIR  = "/run/entropy-shield"
SOCKET_PATH  = os.path.join(RUNTIME_DIR, "daemon.sock")

# Only these keys are forwarded to ConnectionManager.connect().  Anything else a
# client sends in the connect payload is ignored — the trust boundary.
_ALLOWED_CONNECT_KEYS = (
    "use_tor", "use_dnscrypt", "use_i2p", "use_onion_server",
)


# ── startup self-heal (moved from the legacy privileged_runner) ───────────────

def _startup_heal(log) -> None:
    """Remove state left behind by a previous force-killed session.

    Called once at daemon startup BEFORE accepting any client so that the first
    connect() always starts from a clean slate:
      • removes stale nftables tables (firewall rules stuck active)
      • restores dnscrypt-proxy config if a backup was left behind
      • restarts systemd-resolved if it was stopped by the previous session
      • kills orphaned Tor and removes its lock file
    """
    import subprocess as _sp, os as _os, shutil as _shu

    # ── 1. Remove leftover nftables tables ──────────────────────
    for fam in ("ip", "ip6"):
        r = _sp.run(["nft", "list", "table", fam, "entropy-shield"],
                    capture_output=True)
        if r.returncode == 0:
            log(f"[HEAL] Removing stale nftables {fam} table from previous session…")
            _sp.run(["nft", "delete", "table", fam, "entropy-shield"],
                    capture_output=True)

    # ── 2. Restore dnscrypt config if backup exists ──────────────
    _BAK = ".entropy-shield.bak"
    _DNS_CONFIGS = [
        "/etc/dnscrypt-proxy/dnscrypt-proxy.toml",
        "/etc/dnscrypt-proxy.toml",
    ]
    for cfg_path in _DNS_CONFIGS:
        bak = cfg_path + _BAK
        if _os.path.exists(bak):
            log(f"[HEAL] Restoring dnscrypt-proxy config from backup ({cfg_path})…")
            try:
                _shu.copy2(bak, cfg_path)
                _os.unlink(bak)
            except Exception as e:
                log(f"[HEAL] Failed to restore dnscrypt config: {e}")
            break

    # ── 3. Restart systemd-resolved if it was stopped ───────────
    r = _sp.run(["systemctl", "is-active", "systemd-resolved"],
                capture_output=True, text=True)
    if r.stdout.strip() != "active":
        r2 = _sp.run(["systemctl", "is-enabled", "systemd-resolved"],
                     capture_output=True, text=True)
        enabled_state = r2.stdout.strip()
        if enabled_state not in ("disabled", "masked"):
            log("[HEAL] Restarting systemd-resolved (was stopped by previous session)…")
            _sp.run(["systemctl", "start", "systemd-resolved"], capture_output=True)

    # ── 4. Kill orphaned Tor and remove its lock file ───────────
    # IMPORTANT: kill the process FIRST, then remove the lock file.  Removing the
    # lock without killing leaves the orphan holding ports 9040/9050/9051 so the
    # next connect attempt fails.
    import signal as _sig
    lock = "/run/entropy-shield/tor-data/lock"
    if _os.path.exists(lock):
        log("[HEAL] Stale Tor lock file found — killing orphaned process…")
        try:
            with open(lock) as _lf:
                _old_pid = int(_lf.read().strip())
            try:
                with open(f"/proc/{_old_pid}/comm") as _cf:
                    if "tor" in _cf.read().strip().lower():
                        _os.kill(_old_pid, _sig.SIGTERM)
                        import time as _tm; _tm.sleep(0.5)
                        try:
                            _os.kill(_old_pid, _sig.SIGKILL)
                        except ProcessLookupError:
                            pass
                        log(f"[HEAL] Killed orphaned Tor (PID {_old_pid}).")
            except (FileNotFoundError, ValueError, ProcessLookupError):
                pass
        except (FileNotFoundError, ValueError):
            pass
        try:
            _os.unlink(lock)
        except Exception:
            pass


# ── socket setup ──────────────────────────────────────────────────────────────

def _setup_socket() -> socket.socket:
    """Create the listening Unix socket with root:entropy-shield 0660 perms."""
    import grp, stat

    os.makedirs(RUNTIME_DIR, exist_ok=True)
    # Directory: root:entropy-shield 0750 (group may traverse, world may not).
    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
    except KeyError:
        gid = -1  # group missing (e.g. running uninstalled) — fall back to root
        sys.stderr.write(
            f"[WARN] group '{SOCKET_GROUP}' not found — socket will be root-only.\n"
        )
    try:
        os.chown(RUNTIME_DIR, 0, gid)
        os.chmod(RUNTIME_DIR, 0o750)
    except OSError:
        pass

    # Remove a stale socket file from a previous unclean shutdown.
    try:
        if stat.S_ISSOCK(os.stat(SOCKET_PATH).st_mode):
            os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Create the socket with a restrictive umask, then tighten owner/group.
    old_umask = os.umask(0o177)  # 0600 until we chown/chmod below
    try:
        srv.bind(SOCKET_PATH)
    finally:
        os.umask(old_umask)
    try:
        os.chown(SOCKET_PATH, 0, gid)
        os.chmod(SOCKET_PATH, 0o660)
    except OSError:
        pass
    srv.listen(4)
    return srv


def _peer_cred(conn: socket.socket) -> tuple[int, int, int]:
    """Return (pid, uid, gid) of the connected peer via SO_PEERCRED."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                            struct.calcsize("3i"))
    return struct.unpack("3i", creds)


# ── per-client session ────────────────────────────────────────────────────────

# Only one privileged session may be active at a time (firewall/Tor are global
# system state).  The accept loop is single-threaded, so this lock is mostly a
# guard against future changes, but it also serialises the shutdown path.
_session_lock = threading.Lock()


def _handle_client(conn: socket.socket) -> None:
    """Serve a single GUI client for the lifetime of its connection.

    Mirrors the legacy runner's command loop, but reads/writes the socket
    instead of stdin/stdout.  A fresh ConnectionManager is created per client;
    when the client disconnects (EOF) any state it created is rolled back.
    """
    from core.connection import ConnectionManager

    rfile = conn.makefile("rb")
    wfile = conn.makefile("wb")

    def _log(msg: str) -> None:
        # Send to the client and to the journal (stdout) simultaneously.
        try:
            wfile.write((msg + "\n").encode())
            wfile.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    try:
        pid, uid, gid = _peer_cred(conn)
        sys.stdout.write(f"[DAEMON] client connected (pid={pid} uid={uid} gid={gid})\n")
        sys.stdout.flush()
        # Read the *desktop user's* settings, not root's.  Identify them from the
        # socket peer credentials.  Also expose the uid via SUDO_UID so helpers
        # that resolve the invoking user's home (e.g. onion_server) keep working.
        if uid > 0:
            from core import config as _config
            _config.use_user_config(uid)
            os.environ["SUDO_UID"] = str(uid)
    except OSError:
        pass

    mgr: "ConnectionManager | None" = None
    connected = False

    with _session_lock:
        _log("[RUNNER] started")

        while True:
            raw = rfile.readline()
            if not raw:
                # Client disconnected — always clean up whatever mgr touched,
                # regardless of whether connect() fully succeeded.
                if mgr is not None:
                    try:
                        mgr.disconnect()
                    except Exception:
                        pass
                return

            cmd = raw.decode(errors="replace").strip()
            if not cmd:
                continue

            # ── ping ──────────────────────────────────────────────────────────
            if cmd == "ping":
                _log("[OK] pong")

            # ── connect ───────────────────────────────────────────────────────
            elif cmd.startswith("connect "):
                try:
                    params = json.loads(cmd[8:])
                    if not isinstance(params, dict):
                        raise ValueError("connect payload must be a JSON object")
                    # Whitelist: ignore any unexpected keys, coerce to bool.
                    safe = {
                        k: bool(params.get(k, False))
                        for k in _ALLOWED_CONNECT_KEYS
                    }
                    mgr = ConnectionManager(_log)
                    mgr.connect(**safe)
                    connected = True
                except Exception as exc:
                    # connect() rolls back internally; keep the session alive so
                    # the GUI can send disconnect to confirm a clean state.
                    _log(f"[ERR] {exc}")

            # ── disconnect ────────────────────────────────────────────────────
            elif cmd == "disconnect":
                if mgr is not None:
                    try:
                        mgr.disconnect()
                    except Exception as exc:
                        _log(f"[ERR] {exc}")
                connected = False
                return

            # ── new_circuit ───────────────────────────────────────────────────
            elif cmd == "new_circuit":
                if mgr is None or not connected:
                    _log("[ERR] Tor is not connected.")
                    continue
                try:
                    mgr._tor.new_circuit()
                    _log("[TOR] New Tor circuit requested.")
                except Exception as exc:
                    _log(f"[ERR] Circuit renewal failed: {exc}")

            # ── circuit_info ──────────────────────────────────────────────────
            elif cmd == "circuit_info":
                if mgr is None or not connected:
                    _log("[CIRCUIT] {}")
                    continue
                try:
                    info = mgr._tor.get_circuit_info()
                    _log(f"[CIRCUIT] {json.dumps(info)}")
                except Exception:
                    _log("[CIRCUIT] {}")

            # ── status ────────────────────────────────────────────────────────
            elif cmd == "status":
                state = {"connected": connected,
                         "layers": getattr(mgr, "_layers", {}) if mgr else {}}
                _log(f"[STATUS] {json.dumps(state)}")

            # ── panic ─────────────────────────────────────────────────────────
            elif cmd == "panic":
                _log("[PANIC] Emergency disconnect…")
                if mgr is not None:
                    try:
                        mgr.disconnect()
                        connected = False
                    except Exception as exc:
                        _log(f"[PANIC] Disconnect error: {exc}")
                        _force_remove_firewall()
                else:
                    _force_remove_firewall()
                _log("[PANIC] Connection closed.")
                return

            # ── unknown ───────────────────────────────────────────────────────
            else:
                _log("[ERR] unknown command")


def _force_remove_firewall() -> None:
    """Last-resort firewall teardown used by the panic path."""
    try:
        import subprocess as _sp
        _sp.run(["nft", "delete", "table", "ip",  "entropy-shield"],
                capture_output=True)
        _sp.run(["nft", "delete", "table", "ip6", "entropy-shield"],
                capture_output=True)
        _sp.run(["iptables", "-F"], capture_output=True)
        _sp.run(["iptables", "-t", "nat", "-F"], capture_output=True)
    except Exception:
        pass


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if os.geteuid() != 0:
        sys.stderr.write("[ERR] entropy-shield daemon must run as root.\n")
        sys.exit(1)

    def _journal(msg: str) -> None:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    _journal("[DAEMON] Entropy Shield privileged daemon starting…")
    _startup_heal(_journal)

    srv = _setup_socket()
    _journal(f"[DAEMON] Listening on {SOCKET_PATH}")

    stop = threading.Event()

    def _shutdown(*_a):
        stop.set()
        try:
            srv.close()  # unblock accept()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    try:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                break  # socket closed by _shutdown()
            try:
                _handle_client(conn)
            except Exception as exc:
                sys.stderr.write(f"[DAEMON] client handler error: {exc}\n")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    finally:
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass
        _journal("[DAEMON] Stopped.")


if __name__ == "__main__":
    main()
