from __future__ import annotations
import subprocess
import shutil
import os
import time
from typing import Callable

from .config import cfg
from .platform import is_nixos

_CONFIG_PATHS = [
    "/etc/i2pd/i2pd.conf",
    "/etc/i2p/i2p.conf",
]
_BAK_SUFFIX    = ".entropy-shield.bak"
_MARKER_BEGIN  = "# --- entropy-shield-i2p-begin ---"
_MARKER_END    = "# --- entropy-shield-i2p-end ---"


class I2PManager:
    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self._config: str | None = None
        self._was_active: bool = False
        self._cfg_injected: bool = False

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

        with open(self._config, "r") as f:
            content = f.read()

        content = self._strip_block(content)

        # Build config block.
        # ntcpproxy MUST be in the [ntcp2] section (not appended after other
        # sections — ini parsers assign keys to whatever section is current).
        # We prepend the entire block so it comes before any existing section
        # headers that might be in the original file.
        block_lines = [_MARKER_BEGIN]
        if use_tor:
            block_lines += [
                "[ntcp2]",
                "ntcpproxy = socks://127.0.0.1:9050",
            ]
        block_lines += [
            "[httpproxy]",
            "enabled = true",
            f"port = {http_port}",
            "[socksproxy]",
            "enabled = true",
            f"port = {socks_port}",
            _MARKER_END,
            "",
        ]
        block = "\n".join(block_lines) + "\n"

        with open(self._config, "w") as f:
            f.write(block + content)

        self._cfg_injected = True
        self._log(
            f"[I2P] Configured. HTTP proxy: 127.0.0.1:{http_port}  "
            f"SOCKS: 127.0.0.1:{socks_port}"
            + ("  (via Tor SOCKS)" if use_tor else "")
        )

    def start(self) -> None:
        self._log("[I2P] Starting i2pd...")
        r = subprocess.run(["systemctl", "restart", "i2pd"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start i2pd: {r.stderr.strip()}")
        self._wait_active("i2pd", timeout=15)
        self._log(f"[I2P] i2pd active.")

    def stop(self) -> None:
        self._log("[I2P] Stopping i2pd...")
        subprocess.run(["systemctl", "stop", "i2pd"], capture_output=True)

        if not is_nixos() and self._config:
            bak = self._config + _BAK_SUFFIX
            if os.path.exists(bak):
                shutil.copy2(bak, self._config)
                os.unlink(bak)
            self._cfg_injected = False

        if self._was_active:
            subprocess.run(["systemctl", "start", "i2pd"], capture_output=True)

        self._log("[I2P] i2pd stopped.")

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

    def _find_config(self) -> str:
        for path in _CONFIG_PATHS:
            if os.path.exists(path):
                return path
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
