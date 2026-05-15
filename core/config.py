from __future__ import annotations
import json
from pathlib import Path

_DIR  = Path.home() / ".config" / "entropy-shield"
_FILE = _DIR / "config.json"

_DEFAULTS: dict = {
    "theme": "dark",
    "tor": {
        "trans_port":   9040,
        "dns_port":     5300,
        "socks_port":   9050,
        "exit_nodes":   "",
        "strict_nodes": False,
    },
    "dnscrypt": {
        "port":              5300,
        "require_dnssec":    False,
        "require_nolog":     True,
        "require_nofilter":  True,
    },
    "i2p": {
        "http_port":     4444,
        "socks_port":    4447,
        "max_bandwidth": 0,
    },
    "lokinet": {
        "socks_port": 1090,
        "exit_node":  "",
        "use_exit":   False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
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
        _FILE.write_text(json.dumps(self._data, indent=2))

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
