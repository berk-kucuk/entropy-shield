#!/usr/bin/env python3
"""
Entropy Shield — Privileged Runner
Runs as root (via pkexec or sudo).
Communicates with the GUI process via stdin/stdout:

  stdout → log messages, one per line
  stdin  ← commands: connect <json> | disconnect | new_circuit

Subcommands (one-shot, then exit):
  --setup-nopasswd <user> <python_exe> <runner_path>
  --remove-nopasswd
"""
from __future__ import annotations
import os
import sys
import json

# ── NixOS PATH (pkexec strips PATH; ensure system tools are reachable) ────────
for _p in ("/run/current-system/sw/bin", "/run/wrappers/bin",
           "/nix/var/nix/profiles/default/bin"):
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + ":" + os.environ.get("PATH", "")

# ── allow `from core.xxx import ...` to work regardless of cwd ────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SUDOERS_FILE = "/etc/sudoers.d/entropy-shield"


# ── one-shot subcommands ──────────────────────────────────────────────────────

def _setup_nopasswd(username: str, python_exe: str, runner_path: str) -> None:
    runner_path = os.path.abspath(runner_path)
    python_exe  = os.path.abspath(python_exe)
    content = (
        "# Entropy Shield — passwordless privilege escalation\n"
        "# Remove this file to restore password requirement on connect/disconnect\n"
        f"{username} ALL=(root) NOPASSWD: {python_exe} {runner_path}\n"
    )
    import tempfile, shutil
    os.makedirs("/etc/sudoers.d", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir="/etc/sudoers.d", prefix="entropy-shield-tmp-")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.chmod(tmp, 0o440)
        os.rename(tmp, _SUDOERS_FILE)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _remove_nopasswd() -> None:
    try:
        os.unlink(_SUDOERS_FILE)
    except FileNotFoundError:
        pass


# ── interactive loop ──────────────────────────────────────────────────────────

def _startup_heal(log) -> None:
    """Remove any state left behind by a previous force-killed session.

    Called once at runner startup BEFORE accepting any commands.
    This restores system state so a fresh connect() starts from a clean slate:
      • removes stale nftables tables (firewall rules stuck active)
      • restores dnscrypt-proxy config if a backup was left behind
      • restarts systemd-resolved if it was stopped by the previous session
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
        # Only restart if it's in a 'stopped/failed' state, not if it was
        # intentionally masked by the user.
        r2 = _sp.run(["systemctl", "is-enabled", "systemd-resolved"],
                     capture_output=True, text=True)
        enabled_state = r2.stdout.strip()
        if enabled_state not in ("disabled", "masked"):
            log("[HEAL] Restarting systemd-resolved (was stopped by previous session)…")
            _sp.run(["systemctl", "start", "systemd-resolved"], capture_output=True)

    # ── 4. Kill orphaned Tor and remove its lock file ───────────
    # IMPORTANT: kill the process FIRST, then remove the lock file.
    # Removing the lock without killing leaves the orphan holding
    # ports 9040/9050/9051 so the next connect attempt fails.
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


def _run_loop() -> None:
    from core.connection import ConnectionManager

    def _log(msg: str) -> None:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    mgr: ConnectionManager | None = None
    connected = False

    # Heal any leftover state from a previous force-killed session before
    # accepting commands.  This ensures connect() always starts from clean.
    _startup_heal(_log)

    sys.stdout.write("[RUNNER] started\n")
    sys.stdout.flush()

    while True:
        line = sys.stdin.readline()
        if not line:
            # stdin closed — GUI process exited; always clean up whatever
            # mgr touched, regardless of whether connect() fully succeeded.
            if mgr is not None:
                try:
                    mgr.disconnect()
                except Exception:
                    pass
            sys.exit(0)

        cmd = line.strip()
        if not cmd:
            continue

        # ── connect ──────────────────────────────────────────────────────────
        if cmd.startswith("connect "):
            try:
                params = json.loads(cmd[8:])
                mgr = ConnectionManager(_log)
                mgr.connect(**params)
                connected = True
            except Exception as exc:
                # connect() already rolled back firewall / configs internally;
                # report the error but keep the runner alive so the GUI can
                # send a disconnect command to confirm clean state.
                sys.stdout.write(f"[ERR] {exc}\n")
                sys.stdout.flush()

        # ── disconnect ───────────────────────────────────────────────────────
        elif cmd == "disconnect":
            # Always attempt cleanup even if connect() failed mid-way, since
            # partial state (firewall rules, modified configs) might still exist.
            if mgr is not None:
                try:
                    mgr.disconnect()
                except Exception as exc:
                    sys.stdout.write(f"[ERR] {exc}\n")
                    sys.stdout.flush()
            sys.exit(0)

        # ── new_circuit ──────────────────────────────────────────────────────
        elif cmd == "new_circuit":
            if mgr is None or not connected:
                sys.stdout.write("[ERR] Tor is not connected.\n")
                sys.stdout.flush()
                continue
            try:
                mgr._tor.new_circuit()
                sys.stdout.write("[TOR] New Tor circuit requested.\n")
                sys.stdout.flush()
            except Exception as exc:
                sys.stdout.write(f"[ERR] Circuit renewal failed: {exc}\n")
                sys.stdout.flush()

        # ── circuit_info ─────────────────────────────────────────────────────
        elif cmd == "circuit_info":
            if mgr is None or not connected:
                sys.stdout.write("[CIRCUIT] {}\n")
                sys.stdout.flush()
                continue
            try:
                info = mgr._tor.get_circuit_info()
                sys.stdout.write(f"[CIRCUIT] {json.dumps(info)}\n")
                sys.stdout.flush()
            except Exception as exc:
                sys.stdout.write(f"[CIRCUIT] {{}}\n")
                sys.stdout.flush()

        # ── panic ────────────────────────────────────────────────────────────
        elif cmd == "panic":
            sys.stdout.write("[PANIC] Emergency disconnect…\n")
            sys.stdout.flush()
            if mgr is not None:
                try:
                    mgr.disconnect()
                    connected = False
                except Exception as exc:
                    sys.stdout.write(f"[PANIC] Disconnect error: {exc}\n")
                    sys.stdout.flush()
                    # Force-remove firewall as last resort even if disconnect raised.
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
            else:
                # No manager — just nuke the firewall tables directly.
                try:
                    import subprocess as _sp
                    _sp.run(["nft", "delete", "table", "ip",  "entropy-shield"],
                            capture_output=True)
                    _sp.run(["nft", "delete", "table", "ip6", "entropy-shield"],
                            capture_output=True)
                except Exception:
                    pass
            sys.stdout.write("[PANIC] Connection closed.\n")
            sys.stdout.flush()
            sys.exit(0)


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if os.geteuid() != 0:
        sys.stdout.write(
            "[ERR] This helper must run as root (use pkexec or sudo).\n"
        )
        sys.stdout.flush()
        sys.exit(1)

    args = sys.argv[1:]

    if args and args[0] == "--setup-nopasswd":
        if len(args) < 4:
            sys.stderr.write(
                "Usage: --setup-nopasswd <user> <python_path> <runner_path>\n"
            )
            sys.exit(1)
        _setup_nopasswd(args[1], args[2], args[3])
        sys.stdout.write(
            f"[OK] Passwordless mode enabled (for {args[1]}).\n"
        )
        sys.stdout.flush()
        sys.exit(0)

    if args and args[0] == "--remove-nopasswd":
        _remove_nopasswd()
        sys.stdout.write("[OK] Passwordless mode disabled.\n")
        sys.stdout.flush()
        sys.exit(0)

    if args and args[0] == "--headless":
        _run_headless()
        return

    _run_loop()


# ── headless / systemd service mode ──────────────────────────────────────────

def _run_headless() -> None:
    """Connect using saved config and block until SIGTERM/SIGINT."""
    import signal
    import threading
    from core.connection import ConnectionManager
    from core.config import cfg

    def _log(msg: str) -> None:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    _log("[HEADLESS] Entropy Shield headless mode starting…")

    c = cfg().all()
    use_tor      = True   # headless always uses Tor for protection
    use_dnscrypt = bool(c.get("dnscrypt", {}).get("port"))  # if configured
    use_i2p      = False

    mgr = ConnectionManager(_log)
    try:
        mgr.connect(use_tor=use_tor, use_dnscrypt=use_dnscrypt, use_i2p=False)
    except Exception as exc:
        _log(f"[HEADLESS] Connect failed: {exc}")
        sys.exit(1)

    _log("[HEADLESS] Connected. Waiting for SIGTERM to disconnect…")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT,  lambda *_: stop.set())
    stop.wait()

    _log("[HEADLESS] SIGTERM received — disconnecting…")
    try:
        mgr.disconnect()
    except Exception as exc:
        _log(f"[HEADLESS] Disconnect error: {exc}")
    _log("[HEADLESS] Disconnected. Exiting.")
    sys.exit(0)


if __name__ == "__main__":
    main()
