from __future__ import annotations
import re
import subprocess
import shutil
import os
import time
from typing import Callable

from .config import cfg
from .platform import is_nixos

_CONFIG_PATHS = [
    "/etc/i2pd/i2pd.conf",
    "/var/lib/i2pd/i2pd.conf",
    "/etc/i2p/i2p.conf",
]
_BAK_SUFFIX    = ".entropy-shield.bak"
_RUN_DIR       = "/run/entropy-shield"
_REDSOCKS_CONF = "/run/entropy-shield/redsocks.conf"

# Port redsocks listens on locally; must match firewall.py's REDSOCKS_PORT
REDSOCKS_PORT = 9070


def _set_section_option(content: str, section: str, key: str, value: str) -> str:
    sec_pat = re.compile(r'^\[' + re.escape(section) + r'\][ \t]*$', re.MULTILINE)
    m = sec_pat.search(content)

    if not m:
        content = content.rstrip("\n") + f"\n\n[{section}]\n{key} = {value}\n"
        return content

    next_sec  = re.search(r'^\[', content[m.end():], re.MULTILINE)
    body_start = m.end()
    body_end   = body_start + next_sec.start() if next_sec else len(content)
    body       = content[body_start:body_end]

    key_re = re.compile(
        r'^[ \t]*#?[ \t]*' + re.escape(key) + r'[ \t]*=.*$',
        re.MULTILINE,
    )
    if key_re.search(body):
        new_body = key_re.sub(f'{key} = {value}', body, count=1)
    else:
        new_body = body.rstrip("\n") + f"\n{key} = {value}\n"

    return content[:body_start] + new_body + content[body_end:]


class I2PManager:
    def __init__(self, log: Callable[[str], None]):
        self._log             = log
        self._config:          str | None                = None
        self._was_active:      bool                      = False
        self._redsocks_proc:   subprocess.Popen | None   = None
        self._transparent:     bool                      = False

    # ── public API ────────────────────────────────────────────

    @property
    def transparent(self) -> bool:
        """True when redsocks is running and transparent proxy is active."""
        return self._transparent

    def is_installed(self) -> bool:
        return bool(shutil.which("i2pd") or shutil.which("i2prouter"))

    def configure(self, use_tor: bool = False) -> None:
        self._log("[I2P] Configuring i2pd...")
        self._was_active = self._service_active("i2pd")

        if is_nixos():
            self._log("[I2P] NixOS: config managed by module — skipping.")
            return

        self._config = self._find_config()
        http_port  = cfg().get("i2p", "http_port")
        socks_port = cfg().get("i2p", "socks_port")

        bak = self._config + _BAK_SUFFIX
        if not os.path.exists(bak):
            shutil.copy2(self._config, bak)

        with open(self._config) as f:
            content = f.read()

        # Always strip the legacy wrong key (left by older versions) so a
        # previously corrupted config is automatically healed on the next run.
        content = re.sub(
            r'^[ \t]*ntcpproxy[ \t]*=.*\n?', '', content, flags=re.MULTILINE
        )

        content = _set_section_option(content, "httpproxy", "enabled", "true")
        content = _set_section_option(content, "httpproxy", "port", str(http_port))
        content = _set_section_option(content, "socksproxy", "enabled", "true")
        content = _set_section_option(content, "socksproxy", "port", str(socks_port))

        max_bw = cfg().get("i2p", "max_bandwidth")
        if max_bw > 0:
            content = _set_section_option(content, "bandwidth", "outbound", str(max_bw))
            content = _set_section_option(content, "bandwidth", "inbound",  str(max_bw))

        if use_tor:
            tor_socks = cfg().get("tor", "socks_port")
            # Route i2pd outbound connections through Tor's SOCKS proxy so
            # I2P peers are reached anonymously (NTCP2 over Tor).
            content = _set_section_option(
                content, "ntcp2", "proxy", f"socks://127.0.0.1:{tor_socks}"
            )
            # Disable SSU2 (UDP-based I2P transport) entirely when Tor is active.
            # SSU2 uses UDP which is blocked by the Tor firewall rules, so i2pd
            # would silently fail SSU2 handshakes. Disabling it prevents false
            # error logs and UDP leak attempts.
            content = _set_section_option(content, "ssu2", "enabled", "false")

        with open(self._config, "w") as f:
            f.write(content)

        bw_note = f"  BW limit: {max_bw} KB/s" if max_bw > 0 else ""
        self._log(
            f"[I2P] Configured. HTTP proxy: 127.0.0.1:{http_port}  "
            f"SOCKS: 127.0.0.1:{socks_port}"
            + ("  (via Tor SOCKS)" if use_tor else "")
            + bw_note
        )

    def start(self, transparent: bool = True) -> None:
        self._log("[I2P] Starting i2pd...")
        r = subprocess.run(["systemctl", "restart", "i2pd"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start i2pd: {r.stderr.strip()}")
        self._wait_active("i2pd", timeout=15)
        self._log("[I2P] i2pd active.")

        if transparent and shutil.which("redsocks"):
            self._start_redsocks()
        else:
            self._transparent = False
            if transparent:
                self._log(
                    "[I2P] redsocks not installed — running in proxy-only mode. "
                    "Install redsocks (paru -S redsocks) for full transparent routing."
                )

    def stop(self) -> None:
        self._stop_redsocks()

        self._log("[I2P] Stopping i2pd...")
        subprocess.run(["systemctl", "stop", "i2pd"], capture_output=True)

        if not is_nixos() and self._config:
            bak = self._config + _BAK_SUFFIX
            if os.path.exists(bak):
                shutil.copy2(bak, self._config)
                os.unlink(bak)

        if self._was_active:
            subprocess.run(["systemctl", "start", "i2pd"], capture_output=True)

        self._log("[I2P] i2pd stopped.")

    # ── redsocks ──────────────────────────────────────────────

    def _start_redsocks(self) -> None:
        socks_port = cfg().get("i2p", "socks_port")
        conf = (
            "base {\n"
            "    log_debug = off;\n"
            "    log_info  = off;\n"
            '    log       = "stderr";\n'
            "    daemon    = off;\n"
            "    redirector = iptables;\n"
            "}\n"
            "\n"
            "redsocks {\n"
            "    local_ip   = 127.0.0.1;\n"
            f"    local_port = {REDSOCKS_PORT};\n"
            "    ip         = 127.0.0.1;\n"
            f"    port       = {socks_port};\n"
            "    type       = socks5;\n"
            "}\n"
        )
        os.makedirs(_RUN_DIR, mode=0o700, exist_ok=True)
        with open(_REDSOCKS_CONF, "w") as f:
            f.write(conf)
        os.chmod(_REDSOCKS_CONF, 0o600)

        self._redsocks_proc = subprocess.Popen(
            ["redsocks", "-c", _REDSOCKS_CONF],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.4)
        if self._redsocks_proc.poll() is not None:
            err = self._redsocks_proc.stderr.read().decode(errors="replace").strip()
            self._redsocks_proc = None
            raise RuntimeError(f"redsocks failed to start: {err or '(no output)'}")

        self._transparent = True
        self._log(
            f"[I2P] redsocks transparent proxy active "
            f"(:{REDSOCKS_PORT} → i2pd SOCKS :{socks_port})."
        )

    def _stop_redsocks(self) -> None:
        if self._redsocks_proc:
            if self._redsocks_proc.poll() is None:
                self._redsocks_proc.terminate()
                try:
                    self._redsocks_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._redsocks_proc.kill()
            self._redsocks_proc = None
        self._transparent = False
        try:
            os.unlink(_REDSOCKS_CONF)
        except FileNotFoundError:
            pass

    # ── helpers ───────────────────────────────────────────────

    def _find_config(self) -> str:
        for path in _CONFIG_PATHS:
            resolved = os.path.realpath(path)
            if os.path.exists(resolved):
                return resolved
        raise RuntimeError("i2pd config not found. Is i2pd installed?")

    def _service_active(self, name: str) -> bool:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True)
        return r.stdout.strip() == "active"

    def _wait_active(self, name: str, timeout: int = 15) -> None:
        for _ in range(timeout):
            if self._service_active(name):
                return
            time.sleep(1)
        raise RuntimeError(f"{name} did not become active within {timeout}s.")
