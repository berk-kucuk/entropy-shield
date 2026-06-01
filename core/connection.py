from __future__ import annotations
import shutil
import subprocess
from typing import Callable
from .tor          import TorManager, TORRC
from .config       import cfg
from .dnscrypt     import DNSCryptManager
from .i2p          import I2PManager
from .onion_server import OnionServerManager
from .firewall     import FirewallManager
from .mac          import MacRandomizer
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
        self._mac   = MacRandomizer(log)
        self._layers: dict[str, bool] = {}

    def connect(self, use_tor: bool, use_dnscrypt: bool, use_i2p: bool,
                use_onion_server: bool = False) -> None:
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
        # Track which services were *configured* so rollback can restore them
        # even if they never reached the started state.
        self._configured = {
            "tor": False, "dnscrypt": False,
            "i2p": False, "onion_server": False,
        }

        try:
            self._do_connect(use_tor, use_dnscrypt, use_i2p, use_onion_server)
        except Exception:
            self._log("[!] Connect failed — rolling back all changes...")
            self._rollback(use_tor, use_dnscrypt, use_i2p, use_onion_server)
            raise

    def _do_connect(self, use_tor: bool, use_dnscrypt: bool, use_i2p: bool,
                    use_onion_server: bool) -> None:
        # ── PHASE 0: MAC address randomization ────────────────────────────────
        # Done before any network activity so Tor bootstraps with the new MAC
        # already visible to the local network.  Best-effort — never raises.
        if cfg().get("mac_randomize"):
            self._mac.randomize()

        # ── PHASE 1: configure every service (nothing started yet) ────────────
        # All configs are written before the firewall goes up so there is
        # zero window where traffic can reach the clearnet.

        if use_tor:
            self._tor.configure()
            self._configured["tor"] = True
            if use_onion_server and not is_nixos():
                self._onion.configure(TORRC)
                self._configured["onion_server"] = True
            elif use_onion_server and is_nixos():
                self._log(
                    "[ONION] NixOS: torrc is managed by NixOS module — "
                    "configure HiddenService manually in your NixOS config."
                )

        if use_dnscrypt:
            # When Tor is also active, route DNSCrypt's upstream queries through
            # Tor SOCKS — DNS is then both encrypted (DNSCrypt) and anonymised (Tor).
            self._dns.configure(
                via_tor=use_tor,
                tor_socks=cfg().get("tor", "socks_port") if use_tor else 9050,
            )
            self._configured["dnscrypt"] = True

        if use_i2p:
            self._i2p.configure(use_tor=use_tor)
            self._configured["i2p"] = True

        # ── PHASE 2: apply firewall rules BEFORE starting any service ─────────
        # This closes the race-condition window where traffic could leak to the
        # clearnet during service bootstrap.  Tor's UID and i2pd's UID are
        # already exempt inside the rules so they can still reach the network.
        #
        # Pre-determine I2P transparent mode: redsocks must be present AND we
        # are in I2P-only mode (not Tor, not DNSCrypt).
        i2p_will_transparent = (
            use_i2p and not use_tor and not use_dnscrypt
            and shutil.which("redsocks") is not None
        )
        self._fw.apply(
            use_tor, use_dnscrypt, use_i2p,
            i2p_transparent=i2p_will_transparent,
        )

        # ── PHASE 3: pre-start dnscrypt-proxy in DNS-only mode ───────────────
        # In DNSCrypt-only mode (no Tor), dnscrypt-proxy has no upstream
        # dependency and can start immediately.  Starting it BEFORE the DNS
        # redirect in PHASE 4 eliminates the window where port 5380 is
        # redirected-to but nothing is bound yet.
        #
        # In Tor+DNSCrypt mode, dnscrypt-proxy routes its upstream queries
        # through Tor SOCKS — it cannot start until Tor is running, so it
        # is started in PHASE 5 (after Tor bootstrap completes).
        if use_dnscrypt and not use_tor:
            self._dns.start()
            self._layers["dnscrypt"] = True

        # ── PHASE 4: route system DNS through the active privacy layer ────────
        # dnscrypt-proxy is already bound when dns-only (started above), so
        # the redirect is seamless.  In Tor mode, nftables redirects port 53
        # to Tor's DNSPort while Tor bootstraps in PHASE 5.
        self._apply_dns(use_tor, use_dnscrypt)

        # ── PHASE 5: start remaining services ────────────────────────────────

        if use_tor:
            # HTTP file server must be up before Tor establishes the HS circuit.
            if use_onion_server and not is_nixos():
                self._onion.start()
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
                        f"Check {'/var/lib/tor/entropy-shield-hs/hostname'} "
                        "once Tor is fully bootstrapped."
                    )

        if use_dnscrypt and use_tor:
            # Tor is now running — dnscrypt-proxy can reach its SOCKS port.
            self._dns.start()
            self._layers["dnscrypt"] = True

        if use_i2p:
            self._i2p.start(transparent=i2p_will_transparent)
            self._layers["i2p"] = True

        if use_tor and use_i2p:
            self._log(
                "[INFO] Tor+I2P mode: clearnet traffic → Tor TransPort. "
                "I2P eepsites accessible via proxy 127.0.0.1:"
                f"{cfg().get('i2p', 'http_port')}. "
                "i2pd tunnels out via Tor SOCKS (NTCP2, SSU2 disabled)."
            )

        self._log("[OK] All selected layers are active.")

    def _rollback(self, use_tor: bool, use_dnscrypt: bool,
                  use_i2p: bool, use_onion_server: bool) -> None:
        """Best-effort reversal of a partial connect().

        Called automatically when connect() raises.  Restores every
        subsystem that was touched, even if it never reached STARTED state
        (e.g. dnscrypt config was modified but service never launched).
        """
        # Restore DNS routing first so resolver is healthy when rules drop.
        # IMPORTANT: _restore_dns() checks self._layers which is empty when
        # a service fails during startup (PHASE 4 — before _layers is
        # populated).  Use _configured instead so DNS is always reverted even
        # if the service never reached the running state.
        try:
            if is_nixos() or _resolved_running():
                if self._configured.get("dnscrypt"):
                    self._dns.nixos_restore_dns()
                elif self._configured.get("tor"):
                    self._tor.nixos_restore_dns()
        except Exception:
            pass
        # Pre-restore dnscrypt: puts config back + restarts resolved
        # before the firewall drops so there is no DNS gap.
        if self._configured.get("dnscrypt"):
            try:
                self._dns.pre_restore()
            except Exception:
                pass
        # Remove firewall rules (restores connectivity).
        try:
            self._fw.remove()
        except Exception:
            pass
        # Restore original MAC addresses.
        try:
            self._mac.restore()
        except Exception:
            pass
        # Stop / restore services in reverse order.
        # stop() is safe to call even when the service was only configured
        # (not started): it handles the "not running" case gracefully.
        if use_onion_server:
            try:
                self._onion.stop()
            except Exception:
                pass
        if use_i2p and self._configured.get("i2p"):
            try:
                self._i2p.stop()
            except Exception:
                pass
        if use_dnscrypt and self._configured.get("dnscrypt"):
            try:
                self._dns.stop()
            except Exception:
                pass
        if use_tor and self._configured.get("tor"):
            try:
                self._tor.stop()
            except Exception:
                pass

    def disconnect(self) -> None:
        # ── PHASE 1: restore DNS routing ──────────────────────────────────
        # For Tor-only: resolvectl revert + drop-in removal (resolved running).
        try:
            self._restore_dns()
        except Exception:
            pass

        # ── PHASE 2: pre-restore DNSCrypt so there is no DNS gap ──────────
        # If dnscrypt had stopped systemd-resolved to free port 53, restart it
        # BEFORE removing nftables.  Once resolved is up, nftables can drop
        # without leaving a window where DNS is unavailable.
        if self._layers.get("dnscrypt"):
            try:
                self._dns.pre_restore()
            except Exception:
                pass

        # ── PHASE 3: remove firewall (DNS is already operational) ─────────
        self._fw.remove()

        # ── PHASE 4: stop remaining services ──────────────────────────────
        if self._layers.get("onion_server"):
            try:
                self._onion.stop()
            except Exception:
                pass
        if self._layers.get("i2p"):
            try:
                self._i2p.stop()
            except Exception:
                pass
        if self._layers.get("dnscrypt"):
            try:
                self._dns.stop()
            except Exception:
                pass
        if self._layers.get("tor"):
            try:
                self._tor.stop()
            except Exception:
                pass

        self._layers.clear()
        try:
            self._mac.restore()
        except Exception:
            pass
        self._log("[OK] All layers disconnected.")

    # ── DNS routing via resolvectl ─────────────────────────────────────────────

    def _apply_dns(self, use_tor: bool, use_dnscrypt: bool) -> None:
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
