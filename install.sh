#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Entropy Shield — Universal Installer
#  Supports: Arch/Manjaro, Debian/Ubuntu/Mint/Kali, Fedora/RHEL/Alma/Rocky,
#            openSUSE, NixOS
#  Usage:
#    sudo bash install.sh             # install
#    sudo bash install.sh --uninstall # remove
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_BOLD='\033[1m'
    C_GRN='\033[1;32m'
    C_YLW='\033[1;33m'
    C_RED='\033[1;31m'
    C_CYN='\033[1;36m'
    C_RST='\033[0m'
else
    C_BOLD=''; C_GRN=''; C_YLW=''; C_RED=''; C_CYN=''; C_RST=''
fi

info()  { echo -e "${C_CYN}[*]${C_RST} $*"; }
ok()    { echo -e "${C_GRN}[✓]${C_RST} $*"; }
warn()  { echo -e "${C_YLW}[!]${C_RST} $*"; }
die()   { echo -e "${C_RED}[✗]${C_RST} $*" >&2; exit 1; }
step()  { echo -e "\n${C_BOLD}── $* ──${C_RST}"; }

# ── privilege check ───────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    info "Root required. Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/entropy-shield
WRAPPER=/usr/local/bin/entropy-shield
DESKTOP=/usr/share/applications/entropy-shield.desktop
POLKIT=/usr/share/polkit-1/actions/org.entropyshield.policy
SYSTEMD_SERVICE=/etc/systemd/system/entropy-shield.service
SUDOERS_FILE=/etc/sudoers.d/entropy-shield
ICON_SYS_PIX=/usr/share/pixmaps/entropy-shield.png
ICON_SYS_HIC=/usr/share/icons/hicolor/256x256/apps/entropy-shield.png

# Pick best available logo (dark.png preferred; fallback to others)
pick_icon() {
    local logos_dir="$SCRIPT_DIR/logos"
    for name in dark.png binary.png circuit.png pixel.png; do
        [[ -f "$logos_dir/$name" ]] && { echo "$logos_dir/$name"; return; }
    done
    echo ""
}
ICON_SRC="$(pick_icon)"

# ── argument parsing ──────────────────────────────────────────────────────────
UNINSTALL=false
for arg in "$@"; do
    case "$arg" in
        --uninstall|-u) UNINSTALL=true ;;
        --help|-h)
            echo "Usage: sudo bash install.sh [--uninstall]"
            exit 0
            ;;
    esac
done

# ── distro detection ──────────────────────────────────────────────────────────
[[ -f /etc/os-release ]] || die "/etc/os-release not found — cannot detect distribution."
# shellcheck source=/dev/null
source /etc/os-release
DISTRO_ID="${ID:-unknown}"
DISTRO_LIKE="${ID_LIKE:-}"

distro_is() {
    [[ "$DISTRO_ID" == "$1" ]] || echo "$DISTRO_LIKE" | grep -qw "$1"
}

# ─────────────────────────────────────────────────────────────────────────────
#  UNINSTALL
# ─────────────────────────────────────────────────────────────────────────────

_remove_common() {
    # Resolve the invoking user's home directory so we can remove user-level files.
    local real_user="${SUDO_USER:-}"
    local user_home=""
    if [[ -n "$real_user" ]]; then
        user_home="$(getent passwd "$real_user" | cut -d: -f6 2>/dev/null || true)"
    fi

    step "Stopping services"
    for svc in tor dnscrypt-proxy i2pd; do
        systemctl stop "$svc" 2>/dev/null && ok "Stopped $svc" || true
    done

    # Stop and disable entropy-shield headless service if installed
    systemctl stop    entropy-shield 2>/dev/null || true
    systemctl disable entropy-shield 2>/dev/null && ok "Disabled entropy-shield service" || true

    step "Removing application files"
    rm -rf "$DEST"
    rm -f  "$WRAPPER" "$DESKTOP" "$POLKIT" "$ICON_SYS_PIX" "$ICON_SYS_HIC"
    rm -f  "$SYSTEMD_SERVICE" "$SUDOERS_FILE"
    ok "Application files removed."

    step "Removing system-wide runtime / proxy files"
    rm -f  /etc/profile.d/entropy-shield-proxy.sh
    rm -f  /etc/fish/conf.d/entropy-shield-proxy.fish
    rm -f  /run/systemd/resolved.conf.d/entropy-shield.conf
    rm -rf /run/entropy-shield
    ok "System-wide files removed."

    step "Removing user files"
    if [[ -n "$user_home" ]]; then
        rm -f  "$user_home/.config/autostart/entropy-shield.desktop"
        rm -rf "$user_home/.config/entropy-shield"
        rm -f  "$user_home/.config/environment.d/entropy-shield-proxy.conf"
        # Remove instance lock if present
        local rt_dir
        rt_dir="$(getent passwd "$real_user" | cut -d: -f6 2>/dev/null || echo "")"
        local uid
        uid="$(id -u "$real_user" 2>/dev/null || echo "")"
        [[ -n "$uid" ]] && rm -f "/run/user/$uid/entropy-shield.lock" || true
        ok "User files removed for $real_user."
    else
        warn "SUDO_USER not set — skipping user config cleanup."
        warn "Manually remove if needed:"
        warn "  ~/.config/autostart/entropy-shield.desktop"
        warn "  ~/.config/entropy-shield/"
    fi

    step "Restarting affected services"
    # Restart systemd-resolved if it was stopped by entropy-shield
    if systemctl is-enabled --quiet systemd-resolved 2>/dev/null && \
       ! systemctl is-active  --quiet systemd-resolved 2>/dev/null; then
        systemctl start systemd-resolved 2>/dev/null && ok "Restarted systemd-resolved." || true
    fi
    systemctl daemon-reload 2>/dev/null || true

    step "Updating desktop database"
    gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
    ok "Done."
}

do_uninstall() {
    if [[ ! -d "$DEST" && ! -f "$WRAPPER" ]]; then
        warn "Entropy Shield is not installed — nothing to do."
        exit 0
    fi
    _remove_common
    echo ""
    echo -e "${C_GRN}${C_BOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║   Entropy Shield uninstalled successfully.       ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${C_RST}"
    exit 0
}

do_uninstall_nixos() {
    step "Removing application files"
    rm -rf "$DEST"
    rm -f  "$SUDOERS_FILE"
    ok "Removed $DEST"

    local module=/etc/nixos/entropy-shield.nix
    if [[ -f "$module" ]]; then
        step "Removing NixOS module"
        python3 - <<'PYEOF'
import re
cfg = "/etc/nixos/configuration.nix"
with open(cfg) as f:
    content = f.read()
content = re.sub(r'\n?\s*\./entropy-shield\.nix', '', content)
with open(cfg, "w") as f:
    f.write(content)
print("Removed entropy-shield.nix from imports.")
PYEOF
        rm -f "$module"
        ok "NixOS module removed."
    fi

    step "Rebuilding NixOS"
    nixos-rebuild switch 2>&1 | tail -10

    echo ""
    echo -e "${C_GRN}${C_BOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║   Entropy Shield uninstalled successfully.       ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${C_RST}"
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL — per-distro package setup
# ─────────────────────────────────────────────────────────────────────────────

_install_pyqt6() {
    local pm="$1" syspkg="$2"
    python3 -c "import PyQt6" 2>/dev/null && return 0

    case "$pm" in
        pacman) pacman -S --needed --noconfirm "$syspkg" 2>/dev/null || true ;;
        apt)    DEBIAN_FRONTEND=noninteractive apt-get install -y "$syspkg" 2>/dev/null || true ;;
        dnf)    dnf install -y -q "$syspkg" 2>/dev/null || true ;;
        zypper) zypper install -y "$syspkg" 2>/dev/null || true ;;
    esac

    python3 -c "import PyQt6" 2>/dev/null && return 0

    info "System PyQt6 not available — trying pip..."
    pip3 install --quiet --break-system-packages PyQt6 2>/dev/null || \
        pip3 install --quiet PyQt6 2>/dev/null || \
        die "Failed to install PyQt6. Run: pip3 install PyQt6"
}

_aur_install() {
    local pkg="$1"
    command -v "$pkg" &>/dev/null && { ok "$pkg already installed."; return; }
    if command -v paru &>/dev/null; then
        sudo -u "${SUDO_USER:-$USER}" paru -S --noconfirm "$pkg" 2>/dev/null \
            || warn "$pkg AUR install failed. Run: paru -S $pkg"
    elif command -v yay &>/dev/null; then
        sudo -u "${SUDO_USER:-$USER}" yay -S --noconfirm "$pkg" 2>/dev/null \
            || warn "$pkg AUR install failed. Run: yay -S $pkg"
    else
        warn "No AUR helper found. Install $pkg manually for full functionality:"
        echo "    paru -S $pkg   OR   yay -S $pkg"
    fi
}

pkg_arch() {
    step "Installing packages (pacman)"
    pacman -Sy --needed --noconfirm \
        python python-pip python-pyqt6 \
        tor dnscrypt-proxy i2pd \
        nftables iptables-nft iproute2 polkit \
        conntrack-tools bind
    _aur_install redsocks
    _aur_install obfs4proxy
}

pkg_debian() {
    step "Updating package list"
    apt-get update -qq

    step "Installing packages (apt)"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip \
        tor nftables iptables iproute2

    # polkit: name changed in Debian 12 / Ubuntu 22.10
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        policykit-1 2>/dev/null || \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        polkitd libpolkit-agent-1-0 2>/dev/null || \
        warn "polkit not installed — some privilege escalation features may not work."

    for pkg in dnscrypt-proxy i2pd redsocks obfs4proxy conntrack dnsutils; do
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$pkg" 2>/dev/null \
            || warn "$pkg not found in apt repos — install manually if needed."
    done

    _install_pyqt6 apt python3-pyqt6
}

pkg_fedora() {
    step "Installing packages (dnf)"
    dnf install -y -q \
        python3 python3-pip \
        tor nftables iptables iproute polkit \
        conntrack-tools bind-utils

    # redsocks
    command -v redsocks &>/dev/null || \
        dnf install -y -q redsocks 2>/dev/null || {
            info "redsocks not in main repo — trying COPR..."
            dnf copr enable -y zawertun/redsocks 2>/dev/null || true
            dnf install -y -q redsocks 2>/dev/null || \
                warn "redsocks not installed. Transparent I2P routing unavailable."
        }

    # obfs4proxy (for Tor bridges)
    command -v obfs4proxy &>/dev/null || \
        dnf install -y -q obfs4proxy 2>/dev/null || \
        warn "obfs4proxy not installed. Tor bridge support (obfs4) unavailable."

    # dnscrypt-proxy
    dnf install -y -q dnscrypt-proxy 2>/dev/null || {
        info "dnscrypt-proxy not in main repo — trying COPR..."
        dnf copr enable -y varlad/dnscrypt-proxy 2>/dev/null || \
        dnf copr enable -y cromerc/dnscrypt-proxy 2>/dev/null || true
        dnf install -y -q dnscrypt-proxy 2>/dev/null || \
            warn "dnscrypt-proxy not installed. Install manually if needed."
    }

    # i2pd
    dnf install -y -q i2pd 2>/dev/null || {
        info "i2pd not in main repo — trying COPR..."
        dnf copr enable -y i2p/i2pd 2>/dev/null || true
        dnf install -y -q i2pd 2>/dev/null || \
            warn "i2pd not installed. Install manually if needed."
    }

    _install_pyqt6 dnf python3-qt6
}

pkg_opensuse() {
    step "Installing packages (zypper)"
    zypper refresh -q
    zypper install -y \
        python3 python3-pip \
        tor nftables iptables iproute2 polkit

    for pkg in dnscrypt-proxy i2pd redsocks obfs4proxy conntrack-tools bind-utils; do
        zypper install -y "$pkg" 2>/dev/null \
            || warn "$pkg not found in zypper repos — install manually if needed."
    done

    _install_pyqt6 zypper python3-qt6
}

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL — NixOS (special path, no common_install needed)
# ─────────────────────────────────────────────────────────────────────────────

do_install_nixos() {
    step "Copying application to $DEST"
    mkdir -p "$DEST"
    cp -r "$SCRIPT_DIR/." "$DEST/"
    chmod 755 "$DEST/main.py"
    find "$DEST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$DEST" -name "*.pyc" -delete 2>/dev/null || true
    ok "Application copied."

    local nixdir=/etc/nixos
    local module="$nixdir/entropy-shield.nix"

    step "Writing NixOS module → $module"

    # Resolve icon path for Nix module
    local nix_icon="/opt/entropy-shield/logos/dark.png"
    for name in dark.png binary.png circuit.png pixel.png; do
        [[ -f "$DEST/logos/$name" ]] && { nix_icon="/opt/entropy-shield/logos/$name"; break; }
    done

    cat > "$module" <<NIXEOF
# Entropy Shield — NixOS module (auto-generated by install.sh)
{ config, pkgs, lib, ... }:
let
  pythonEnv = pkgs.python3.withPackages (ps: [ ps.pyqt6 ]);
  desktopEntry = pkgs.makeDesktopItem {
    name           = "entropy-shield";
    desktopName    = "Entropy Shield";
    comment        = "Network Privacy Stack — Tor, DNSCrypt, I2P";
    exec           = "entropy-shield";
    icon           = "${nix_icon}";
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
    pkgs.conntrack-tools
    pkgs.obfs4
    pkgs.bind                 # provides dig for leak tests
    desktopEntry
    (pkgs.writeShellScriptBin "entropy-shield" ''
      exec \${pythonEnv}/bin/python3 /opt/entropy-shield/main.py "\$@"
    '')
  ];

  # Port 5380 matches the entropy-shield default (core/config.py).
  # 5353 is reserved for mDNS (avahi) and must NOT be used here.
  environment.etc."dnscrypt-proxy/dnscrypt-proxy.toml".text = ''
    listen_addresses = ["127.0.0.1:5380", "[::1]:5380"]
    require_nolog    = true
    require_nofilter = true
    ipv6_servers     = false

    [sources]
      [sources.public-resolvers]
      urls         = ["https://raw.githubusercontent.com/DNSCrypt/dnscrypt-resolvers/master/v3/public-resolvers.md"]
      cache_file   = "/var/cache/dnscrypt-proxy/public-resolvers.md"
      minisign_key = "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
      refresh_delay = 72
      prefix = ""
  '';

  systemd.services.dnscrypt-proxy = {
    description = "DNSCrypt proxy (Entropy Shield)";
    after       = [ "network.target" ];
    serviceConfig = {
      Type           = "simple";
      ExecStart      = "\${pkgs.dnscrypt-proxy}/bin/dnscrypt-proxy -config /etc/dnscrypt-proxy/dnscrypt-proxy.toml";
      Restart        = "on-failure";
      CacheDirectory = "dnscrypt-proxy";
    };
  };

  systemd.services.i2pd = {
    description = "I2P daemon (Entropy Shield)";
    after       = [ "network.target" ];
    serviceConfig = {
      Type           = "simple";
      ExecStart      = "\${pkgs.i2pd}/bin/i2pd --datadir=/var/lib/i2pd";
      Restart        = "on-failure";
      StateDirectory = "i2pd";
    };
  };

  services.tor.enable = true;
  services.tor.settings = {
    VirtualAddrNetworkIPv4 = "10.192.0.0/10";
    AutomapHostsOnResolve  = true;
    AutomapHostsSuffixes   = ".onion,.exit";
    # Ports must match entropy-shield defaults (core/config.py)
    TransPort   = [{ addr = "127.0.0.1"; port = 9040; }];
    DNSPort     = [{ addr = "127.0.0.1"; port = 5300; }];
    SocksPort   = [{ addr = "127.0.0.1"; port = 9050; flags = ["IsolateDestAddr" "IsolateDestPort"]; }];
    ControlPort = [{ addr = "127.0.0.1"; port = 9051; }];
    CookieAuthentication = true;
  };

  security.polkit.extraConfig = ''
    /* Allow wheel-group users to run the entropy-shield privileged runner
       via pkexec without a password prompt.  Remove this block to require
       password authentication on every connect/disconnect. */
    polkit.addRule(function(action, subject) {
      if (action.id === "org.entropyshield.run" &&
          subject.isInGroup("wheel")) {
        return polkit.Result.YES;
      }
    });
  '';

  /* Optional: install as a system service for headless / server use.
     Enable with: systemctl enable --now entropy-shield             */
  systemd.services.entropy-shield = {
    description = "Entropy Shield — Network Privacy (Headless)";
    after       = [ "network-online.target" ];
    wants       = [ "network-online.target" ];
    serviceConfig = {
      Type       = "simple";
      ExecStart  = "\${pkgs.python3}/bin/python3 /opt/entropy-shield/core/privileged_runner.py --headless";
      Restart    = "on-failure";
      RestartSec = 10;
    };
  };
}
NIXEOF
    ok "Module written."

    step "Patching $nixdir/configuration.nix"
    local backup="$nixdir/configuration.nix.entropy-shield.bak"
    [[ ! -f "$backup" ]] && cp "$nixdir/configuration.nix" "$backup" \
        && ok "Backup → $backup"

    python3 - <<'PYEOF'
import re, sys
cfg = "/etc/nixos/configuration.nix"
ref = "./entropy-shield.nix"
with open(cfg) as f:
    content = f.read()
if ref in content:
    print("Already imported, skipping.")
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
print("entropy-shield.nix added to imports.")
PYEOF

    step "Running nixos-rebuild switch"
    nixos-rebuild switch 2>&1 | tail -20

    update-desktop-database 2>/dev/null || true
    gtk-update-icon-cache -f -t /run/current-system/sw/share/icons/hicolor 2>/dev/null || true
    REAL_USER="${SUDO_USER:-}"
    [[ -n "$REAL_USER" ]] && \
        runuser -l "$REAL_USER" -c "kbuildsycoca6 --noincremental" 2>/dev/null || true

    _print_success_nixos
}

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL — common post-package steps (all non-NixOS distros)
# ─────────────────────────────────────────────────────────────────────────────

common_install() {
    step "Copying application to $DEST"
    mkdir -p "$DEST"
    cp -r "$SCRIPT_DIR/." "$DEST/"
    chmod 755 "$DEST/main.py"
    find "$DEST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$DEST" -name "*.pyc" -delete 2>/dev/null || true
    ok "Application copied."

    step "Installing icon"
    if [[ -n "$ICON_SRC" ]]; then
        mkdir -p /usr/share/pixmaps /usr/share/icons/hicolor/256x256/apps
        cp "$ICON_SRC" "$ICON_SYS_PIX"
        cp "$ICON_SRC" "$ICON_SYS_HIC"
        gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
        ok "Icon installed."
    else
        warn "No logo file found in logos/ — skipping icon install."
    fi

    step "Creating launcher → $WRAPPER"
    cat > "$WRAPPER" <<'EOF'
#!/usr/bin/env bash
# Entropy Shield launcher — runs as normal user; privileged operations use pkexec/sudo
exec python3 /opt/entropy-shield/main.py "$@"
EOF
    chmod 755 "$WRAPPER"
    ok "Launcher created."

    step "Creating desktop entry"
    mkdir -p /usr/share/applications
    local icon_path="${ICON_SYS_PIX}"
    [[ -z "$ICON_SRC" ]] && icon_path="utilities-terminal"
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Name=Entropy Shield
Comment=Network Privacy Stack — Tor, DNSCrypt, I2P
Exec=entropy-shield
Icon=${icon_path}
Type=Application
Categories=Network;Security;
Terminal=false
StartupWMClass=entropy-shield
EOF
    update-desktop-database /usr/share/applications 2>/dev/null || true
    ok "Desktop entry created."

    step "Creating polkit policy"
    # Detect the real python3 binary (may be versioned, e.g. /usr/bin/python3.13).
    # pkexec matches exec.path against the ACTUAL binary, so we must use the
    # real path — not a generic "python3" symlink — to avoid exit code 127.
    PYTHON3_EXEC="$(python3 -c 'import sys; print(sys.executable)' 2>/dev/null \
                   || readlink -f "$(which python3)" 2>/dev/null \
                   || echo '/usr/bin/python3')"
    info "Using python3 binary: $PYTHON3_EXEC"
    mkdir -p /usr/share/polkit-1/actions
    cat > "$POLKIT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
  "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
  "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <action id="org.entropyshield.run">
    <description>Entropy Shield — Manage Network Privacy</description>
    <message>Authentication required to connect or disconnect privacy layers (Tor, DNSCrypt, I2P)</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">${PYTHON3_EXEC}</annotate>
    <annotate key="org.freedesktop.policykit.exec.argv1">/opt/entropy-shield/core/privileged_runner.py</annotate>
  </action>
</policyconfig>
EOF
    ok "Polkit policy created (python3: ${PYTHON3_EXEC})."

    step "Installing systemd service (headless/server mode)"
    if [[ -f "$SCRIPT_DIR/entropy-shield.service" ]]; then
        cp "$SCRIPT_DIR/entropy-shield.service" "$SYSTEMD_SERVICE"
        systemctl daemon-reload 2>/dev/null || true
        ok "Service file installed → $SYSTEMD_SERVICE"
        info "To enable headless mode: systemctl enable --now entropy-shield"
    else
        warn "entropy-shield.service not found — headless mode unavailable."
    fi

    # SELinux context (Fedora/RHEL/CentOS)
    if command -v setenforce &>/dev/null && selinuxenabled 2>/dev/null; then
        step "Applying SELinux context"
        chcon -t bin_t "$WRAPPER" 2>/dev/null || true
        restorecon -r "$DEST" 2>/dev/null || true
        ok "SELinux context applied."
    fi

    _print_success
}

# ─────────────────────────────────────────────────────────────────────────────
#  Success banners
# ─────────────────────────────────────────────────────────────────────────────

_print_success() {
    echo ""
    echo -e "${C_GRN}${C_BOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║   Entropy Shield installed successfully.         ║"
    echo "  ║                                                  ║"
    echo "  ║   Run:  entropy-shield                           ║"
    echo "  ║   Or open it from your application menu.         ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${C_RST}"
}

_print_success_nixos() {
    echo ""
    echo -e "${C_GRN}${C_BOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║   Entropy Shield installed successfully (NixOS). ║"
    echo "  ║                                                  ║"
    echo "  ║   Run:  entropy-shield                           ║"
    echo "  ║   Or open it from your application menu.         ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${C_RST}"
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
#  Clean previous install before installing
# ─────────────────────────────────────────────────────────────────────────────

_clean_previous() {
    if [[ -d "$DEST" ]]; then
        info "Existing installation found — performing clean reinstall..."
        for svc in tor dnscrypt-proxy i2pd; do
            systemctl stop "$svc" 2>/dev/null || true
        done
        rm -rf "$DEST"
        rm -f "$WRAPPER"
        ok "Old installation removed."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  Main dispatch
# ─────────────────────────────────────────────────────────────────────────────

info "Detected: ${PRETTY_NAME:-$DISTRO_ID}"
echo ""

# ── uninstall ─────────────────────────────────────────────────
if $UNINSTALL; then
    if [[ "$DISTRO_ID" == "nixos" ]]; then
        do_uninstall_nixos
    else
        do_uninstall
    fi
fi

# ── install ───────────────────────────────────────────────────
_clean_previous

if [[ "$DISTRO_ID" == "nixos" ]]; then
    do_install_nixos

elif distro_is arch || distro_is manjaro || distro_is endeavouros || distro_is garuda || distro_is cachyos; then
    pkg_arch
    common_install

elif distro_is debian || distro_is ubuntu || distro_is linuxmint || distro_is pop || distro_is elementary || distro_is kali || distro_is zorin || distro_is parrot; then
    pkg_debian
    common_install

elif distro_is fedora || distro_is rhel || distro_is centos || distro_is almalinux || distro_is rocky || distro_is nobara; then
    pkg_fedora
    common_install

elif distro_is opensuse || distro_is suse || distro_is tumbleweed; then
    pkg_opensuse
    common_install

else
    die "Unrecognized distribution: $DISTRO_ID
Supported: Arch/Manjaro, Debian/Ubuntu/Mint/Kali, Fedora/RHEL/Alma/Rocky, openSUSE, NixOS

Override with: DISTRO_ID=arch sudo bash install.sh
               DISTRO_ID=debian sudo bash install.sh
               DISTRO_ID=fedora sudo bash install.sh"
fi
