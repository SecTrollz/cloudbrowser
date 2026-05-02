#!/usr/bin/env python3
"""
Ghost Browser Dashboard
Reads Docker labels to auto-discover browser profiles and serves the control panel.
"""

import os
import json
from flask import Flask, render_template_string, jsonify, request, redirect
import docker

app = Flask(__name__)
client = docker.from_env()

def get_profiles():
    """Auto-discover browser profiles from Docker labels."""
    profiles = []
    try:
        containers = client.containers.list(all=True, filters={"label": "ghost.role=browser"})
        for c in containers:
            labels = c.labels
            running = c.status == "running"
            profiles.append({
                "name": labels.get("ghost.profile", "unknown"),
                "browser": labels.get("ghost.browser", "chrome"),
                "color": labels.get("ghost.color", "#6B7280"),
                "icon": labels.get("ghost.icon", "🌐"),
                "port": labels.get("ghost.port", "6901"),
                "container": c.name,
                "running": running,
                "status": c.status,
            })
    except Exception as e:
        print(f"Docker error: {e}")
    return sorted(profiles, key=lambda x: x["name"])

def container_action(name, action):
    try:
        c = client.containers.get(name)
        if action == "start":
            c.start()
        elif action == "stop":
            c.stop(timeout=5)
        elif action == "restart":
            c.restart(timeout=5)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ghost Browser</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface2: #1a1a26;
    --border: rgba(255,255,255,0.07);
    --text: #e8e8f0;
    --muted: #6b6b8a;
    --accent: #7c6cfc;
    --green: #22d3a0;
    --red: #f4476b;
    --mono: 'Space Mono', monospace;
    --sans: 'DM Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
    background-image:
      radial-gradient(ellipse 60% 40% at 70% 10%, rgba(124,108,252,0.08) 0%, transparent 60%),
      radial-gradient(ellipse 40% 30% at 10% 80%, rgba(34,211,160,0.05) 0%, transparent 50%);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 24px 40px;
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(8px);
    position: sticky;
    top: 0;
    z-index: 100;
    background: rgba(10,10,15,0.85);
  }

  .logo {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .logo-mark {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, var(--accent), var(--green));
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
  }

  .logo-text {
    font-family: var(--mono);
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .logo-sub {
    font-size: 11px;
    color: var(--muted);
    font-family: var(--mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .header-meta {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
    display: inline-block;
    margin-right: 5px;
  }

  main {
    max-width: 1200px;
    margin: 0 auto;
    padding: 48px 40px;
  }

  .section-header {
    margin-bottom: 28px;
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .section-title {
    font-family: var(--mono);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: var(--muted);
  }

  .section-line {
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 16px;
    margin-bottom: 60px;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 24px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.2s, transform 0.15s;
    cursor: default;
  }

  .card:hover {
    border-color: rgba(255,255,255,0.14);
    transform: translateY(-1px);
  }

  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--profile-color);
    opacity: 0.8;
  }

  .card-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 20px;
  }

  .profile-icon {
    width: 48px; height: 48px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px;
    background: color-mix(in srgb, var(--profile-color) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--profile-color) 25%, transparent);
  }

  .badge {
    font-family: var(--mono);
    font-size: 10px;
    padding: 4px 8px;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 700;
  }

  .badge-running {
    background: rgba(34,211,160,0.12);
    color: var(--green);
    border: 1px solid rgba(34,211,160,0.25);
  }

  .badge-stopped {
    background: rgba(107,107,138,0.12);
    color: var(--muted);
    border: 1px solid rgba(107,107,138,0.2);
  }

  .profile-name {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 4px;
    text-transform: capitalize;
  }

  .profile-meta {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    display: flex;
    gap: 12px;
  }

  .profile-meta span {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .fingerprint-block {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    margin: 16px 0;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    line-height: 1.8;
  }

  .fp-row {
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }

  .fp-key { color: var(--muted); }
  .fp-val { color: var(--text); opacity: 0.7; }

  .actions {
    display: flex;
    gap: 8px;
    margin-top: 16px;
  }

  .btn {
    flex: 1;
    padding: 9px 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: transparent;
    color: var(--text);
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.15s;
    text-decoration: none;
    display: flex; align-items: center; justify-content: center;
    gap: 5px;
  }

  .btn:hover {
    background: rgba(255,255,255,0.05);
    border-color: rgba(255,255,255,0.15);
  }

  .btn-open {
    background: color-mix(in srgb, var(--profile-color) 12%, transparent);
    border-color: color-mix(in srgb, var(--profile-color) 30%, transparent);
    color: var(--profile-color);
  }

  .btn-open:hover {
    background: color-mix(in srgb, var(--profile-color) 20%, transparent);
  }

  .btn-stop { color: var(--red); }
  .btn-stop:hover { border-color: rgba(244,71,107,0.3); background: rgba(244,71,107,0.07); }

  .btn-start { color: var(--green); }
  .btn-start:hover { border-color: rgba(34,211,160,0.3); background: rgba(34,211,160,0.07); }

  .privacy-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 28px 32px;
    margin-bottom: 40px;
  }

  .privacy-title {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 700;
    margin-bottom: 6px;
    display: flex; align-items: center; gap: 8px;
  }

  .privacy-desc {
    font-size: 13px;
    color: var(--muted);
    line-height: 1.6;
    margin-bottom: 20px;
    max-width: 700px;
  }

  .principles {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
  }

  .principle {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    font-size: 12px;
  }

  .principle-icon { font-size: 18px; margin-bottom: 6px; }
  .principle-title { font-weight: 600; margin-bottom: 3px; }
  .principle-text { color: var(--muted); font-size: 11px; line-height: 1.5; }

  .add-card {
    background: transparent;
    border: 1px dashed rgba(255,255,255,0.1);
    border-radius: 16px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 40px;
    cursor: pointer;
    transition: all 0.2s;
    color: var(--muted);
    text-decoration: none;
    min-height: 200px;
    font-family: var(--mono);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .add-card:hover {
    border-color: rgba(124,108,252,0.3);
    background: rgba(124,108,252,0.04);
    color: var(--accent);
  }

  .add-icon {
    font-size: 28px;
    opacity: 0.4;
  }

  footer {
    border-top: 1px solid var(--border);
    padding: 20px 40px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .tag {
    font-family: var(--mono);
    font-size: 9px;
    padding: 2px 6px;
    background: rgba(124,108,252,0.1);
    color: var(--accent);
    border-radius: 4px;
    border: 1px solid rgba(124,108,252,0.2);
  }

  @media (max-width: 640px) {
    header, main, footer { padding-left: 20px; padding-right: 20px; }
    .grid { grid-template-columns: 1fr; }
    .header-meta { display: none; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-mark">👻</div>
    <div>
      <div class="logo-text">GHOST BROWSER</div>
      <div class="logo-sub">Isolated Profile Manager</div>
    </div>
  </div>
  <div class="header-meta">
    <span><span class="status-dot"></span> self-hosted</span>
    <span>{{ profiles|length }} profiles</span>
    <span>{{ profiles|selectattr('running')|list|length }} active</span>
  </div>
</header>

<main>

  <div class="privacy-panel">
    <div class="privacy-title">🔐 Fingerprint Isolation Architecture</div>
    <div class="privacy-desc">
      Each profile runs in its own Docker container with a separate filesystem, network stack, and browser state.
      Profiles cannot share cookies, storage, or canvas/WebGL fingerprints. No cross-profile association is possible at the OS level.
    </div>
    <div class="principles">
      <div class="principle">
        <div class="principle-icon">🗂️</div>
        <div class="principle-title">Separate Volumes</div>
        <div class="principle-text">Each profile has its own Docker volume — cookies, history, and storage never mix.</div>
      </div>
      <div class="principle">
        <div class="principle-icon">🌐</div>
        <div class="principle-title">Network Isolation</div>
        <div class="principle-text">Each container gets its own IP on the ghost_net bridge. No shared sockets.</div>
      </div>
      <div class="principle">
        <div class="principle-icon">🖥️</div>
        <div class="principle-title">VNC Streaming</div>
        <div class="principle-text">Browser runs headless in container, streamed via KasmVNC — your local browser sees only pixels.</div>
      </div>
      <div class="principle">
        <div class="principle-icon">🧬</div>
        <div class="principle-title">Locale Spoofing</div>
        <div class="principle-text">Each profile uses a different timezone and language setting for distinct fingerprints.</div>
      </div>
      <div class="principle">
        <div class="principle-icon">🗑️</div>
        <div class="principle-title">Disposable Mode</div>
        <div class="principle-text">Stop a container and its ephemeral state is gone. Persistent profiles are opt-in via volumes.</div>
      </div>
      <div class="principle">
        <div class="principle-icon">🔒</div>
        <div class="principle-title">No Shared Secrets</div>
        <div class="principle-text">Each profile has its own VNC password. The dashboard never exposes container internals.</div>
      </div>
    </div>
  </div>

  <div class="section-header">
    <span class="section-title">Browser Profiles</span>
    <div class="section-line"></div>
  </div>

  <div class="grid" id="profiles-grid">
    {% for p in profiles %}
    <div class="card" style="--profile-color: {{ p.color }}">
      <div class="card-top">
        <div class="profile-icon">{{ p.icon }}</div>
        <span class="badge {{ 'badge-running' if p.running else 'badge-stopped' }}">
          {{ '● running' if p.running else '○ stopped' }}
        </span>
      </div>

      <div class="profile-name">{{ p.name }}</div>
      <div class="profile-meta">
        <span>🌍 {{ p.browser }}</span>
        <span>⚡ :{{ p.port }}</span>
        <span class="tag">isolated</span>
      </div>

      <div class="fingerprint-block">
        {% if p.name == 'work' %}
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">en-US / New York</span></div>
        <div class="fp-row"><span class="fp-key">engine</span><span class="fp-val">Blink / V8</span></div>
        <div class="fp-row"><span class="fp-key">ip-source</span><span class="fp-val">container 172.20.0.10</span></div>
        {% elif p.name == 'personal' %}
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">en-GB / London</span></div>
        <div class="fp-row"><span class="fp-key">engine</span><span class="fp-val">Gecko / SpiderMonkey</span></div>
        <div class="fp-row"><span class="fp-key">ip-source</span><span class="fp-val">container 172.20.0.11</span></div>
        {% elif p.name == 'research' %}
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">de-DE / Berlin</span></div>
        <div class="fp-row"><span class="fp-key">engine</span><span class="fp-val">Gecko (Tor)</span></div>
        <div class="fp-row"><span class="fp-key">routing</span><span class="fp-val">Tor Network</span></div>
        {% elif p.name == 'social' %}
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">en-AU / Sydney</span></div>
        <div class="fp-row"><span class="fp-key">engine</span><span class="fp-val">Blink / V8</span></div>
        <div class="fp-row"><span class="fp-key">ip-source</span><span class="fp-val">container 172.20.0.13</span></div>
        {% elif p.name == 'banking' %}
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">en-US / Chicago</span></div>
        <div class="fp-row"><span class="fp-key">engine</span><span class="fp-val">Gecko / SpiderMonkey</span></div>
        <div class="fp-row"><span class="fp-key">ip-source</span><span class="fp-val">container 172.20.0.14</span></div>
        {% else %}
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">en-US / Los Angeles</span></div>
        <div class="fp-row"><span class="fp-key">engine</span><span class="fp-val">Blink (Chromium)</span></div>
        <div class="fp-row"><span class="fp-key">ip-source</span><span class="fp-val">container 172.20.0.15</span></div>
        {% endif %}
        <div class="fp-row"><span class="fp-key">storage</span><span class="fp-val">isolated volume</span></div>
      </div>

      <div class="actions">
        {% if p.running %}
        <a href="http://localhost:{{ p.port }}" target="_blank" class="btn btn-open">↗ Open</a>
        <button class="btn btn-stop" onclick="containerAction('{{ p.container }}', 'stop')">■ Stop</button>
        <button class="btn" onclick="containerAction('{{ p.container }}', 'restart')">↺</button>
        {% else %}
        <button class="btn btn-start" onclick="containerAction('{{ p.container }}', 'start')">▶ Start</button>
        {% endif %}
      </div>
    </div>
    {% endfor %}

    <a href="#" class="add-card" onclick="alert('Edit docker-compose.yml to add profiles, then run: docker compose up -d')">
      <div class="add-icon">+</div>
      <span>Add Profile</span>
      <span style="opacity:0.5; font-size:9px;">edit docker-compose.yml</span>
    </a>
  </div>

</main>

<footer>
  <span>Ghost Browser — Self-Hosted · Open Source</span>
  <span>All browsing isolated in Docker containers · No telemetry · No cloud</span>
</footer>

<script>
async function containerAction(name, action) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const res = await fetch(`/api/container/${name}/${action}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      setTimeout(() => location.reload(), 1500);
    } else {
      alert('Error: ' + (data.error || 'unknown'));
      btn.disabled = false;
    }
  } catch(e) {
    alert('Request failed: ' + e.message);
    btn.disabled = false;
  }
}
</script>

</body>
</html>"""

@app.route("/")
def index():
    profiles = get_profiles()
    return render_template_string(DASHBOARD_HTML, profiles=profiles)

@app.route("/api/profiles")
def api_profiles():
    return jsonify(get_profiles())

@app.route("/api/container/<name>/<action>", methods=["POST"])
def api_container(name, action):
    if action not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "error": "invalid action"}), 400
    result = container_action(name, action)
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
