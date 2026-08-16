"""Configuration store — with strict validation of every value.

SECURITY
--------
``config.json`` lives in the *desktop user's* home directory and is fully
writable by that user, but the **root daemon reads it** (see
:func:`use_user_config`) and feeds the values into privileged operations:

  * ports are written into the torrc that root-launched Tor parses
  * ports are interpolated into the ``nft -f -`` script
  * ports are written into dnscrypt-proxy's TOML and i2pd's conf

An unvalidated value is therefore a code-injection channel into root's
configuration files.  A string such as::

    "trans_port": "9040\\nClientTransportPlugin x exec /tmp/evil"

would inject an extra torrc directive that makes Tor execute an arbitrary
binary.  The GUI's spin boxes are *not* a security boundary — anyone in the
``entropy-shield`` group can edit the JSON by hand.

Every value loaded from disk is therefore coerced against the schema below.
Anything of the wrong type, out of range, or containing characters that are
meaningless for the field is discarded and the built-in default is used.  This
is the single choke point: both the GUI and the daemon read through
:func:`cfg`, so no caller can see an unvalidated value.
"""
from __future__ import annotations
import json
import os
import re
import stat
from pathlib import Path

_DIR  = Path.home() / ".config" / "entropy-shield"
_FILE = _DIR / "config.json"

# A config file is a few KB at most.  Cap the read so a huge (or endless) file
# planted at the config path cannot exhaust the daemon's memory.
_MAX_CONFIG_BYTES = 256 * 1024

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
    "update_check":             True,
    "kill_switch":              True,
    "auto_connect":             False,
    "autostart":                True,
    "mac_randomize":            False,
    "doh_block":                True,
    "circuit_renewal_minutes":  0,
}


# ── field validators ──────────────────────────────────────────────────────────
#
# Each validator receives the raw value from the file and returns a clean value,
# or raises ValueError/TypeError to fall back to the default.

class _Invalid(ValueError):
    """Raised by a validator to signal 'use the default instead'."""


def _port(v):
    """A TCP/UDP port number.  Rejects bools, strings and out-of-range ints."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise _Invalid("port must be an integer")
    if not (1 <= v <= 65535):
        raise _Invalid("port out of range")
    return v


def _int_range(lo: int, hi: int):
    def _check(v):
        if not isinstance(v, int) or isinstance(v, bool):
            raise _Invalid("expected an integer")
        if not (lo <= v <= hi):
            raise _Invalid("integer out of range")
        return v
    return _check


def _enum(*allowed: str):
    def _check(v):
        if v not in allowed:
            raise _Invalid("value not in the allowed set")
        return v
    return _check


# Exit-node tokens: 2-letter country codes, relay nicknames (1-19 alphanumerics)
# and $-prefixed 40-hex-digit fingerprints.  Everything else is dropped.
_EXIT_COUNTRY = re.compile(r"^[A-Za-z]{2}$")
_EXIT_NICK    = re.compile(r"^[A-Za-z0-9]{1,19}$")
_EXIT_FP      = re.compile(r"^\$[0-9A-Fa-f]{40}$")


def _exit_nodes(v):
    """Normalise the ExitNodes list to Tor's own syntax.

    Accepts what the UI's placeholder suggests (``{de},{nl}``) as well as the
    looser ``de,nl`` a user is likely to type, and emits the canonical
    ``{de},{nl}`` form.  Tokens that match none of the three legal shapes are
    dropped rather than passed through — this is what keeps a crafted value
    from reaching the torrc.
    """
    if not isinstance(v, str):
        raise _Invalid("exit_nodes must be a string")
    out: list[str] = []
    for tok in v.replace("{", " ").replace("}", " ").replace(",", " ").split():
        if _EXIT_FP.match(tok):
            out.append(tok)
        elif _EXIT_COUNTRY.match(tok):
            out.append("{" + tok.lower() + "}")
        elif _EXIT_NICK.match(tok):
            out.append(tok)
        # anything else: silently dropped
    return ",".join(out[:64])


# A bridge line is an address, a fingerprint and transport key=value pairs.
# obfs4 certs are base64, hence '+', '/' and '='.  IPv6 addresses need brackets.
_BRIDGE_ALLOWED = re.compile(r"^[A-Za-z0-9 .:_/=+\[\],-]{1,512}$")


def _bridge_lines(v):
    if not isinstance(v, list):
        raise _Invalid("bridges.lines must be a list")
    out: list[str] = []
    for item in v[:64]:
        if not isinstance(item, str):
            continue
        line = item.strip()
        if line and _BRIDGE_ALLOWED.match(line):
            out.append(line)
    return out


_USERNAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")


def _routing_rules(v):
    """Validate per-app routing rules.

    ``uid_or_user`` ends up in an nftables ``meta skuid`` expression, so it must
    be either a plain decimal uid or a POSIX-portable user name.  Note that
    ``str.isdigit()`` is true for non-ASCII digits ("٥"), which would produce a
    broken rule — the explicit character class avoids that.
    """
    if not isinstance(v, list):
        raise _Invalid("per_app_routing.rules must be a list")
    out: list[dict] = []
    for item in v[:128]:
        if not isinstance(item, dict):
            continue
        who = item.get("uid_or_user", "")
        if not isinstance(who, str):
            continue
        who = who.strip()
        if not (who.isascii() and who.isdigit()) and not _USERNAME.match(who):
            continue
        if who.isdigit() and int(who) > 0xFFFFFFFF:
            continue
        action = item.get("action", "tor")
        if action not in ("tor", "direct", "block"):
            action = "tor"
        name = item.get("name", "")
        name = _clean_line(name)[:64] if isinstance(name, str) else ""
        out.append({"name": name, "uid_or_user": who, "action": action})
    return out


def _clean_line(s: str) -> str:
    """Collapse control characters so a value can never span two config lines."""
    return "".join(" " if (ord(c) < 0x20 or ord(c) == 0x7F) else c for c in s)


def _serve_dir(v):
    """Directory served by the onion HTTP server.

    The server itself drops privileges to the invoking user, so this is not a
    privilege boundary — but the path must still be a sane single-line absolute
    path before it is logged and passed to the child process.
    """
    if not isinstance(v, str):
        raise _Invalid("serve_dir must be a string")
    p = _clean_line(v).strip()
    if not p:
        return ""
    if not p.startswith("/") or "\x00" in p or len(p) > 4096:
        raise _Invalid("serve_dir must be an absolute path")
    return p


# Path → validator.  Any key not listed here is checked against the *type* of
# its default value (see _coerce), which is enough for the plain booleans.
_VALIDATORS = {
    ("theme",):                          _enum("oled", "dark", "light",
                                               "binary", "circuit", "pixel"),
    ("tor", "trans_port"):               _port,
    ("tor", "dns_port"):                 _port,
    ("tor", "socks_port"):               _port,
    ("tor", "control_port"):             _port,
    ("tor", "exit_nodes"):               _exit_nodes,
    ("bridges", "transport"):            _enum("obfs4", "meek-azure",
                                               "snowflake", "manual"),
    ("bridges", "lines"):                _bridge_lines,
    ("dnscrypt", "port"):                _port,
    ("i2p", "http_port"):                _port,
    ("i2p", "socks_port"):               _port,
    ("i2p", "max_bandwidth"):            _int_range(0, 1_000_000),
    ("onion_server", "local_port"):      _port,
    ("onion_server", "hs_port"):         _port,
    ("onion_server", "serve_dir"):       _serve_dir,
    ("per_app_routing", "rules"):        _routing_rules,
    ("auto_reconnect", "delay_seconds"): _int_range(1, 3600),
    ("auto_reconnect", "max_attempts"):  _int_range(0, 100),
    ("circuit_renewal_minutes",):        _int_range(0, 10080),
}


def _coerce(default, value, path: tuple[str, ...]):
    """Return a validated *value*, or *default* if it fails validation."""
    validator = _VALIDATORS.get(path)
    if validator is not None:
        try:
            return validator(value)
        except (ValueError, TypeError):
            return default
    # No explicit validator: the value must at least have the default's type.
    # bool is checked first because bool is a subclass of int.
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return _clean_line(value)[:512] if isinstance(value, str) else default
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    return value


def _merge_validated(base: dict, override: dict,
                     path: tuple[str, ...] = ()) -> dict:
    """Merge *override* into *base*, keeping ONLY keys defined in base.

    Keys in override that are not in base (deprecated keys) are silently
    dropped, and every surviving value is run through :func:`_coerce`.
    """
    result = dict(base)
    if not isinstance(override, dict):
        return result
    for k, v in override.items():
        if k not in result:
            continue  # drop deprecated / unknown keys
        sub = path + (k,)
        if isinstance(result[k], dict):
            result[k] = _merge_validated(result[k], v, sub) if isinstance(v, dict) \
                else result[k]
        else:
            result[k] = _coerce(result[k], v, sub)
    return result


class Config:
    def __init__(self) -> None:
        self._data = _merge_validated(_DEFAULTS, self._load())

    def _load(self) -> dict:
        """Read config.json defensively.

        The daemon runs this as root against a path inside the *user's* home,
        so the open must not follow a symlink into a file root can read but the
        user cannot, and must not block on a FIFO planted at the config path
        (that would hang the single-threaded daemon for every user).
        """
        try:
            fd = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return {}
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return {}
            # Clear O_NONBLOCK now that we know it is a regular file, so the
            # read behaves normally.
            os.set_blocking(fd, True)
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
                fd = -1  # ownership passed to the file object
                raw = f.read(_MAX_CONFIG_BYTES)
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def save(self) -> None:
        _DIR.mkdir(parents=True, exist_ok=True)
        # Bridge lines are censorship-circumvention secrets — keep the config
        # readable only by its owner.
        try:
            os.chmod(_DIR, 0o700)
        except OSError:
            pass
        # Write to a temp file then rename atomically so a crash mid-write
        # never leaves a corrupted config.json.
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
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
        d[keys[-1]] = _coerce(_default_for(tuple(keys)), value, tuple(keys))

    def all(self) -> dict:
        return self._data


def _default_for(path: tuple[str, ...]):
    """Return the built-in default at *path* (used to type-check set())."""
    d = _DEFAULTS
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


_instance: "Config | None" = None


def cfg() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance


def cfg_port(*keys) -> int:
    """Return the port at *keys*, re-checked immediately before use.

    :class:`Config` already validates on load, so this is defence in depth: it
    guarantees that every value which reaches a torrc, an nftables script or a
    dnscrypt/i2pd config file is a plain in-range integer, even if a future
    caller mutates the live config object or a new field is added without a
    validator.  A bad value falls back to the built-in default rather than
    raising, so a hand-edited config cannot break connect().
    """
    value = cfg().get(*keys)
    try:
        return _port(value)
    except (ValueError, TypeError):
        fallback = _default_for(tuple(keys))
        return fallback if isinstance(fallback, int) else 0


def use_user_config(uid: int) -> None:
    """Rebind the config location to *uid*'s home directory.

    The privileged daemon runs as root, so ``Path.home()`` would resolve to
    ``/root`` and it would ignore the desktop user's GUI settings.  The daemon
    learns the connecting user's uid from the socket peer credentials and calls
    this so it reads ``~user/.config/entropy-shield/config.json`` instead.
    """
    global _DIR, _FILE, _instance
    import pwd
    try:
        home = Path(pwd.getpwuid(uid).pw_dir)
    except KeyError:
        return
    _DIR  = home / ".config" / "entropy-shield"
    _FILE = _DIR / "config.json"
    _instance = None  # force reload from the new location on next cfg()
