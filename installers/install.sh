#!/usr/bin/env bash
# Entropy Shield — Universal Installer
# Detects the Linux distribution and runs the appropriate installation steps.
set -euo pipefail

# ── privilege check ───────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "[*] Root required. Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"
DEST=/opt/entropy-shield
WRAPPER=/usr/local/bin/entropy-shield
ICON_SRC="$SRC_DIR/logos/entropy-logo.png"

# ── distro detection ─────────────────────────────────────────────────────────
if [[ ! -f /etc/os-release ]]; then
    echo "[!] /etc/os-release not found. Cannot detect distribution."
    exit 1
fi

# shellcheck source=/dev/null
source /etc/os-release
DISTRO_ID="${ID:-unknown}"
DISTRO_ID_LIKE="${ID_LIKE:-}"

distro_is() {
    local target="$1"
    [[ "$DISTRO_ID" == "$target" ]] || echo "$DISTRO_ID_LIKE" | grep -qw "$target"
}

echo "[*] Detected distribution: ${PRETTY_NAME:-$DISTRO_ID}"

# ── clean previous installation ───────────────────────────────────────────────
if [[ -d "$DEST" ]]; then
    echo "[*] Existing installation found — performing clean reinstall..."
    for svc in dnscrypt-proxy i2pd tor; do
        systemctl stop "$svc" 2>/dev/null || true
    done
    rm -rf "$DEST"
    rm -f "$WRAPPER"
    echo "[>] Old installation removed."
else
    echo "[*] Fresh installation..."
fi

# ── install PyQt6 helper ──────────────────────────────────────────────────────
# Try system package first, fall back to pip (respects PEP 668 on newer distros)
install_pyqt6() {
    local pkg_manager="$1"
    local sys_pkg="$2"

    python3 -c "import PyQt6" 2>/dev/null && return 0

    case "$pkg_manager" in
        pacman)  pacman -S --needed --noconfirm "$sys_pkg" 2>/dev/null || true ;;
        apt)     DEBIAN_FRONTEND=noninteractive apt-get install -y "$sys_pkg" 2>/dev/null || true ;;
        dnf)     dnf install -y -q "$sys_pkg" 2>/dev/null || true ;;
        zypper)  zypper install -y "$sys_pkg" 2>/dev/null || true ;;
    esac

    python3 -c "import PyQt6" 2>/dev/null && return 0

    echo "[*] System PyQt6 not available, trying pip..."
    pip3 install --quiet --break-system-packages PyQt6 2>/dev/null || \
        pip3 install --quiet PyQt6 2>/dev/null || {
            echo "[!] Failed to install PyQt6. Install it manually: pip3 install PyQt6"
            return 1
        }
}

# ── distro-specific package installation ──────────────────────────────────────
install_packages_arch() {
    echo "[*] Installing dependencies via pacman..."
    pacman -Sy --needed --noconfirm \
        python python-pip \
        python-pyqt6 \
        tor \
        dnscrypt-proxy \
        i2pd \
        nftables \
        iptables-nft \
        iproute2 \
        polkit
}

install_packages_debian() {
    echo "[*] Updating package list..."
    apt-get update -qq

    echo "[*] Installing dependencies via apt..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip \
        tor \
        nftables \
        iptables \
        iproute2

    # polkit package name changed in Debian 12 / Ubuntu 22.10
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        policykit-1 2>/dev/null || \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        polkitd libpolkit-agent-1-0

    # dnscrypt-proxy: not in main repo on older releases, try then warn
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        dnscrypt-proxy 2>/dev/null || \
        echo "[!] dnscrypt-proxy not found in apt repos. Install manually if needed."

    # i2pd: not in main Debian/Ubuntu repos — try, then warn
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        i2pd 2>/dev/null || \
        echo "[!] i2pd not found in apt repos. Add the i2pd repo or install manually."

    install_pyqt6 apt python3-pyqt6
}

install_packages_fedora() {
    echo "[*] Installing dependencies via dnf..."
    dnf install -y -q \
        python3 python3-pip \
        tor \
        nftables \
        iptables \
        iproute \
        polkit

    # dnscrypt-proxy — try main repo, then Copr
    dnf install -y -q dnscrypt-proxy 2>/dev/null || {
        echo "[!] dnscrypt-proxy not in main repo, trying Copr..."
        dnf copr enable -y varlad/dnscrypt-proxy 2>/dev/null || \
        dnf copr enable -y cromerc/dnscrypt-proxy 2>/dev/null || true
        dnf install -y -q dnscrypt-proxy 2>/dev/null || \
            echo "[!] dnscrypt-proxy not installed. Install manually if needed."
    }

    # i2pd — try main repo, then Copr
    dnf install -y -q i2pd 2>/dev/null || {
        echo "[!] i2pd not in main repo, trying Copr..."
        dnf copr enable -y i2p/i2pd 2>/dev/null || true
        dnf install -y -q i2pd 2>/dev/null || \
            echo "[!] i2pd not installed. Install manually if needed."
    }

    install_pyqt6 dnf python3-qt6
}

install_packages_opensuse() {
    echo "[*] Installing dependencies via zypper..."
    zypper refresh -q
    zypper install -y \
        python3 python3-pip \
        tor \
        nftables \
        iptables \
        iproute2 \
        polkit

    zypper install -y dnscrypt-proxy 2>/dev/null || \
        echo "[!] dnscrypt-proxy not found in zypper repos. Install manually if needed."

    zypper install -y i2pd 2>/dev/null || \
        echo "[!] i2pd not found in zypper repos. Install manually if needed."

    install_pyqt6 zypper python3-qt6
}

# ── NixOS: special path ───────────────────────────────────────────────────────
install_nixos() {
    NIXOS_DIR=/etc/nixos
    MODULE_DEST="$NIXOS_DIR/entropy-shield.nix"

    if [[ -f "$MODULE_DEST" ]]; then
        echo "[*] Existing NixOS module found — removing for reinstall..."
        rm -f "$MODULE_DEST"
    fi

    echo "[*] Installing application to $DEST..."
    mkdir -p "$DEST"
    cp -r "$SRC_DIR/." "$DEST/"
    chmod 755 "$DEST/main.py"

    echo "[*] Writing NixOS module to $MODULE_DEST..."
    cat > "$MODULE_DEST" <<'NIXEOF'
# Entropy Shield — NixOS module
{ config, pkgs, lib, ... }:
let
  pythonEnv = pkgs.python3.withPackages (ps: [ ps.pyqt6 ]);
  desktopEntry = pkgs.makeDesktopItem {
    name           = "entropy-shield";
    desktopName    = "Entropy Shield";
    comment        = "Network Privacy Stack — Tor, DNSCrypt, I2P";
    exec           = "entropy-shield";
    icon           = "/opt/entropy-shield/logos/entropy-logo.png";
    categories     = [ "Network" "Security" ];
    terminal       = false;
    startupWMClass = "entropy-shield";
  };
in
{
  environment.systemPackages = [
    pythonEnv
    pkgs.dnscrypt-proxy
    pkgs.i2pd
    desktopEntry
    (pkgs.writeShellScriptBin "entropy-shield" ''
      exec ${pythonEnv}/bin/python3 /opt/entropy-shield/main.py "$@"
    '')
  ];

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

  systemd.services.dnscrypt-proxy = {
    description = "DNSCrypt proxy (Entropy Shield)";
    after       = [ "network.target" ];
    serviceConfig = {
      Type           = "simple";
      ExecStart      = "${pkgs.dnscrypt-proxy}/bin/dnscrypt-proxy -config /etc/dnscrypt-proxy/dnscrypt-proxy.toml";
      Restart        = "on-failure";
      CacheDirectory = "dnscrypt-proxy";
    };
  };

  systemd.services.i2pd = {
    description = "I2P daemon (Entropy Shield)";
    after       = [ "network.target" ];
    serviceConfig = {
      Type           = "simple";
      ExecStart      = "${pkgs.i2pd}/bin/i2pd --datadir=/var/lib/i2pd";
      Restart        = "on-failure";
      StateDirectory = "i2pd";
    };
  };

  services.tor.settings = {
    VirtualAddrNetworkIPv4 = "10.192.0.0/10";
    AutomapHostsOnResolve  = true;
    TransPort = [{ addr = "127.0.0.1"; port = 9040; }];
    DNSPort   = [{ addr = "127.0.0.1"; port = 5300; }];
  };

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

    echo "[*] Patching $NIXOS_DIR/configuration.nix..."
    BACKUP="$NIXOS_DIR/configuration.nix.entropy-shield.bak"
    if [[ ! -f "$BACKUP" ]]; then
        cp "$NIXOS_DIR/configuration.nix" "$BACKUP"
        echo "[>] Backed up configuration.nix to $BACKUP"
    fi

    python3 - <<'PYEOF'
import re, sys

cfg = "/etc/nixos/configuration.nix"
ref = "./entropy-shield.nix"

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

    echo "[*] Running nixos-rebuild switch..."
    nixos-rebuild switch 2>&1 | tail -20

    update-desktop-database 2>/dev/null || true
    gtk-update-icon-cache -f -t /run/current-system/sw/share/icons/hicolor 2>/dev/null || true

    # Rebuild KDE app menu for the actual logged-in user (not hardcoded)
    REAL_USER="${SUDO_USER:-}"
    if [[ -n "$REAL_USER" ]]; then
        runuser -l "$REAL_USER" -c "kbuildsycoca6 --noincremental" 2>/dev/null || true
    fi

    echo ""
    echo "────────────────────────────────────────────────────────"
    echo " Entropy Shield installed successfully (NixOS)."
    echo " Run: entropy-shield"
    echo " Or open it from your application menu."
    echo "────────────────────────────────────────────────────────"
    exit 0
}

# ── common post-install steps (non-NixOS) ────────────────────────────────────
common_install() {
    echo "[*] Installing application to $DEST..."
    mkdir -p "$DEST"
    cp -r "$SRC_DIR/." "$DEST/"
    chmod 755 "$DEST/main.py"

    echo "[*] Installing icon..."
    mkdir -p /usr/share/pixmaps
    mkdir -p /usr/share/icons/hicolor/256x256/apps
    cp "$ICON_SRC" /usr/share/pixmaps/entropy-shield.png
    cp "$ICON_SRC" /usr/share/icons/hicolor/256x256/apps/entropy-shield.png
    gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

    echo "[*] Creating launcher..."
    cat > "$WRAPPER" <<'EOF'
#!/usr/bin/env bash
exec python3 /opt/entropy-shield/main.py "$@"
EOF
    chmod 755 "$WRAPPER"

    echo "[*] Creating desktop entry..."
    mkdir -p /usr/share/applications
    cat > /usr/share/applications/entropy-shield.desktop <<EOF
[Desktop Entry]
Name=Entropy Shield
Comment=Network Privacy Stack — Tor, DNSCrypt, I2P
Exec=entropy-shield
Icon=/usr/share/pixmaps/entropy-shield.png
Type=Application
Categories=Network;Security;
Terminal=false
StartupWMClass=entropy-shield
EOF
    update-desktop-database /usr/share/applications 2>/dev/null || true

    echo "[*] Creating polkit policy..."
    mkdir -p /usr/share/polkit-1/actions
    cat > /usr/share/polkit-1/actions/org.entropyshield.policy <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
  "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
  "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <action id="org.entropyshield.run">
    <description>Run Entropy Shield</description>
    <message>Authentication required to manage network privacy layers</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/bin/python3</annotate>
    <annotate key="org.freedesktop.policykit.exec.argv1">/opt/entropy-shield/main.py</annotate>
  </action>
</policyconfig>
EOF

    # SELinux context (Fedora/RHEL/CentOS)
    if command -v setenforce &>/dev/null && selinuxenabled 2>/dev/null; then
        echo "[*] Applying SELinux context..."
        chcon -t bin_t "$WRAPPER" 2>/dev/null || true
        restorecon -r "$DEST" 2>/dev/null || true
    fi

    echo ""
    echo "────────────────────────────────────────────────────────"
    echo " Entropy Shield installed successfully."
    echo " Run: entropy-shield"
    echo " Or open it from your application menu."
    echo "────────────────────────────────────────────────────────"
}

# ── main dispatch ─────────────────────────────────────────────────────────────
if [[ "$DISTRO_ID" == "nixos" ]]; then
    install_nixos
elif distro_is arch || distro_is manjaro || distro_is endeavouros || distro_is garuda; then
    install_packages_arch
    common_install
elif distro_is debian || distro_is ubuntu || distro_is linuxmint || distro_is pop || distro_is elementary || distro_is kali || distro_is zorin; then
    install_packages_debian
    common_install
elif distro_is fedora || distro_is rhel || distro_is centos || distro_is almalinux || distro_is rocky; then
    install_packages_fedora
    common_install
elif distro_is opensuse || distro_is suse; then
    install_packages_opensuse
    common_install
else
    echo "[!] Unrecognized distribution: $DISTRO_ID"
    echo "    Supported: Arch/Manjaro, Debian/Ubuntu/Mint/Kali, Fedora/RHEL/Alma/Rocky, openSUSE, NixOS"
    echo ""
    echo "    You can install dependencies manually and then re-run with:"
    echo "      DISTRO_ID=arch   bash install.sh   (or debian / fedora / opensuse)"
    exit 1
fi
