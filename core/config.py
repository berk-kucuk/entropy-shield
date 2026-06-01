from __future__ import annotations
import json
from pathlib import Path

_DIR  = Path.home() / ".config" / "entropy-shield"
_FILE = _DIR / "config.json"

_DEFAULTS: dict = {
    "theme": "oled",
    "tor": {
        "trans_port":    9040,
        "dns_port":      5300,
        "socks_port":    9050,
        "control_port":  9051,
        "exit_nodes":    "",
        "strict_nodes":  False,
    },
    "bridges": {
        "enabled":   False,
        "transport": "obfs4",   # obfs4 | meek-azure | snowflake | manual
        "lines":     [],        # list of "Bridge ..." strings
    },
    "dnscrypt": {
        "port":              5380,  # 5353 is reserved for mDNS (avahi-daemon)
        "require_dnssec":    False,
        "require_nolog":     True,
        "require_nofilter":  True,
    },
    "i2p": {
        "http_port":     4444,
        "socks_port":    4447,
        "max_bandwidth": 0,
    },
    "onion_server": {
        "local_port": 8080,
        "hs_port":    80,
        "serve_dir":  "",
    },
    "per_app_routing": {
        "enabled":    False,
        "rules":      [],  # [{name, uid_or_user, action}]  action: tor|direct|block
    },
    "auto_reconnect": {
        "enabled":         True,
        "delay_seconds":   15,
        "max_attempts":    3,
    },
    "update_check":   True,
    "kill_switch":    True,
    "auto_connect":   False,
    "autostart":      True,
    "mac_randomize":  False,
    "doh_block":      True,
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, keeping ONLY keys defined in base.
    Keys in override that are not in base (deprecated keys) are silently dropped.
    """
    result = dict(base)
    for k, v in override.items():
        if k not in result:
            continue  # drop deprecated / unknown keys
        if isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class Config:
    def __init__(self) -> None:
        self._data = _deep_merge(_DEFAULTS, self._load())

    def _load(self) -> dict:
        if _FILE.exists():
            try:
                return json.loads(_FILE.read_text())
            except Exception:
                return {}
        return {}

    def save(self) -> None:
        _DIR.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename atomically so a crash mid-write
        # never leaves a corrupted config.json.
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(_FILE)

    def get(self, *keys):
        d = self._data
        for k in keys:
            d = d[k]
        return d

    def set(self, *keys_and_value) -> None:
        *keys, value = keys_and_value
        d = self._data
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value

    def all(self) -> dict:
        return self._data


_instance: "Config | None" = None


def cfg() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
