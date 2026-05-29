<div align="center">

<br/>

<img src="logos/oled.png" alt="Entropy Shield" width="110" />

# Entropy Shield

**A modern Linux desktop privacy stack — Tor · DNSCrypt · I2P · Onion Server**

[![Website](https://img.shields.io/badge/Website-entropy--shield.berkkucukk.com-0f172a?style=flat-square&logo=googlechrome&logoColor=white)](https://entropy-shield.berkkucukk.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-6.4%2B-41CD52?style=flat-square&logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![Platform](https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=black)](https://kernel.org)
[![NixOS](https://img.shields.io/badge/NixOS-Ready-5277C3?style=flat-square&logo=nixos&logoColor=white)](https://nixos.org)

<br/>

*One-click control over your entire privacy layer stack.*

</div>

---

## Table of Contents

- [Overview](#overview)
- [Screenshots](#screenshots)
- [Features](#features)
- [Privacy Layers](#privacy-layers)
- [Themes](#themes)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Supported Distributions](#supported-distributions)
- [License](#license)

---

## Overview

Entropy Shield is a graphical frontend for managing multiple privacy and anonymity services on Linux. Instead of manually configuring `torrc`, writing nftables rules, and restarting daemons, Entropy Shield does it all through a single polished interface.

It routes your traffic through whichever combination of layers you choose — Tor transparent proxy, encrypted DNS via DNSCrypt, the I2P anonymity network, or a self-hosted Tor hidden service — then tears everything back down cleanly when you disconnect. System configs are backed up before modification and restored on exit.

> **Website:** [entropy-shield.berkkucukk.com](https://entropy-shield.berkkucukk.com)

---

## Screenshots

### OLED — Pure black, ambient glow

<div align="center">
<img src="screenshots/oled.png" alt="OLED Theme" width="520" />
</div>

<br/>

### Light — Clean white panels

<div align="center">
<img src="screenshots/light.png" alt="Light Theme" width="520" />
</div>

<br/>

### Binary — Hacker terminal with live binary rain

<div align="center">
<img src="screenshots/binary.png" alt="Binary Theme" width="520" />
</div>

<br/>

### Circuit — PCB trace background, dark grey

<div align="center">
<img src="screenshots/circuit.png" alt="Circuit Theme" width="520" />
</div>

<br/>

### Pixel — Retro pixel art, Minecraft-inspired monochrome

<div align="center">
<img src="screenshots/pixel.png" alt="Pixel Theme" width="520" />
</div>

<br/>

### System Tray

<div align="center">
<img src="screenshots/tray_icon.png" alt="System Tray" width="300" />
</div>

---

## Features

| | Feature |
|---|---|
| 🔒 | **Layered privacy** — combine Tor, DNSCrypt, I2P, and Onion Server in any combination |
| 🔥 | **Firewall integration** — nftables/iptables rules applied and removed automatically |
| 🛡️ | **IPv6 leak protection** — separate `table ip6` rules block IPv6 leaks under Tor; DNSCrypt redirects IPv6 DNS queries too |
| 🧅 | **Onion Server** — publish any local directory as a Tor hidden service with a built-in HTTP file server |
| 🦊 | **Privacy browsers** — launch isolated Firefox instances pre-configured for Tor or I2P without touching your normal profile |
| 🎨 | **5 themes** — OLED, Light, Binary, Circuit, Pixel; each with unique palette, animated background, and font |
| 🗂️ | **System tray** — minimize to tray; Show/Hide, Connect/Disconnect, and Quit from the notification area |
| ♻️ | **Zero footprint** — all config changes are backed up and reverted on disconnect |
| ⚙️ | **Per-service settings** — configure ports, exit nodes, DNSSEC, bandwidth limits, serve directory, and more |
| 💀 | **Kill switch** — if a privacy service drops while connected, all layers disconnect automatically |
| 🚀 | **Auto-connect** — optionally connect with current layer selection on application startup |
| 📶 | **Real-time speed** — live download/upload speed bar while connected |
| 🐧 | **Multi-distro support** — one universal installer for Arch, Debian, Fedora, openSUSE, NixOS |
| ❄️ | **NixOS native** — declarative NixOS module, no mutable config patching |

---

## Privacy Layers

<table>
<tr>
<td align="center" width="25%">

### 🧅 Tor

Transparent proxy that routes **all TCP traffic** through the Tor network using nftables `REDIRECT` rules. DNS queries are redirected to Tor's `DNSPort`. **All IPv6 is blocked** to prevent leaks since Tor's TransPort is IPv4-only. Supports custom exit nodes and `StrictNodes`.

</td>
<td align="center" width="25%">

### 🔐 DNSCrypt

Encrypts DNS queries using [dnscrypt-proxy](https://github.com/DNSCrypt/dnscrypt-proxy). Redirects both **IPv4 and IPv6** DNS traffic through the proxy to prevent leaks. Enforces no-log and no-filter server requirements. Integrates with `systemd-resolved` via `resolvectl`.

</td>
<td align="center" width="25%">

### 🌐 I2P

Starts [i2pd](https://i2pd.website) and configures its HTTP proxy and SOCKS proxy. When `redsocks` is installed, enables **full transparent proxy** mode for all TCP. When used together with Tor, I2P's outbound traffic tunnels through Tor's SOCKS port for additional anonymity.

</td>
<td align="center" width="25%">

### 📡 Onion Server

Starts a built-in **HTTP file server** and publishes it as a Tor hidden service. Choose any directory to serve — its contents become accessible at a `.onion` address shown in the activity log. Requires Tor to be active (enforced automatically).

</td>
</tr>
</table>

---

## Privacy Browsers

The **TOR BROWSER** and **I2P BROWSER** buttons (enabled after connecting) launch Firefox with a **fully isolated temporary profile** pre-configured for the active network. Your normal Firefox profile is never touched.

| Button | Proxy configuration |
|---|---|
| TOR BROWSER | SOCKS5 → `127.0.0.1:9050` with remote DNS (`socks_remote_dns=true`), WebRTC disabled |
| I2P BROWSER | HTTP proxy → `127.0.0.1:4444`, SOCKS5 → `127.0.0.1:4447`, homepage set to I2P router console |

Both instances disable DNS prefetch, HTTPS prefetch, and media peer connections to prevent any DNS or IP leaks.

> **Note:** Browsers with DNS-over-HTTPS (DoH) enabled bypass nftables rules entirely since DoH uses regular HTTPS (port 443). The privacy browser instances have DoH disabled. For your normal browser, disable DoH manually if you rely on DNSCrypt.

---

## Themes

Entropy Shield ships with five hand-crafted themes, each with its own color palette, background animation, font, and window aesthetic. All themes feature rounded corners and an animated glow border that shifts color with connection state.

| Theme | Style | Font | Background Effect |
|---|---|---|---|
| **OLED** | Pure black, white accents | Inter / SF Pro | Ambient radial blobs |
| **Light** | White panels, dark text | Inter / SF Pro | Ambient radial blobs |
| **Binary** | Dark black, white highlights | JetBrains Mono / Fira Code | Animated binary rain (0s and 1s) |
| **Circuit** | Near-black with blue-grey tones | Inter / SF Pro | PCB grid with animated node glows |
| **Pixel** | Deep black, monochrome grey scale | Pixeled (pixel-art bitmap) | Animated Minecraft-style pixel blocks |

Switch themes at any time via **Settings → General → Theme**. The entire interface — window frame, service cards, status ring, overlays, and tray icon — updates instantly.

---

## Requirements

- **OS:** Linux (systemd-based)
- **Python:** 3.10 or later
- **PyQt6:** 6.4 or later
- **Privileges:** Root (via `pkexec` — polkit policy installed automatically)

**Privacy service dependencies** (installed automatically by the installer):

| Service | Package |
|---|---|
| Tor | `tor` |
| DNSCrypt | `dnscrypt-proxy` |
| I2P | `i2pd` |
| Transparent I2P proxy (optional) | `redsocks` |
| Firefox (privacy browser buttons) | `firefox` or `firefox-esr` |
| Firewall | `nftables` / `iptables` |

---

## Installation

### Universal Installer (Recommended)

The universal installer automatically detects your distribution and installs all dependencies.

```bash
git clone https://github.com/berkkucukk/entropy-shield.git
cd entropy-shield
sudo bash install.sh
```

The installer handles everything:

- Package installation per distro (pacman / apt / dnf / zypper / nix)
- PyQt6 via system package with pip fallback (PEP 668 compliant)
- Desktop entry, application icon, launcher wrapper at `/usr/local/bin/entropy-shield`
- Polkit policy so `pkexec` works without repeated password prompts
- SELinux context labels on Fedora / RHEL
- NixOS module generation + `nixos-rebuild switch`
- Clean reinstall support: removes previous installation before copying

> For an unrecognised distro, override detection:
> ```bash
> DISTRO_ID=arch sudo bash install.sh
> ```

### Arch Linux — AUR (redsocks)

`redsocks` is available in the AUR and provides transparent I2P proxy support. Install it with your preferred AUR helper:

```bash
paru -S redsocks
# or
yay -S redsocks
```

### Uninstall

```bash
sudo bash install.sh --uninstall
```

---

### NixOS

The NixOS installer writes a declarative module to `/etc/nixos/entropy-shield.nix` and patches `configuration.nix` to import it, then runs `nixos-rebuild switch`. Services are defined as on-demand systemd units with **no `wantedBy`** — they never auto-start at boot; Entropy Shield controls them entirely.

```nix
# Automatically added to /etc/nixos/configuration.nix:
imports = [ ./entropy-shield.nix ];
```

---

### Manual / Development

```bash
git clone https://github.com/berkkucukk/entropy-shield.git
cd entropy-shield
pip install PyQt6
sudo python3 main.py
```

---

## Usage

Launch from the application menu or run:

```bash
entropy-shield
```

The application requests elevated privileges via `pkexec` on first launch. After the polkit policy is installed, subsequent launches authenticate transparently without a password dialog.

**Workflow:**

1. Toggle the service cards you want to activate (Tor, DNSCrypt, I2P, Onion Server)
2. Click **CONNECT** — services start, firewall rules are applied, DNS is redirected
3. The status ring turns and the border glows to confirm protection is active
4. Optionally click **TOR BROWSER** or **I2P BROWSER** to open an isolated Firefox window
5. Click **DISCONNECT** to stop all services and restore the original system configuration

**Onion Server:**

1. Open Settings → **ONION SERVER** tab
2. Set the directory you want to publish and configure ports
3. Enable the **ONION SERVER** card (Tor is activated automatically)
4. Click **CONNECT** — your `.onion` address appears in the activity log once Tor bootstraps

**System tray:** Closing the window minimises to the system tray. Click the tray icon to show a menu with **Show / Hide**, **Connect / Disconnect**, and **Quit**.

---

## Configuration

Settings are stored at `~/.config/entropy-shield/config.json` and can be edited through the in-app **Settings** panel (⚙ button in the top-right corner).

<details>
<summary>Default configuration</summary>

```json
{
  "theme": "oled",
  "kill_switch": true,
  "auto_connect": false,
  "autostart": false,
  "tor": {
    "trans_port": 9040,
    "dns_port": 5300,
    "socks_port": 9050,
    "exit_nodes": "",
    "strict_nodes": false
  },
  "dnscrypt": {
    "port": 5353,
    "require_dnssec": false,
    "require_nolog": true,
    "require_nofilter": true
  },
  "i2p": {
    "http_port": 4444,
    "socks_port": 4447,
    "max_bandwidth": 0
  },
  "onion_server": {
    "local_port": 8080,
    "hs_port": 80,
    "serve_dir": ""
  }
}
```

</details>

### Tor Exit Nodes

Restrict exit traffic to specific countries by entering ISO codes in **Settings → Tor**:

```
Exit Nodes:   {us},{de},{nl}
Strict Nodes: ✓
```

### DNSCrypt Server Requirements

| Option | Description |
|---|---|
| Require no-log | Only use resolvers that do not log queries |
| Require no-filter | Exclude resolvers that apply content filtering |
| Require DNSSEC | Only use DNSSEC-validating resolvers |

### Onion Server Options

| Option | Description |
|---|---|
| Serve Directory | Local folder to publish — leave blank to use your home directory |
| Local HTTP Port | Port the built-in HTTP server binds on (`127.0.0.1`) |
| Onion Port | Port exposed on the `.onion` address (usually `80`) |

---

## Architecture

```
entropy-shield/
├── main.py                  # Entry point — privilege escalation via pkexec
├── install.sh               # Universal distro-detecting installer
├── core/
│   ├── config.py            # JSON config with deep-merge defaults
│   ├── connection.py        # Orchestrates all layers (connect / disconnect)
│   ├── tor.py               # torrc patching, DNS redirect, systemd control
│   ├── dnscrypt.py          # dnscrypt-proxy config, IPv6 listen, resolved integration
│   ├── i2p.py               # i2pd config, redsocks transparent proxy, Tor-tunnel mode
│   ├── onion_server.py      # Tor hidden service config + built-in HTTP file server
│   ├── browser.py           # Isolated Firefox launcher (Tor / I2P profiles)
│   ├── firewall.py          # nftables / iptables rules, IPv6 leak prevention
│   ├── autostart.py         # XDG autostart entry management
│   └── tray_helper.py       # System tray subprocess (runs as real user)
├── gui/
│   ├── main_window.py       # Main window, animated glow border, worker thread
│   ├── settings_panel.py    # Slide-in settings overlay
│   ├── themes.py            # 5-theme palette + QSS generation with per-theme font scaling
│   └── widgets.py           # ServiceCard, StatusRing, Spinner, ToggleSwitch, NetSpeedBar
├── logos/
│   ├── oled.png             # OLED theme logo (square)
│   ├── dark.png             # Dark variant logo
│   ├── binary.png           # Binary theme logo
│   ├── circuit.png          # Circuit theme logo
│   └── pixel.png            # Pixel theme logo
└── screenshots/
    ├── oled.png
    ├── light.png
    ├── binary.png
    ├── circuit.png
    ├── pixel.png
    └── tray_icon.png
```

### How it works

Entropy Shield runs as root (via `pkexec`) to manage system services and firewall rules. The system tray helper is launched as a subprocess under the original user's session to access the D-Bus session bus and register the SNI tray icon — this avoids the common problem where root processes cannot reach the user's display server.

Privacy browser instances are also launched under the real user's identity (not root) with a temporary isolated profile written to `/tmp/entropy-shield-ff-{tor,i2p}/`.

**CONNECT flow:**

1. Selected service configs are patched (originals backed up with `.entropy-shield.bak` suffix)
2. If Onion Server is enabled, the hidden service block is appended to `torrc` and the HTTP file server starts on `127.0.0.1:local_port`
3. Services are started via `systemctl restart`
4. `systemd-resolved` is pointed at the active proxy via `resolvectl` (when running)
5. `FirewallManager` applies nftables rules:
   - Tor mode: TCP → `TransPort`, DNS → `DNSPort`, **all IPv6 dropped**
   - DNSCrypt mode: DNS (UDP+TCP, IPv4 and IPv6) → dnscrypt-proxy
   - I2P mode: optionally TCP → `redsocks` → i2pd SOCKS

**DISCONNECT flow:**

1. DNS settings are restored via `resolvectl`
2. Firewall rules are flushed (`table ip` and `table ip6`)
3. HTTP file server is shut down (non-blocking background thread)
4. All started services are stopped
5. Config files are restored from `.entropy-shield.bak` backups
6. System proxy environment variables are cleared

### DNS leak prevention

| Scenario | IPv4 DNS | IPv6 DNS |
|---|---|---|
| Tor active | Redirected to `DNSPort` | Entire IPv6 stack blocked |
| DNSCrypt active | Redirected to dnscrypt-proxy | Redirected to `[::1]:port` (dnscrypt-proxy listens on both) |
| Tor + DNSCrypt | Redirected through dnscrypt-proxy | IPv6 stack blocked by Tor rules |

---

## Supported Distributions

| Distribution | Package Manager | Notes |
|---|---|---|
| Arch Linux, Manjaro, EndeavourOS, Garuda, CachyOS | `pacman` | All packages in official repos |
| Debian, Ubuntu, Linux Mint, Kali, Pop!\_OS, Zorin, Parrot | `apt` | i2pd may require a third-party repo |
| Fedora, RHEL, AlmaLinux, Rocky Linux, Nobara | `dnf` | dnscrypt-proxy / i2pd via Copr if absent from main repo |
| openSUSE Leap / Tumbleweed | `zypper` | |
| NixOS | `nixos-rebuild` | Declarative module — no mutable config files |

---

## License

MIT © [Berk Küçük](https://berkkucukk.com)

---

<div align="center">

**[entropy-shield.berkkucukk.com](https://entropy-shield.berkkucukk.com)**

<sub>Built with Python · PyQt6 · Tor · DNSCrypt · I2P · Onion Server</sub>

</div>
