# 🍞 Toast Browser

**Self-hosted, Docker-based isolated browser profiles.**  
A single-user alternative to NetworkChuck's cloud browser — runs entirely on your machine, zero cloud dependency, zero subscription.

---

## What This Is

NetworkChuck's browser is built on [Kasm Workspaces](https://www.kasmweb.com/) — a VNC container streaming platform. toast Browser takes the same core idea and strips it down to exactly what a single user needs:

- **Multiple browser profiles** each running in their own Docker container
- **True fingerprint isolation** — separate filesystem, network, browser engine, locale, timezone
- **VNC streaming** — the browser runs headless inside the container; your local browser sees only pixels
- **Persistent or disposable** — volumes persist by default, `toast.sh nuke <profile>` wipes for a fresh fingerprint
- **Dashboard UI** — one page to launch/stop/manage all profiles

No enterprise features. No user management. No telemetry. No monthly bill.

---

## Architecture

```
Your Browser (localhost)
       │
       ├─ :8080 → Dashboard (Flask + Docker API)
       │
       ├─ :6901 → toast_work      (Chrome,   en-US/NY)
       ├─ :6902 → toast_personal  (Firefox,  en-GB/London)
       ├─ :6903 → toast_research  (Tor,      de-DE/Berlin)
       ├─ :6904 → toast_social    (Chrome,   en-AU/Sydney)
       ├─ :6905 → toast_banking   (Firefox,  en-US/Chicago)
       └─ :6906 → toast_dev       (Chromium, en-US/LA)

Each container:
  ├── Own Docker volume   (cookies, history, extensions)
  ├── Own IP on toast_net (172.20.0.x)
  ├── Own locale + TZ     (affects JS navigator, Date APIs)
  └── KasmVNC streamed    (browser code never runs locally)
```

---

## Why Profiles Can't Be Linked

| Fingerprint Vector | Isolation Method |
|---|---|
| Cookies / LocalStorage | Separate Docker volume per profile |
| Browser History | Separate Docker volume per profile |
| Canvas / WebGL fingerprint | Separate container, different GPU context |
| User-Agent string | Different browser engine per profile |
| Language / locale | `LANG` env var per container |
| Timezone | `TZ` env var per container |
| IP address | Each container has its own IP on the bridge |
| Screen resolution | KasmVNC reports container display, not your monitor |
| Font enumeration | Container filesystem fonts only |
| CPU / hardware info | Containerized; no host hardware access |

---

## Quick Start

### Requirements

- Docker + Docker Compose v2
- Linux / macOS / WSL2
- ~8GB disk for all images (~1.3GB each)
- Minimum 4GB RAM (2GB+ recommended per active profile)

### Install

```bash
git clone https://github.com/SecTrollz/cloudbrowser.git
cd cloudbrowser
chmod +x setup.sh toast.sh
./setup.sh
```

That's it. Dashboard opens at **http://localhost:8080**

---

## Profiles

| Profile | Browser | Locale | Use Case |
|---|---|---|---|
|  work | Chrome | en-US / New York | Work accounts, Google Workspace |
|  personal | Firefox | en-GB / London | Personal email, shopping |
|  research | Tor Browser | de-DE / Berlin | Anonymous research, OSINT |
|  social | Chrome | en-AU / Sydney | Social media, forums |
|  banking | Firefox | en-US / Chicago | Financial accounts |
|  dev | Chromium | en-US / LA | Dev tools, localhost testing |

---

## Management

```bash
# Status overview
./toast.sh status

# Start/stop a profile
./toast.sh start work
./toast.sh stop work

# Open a profile directly
./toast.sh open banking

# Wipe a profile (fresh fingerprint)
./toast.sh nuke research

# Tail logs
./toast.sh logs social

# All up / all down
./toast.sh up
./toast.sh down
```

---

## Adding a New Profile

1. Add a new service block to `docker-compose.yml` following the existing pattern
2. Pick a unique port (6907, 6908...) and IP (172.20.0.16...)
3. Set a distinct locale + timezone for fingerprint separation
4. Run `docker compose up -d browser_myprofile`

Available browser images from KasmWeb:
- `kasmweb/chrome:1.16.0`
- `kasmweb/firefox:1.16.0`
- `kasmweb/chromium:1.16.0`
- `kasmweb/tor-browser:1.16.0`
- `kasmweb/brave:1.16.0`
- `kasmweb/opera:1.16.0`

---

## Hardening for Production Use

### 1. Change All VNC Passwords

Edit `.env` before first launch. Passwords must be 6+ chars.

### 2. Add a Reverse Proxy with TLS

Put Caddy or nginx in front for HTTPS:

```caddyfile
toast.yourdomain.local {
    reverse_proxy dashboard:8080
}
work.yourdomain.local {
    reverse_proxy browser_work:6901
}
```

### 3. Add Per-Profile Proxy Routing (HTTP/SOCKS5)

To route each profile through a different proxy, add to the container's environment:

```yaml
environment:
  - http_proxy=socks5://your-proxy:1080
  - https_proxy=socks5://your-proxy:1080
```

Or pass Chrome/Firefox flags via KasmVNC's startup command environment.

### 4. Firewall the VNC Ports

If running on a server, bind ports to localhost only:

```yaml
ports:
  - "127.0.0.1:6901:6901"
```

### 5. Disposable Research Sessions

For truly ephemeral sessions (no persistence), remove the volume mount from a profile:

```yaml
# Remove this line to make the profile disposable:
# volumes:
#   - profile_research:/home/kasm-user
```

---

## Resource Usage

Each running container uses approximately:
- **RAM**: 300–600MB at idle, up to 1.5GB under heavy use
- **CPU**: <5% idle, spikes on page load
- **Disk**: ~1.3GB per image (shared layers reduce total)

Tip: Only start the profiles you're actively using. The dashboard start/stop buttons are your friends.

---

## What Was Removed vs NetworkChuck's Version

| Feature | NetworkChuck | toast Browser | Reason |
|---|---|---|---|
| Enterprise multi-user | ✓ | ✗ | Not needed for 1 user |
| SAML/OIDC SSO | ✓ | ✗ | Overkill |
| Session recording | ✓ | ✗ | Privacy — no recordings |
| Global cloud PoPs | ✓ | ✗ | Self-hosted = you control it |
| Paid subscription | $7–$30/mo | $0 | Self-hosted |
| Admin panel | Full Kasm | Lightweight | Simpler = less attack surface |
| DLP policies | ✓ | ✗ | Single user |
| Auto-destroy on inactivity | ✓ | ✗ | Manual control preferred |

---

## .gitignore

```
.env
profiles/
data/
*.log
```

**Never commit `.env`** — it contains your VNC passwords.

---

## Credits

Built on [KasmVNC](https://github.com/kasmtech/KasmVNC) open source streaming technology and [KasmWeb Docker images](https://hub.docker.com/u/kasmweb). Inspired by the NetworkChuck cloud browser concept.

