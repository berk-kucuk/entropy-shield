from __future__ import annotations
import subprocess
import shutil
import time
import os
from typing import Callable

from .config import cfg
from .platform import is_nixos

TORRC      = "/etc/tor/torrc"
TORRC_BAK  = "/etc/tor/torrc.entropy-shield.bak"
RESOLV     = "/etc/resolv.conf"
RESOLV_BAK = "/etc/resolv.conf.entropy-shield.bak"

_MARKER_BEGIN = "# --- entropy-shield-begin ---"
_MARKER_END   = "# --- entropy-shield-end ---"

_TORRC_TEMPLATE = """
# --- entropy-shield-begin ---
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
TransPort  127.0.0.1:{trans_port}
DNSPort    127.0.0.1:{dns_port}
SocksPort  127.0.0.1:{socks_port}
{exit_line}
{strict_line}
# --- entropy-shield-end ---
"""


class TorManager:
    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self._was_active: bool = False

    def is_installed(self) -> bool:
        return bool(shutil.which("tor"))

    def configure(self) -> None:
        self._log("[TOR] Configuring torrc...")
        self._was_active = self._service_active("tor")

        if is_nixos():
            self._log(
                "[TOR] NixOS detected — torrc is managed by the NixOS module. "
                "Skipping config file modification."
            )
            return

        if not os.path.exists(TORRC):
            raise RuntimeError("torrc not found. Is tor installed?")

        trans  = cfg().get("tor", "trans_port")
        dns    = cfg().get("tor", "dns_port")
        socks  = cfg().get("tor", "socks_port")
        exits  = cfg().get("tor", "exit_nodes").strip()
        strict = cfg().get("tor", "strict_nodes")

        block = _TORRC_TEMPLATE.format(
            trans_port  = trans,
            dns_port    = dns,
            socks_port  = socks,
            exit_line   = f"ExitNodes {{{exits}}}" if exits else "",
            strict_line = "StrictNodes 1" if strict else "",
        )

        shutil.copy2(TORRC, TORRC_BAK)
        with open(TORRC, "r") as f:
            content = f.read()

        content = self._strip_block(content)
        with open(TORRC, "w") as f:
            f.write(content)
            f.write(block)

        if not os.path.exists(RESOLV_BAK):
            shutil.copy2(RESOLV, RESOLV_BAK)
        with open(RESOLV, "w") as f:
            f.write("nameserver 127.0.0.1\n")

        self._log("[TOR] torrc updated.")

    def start(self) -> None:
        self._log("[TOR] Starting tor service...")
        self._service_restart("tor")
        self._wait_active("tor", timeout=30)
        self._log("[TOR] Tor is active.")

    def stop(self) -> None:
        if is_nixos():
            self._log("[TOR] NixOS: Tor is a system service — leaving it running.")
            return

        self._log("[TOR] Stopping tor service...")

        subprocess.run(["systemctl", "stop", "tor"], capture_output=True)

        if os.path.exists(TORRC_BAK):
            shutil.copy2(TORRC_BAK, TORRC)
            os.unlink(TORRC_BAK)

        if os.path.exists(RESOLV_BAK):
            shutil.copy2(RESOLV_BAK, RESOLV)
            os.unlink(RESOLV_BAK)

        if self._was_active:
            subprocess.run(["systemctl", "start", "tor"], capture_output=True)

        self._log("[TOR] Tor stopped and config restored.")

    def _strip_block(self, content: str) -> str:
        lines  = content.splitlines(keepends=True)
        out    = []
        inside = False
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

    # ── NixOS public DNS control ─────────────────────────────────

    def nixos_redirect_dns(self) -> None:
        """Route system DNS through Tor's DNSPort via systemd-resolved."""
        self._nixos_redirect_dns()

    def nixos_restore_dns(self) -> None:
        """Revert systemd-resolved DNS to system defaults."""
        self._nixos_restore_dns()

    # ── NixOS helpers ────────────────────────────────────────────

    _DROPIN_DIR  = "/run/systemd/resolved.conf.d"
    _DROPIN_FILE = "/run/systemd/resolved.conf.d/entropy-shield.conf"

    def _nixos_ifaces(self) -> list[str]:
        r = subprocess.run(
            ["ip", "-o", "link", "show", "up"],
            capture_output=True, text=True,
        )
        ifaces = []
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2:
                iface = parts[1].strip().split("@")[0].strip()
                if iface and iface != "lo":
                    ifaces.append(iface)
        return ifaces

    def _resolved_running(self) -> bool:
        r = subprocess.run(
            ["systemctl", "is-active", "systemd-resolved"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() == "active"

    def _nixos_redirect_dns(self) -> None:
        """
        Route all system DNS through Tor's DNSPort.

        When systemd-resolved is running: write drop-in + per-interface config.
        When not running: nftables rules redirect port 53 → Tor DNSPort already.
        """
        dns_port = cfg().get("tor", "dns_port")
        dns_addr = f"127.0.0.1:{dns_port}"

        if not self._resolved_running():
            self._log(f"[TOR] systemd-resolved not active — "
                      f"nftables handles DNS redirect to {dns_addr}.")
            return

        # ── 1. persistent drop-in ──────────────────────────────
        os.makedirs(self._DROPIN_DIR, exist_ok=True)
        with open(self._DROPIN_FILE, "w") as f:
            f.write("[Resolve]\n")
            f.write(f"DNS={dns_addr}\n")
            f.write("Domains=~.\n")

        # ── 2. per-interface (immediate) ───────────────────────
        for iface in self._nixos_ifaces():
            subprocess.run(
                ["resolvectl", "dns", iface, dns_addr],
                capture_output=True,
            )
            subprocess.run(
                ["resolvectl", "domain", iface, "~."],
                capture_output=True,
            )

        subprocess.run(
            ["systemctl", "reload", "systemd-resolved"],
            capture_output=True,
        )
        self._log(f"[TOR] System DNS → Tor DNSPort ({dns_addr}).")

    def _nixos_restore_dns(self) -> None:
        if not self._resolved_running():
            return

        if os.path.exists(self._DROPIN_FILE):
            os.unlink(self._DROPIN_FILE)

        for iface in self._nixos_ifaces():
            subprocess.run(
                ["resolvectl", "revert", iface],
                capture_output=True,
            )

        subprocess.run(
            ["systemctl", "reload", "systemd-resolved"],
            capture_output=True,
        )
        self._log("[TOR] DNS settings reverted to system defaults.")

    # ── internal helpers ─────────────────────────────────────────

    def _service_active(self, name: str) -> bool:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True)
        return r.stdout.strip() == "active"

    def _service_restart(self, name: str) -> None:
        r = subprocess.run(["systemctl", "restart", name],
                           capture_output=True, text=True)
        if r.returncode != 0:
            r2 = subprocess.run(["service", name, "restart"],
                                capture_output=True, text=True)
            if r2.returncode != 0:
                raise RuntimeError(f"Failed to start {name}: {r2.stderr.strip()}")

    def _wait_active(self, name: str, timeout: int = 30) -> None:
        for _ in range(timeout):
            if self._service_active(name):
                return
            time.sleep(1)
        raise RuntimeError(f"{name} did not become active within {timeout}s.")
