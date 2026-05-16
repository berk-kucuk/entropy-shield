#!/usr/bin/env bash
set -euo pipefail

# ── privilege check ───────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "[*] Root required. Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"
DEST=/opt/entropy-shield
NIXOS_DIR=/etc/nixos
MODULE_DEST="$NIXOS_DIR/entropy-shield.nix"
ICON_SRC="$SRC_DIR/logos/entropy-logo.png"

# ── detect & clean previous installation ─────────────────────
if [[ -d "$DEST" ]] || [[ -f "$MODULE_DEST" ]]; then
    echo "[*] Existing installation detected — performing clean reinstall..."

    # Stop any services that may be running from the previous install
    for svc in dnscrypt-proxy i2pd; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            echo "    Stopping $svc..."
            systemctl stop "$svc" || true
        fi
        if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
            systemctl disable "$svc" || true
        fi
    done

    # Remove old application directory
    rm -rf "$DEST"

    # Remove old NixOS module (will be rewritten below)
    rm -f "$MODULE_DEST"

    echo "[>] Old installation removed."
else
    echo "[*] Fresh installation..."
fi

# ── copy application ──────────────────────────────────────────
echo "[*] Installing application to $DEST..."
mkdir -p "$DEST"
cp -r "$SRC_DIR/." "$DEST/"
chmod 755 "$DEST/main.py"

# ── write NixOS module ────────────────────────────────────────
echo "[*] Writing NixOS module to $MODULE_DEST..."
cat > "$MODULE_DEST" <<'NIXEOF'
# Entropy Shield — NixOS module
#
# IMPORTANT: services.dnscrypt-proxy2 and services.i2pd are intentionally
# NOT used here. Those NixOS modules auto-start services and modify system
# DNS at boot. Instead, we define bare systemd units (no wantedBy) so
# Entropy Shield can start/stop them on demand via systemctl.
{ config, pkgs, lib, ... }:
let
  pythonEnv = pkgs.python3.withPackages (ps: [ ps.pyqt6 ]);

  # Desktop entry installed into the Nix store so KDE picks it up
  # from /run/current-system/sw/share/applications/ automatically.
  desktopEntry = pkgs.makeDesktopItem {
    name            = "entropy-shield";
    desktopName     = "Entropy Shield";
    comment         = "Network Privacy Stack — Tor, DNSCrypt, I2P";
    exec            = "entropy-shield";
    icon            = "/opt/entropy-shield/logos/entropy-logo.png";
    categories      = [ "Network" "Security" ];
    terminal        = false;
    startupWMClass  = "entropy-shield";
  };
in
{
  environment.systemPackages = [
    pythonEnv
    pkgs.dnscrypt-proxy
    pkgs.i2pd
    pkgs.redsocks
    desktopEntry
    (pkgs.writeShellScriptBin "entropy-shield" ''
      exec ${pythonEnv}/bin/python3 /opt/entropy-shield/main.py "$@"
    '')
  ];

  # Static dnscrypt-proxy config — read-only, not modified at runtime on NixOS.
  environment.etc."dnscrypt-proxy/dnscrypt-proxy.toml".text = ''
    listen_addresses = ["127.0.0.1:5353"]
    require_nolog    = true
    require_nofilter = true
    ipv6_servers     = false

    [sources]
      [sources.public-resolvers]
      urls        = ["https://raw.githubusercontent.com/DNSCrypt/dnscrypt-resolvers/master/v3/public-resolvers.md"]
      cache_file  = "/var/cache/dnscrypt-proxy/public-resolvers.md"
      minisign_key = "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
      refresh_delay = 72
      prefix = ""
  '';

  # DNSCrypt — unit present so `systemctl start dnscrypt-proxy` works,
  # but NO wantedBy → never starts at boot, never touches system DNS.
  systemd.services.dnscrypt-proxy = {
    description = "DNSCrypt proxy (Entropy Shield)";
    after       = [ "network.target" ];
    serviceConfig = {
      Type           = "simple";
      ExecStart      = "${pkgs.dnscrypt-proxy}/bin/dnscrypt-proxy -config /etc/dnscrypt-proxy/dnscrypt-proxy.toml";
      Restart        = "on-failure";
      CacheDirectory = "dnscrypt-proxy";
    };
    # wantedBy intentionally omitted
  };

  # I2P — same pattern: unit exists, never auto-starts.
  systemd.services.i2pd = {
    description = "I2P daemon (Entropy Shield)";
    after       = [ "network.target" ];
    serviceConfig = {
      Type           = "simple";
      ExecStart      = "${pkgs.i2pd}/bin/i2pd --datadir=/var/lib/i2pd";
      Restart        = "on-failure";
      StateDirectory = "i2pd";
    };
    # wantedBy intentionally omitted
  };

  # Tor transparent-proxy ports — merged with user's existing tor config.
  services.tor.settings = {
    VirtualAddrNetworkIPv4 = "10.192.0.0/10";
    AutomapHostsOnResolve  = true;
    TransPort = [{ addr = "127.0.0.1"; port = 9040; }];
    DNSPort   = [{ addr = "127.0.0.1"; port = 5300; }];
  };

  # polkit: wheel group can run entropy-shield as root via pkexec.
  security.polkit.extraConfig = ''
    polkit.addRule(function(action, subject) {
      if (action.id === "org.freedesktop.policykit.exec" &&
          subject.isInGroup("wheel")) {
        return polkit.Result.YES;
      }
    });
  '';
}
NIXEOF

# ── auto-patch /etc/nixos/configuration.nix ──────────────────
echo "[*] Patching $NIXOS_DIR/configuration.nix..."
# Only keep one backup of the original (pre-Entropy-Shield) state.
BACKUP="$NIXOS_DIR/configuration.nix.entropy-shield.bak"
if [[ ! -f "$BACKUP" ]]; then
    cp "$NIXOS_DIR/configuration.nix" "$BACKUP"
    echo "[>] Original configuration.nix backed up to $BACKUP"
fi

python3 - <<'PYEOF'
import re, sys

cfg  = "/etc/nixos/configuration.nix"
ref  = "./entropy-shield.nix"

with open(cfg) as f:
    content = f.read()

if ref in content:
    print("[>] Already imported, skipping.")
    sys.exit(0)

m = re.search(r'imports\s*=\s*\[', content)
if m:
    pos = m.end()
    content = content[:pos] + f"\n    {ref}" + content[pos:]
else:
    content = re.sub(
        r'(^\s*\{)',
        r'\1\n  imports = [ ./entropy-shield.nix ];',
        content, count=1, flags=re.MULTILINE
    )

with open(cfg, "w") as f:
    f.write(content)
print("[>] entropy-shield.nix added to imports.")
PYEOF

# ── icon: place in Nix-managed path so KDE finds it ──────────
# The desktop entry (makeDesktopItem) references the absolute path
# /opt/entropy-shield/logos/entropy-logo.png which is always present.
# We also drop it into the hicolor theme for panel/taskbar use.
echo "[*] Installing icon..."
HICOLOR=/run/current-system/sw/share/icons/hicolor/256x256/apps
mkdir -p "$HICOLOR" 2>/dev/null || true
cp "$ICON_SRC" "$HICOLOR/entropy-shield.png" 2>/dev/null || true

# ── nixos-rebuild ─────────────────────────────────────────────
echo ""
echo "[*] Running nixos-rebuild switch (this may take a few minutes)..."
nixos-rebuild switch 2>&1 | tail -20

# Refresh desktop/icon caches so KDE picks up the new entry immediately
update-desktop-database 2>/dev/null || true
gtk-update-icon-cache -f -t /run/current-system/sw/share/icons/hicolor 2>/dev/null || true
# KDE-specific: rebuild sycoca (application menu database)
runuser -l berkkucukk -c "kbuildsycoca6 --noincremental" 2>/dev/null || \
    runuser -l "$SUDO_USER" -c "kbuildsycoca6 --noincremental" 2>/dev/null || true

echo ""
echo "────────────────────────────────────────────────────────"
echo " Entropy Shield installed successfully."
echo " Run: entropy-shield"
echo " Or open it from your application menu."
echo "────────────────────────────────────────────────────────"
