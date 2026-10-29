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
# Members of this group may talk to the privileged daemon's socket.
APP_GROUP=entropy-shield
# The unprivileged desktop user that invoked `sudo bash install.sh`.
REAL_USER="${SUDO_USER:-}"
ICON_SYS_PIX=/usr/share/pixmaps/entropy-shield.png
ICON_HICOLOR=/usr/share/icons/hicolor
ICON_SIZES=(16 22 24 32 48 64 128 256 512)

# Pick best available logo (oled.png preferred; fallback to others)
pick_icon() {
    local logos_dir="$SCRIPT_DIR/logos"
    for name in oled.png dark.png binary.png circuit.png pixel.png; do
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

# Every supported install path relies on systemd: the privileged daemon runs as
# a systemd service and /run/entropy-shield is created via its RuntimeDirectory=
# directive.  Bail out early (and clearly) on non-systemd init systems.
require_systemd() {
    if [[ ! -d /run/systemd/system ]]; then
        die "Your system does not use systemd, which is required by Entropy Shield.
Non-systemd init systems (OpenRC, runit, s6, SysVinit, etc.) are not supported."
    fi
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
    # Only our own daemon. Stopping it rolls back an active session (its Tor
    # child, firewall rules, DNS and the dnscrypt/i2pd configs). The system's
    # tor / dnscrypt-proxy / i2pd units belong to the user — stopping them
    # here used to take down services they run independently of this app.
    systemctl stop    entropy-shield 2>/dev/null || true
    systemctl disable entropy-shield 2>/dev/null && ok "Disabled entropy-shield service" || true

    step "Removing application files"
    rm -rf "$DEST"
    rm -f  "$WRAPPER" "$DESKTOP" "$POLKIT" "$ICON_SYS_PIX"
    for size in "${ICON_SIZES[@]}"; do
        rm -f "$ICON_HICOLOR/${size}x${size}/apps/entropy-shield.png"
    done
    rm -f  "$SYSTEMD_SERVICE" "$SUDOERS_FILE"
    ok "Application files removed."

    step "Removing '$APP_GROUP' group"
    if getent group "$APP_GROUP" >/dev/null 2>&1; then
        groupdel "$APP_GROUP" 2>/dev/null && ok "Group '$APP_GROUP' removed." \
            || warn "Could not remove group '$APP_GROUP' (it may have members logged in)."
    fi

    step "Removing system-wide runtime / proxy files"
    rm -f  /etc/profile.d/entropy-shield-proxy.sh
    rm -f  /etc/fish/conf.d/entropy-shield-proxy.fish
    rm -f  /run/systemd/resolved.conf.d/entropy-shield.conf
    rm -rf /run/entropy-shield
    ok "System-wide files removed."

    step "Removing user files"
    if [[ -n "$user_home" ]]; then
        # As the user, not root: every directory on these paths is theirs, so a
        # root rm would follow a symlinked ~/.config anywhere on the system.
        runuser -u "$real_user" -- rm -rf -- \
            "$user_home/.config/autostart/entropy-shield.desktop" \
            "$user_home/.config/entropy-shield" \
            "$user_home/.config/environment.d/entropy-shield-proxy.conf" \
            2>/dev/null || true
        # Remove instance lock if present
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

# Install a single package with the given package manager. Returns the package
# manager's own exit status so callers can react to failures.
_pm_install_one() {
    local pm="$1" p="$2"
    case "$pm" in
        pacman) pacman -S --needed --noconfirm "$p" ;;
        apt)    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$p" ;;
        dnf)    dnf install -y -q "$p" ;;
        zypper) zypper install -y "$p" ;;
        *)      return 1 ;;
    esac
}

# Install packages that the app genuinely needs. Any that cannot be installed
# are collected and reported by name, then the installer aborts with guidance —
# far clearer than `set -e` killing the whole script on the first bad name.
install_critical() {
    local pm="$1"; shift
    local failed=()
    local p
    for p in "$@"; do
        _pm_install_one "$pm" "$p" || failed+=("$p")
    done
    if (( ${#failed[@]} )); then
        die "Failed to install required package(s): ${failed[*]}
Your distribution's repositories may name these differently, or the packages
may be unavailable. Install them manually, then re-run this installer."
    fi
}

# Install optional packages one by one; missing ones only degrade features and
# must never abort the install.
install_optional() {
    local pm="$1"; shift
    local p
    for p in "$@"; do
        _pm_install_one "$pm" "$p" 2>/dev/null \
            || warn "$p not installed — some features may be unavailable."
    done
}

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
    # No `pacman -Sy` here: refreshing the databases and then installing
    # without upgrading is a partial upgrade, which Arch does not support and
    # which can leave the system with mismatched libraries. Install against the
    # current databases; if a package is not found, the error below says what
    # is missing and the user can run `pacman -Syu` first.

    step "Installing required packages (pacman)"
    install_critical pacman \
        python python-pip python-pyqt6 qt6-svg \
        tor nftables iptables-nft iproute2

    step "Installing optional packages (pacman)"
    install_optional pacman dnscrypt-proxy i2pd conntrack-tools bind curl
    _aur_install redsocks
    _aur_install obfs4proxy
}

pkg_debian() {
    step "Updating package list"
    apt-get update -qq || warn "apt-get update failed — continuing with cached lists."

    step "Installing required packages (apt)"
    install_critical apt \
        python3 python3-pip \
        tor nftables iptables iproute2

    step "Installing optional packages (apt)"
    install_optional apt dnscrypt-proxy i2pd redsocks obfs4proxy conntrack dnsutils curl

    _install_pyqt6 apt python3-pyqt6
    install_optional apt python3-pyqt6.qtsvg   # SVG icons
}

pkg_fedora() {
    step "Installing required packages (dnf)"
    install_critical dnf \
        python3 python3-pip \
        tor nftables iptables iproute

    step "Installing optional packages (dnf)"
    install_optional dnf conntrack-tools bind-utils curl

    # Third-party COPR repositories are NOT enabled automatically: a personal
    # COPR is a package source run by a single individual, and enabling it as
    # root hands them every future update of everything it ships. Only the
    # i2pd upstream project's own COPR is used (below).

    # redsocks
    command -v redsocks &>/dev/null || \
        dnf install -y -q redsocks 2>/dev/null || \
        warn "redsocks not installed. Transparent I2P routing unavailable."

    # obfs4proxy (for Tor bridges)
    command -v obfs4proxy &>/dev/null || \
        dnf install -y -q obfs4proxy 2>/dev/null || \
        warn "obfs4proxy not installed. Tor bridge support (obfs4) unavailable."

    # dnscrypt-proxy (packaged in Fedora's own repositories)
    dnf install -y -q dnscrypt-proxy 2>/dev/null || \
        warn "dnscrypt-proxy not installed. Install manually if needed."

    # i2pd
    dnf install -y -q i2pd 2>/dev/null || {
        info "i2pd not in main repo — trying COPR..."
        dnf copr enable -y i2p/i2pd 2>/dev/null || true
        dnf install -y -q i2pd 2>/dev/null || \
            warn "i2pd not installed. Install manually if needed."
    }

    _install_pyqt6 dnf python3-qt6
    install_optional dnf qt6-qtsvg              # SVG icons
}

pkg_opensuse() {
    step "Refreshing repositories (zypper)"
    zypper refresh -q || warn "zypper refresh failed — continuing with cached metadata."

    step "Installing required packages (zypper)"
    install_critical zypper \
        python3 python3-pip \
        tor nftables iptables iproute2

    step "Installing optional packages (zypper)"
    install_optional zypper dnscrypt-proxy i2pd redsocks obfs4proxy conntrack-tools bind-utils curl

    _install_pyqt6 zypper python3-qt6
    install_optional zypper qt6-svg             # SVG icons
}

# Copy the application into $DEST as root-owned, user-read-only files.
#
# SECURITY: the root daemon imports this code. Only the runtime files are
# copied (not .git, packaging, screenshots …), symlinks are dereferenced, and
# any link that survives is deleted: a symlink copied verbatim from the
# user's checkout (say core/x.py -> ~/src/x.py) would keep root importing a
# file the user can edit long after the install.
_copy_app() {
    mkdir -p "$DEST"
    local item
    for item in main.py core gui Fonts logos LICENSE entropy-shield.service; do
        [[ -e "$SCRIPT_DIR/$item" ]] || continue
        cp -RL --no-preserve=ownership "$SCRIPT_DIR/$item" "$DEST/"
    done
    find "$DEST" -type l -delete
    find "$DEST" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$DEST" -name "*.pyc" -delete 2>/dev/null || true
    chown -R root:root "$DEST"
    find "$DEST" -type d -exec chmod 755 {} +
    find "$DEST" -type f -exec chmod 644 {} +
    chmod 755 "$DEST/main.py" "$DEST/core/daemon.py"
}

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL — NixOS (special path, no common_install needed)
# ─────────────────────────────────────────────────────────────────────────────

do_install_nixos() {
    step "Copying application to $DEST"
    _copy_app
    ok "Application copied."

    local nixdir=/etc/nixos
    local module="$nixdir/entropy-shield.nix"

    step "Writing NixOS module → $module"

    # Resolve icon path for Nix module
    local nix_icon="/opt/entropy-shield/logos/oled.png"
    for name in oled.png dark.png binary.png circuit.png pixel.png; do
        [[ -f "$DEST/logos/$name" ]] && { nix_icon="/opt/entropy-shield/logos/$name"; break; }
    done

    # Add the invoking user to the entropy-shield group (so the GUI can reach the
    # daemon socket).  Empty if we cannot determine the user.
    local nix_user_group=""
    if [[ -n "$REAL_USER" ]]; then
        nix_user_group="  users.users.\"$REAL_USER\".extraGroups = [ \"entropy-shield\" ];"
    fi

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

  /* Group that gates access to the privileged daemon's Unix socket.
     No polkit/pkexec rule is needed: the GUI never escalates privileges,
     it only talks to the root daemon over this group-restricted socket. */
  users.groups."entropy-shield" = {};
${nix_user_group}

  /* Privileged daemon — runs as root, started at boot.  The GUI (normal user)
     connects to /run/entropy-shield/daemon.sock to drive it. */
  systemd.services.entropy-shield = {
    description = "Entropy Shield — Privileged Network Privacy Daemon";
    after       = [ "network-online.target" ];
    wants       = [ "network-online.target" ];
    wantedBy    = [ "multi-user.target" ];
    serviceConfig = {
      Type                 = "simple";
      ExecStart            = "\${pkgs.python3}/bin/python3 /opt/entropy-shield/core/daemon.py";
      Restart              = "on-failure";
      RestartSec           = 10;
      RuntimeDirectory     = "entropy-shield";
      RuntimeDirectoryMode = "0751";
      TimeoutStopSec       = 180;
      # Same sandbox as entropy-shield.service (see the comments there).
      ProtectSystem           = "true";
      PrivateTmp              = true;
      ProtectClock            = true;
      ProtectHostname         = true;
      ProtectKernelLogs       = true;
      ProtectKernelModules    = true;
      ProtectKernelTunables   = true;
      ProtectControlGroups    = true;
      RestrictRealtime        = true;
      RestrictSUIDSGID        = true;
      RestrictNamespaces      = true;
      LockPersonality         = true;
      SystemCallArchitectures = "native";
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" "AF_NETLINK" ];
      SystemCallFilter        = [ "@system-service" ];
      SystemCallErrorNumber   = "EPERM";
    };
  };
}
NIXEOF
    ok "Module written."

    # Flake-based configurations own their module graph through flake.nix; we
    # cannot safely rewrite that with a regex, and there may be no classic
    # configuration.nix to patch at all. Detect this and hand the user precise
    # manual steps instead of corrupting their config.
    if [[ -f "$nixdir/flake.nix" || ! -f "$nixdir/configuration.nix" ]]; then
        warn "Flake-based (or non-standard) NixOS configuration detected."
        warn "The module was written to $module but NOT wired in automatically."
        warn "Add it to your configuration's imports and rebuild manually:"
        warn "    imports = [ ./entropy-shield.nix ];"
        warn "    sudo nixos-rebuild switch"
        _print_success_nixos
        return
    fi

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
    step "Creating '$APP_GROUP' group"
    if getent group "$APP_GROUP" >/dev/null 2>&1; then
        ok "Group '$APP_GROUP' already exists."
    else
        groupadd --system "$APP_GROUP" && ok "Group '$APP_GROUP' created." \
            || warn "Could not create group '$APP_GROUP'."
    fi

    step "Adding $REAL_USER to '$APP_GROUP' group"
    if [[ -n "$REAL_USER" ]]; then
        usermod -aG "$APP_GROUP" "$REAL_USER" \
            && ok "$REAL_USER added to '$APP_GROUP' (re-login required)." \
            || warn "Could not add $REAL_USER to '$APP_GROUP'."
    else
        warn "Could not determine the installing user — add it manually:"
        warn "    sudo usermod -aG $APP_GROUP <your-user>"
    fi

    step "Copying application to $DEST"
    _copy_app
    ok "Application copied (root-owned, user-read-only)."

    step "Installing icon"
    if [[ -n "$ICON_SRC" ]]; then
        install -Dm644 "$ICON_SRC" "$ICON_SYS_PIX"
        for size in "${ICON_SIZES[@]}"; do
            local sized="$SCRIPT_DIR/logos/icons/entropy-shield-$size.png"
            [[ -f "$sized" ]] || continue
            install -Dm644 "$sized" "$ICON_HICOLOR/${size}x${size}/apps/entropy-shield.png"
        done
        gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
        ok "Icon installed."
    else
        warn "No logo file found in logos/ — skipping icon install."
    fi

    step "Creating launcher → $WRAPPER"
    cat > "$WRAPPER" <<'EOF'
#!/usr/bin/env bash
# Entropy Shield launcher — runs as the normal user; privileged operations go
# through the root entropy-shield daemon over its group-restricted socket.
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
Comment=Network Privacy Stack — Tor, DNSCrypt, I2P — for Maze Linux
Exec=entropy-shield
Icon=${icon_path}
Type=Application
Categories=Network;Security;
Terminal=false
StartupWMClass=entropy-shield
EOF
    update-desktop-database /usr/share/applications 2>/dev/null || true
    ok "Desktop entry created."

    # NOTE: No polkit/pkexec policy is installed anymore.  Privileged operations
    # run inside the root daemon below; the GUI never escalates privileges.  Any
    # leftover policy from an older version is removed during uninstall.
    rm -f "$POLKIT" 2>/dev/null || true

    step "Installing & enabling the privileged daemon service"
    if [[ -f "$SCRIPT_DIR/entropy-shield.service" ]]; then
        install -Dm644 "$SCRIPT_DIR/entropy-shield.service" "$SYSTEMD_SERVICE"
        systemctl daemon-reload 2>/dev/null || true
        ok "Service file installed → $SYSTEMD_SERVICE"
        if systemctl enable --now entropy-shield 2>/dev/null; then
            ok "entropy-shield daemon enabled and started."
        else
            warn "Could not enable the service automatically. Start it with:"
            warn "    sudo systemctl enable --now entropy-shield"
        fi
    else
        warn "entropy-shield.service not found — daemon not installed."
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
    echo ""
    warn "IMPORTANT: you were added to the '$APP_GROUP' group."
    warn "Log out and back in (or reboot) before launching, otherwise the GUI"
    warn "cannot reach the privileged daemon and Connect will fail."
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
    echo ""
    warn "IMPORTANT: you were added to the '$APP_GROUP' group."
    warn "Log out and back in (or reboot) before launching, otherwise the GUI"
    warn "cannot reach the privileged daemon and Connect will fail."
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
#  Clean previous install before installing
# ─────────────────────────────────────────────────────────────────────────────

_clean_previous() {
    # SECURITY: always remove privilege-escalation artifacts from older versions,
    # even if $DEST was removed manually.  Pre-daemon releases installed a
    # passwordless sudoers rule and a pkexec polkit policy — leaving either in
    # place after upgrading would defeat the whole point of the daemon model.
    if [[ -f "$SUDOERS_FILE" ]]; then
        rm -f "$SUDOERS_FILE" && ok "Removed legacy passwordless sudoers rule."
    fi
    if [[ -f "$POLKIT" ]]; then
        rm -f "$POLKIT" && ok "Removed legacy pkexec polkit policy."
    fi

    if [[ -d "$DEST" ]]; then
        info "Existing installation found — performing clean reinstall..."
        # Stop the old daemon before overwriting its unit/code (this also rolls
        # back any active session; system tor/dnscrypt/i2pd are left alone).
        systemctl stop entropy-shield 2>/dev/null || true
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
# All supported distros are systemd-based — refuse anything else up front.
require_systemd
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
    die "Your distribution (${PRETTY_NAME:-$DISTRO_ID}) is not supported.

Supported distributions:
  • Arch / Manjaro / EndeavourOS / Garuda / CachyOS
  • Debian / Ubuntu / Mint / Pop!_OS / elementary / Kali / Zorin / Parrot
  • Fedora / RHEL / CentOS / AlmaLinux / Rocky / Nobara
  • openSUSE (Leap / Tumbleweed)
  • NixOS

If your distribution is a derivative of one of the above, you can force a
matching package path:
    DISTRO_ID=arch   sudo bash install.sh
    DISTRO_ID=debian sudo bash install.sh
    DISTRO_ID=fedora sudo bash install.sh"
fi
