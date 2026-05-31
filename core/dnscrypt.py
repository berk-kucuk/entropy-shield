from __future__ import annotations
import subprocess
import shutil
import re
import os
import time
from typing import Callable

from .config import cfg
from .platform import is_nixos

_CONFIG_PATHS = [
    "/etc/dnscrypt-proxy/dnscrypt-proxy.toml",
    "/etc/dnscrypt-proxy.toml",
]
_BAK_SUFFIX   = ".entropy-shield.bak"
_LISTEN_RE    = re.compile(r"^listen_addresses\s*=.*$", re.MULTILINE)
_DNSSEC_RE    = re.compile(r"^require_dnssec\s*=.*$", re.MULTILINE)
_NOLOG_RE     = re.compile(r"^require_nolog\s*=.*$", re.MULTILINE)
_NOFILTER_RE  = re.compile(r"^require_nofilter\s*=.*$", re.MULTILINE)
_PROXY_RE     = re.compile(r"^#?\s*proxy\s*=.*$", re.MULTILINE)


class DNSCryptManager:
    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self._config: str | None = None
        self._was_active: bool = False
        self._resolved_was_active: bool = False

    def is_installed(self) -> bool:
        return bool(shutil.which("dnscrypt-proxy"))

    def configure(self, via_tor: bool = False, tor_socks: int = 9050) -> None:
        self._log("[DNS] Configuring dnscrypt-proxy…")
        self._was_active = self._service_active("dnscrypt-proxy")
        self._resolved_was_active = self._service_active("systemd-resolved")

        if is_nixos():
            self._log("[DNS] NixOS: config managed by module — skipping file edit.")
            return

        self._config = self._find_config()
        port        = cfg().get("dnscrypt", "port")
        listen_line = f"listen_addresses = ['127.0.0.1:{port}', '[::1]:{port}']"

        dnssec   = "true" if cfg().get("dnscrypt", "require_dnssec")   else "false"
        nolog    = "true" if cfg().get("dnscrypt", "require_nolog")     else "false"
        nofilter = "true" if cfg().get("dnscrypt", "require_nofilter")  else "false"

        bak = self._config + _BAK_SUFFIX
        if not os.path.exists(bak):
            shutil.copy2(self._config, bak)

        with open(self._config, "r") as f:
            content = f.read()

        def _replace_or_append(text: str, pattern: re.Pattern, key: str, val: str) -> str:
            line = f"{key} = {val}"
            if pattern.search(text):
                return pattern.sub(line, text)
            return text + f"\n{line}\n"

        if _LISTEN_RE.search(content):
            content = _LISTEN_RE.sub(listen_line, content)
        else:
            content += f"\n{listen_line}\n"

        content = _replace_or_append(content, _DNSSEC_RE,   "require_dnssec",   dnssec)
        content = _replace_or_append(content, _NOLOG_RE,    "require_nolog",    nolog)
        content = _replace_or_append(content, _NOFILTER_RE, "require_nofilter", nofilter)

        if via_tor:
            # Route DNSCrypt upstream queries through Tor SOCKS so DNS is
            # both encrypted (DNSCrypt) AND anonymised (Tor network).
            proxy_val = f"'socks5://127.0.0.1:{tor_socks}'"
            content = _replace_or_append(content, _PROXY_RE, "proxy", proxy_val)
            self._log(f"[DNS] dnscrypt-proxy upstream → Tor SOCKS :{tor_socks}")
        else:
            # Remove any leftover proxy setting from a previous Tor+DNS session.
            content = _PROXY_RE.sub("", content)

        with open(self._config, "w") as f:
            f.write(content)

        if self._resolved_was_active:
            self._log("[DNS] Stopping systemd-resolved to free port 53…")
            subprocess.run(["systemctl", "stop", "systemd-resolved"],
                           capture_output=True)

        self._log(f"[DNS] dnscrypt-proxy configured on port {port}.")

    def start(self) -> None:
        self._log("[DNS] Starting dnscrypt-proxy...")
        r = subprocess.run(["systemctl", "restart", "dnscrypt-proxy"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start dnscrypt-proxy: {r.stderr.strip()}")
        self._wait_active("dnscrypt-proxy", timeout=30)
        self._log("[DNS] dnscrypt-proxy active.")

    def pre_restore(self) -> None:
        """Restore config and restart systemd-resolved BEFORE nftables is removed.

        Called by ConnectionManager.disconnect() so DNS is immediately available
        once the firewall rules drop — no gap between nftables removal and resolver
        becoming ready.  stop() skips the steps already done here.
        """
        if is_nixos():
            return
        if self._config:
            bak = self._config + _BAK_SUFFIX
            if os.path.exists(bak):
                shutil.copy2(bak, self._config)
                os.unlink(bak)
                self._config = None  # signal stop() to skip config restore
        if self._resolved_was_active:
            subprocess.run(["systemctl", "start", "systemd-resolved"],
                           capture_output=True)
            self._resolved_was_active = False  # signal stop() to skip restart

    def stop(self) -> None:
        if is_nixos():
            subprocess.run(["systemctl", "stop", "dnscrypt-proxy"],
                           capture_output=True)
            self._log("[DNS] dnscrypt-proxy stopped.")
            return

        self._log("[DNS] Stopping dnscrypt-proxy...")
        subprocess.run(["systemctl", "stop", "dnscrypt-proxy"], capture_output=True)

        # Restore config and restart resolved only if pre_restore() wasn't called.
        if self._config:
            bak = self._config + _BAK_SUFFIX
            if os.path.exists(bak):
                shutil.copy2(bak, self._config)
                os.unlink(bak)

        if self._resolved_was_active:
            subprocess.run(["systemctl", "start", "systemd-resolved"],
                           capture_output=True)

        if self._was_active:
            subprocess.run(["systemctl", "start", "dnscrypt-proxy"],
                           capture_output=True)

        self._log("[DNS] dnscrypt-proxy stopped and config restored.")

    # ── NixOS public DNS control ─────────────────────────────────

    def nixos_redirect_dns(self) -> None:
        """Route system DNS through dnscrypt-proxy via systemd-resolved."""
        self._nixos_redirect_dns()

    def nixos_restore_dns(self) -> None:
        """Revert systemd-resolved DNS to system defaults."""
        self._nixos_restore_dns()

    # ── NixOS helpers ────────────────────────────────────────────

    _DROPIN_DIR  = "/run/systemd/resolved.conf.d"
    _DROPIN_FILE = "/run/systemd/resolved.conf.d/entropy-shield.conf"

    def _nixos_ifaces(self) -> list[str]:
        """Return UP network interfaces (excluding loopback)."""
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
        Route all system DNS through dnscrypt-proxy.

        When systemd-resolved is running: write a resolved.conf.d drop-in
        and set per-interface DNS so every query goes through dnscrypt-proxy.

        When systemd-resolved is NOT running: nftables rules (applied by
        FirewallManager) already redirect port 53 → 5353, so nothing extra
        is needed here.
        """
        port = cfg().get("dnscrypt", "port")
        dns_addr = f"127.0.0.1:{port}"

        if not self._resolved_running():
            self._log(f"[DNS] systemd-resolved not active — "
                      f"nftables handles DNS redirect to {dns_addr}.")
            return

        # ── 1. persistent drop-in ──────────────────────────────
        os.makedirs(self._DROPIN_DIR, exist_ok=True)
        with open(self._DROPIN_FILE, "w") as f:
            f.write("[Resolve]\n")
            f.write(f"DNS={dns_addr}\n")
            f.write("Domains=~.\n")

        # ── 2. per-interface (immediate, ~. routing domain) ────
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
        self._log(f"[DNS] System DNS → dnscrypt-proxy ({dns_addr}).")

    def _nixos_restore_dns(self) -> None:
        """Remove drop-in and revert per-interface DNS to system defaults."""
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
        self._log("[DNS] DNS settings reverted to system defaults.")

    # ── internal helpers ─────────────────────────────────────────

    def _find_config(self) -> str:
        for path in _CONFIG_PATHS:
            if os.path.exists(path):
                return path
        raise RuntimeError("dnscrypt-proxy config not found. Is it installed?")

    def _service_active(self, name: str) -> bool:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True)
        return r.stdout.strip() == "active"

    def _wait_active(self, name: str, timeout: int = 30) -> None:
        for _ in range(timeout):
            if self._service_active(name):
                return
            time.sleep(1)
        raise RuntimeError(f"{name} did not become active within {timeout}s.")
