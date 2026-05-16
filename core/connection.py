from __future__ import annotations
import subprocess
from typing import Callable
from .tor          import TorManager, TORRC
from .dnscrypt     import DNSCryptManager
from .i2p          import I2PManager
from .onion_server import OnionServerManager
from .firewall     import FirewallManager
from .platform     import is_nixos


def _resolved_running() -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", "systemd-resolved"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "active"


class ConnectionManager:
    def __init__(self, log: Callable[[str], None]):
        self._log   = log
        self._tor   = TorManager(log)
        self._dns   = DNSCryptManager(log)
        self._i2p   = I2PManager(log)
        self._onion = OnionServerManager(log)
        self._fw    = FirewallManager(log)
        self._layers: dict[str, bool] = {}

    def connect(self, use_tor: bool, use_dnscrypt: bool, use_i2p: bool,
                use_onion_server: bool = False) -> None:
        # Onion server requires Tor
        if use_onion_server:
            use_tor = True

        if not (use_tor or use_dnscrypt or use_i2p):
            raise ValueError("Select at least one privacy layer.")

        if use_tor      and not self._tor.is_installed():
            raise RuntimeError("tor is not installed.")
        if use_dnscrypt and not self._dns.is_installed():
            raise RuntimeError("dnscrypt-proxy is not installed.")
        if use_i2p      and not self._i2p.is_installed():
            raise RuntimeError("i2pd is not installed.")

        self._layers = {}

        # ── configure + start each layer ──────────────────────────
        # Each layer is started before applying the firewall so that
        # the service is ready to accept redirected traffic immediately
        # when the rules go live.

        if use_tor:
            self._tor.configure()
            if use_onion_server and not is_nixos():
                self._onion.configure(TORRC)
                self._onion.start()   # start HTTP file server before Tor routes traffic
            elif use_onion_server and is_nixos():
                self._log(
                    "[ONION] NixOS: torrc is managed by NixOS module — "
                    "configure HiddenService manually in your NixOS config."
                )
            self._tor.start()
            self._layers["tor"] = True
            if use_onion_server:
                self._layers["onion_server"] = True
                addr = self._onion.onion_address(wait=10)
                if addr:
                    self._log(f"[ONION] Your .onion address: {addr}")
                else:
                    self._log(
                        "[ONION] .onion address not yet ready. "
                        f"Check {'/var/lib/tor/entropy-shield-hs/hostname'} once Tor is fully bootstrapped."
                    )

        if use_dnscrypt:
            self._dns.configure()
            self._dns.start()
            self._layers["dnscrypt"] = True

        if use_i2p:
            self._i2p.configure(use_tor=use_tor)
            self._i2p.start()
            self._layers["i2p"] = True

        # ── route system DNS through the active privacy layer ──────
        # Call resolvectl on any system where systemd-resolved is
        # running (not just NixOS) so DNS doesn't bypass the proxy.
        self._apply_dns(use_tor, use_dnscrypt)

        # ── apply firewall rules ───────────────────────────────────
        self._fw.apply(use_tor, use_dnscrypt, use_i2p,
                       i2p_transparent=self._i2p.transparent)
        self._log("[OK] All selected layers are active.")

    def disconnect(self) -> None:
        # Restore DNS before removing firewall rules
        self._restore_dns()

        self._fw.remove()

        if self._layers.get("onion_server"):
            self._onion.stop()
        if self._layers.get("i2p"):
            self._i2p.stop()
        if self._layers.get("dnscrypt"):
            self._dns.stop()
        if self._layers.get("tor"):
            self._tor.stop()

        self._layers.clear()
        self._log("[OK] All layers disconnected.")

    # ── DNS routing via resolvectl ─────────────────────────────────

    def _apply_dns(self, use_tor: bool, use_dnscrypt: bool) -> None:
        """Route system DNS through the active privacy layer.

        Uses resolvectl when systemd-resolved is running so that the
        stub resolver forwards queries to our proxy instead of the
        upstream nameservers.  The nftables/iptables redirect rule
        catches any remaining port-53 traffic as a second safety net.
        """
        if not (is_nixos() or _resolved_running()):
            return
        if use_dnscrypt:
            self._log("[DNS] Routing system DNS → dnscrypt-proxy.")
            self._dns.nixos_redirect_dns()
        elif use_tor:
            self._log("[TOR] Routing system DNS → Tor DNSPort.")
            self._tor.nixos_redirect_dns()

    def _restore_dns(self) -> None:
        if not (is_nixos() or _resolved_running()):
            return
        if self._layers.get("dnscrypt"):
            self._dns.nixos_restore_dns()
        elif self._layers.get("tor"):
            self._tor.nixos_restore_dns()
