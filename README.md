<div align="center">

<br/>

<img src="logos/oled.png" alt="Entropy Shield" width="110" />

# Entropy Shield

### Linux Privacy Stack — Tor Transparent Proxy · DNSCrypt · I2P · Onion Server · GUI

[![Website](https://img.shields.io/badge/Website-entropy--shield.berkkucukk.com-0f172a?style=flat-square&logo=googlechrome&logoColor=white)](https://entropy-shield.berkkucukk.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-6.4%2B-41CD52?style=flat-square&logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![Platform](https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=black)](https://kernel.org)
[![NixOS](https://img.shields.io/badge/NixOS-Ready-5277C3?style=flat-square&logo=nixos&logoColor=white)](https://nixos.org)

<br/>

**A graphical front-end to manage Tor, DNSCrypt-proxy, I2P (i2pd), and Tor hidden services (onion services) on Linux — all in one place.**

*One-click control over your entire anonymity and privacy layer stack.*

</div>

---

## What is Entropy Shield?

**Entropy Shield** is an open-source Linux privacy tool that lets you combine and control multiple anonymity layers through a single GUI:

- **Tor transparent proxy** — route all TCP traffic through the Tor network
- **DNSCrypt / DNS encryption** — encrypt DNS queries, prevent DNS leaks
- **I2P (i2pd)** — access the I2P anonymous network with full transparent proxy support
- **Onion Server** — host a Tor hidden service (.onion address) from any local directory
- **Privacy-hardened Firefox** — isolated browser profiles for Tor and I2P browsing
- **Firewall (nftables/iptables)** — automatic firewall rules with IPv6 leak prevention
- **Kill switch** — auto-disconnect all layers if any privacy service drops

No more editing `torrc` by hand, writing nftables rules, or restarting systemd services manually. Entropy Shield handles everything and restores your original system config on disconnect.

> **Website:** [entropy-shield.berkkucukk.com](https://entropy-shield.berkkucukk.com)

---

## Table of Contents

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

## Screenshots

### OLED — Pure black, ambient glow

<div align="center">
<img src="screenshots/oled.png" alt="Entropy Shield OLED Theme - Tor Privacy GUI Linux" width="520" />
</div>

<br/>

### Light — Clean white panels

<div align="center">
<img src="screenshots/light.png" alt="Entropy Shield Light Theme" width="520" />
</div>

<br/>

### Binary — Hacker terminal with live binary rain

<div align="center">
<img src="screenshots/binary.png" alt="Entropy Shield Binary Theme" width="520" />
</div>

<br/>

### Circuit — PCB trace background, dark grey

<div align="center">
<img src="screenshots/circuit.png" alt="Entropy Shield Circuit Theme" width="520" />
</div>

<br/>

### Pixel — Retro pixel art, Minecraft-inspired monochrome

<div align="center">
<img src="screenshots/pixel.png" alt="Entropy Shield Pixel Theme" width="520" />
</div>

<br/>

### System Tray

<div align="center">
<img src="screenshots/tray_icon.png" alt="Entropy Shield System Tray" width="300" />
</div>

---

## Features

| | Feature |
|---|---|
| 🔒 | **Layered anonymity** — combine Tor, DNSCrypt, I2P, and Onion Server in any combination |
| 🔥 | **Automatic firewall rules** — nftables/iptables applied and removed automatically |
| 🛡️ | **IPv6 leak protection** — `table ip6` rules block all IPv6 under Tor; DNSCrypt redirects IPv6 DNS too |
| 🧅 | **Tor hidden service (onion server)** — publish any local directory as a `.onion` address |
| 🦊 | **Isolated privacy browsers** — launch Firefox pre-configured for Tor or I2P without touching your normal profile |
| 🎨 | **5 themes** — OLED, Light, Binary, Circuit, Pixel |
| 🗂️ | **System tray** — minimize to tray with quick Connect/Disconnect/Quit |
| ♻️ | **Zero footprint** — all config changes are backed up and reverted on disconnect |
| ⚙️ | **Per-service settings** — ports, exit nodes, DNSSEC, bandwidth limits, serve directory |
| 💀 | **Kill switch** — auto-disconnect if a privacy service drops |
| 🚀 | **Auto-connect on startup** — connect with saved layer selection at launch |
| 📶 | **Real-time network speed** — live download/upload bar while connected |
| 🐧 | **Multi-distro support** — Arch, Debian, Fedora, openSUSE, NixOS |
| ❄️ | **NixOS native** — declarative NixOS module, no mutable config patching |

---

## Privacy Layers

<table>
<tr>
<td align="center" width="25%">

### 🧅 Tor Transparent Proxy

Routes **all TCP traffic** through the Tor anonymity network using nftables `REDIRECT` rules. DNS queries go to Tor's `DNSPort`. **All IPv6 is blocked** to prevent leaks (Tor's TransPort is IPv4-only). Supports custom exit nodes and `StrictNodes`.

</td>
<td align="center" width="25%">

### 🔐 DNSCrypt-proxy

Encrypts DNS queries using [dnscrypt-proxy](https://github.com/DNSCrypt/dnscrypt-proxy). Redirects both **IPv4 and IPv6** DNS traffic to prevent DNS leaks. Enforces no-log and no-filter server requirements. Integrates with `systemd-resolved` via `resolvectl`.

</td>
<td align="center" width="25%">

### 🌐 I2P (i2pd)

Starts [i2pd](https://i2pd.website) and configures its HTTP proxy and SOCKS proxy. When `redsocks` is available, enables **full transparent proxy** mode for all TCP. When combined with Tor, I2P outbound traffic tunnels through Tor's SOCKS port for layered anonymity.

</td>
<td align="center" width="25%">

### 📡 Tor Hidden Service (Onion Server)

Starts a built-in **HTTP file server** and publishes it as a Tor hidden service. Serve any local directory — its contents become accessible at a `.onion` address shown in the activity log. Requires Tor to be active (enforced automatically).

</td>
</tr>
</table>

---

## Privacy Browsers — Isolated Firefox for Tor and I2P

The **TOR BROWSER** and **I2P BROWSER** buttons launch Firefox with a **fully isolated temporary profile** pre-configured for the active network. Your normal Firefox profile is never modified.

| Button | Proxy configuration |
|---|---|
| TOR BROWSER | SOCKS5 → `127.0.0.1:9050`, remote DNS enabled, WebRTC disabled |
| I2P BROWSER | HTTP proxy → `127.0.0.1:4444`, SOCKS5 → `127.0.0.1:4447`, homepage set to I2P router console |

Both instances disable DNS prefetch, HTTPS prefetch, and media peer connections to prevent DNS and IP leaks.

> **Note on DNS over HTTPS (DoH):** Browsers with DoH enabled bypass nftables rules since DoH uses port 443. Entropy Shield's privacy browser profiles disable DoH automatically. For your normal browser, disable DoH manually if you rely on DNSCrypt for DNS encryption.

---

## Themes

Five hand-crafted themes, each with its own color palette, background animation, font, and window style. All themes feature rounded corners and an animated glow border that shifts color with connection state.

| Theme | Style | Font | Background Effect |
|---|---|---|---|
| **OLED** | Pure black, white accents | Inter / SF Pro | Ambient radial blobs |
| **Light** | White panels, dark text | Inter / SF Pro | Ambient radial blobs |
| **Binary** | Dark black, white highlights | JetBrains Mono / Fira Code | Animated binary rain (0s and 1s) |
| **Circuit** | Near-black, blue-grey tones | Inter / SF Pro | PCB grid with animated node glows |
| **Pixel** | Deep black, monochrome grey | Pixeled (pixel-art bitmap) | Animated Minecraft-style pixel blocks |

Switch themes at any time via **Settings → General → Theme**. The entire interface updates instantly.

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
| Firefox (privacy browser) | `firefox` or `firefox-esr` |
| Firewall | `nftables` / `iptables` |

---

## Installation

### Universal Installer (Recommended)

Auto-detects your Linux distribution and installs all dependencies.

```bash
git clone https://github.com/berkkucukk/entropy-shield.git
cd entropy-shield
sudo bash install.sh
```

The installer handles:

- Package installation per distro (pacman / apt / dnf / zypper / nix)
- PyQt6 via system package with pip fallback (PEP 668 compliant)
- Desktop entry, application icon, launcher at `/usr/local/bin/entropy-shield`
- Polkit policy so `pkexec` works without repeated password prompts
- SELinux context labels on Fedora / RHEL
- NixOS module generation + `nixos-rebuild switch`
- Clean reinstall support

> For an unrecognised distro, override detection:
> ```bash
> DISTRO_ID=arch sudo bash install.sh
> ```

### Arch Linux — AUR

```bash
paru -S entropy-shield
# or
yay -S entropy-shield
```

### Uninstall

```bash
paru -Rnsc entropy-shield
# or
yay -Rnsc entropy-shield
```

### NixOS

The installer writes a declarative module to `/etc/nixos/entropy-shield.nix` and patches `configuration.nix`, then runs `nixos-rebuild switch`. Services never auto-start at boot — Entropy Shield controls them entirely via on-demand systemd units.

```nix
imports = [ ./entropy-shield.nix ];
```

### Manual / Development

```bash
git clone https://github.com/berkkucukk/entropy-shield.git
cd entropy-shield
pip install PyQt6
sudo python3 main.py
```

---

## Usage

```bash
entropy-shield
```

**Basic workflow:**

1. Toggle the service cards you want — Tor, DNSCrypt, I2P, Onion Server
2. Click **CONNECT** — services start, firewall rules apply, DNS redirects
3. The status ring and border glow confirm active protection
4. Optionally open **TOR BROWSER** or **I2P BROWSER** for an isolated Firefox window
5. Click **DISCONNECT** to stop all services and restore your original system config

**Setting up an Onion Server (.onion hidden service):**

1. Open Settings → **ONION SERVER** tab
2. Set the directory to publish and configure ports
3. Enable the **ONION SERVER** card (Tor activates automatically)
4. Click **CONNECT** — your `.onion` address appears in the activity log

**System tray:** Closing the window minimises to tray. The tray icon gives quick access to Show/Hide, Connect/Disconnect, and Quit.

---

## Configuration

Settings are stored at `~/.config/entropy-shield/config.json` and editable via the in-app **Settings** panel.

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
| Serve Directory | Local folder to publish |
| Local HTTP Port | Port the HTTP server binds on (`127.0.0.1`) |
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
│   ├── themes.py            # 5-theme palette + QSS generation
│   └── widgets.py           # ServiceCard, StatusRing, Spinner, ToggleSwitch, NetSpeedBar
└── screenshots/
```

### How it Works

Entropy Shield runs as root via `pkexec` to manage system services and firewall rules. The system tray helper runs as a subprocess under the original user's session to access the D-Bus session bus and register the SNI tray icon. Privacy browsers also run under the real user's identity with a temporary isolated profile at `/tmp/entropy-shield-ff-{tor,i2p}/`.

**CONNECT flow:**
1. Service configs are patched (originals backed up with `.entropy-shield.bak`)
2. If Onion Server is enabled, hidden service config is appended to `torrc` and the HTTP file server starts
3. Services are started via `systemctl restart`
4. `systemd-resolved` is pointed at the active proxy via `resolvectl`
5. `FirewallManager` applies nftables rules (Tor TransPort redirect, IPv6 drop, DNS redirect)

**DISCONNECT flow:**
1. DNS settings restored via `resolvectl`
2. Firewall rules flushed (`table ip` and `table ip6`)
3. HTTP file server stopped
4. All started services stopped
5. Config files restored from `.entropy-shield.bak` backups
6. System proxy environment variables cleared

### DNS Leak Prevention

| Scenario | IPv4 DNS | IPv6 DNS |
|---|---|---|
| Tor active | Redirected to `DNSPort` | Entire IPv6 stack blocked |
| DNSCrypt active | Redirected to dnscrypt-proxy | Redirected to `[::1]:port` |
| Tor + DNSCrypt | Redirected through dnscrypt-proxy | IPv6 blocked by Tor rules |

---

## Supported Distributions

| Distribution | Package Manager | Notes |
|---|---|---|
| Arch Linux, Manjaro, EndeavourOS, Garuda, CachyOS | `pacman` | All packages in official repos |
| Debian, Ubuntu, Linux Mint, Kali, Pop!\_OS, Zorin, Parrot | `apt` | i2pd may need a third-party repo |
| Fedora, RHEL, AlmaLinux, Rocky Linux, Nobara | `dnf` | dnscrypt-proxy / i2pd via Copr if absent |
| openSUSE Leap / Tumbleweed | `zypper` | |
| NixOS | `nixos-rebuild` | Declarative module |

---

## Related Projects & Keywords

> *This project is related to: tor gui linux, tor transparent proxy linux, dnscrypt gui, i2p linux gui, onion service linux, anonymous browsing linux, privacy linux desktop, dns leak prevention linux, tor frontend linux, i2pd gui, nftables tor, linux anonymity tool, tor proxy manager, dnscrypt-proxy gui, linux privacy software, open source vpn alternative linux, tor network linux app*

---

## License

MIT © [Berk Küçük](https://berkkucukk.com)

---

<div align="center">

**[entropy-shield.berkkucukk.com](https://entropy-shield.berkkucukk.com)**

<sub>Built with Python · PyQt6 · Tor · DNSCrypt · I2P · Onion Server · nftables · Linux</sub>

</div>
