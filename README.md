<p align="center">
  <img src="https://raw.githubusercontent.com/berk-kucuk/entropy-shield/main/logos/entropy-logo.png" width="120" alt="Entropy Shield logo" />
</p>

<h1 align="center">Entropy Shield</h1>

<p align="center">
  A desktop application for Linux that routes your network traffic through Tor, DNSCrypt, I2P and Lokinet.<br>
  Built with Python and PyQt6.
</p>

---

A graphical interface to control each privacy layer, monitor activity in real time, and configure service settings without touching configuration files manually.

---

## Screenshots

<table>
  <tr>
    <th>Disconnected</th>
    <th>Connected</th>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/berk-kucuk/entropy-shield/main/screenshots/disconnected.png" alt="Disconnected state" width="420"/></td>
    <td><img src="https://raw.githubusercontent.com/berk-kucuk/entropy-shield/main/screenshots/connected.png" alt="Connected state" width="420"/></td>
  </tr>
</table>

<table>
  <tr>
    <th>Tray Icon</th>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/berk-kucuk/entropy-shield/refs/heads/main/screenshots/tray_icon.png" alt="Disconnected state" width="420"/></td>
  </tr>
</table>

---

## What it does

- **Tor**: Routes all TCP traffic through the Tor network via transparent proxying. Your real IP address is hidden from the sites and services you connect to.
- **DNSCrypt**: Encrypts and authenticates all DNS queries so your ISP cannot see or tamper with which hostnames you resolve.
- **I2P**: Connects to the I2P anonymous overlay network via i2pd. Useful for accessing I2P-internal services (.i2p domains).
- **Lokinet**: An onion-routing network using the LLARP protocol that operates at the network layer (Layer 3). It tunnels TCP, UDP, and ICMP traffic, providing high-speed access to .loki domains and clearnet exit nodes with lower latency than traditional overlays.

You can enable any combination. When you press Connect, the application applies firewall rules (nftables or iptables) and starts the selected services. When you press Disconnect, everything is reversed and traffic flows normally again.

The application must run as root because it modifies firewall rules and network configuration. It uses `pkexec` (Polkit) to ask for your password — it does not run permanently as a system service.

---

## Supported distributions

| Distribution | Installer |
|---|---|
| Debian, Ubuntu, and derivatives | `installers/install-debian.sh` |
| Fedora, RHEL, CentOS Stream | `installers/install-fedora.sh` |
| Arch Linux, Manjaro, Endeavour | `installers/install-arch.sh` |
| NixOS | `installers/install-nixos.sh` |

---

## Requirements

### All distributions
- Python 3.10 or newer
- PyQt6
- Tor
- dnscrypt-proxy
- i2pd
- nftables or iptables
- Polkit (pkexec)

The installers handle all of these automatically.

### NixOS only
- An existing `services.tor.enable = true` entry in your `configuration.nix` is recommended but not required. The installer adds the necessary Tor port settings to your NixOS module.
- `nixos-rebuild switch` is run automatically during installation.

---

## Installation

### Clone or download the project

```
git clone https://github.com/berk-kucuk/entropy-shield.git
cd entropy-shield
```

Or download and extract the archive, then navigate into the folder.

---

### Debian / Ubuntu

```
bash installers/install-debian.sh
```

The script will:
1. Run `apt-get update` and install all required packages
2. Copy the application to `/opt/entropy-shield`
3. Create the `/usr/local/bin/entropy-shield` launcher
4. Install the desktop entry and application icon
5. Create a Polkit policy so the privilege prompt works correctly

---

### Fedora

```
bash installers/install-fedora.sh
```

The script will:
1. Install packages via `dnf`. dnscrypt-proxy and i2pd may come from Copr repositories if not available in the main repo. The script enables them automatically.
2. Copy the application to `/opt/entropy-shield`
3. Create the launcher, desktop entry, and icon
4. Create a Polkit policy
5. Apply SELinux context if SELinux is enforcing

---

### Arch Linux

```
bash installers/install-arch.sh
```

The script will:
1. Install packages via `pacman`. PyQt6 is installed from the official repos (`python-pyqt6`).
2. Copy the application to `/opt/entropy-shield`
3. Create the launcher, desktop entry, and icon
4. Create a Polkit policy

---

### NixOS

```
bash installers/install-nixos.sh
```

NixOS installation is different from the others because the system is managed declaratively.

The script will:
1. Stop and disable any conflicting service instances from a previous install
2. Copy the application to `/opt/entropy-shield`
3. Write `/etc/nixos/entropy-shield.nix`, a NixOS module that:
   - Provides Python with PyQt6, dnscrypt-proxy, and i2pd
   - Defines systemd units for dnscrypt-proxy and i2pd **without** `wantedBy`, so they never start automatically at boot
   - Adds Tor transparent proxy ports (`TransPort 9040`, `DNSPort 5300`) to your existing Tor configuration
   - Adds a Polkit rule allowing the `wheel` group to run the app as root
   - Registers the application in the KDE/GNOME application menu via `pkgs.makeDesktopItem`
4. Patch `/etc/nixos/configuration.nix` to import `./entropy-shield.nix`
5. Run `nixos-rebuild switch`

A backup of your original `configuration.nix` is saved as `configuration.nix.entropy-shield.bak` the first time you install. Subsequent reinstalls preserve this backup.

After installation, the `entropy-shield` command is available in your PATH via the Nix store.

---

## Running the application

After installation, you can launch Entropy Shield from your desktop application menu (look for it under Network or Security), or from the terminal:

```
entropy-shield
```

The application will ask for your password via a Polkit dialog on first run. This is required because it needs root access to configure firewall rules.

---

## How to use

1. Open the application.
2. In the main window, use the toggle switches on each service card to select which layers you want to enable. All three are enabled by default.
3. Press **Connect**. The application will start the selected services and apply firewall rules. The status ring in the center will turn green when the connection is active.
4. To verify: open a DNS leak test site (for example dnsleaktest.com) and run an extended test. The DNS servers shown should not be your ISP's servers.
5. Press **Disconnect** to stop all services and remove all firewall rules. Traffic returns to its normal path.

### Layer combinations

- **Tor only**: All TCP traffic is routed through Tor. DNS is resolved anonymously through Tor's internal DNS resolver.
- **DNSCrypt only**: DNS queries are encrypted and authenticated. Your TCP connections still use your real IP. Use this if you want encrypted DNS without routing all traffic through Tor.
- **Tor + DNSCrypt**: TCP traffic goes through Tor. DNS queries are encrypted by DNSCrypt rather than resolved through Tor's DNS port. This is the highest-privacy combination for most users.
- **I2P**: Starts i2pd and sets your system HTTP proxy to `127.0.0.1:4444`. Used for accessing I2P-internal services. Can be combined with Tor.

---

## Settings

Click the gear button in the top right corner to open the settings panel. You can configure:

- **Tor**: TransPort, DNSPort, SocksPort, exit node countries, StrictNodes
- **DNSCrypt**: listen port, require DNSSEC, require no-log, require no-filter
- **I2P**: HTTP proxy port, SOCKS proxy port, bandwidth limit
- **Theme**: dark or light

Settings are saved to `~/.config/entropy-shield/config.json`.

On NixOS, service configuration files are managed by the NixOS module and are not modified at runtime. Port settings in the GUI still apply to how the firewall rules are constructed.

---

## How it works internally

### Firewall

When you connect, the application writes firewall rules using nftables (preferred) or iptables as a fallback. On NixOS, nftables is always used since the system firewall is nftables-based.

For Tor transparent proxying, an `ip entropy-shield` table is created with a `nat output` chain that:
- Returns traffic from the Tor process itself (to prevent routing loops)
- Returns traffic destined for local networks
- Redirects all DNS (UDP/TCP port 53) to Tor's DNSPort or dnscrypt-proxy
- Redirects all outgoing TCP SYN packets to Tor's TransPort

For DNSCrypt only, only the DNS redirect rules are applied.

When you disconnect, the `entropy-shield` table is deleted entirely.

### DNS on NixOS

NixOS may not run `systemd-resolved`. In this case, the nftables DNS redirect handles everything. If `systemd-resolved` is running, the application additionally configures it via `resolvectl` to route all queries through the active service, and creates a drop-in file at `/run/systemd/resolved.conf.d/entropy-shield.conf` that persists across NetworkManager reconnections.

---

## Uninstallation

### Debian / Fedora / Arch

```
sudo rm -rf /opt/entropy-shield
sudo rm -f /usr/local/bin/entropy-shield
sudo rm -f /usr/share/applications/entropy-shield.desktop
sudo rm -f /usr/share/pixmaps/entropy-shield.png
sudo rm -f /usr/share/icons/hicolor/256x256/apps/entropy-shield.png
sudo rm -f /usr/share/polkit-1/actions/org.entropyshield.policy
```

### NixOS

1. Remove `./entropy-shield.nix` from the `imports` list in `/etc/nixos/configuration.nix`
2. Delete the module file: `sudo rm /etc/nixos/entropy-shield.nix`
3. Delete the application: `sudo rm -rf /opt/entropy-shield`
4. Run `sudo nixos-rebuild switch`

Your original `configuration.nix` backup is at `/etc/nixos/configuration.nix.entropy-shield.bak` if you need to restore it.

---

## Troubleshooting

### The application does not appear in the application menu (NixOS)

Run `kbuildsycoca6 --noincremental` as your normal user to force KDE to rebuild its application menu database. This usually resolves the issue immediately after installation.

### "tor is not installed" or "dnscrypt-proxy is not installed"

The service binary is not in PATH. On NixOS, this can happen if `nixos-rebuild switch` did not complete successfully. Run it manually: `sudo nixos-rebuild switch`. On other distributions, re-run the installer.

### Traffic does not appear to go through Tor

Check that the firewall rules are active: `sudo nft list table ip entropy-shield`. If the table does not exist, the rules were not applied. Look at the activity log inside the application for error messages.

### DNS is not encrypted (DNSCrypt)

Run `systemctl status dnscrypt-proxy` to confirm the service is active. Then run a DNS leak test. If your ISP's DNS still appears, check the activity log for errors during connection. On NixOS, also verify the dnscrypt-proxy config is present: `cat /etc/dnscrypt-proxy/dnscrypt-proxy.toml`.

### The privilege dialog does not appear

Ensure Polkit is running: `systemctl status polkit`. On NixOS, the installer adds a Polkit rule for the `wheel` group. Make sure your user is in the `wheel` group: `groups $USER`.

### Internet connection lost after applying NixOS module

This can happen if a previous installation left dnscrypt-proxy or i2pd running and interfering with system DNS. Re-run the installer — it will stop and clean up those services before reinstalling.

---

## Project structure

```
entropy-shield/
  main.py                  Entry point. Handles privilege escalation via pkexec.
  logos/
    entropy-logo.png       Application icon.
  core/
    config.py              JSON configuration singleton.
    connection.py          Orchestrates connect/disconnect across all layers.
    firewall.py            Applies and removes nftables/iptables rules.
    tor.py                 Tor service lifecycle and configuration.
    dnscrypt.py            DNSCrypt service lifecycle and configuration.
    i2p.py                 I2P (i2pd) service lifecycle and configuration.
    platform.py            OS detection and firewall backend selection.
  gui/
    main_window.py         Main application window.
    widgets.py             Custom widgets: ToggleSwitch, ServiceCard, StatusRing, Spinner.
    themes.py              Dark and light colour palettes and Qt stylesheets.
    settings_panel.py      Slide-in settings panel.
  installers/
    install-debian.sh
    install-fedora.sh
    install-arch.sh
    install-nixos.sh
```

---

## License

This project is released under the MIT License.
