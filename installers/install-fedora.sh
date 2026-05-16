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
WRAPPER=/usr/local/bin/entropy-shield
ICON_SRC="$SRC_DIR/logos/entropy-logo.png"

# ── detect & clean previous installation ─────────────────────
if [[ -d "$DEST" ]]; then
    echo "[*] Existing installation detected — performing clean reinstall..."
    for svc in dnscrypt-proxy i2pd tor; do
        systemctl stop "$svc" 2>/dev/null || true
    done
    rm -rf "$DEST"
    rm -f "$WRAPPER"
    echo "[>] Old installation removed."
else
    echo "[*] Fresh installation..."
fi

# ── packages ──────────────────────────────────────────────────
echo "[*] Installing dependencies..."
dnf install -y -q \
    python3 python3-pip \
    tor \
    nftables \
    iptables \
    iproute \
    polkit

if ! command -v redsocks &>/dev/null; then
    dnf install -y -q redsocks 2>/dev/null || {
        echo "[!] redsocks not in main repo, trying COPR..."
        dnf copr enable -y zawertun/redsocks 2>/dev/null || true
        dnf install -y -q redsocks 2>/dev/null || \
            echo "[!] redsocks not installed. Transparent I2P routing unavailable."
    }
else
    echo "[>] redsocks already installed, skipping."
fi

# dnscrypt-proxy: try official repo then Copr
dnf install -y -q dnscrypt-proxy 2>/dev/null || {
    echo "[!] dnscrypt-proxy not in main repo, trying copr:cromerc/dnscrypt-proxy..."
    dnf copr enable -y cromerc/dnscrypt-proxy 2>/dev/null || true
    dnf install -y -q dnscrypt-proxy 2>/dev/null || \
        echo "[!] dnscrypt-proxy not installed. Install manually if needed."
}

# i2pd
dnf install -y -q i2pd 2>/dev/null || {
    echo "[!] i2pd not in main repo."
    dnf copr enable -y i2p/i2pd 2>/dev/null || true
    dnf install -y -q i2pd 2>/dev/null || \
        echo "[!] i2pd not installed. Install manually if needed."
}

# PyQt6
pip3 install --quiet PyQt6

# ── install application ───────────────────────────────────────
echo "[*] Installing application to $DEST..."
mkdir -p "$DEST"
cp -r "$SRC_DIR/." "$DEST/"
chmod 755 "$DEST/main.py"

# ── install icon ──────────────────────────────────────────────
echo "[*] Installing icon..."
mkdir -p /usr/share/pixmaps
mkdir -p /usr/share/icons/hicolor/256x256/apps
cp "$ICON_SRC" /usr/share/pixmaps/entropy-shield.png
cp "$ICON_SRC" /usr/share/icons/hicolor/256x256/apps/entropy-shield.png
gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

# ── launcher ──────────────────────────────────────────────────
echo "[*] Creating launcher..."
cat > "$WRAPPER" <<'EOF'
#!/usr/bin/env bash
exec python3 /opt/entropy-shield/main.py "$@"
EOF
chmod 755 "$WRAPPER"

# ── desktop entry ─────────────────────────────────────────────
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

# ── polkit policy ─────────────────────────────────────────────
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

# ── SELinux ───────────────────────────────────────────────────
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
