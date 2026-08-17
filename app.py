#!/usr/bin/env python3
"""
Toast Browser Dashboard
Reads Docker labels to auto-discover browser profiles and serves the control panel.
"""

import os
import json
import time
import ssl
import base64
import asyncio
import tempfile
import threading
import yaml
import websockets
from functools import wraps
from flask import Flask, render_template_string, jsonify, request, redirect, Response
import docker
from vncdotool import api as vnc_api

app = Flask(__name__)
client = docker.from_env()

COMPOSE_FILE = os.path.join(os.path.dirname(__file__), "docker-compose.yml")
DEFAULT_SECRET_PLACEHOLDER = "changeme_use_env_file"
TOAST_SECRET = os.environ.get("TOAST_SECRET", "")
VNC_IDLE_TIMEOUT = int(os.environ.get("TOAST_VNC_IDLE_TIMEOUT", "300"))
VNC_CONNECT_TIMEOUT = 10
KASM_INTERNAL_PORT = "6901"
KASM_WS_PATH = "/websockify"
KASM_USER = "kasm_user"

if not TOAST_SECRET or TOAST_SECRET == DEFAULT_SECRET_PLACEHOLDER:
    print("=" * 60)
    print("WARNING: TOAST_SECRET is unset or still the default placeholder.")
    print("  /api/container/* and /api/control/* are running WITHOUT auth.")
    print("  Set a real TOAST_SECRET in .env to require a bearer token.")
    print("=" * 60)

def parse_env(env_list):
    d = {}
    for item in env_list or []:
        if "=" in item:
            k, v = item.split("=", 1)
            d[k] = v
    return d

def get_compose_browser_services():
    """Read docker-compose.yml for every service labeled toast.role=browser,
    regardless of whether it's ever been created in Docker yet."""
    services = {}
    try:
        with open(COMPOSE_FILE) as f:
            compose = yaml.safe_load(f) or {}
        for service_name, spec in (compose.get("services") or {}).items():
            labels = spec.get("labels") or []
            label_map = {}
            for item in labels:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    label_map[k] = v
            if label_map.get("toast.role") != "browser":
                continue
            services[label_map.get("toast.profile", service_name)] = {
                "service": service_name,
                "browser": label_map.get("toast.browser", "chrome"),
                "color": label_map.get("toast.color", "#6B7280"),
                "icon": label_map.get("toast.icon", "🌐"),
                "port": label_map.get("toast.port", "6901"),
            }
    except Exception as e:
        print(f"Compose parse error: {e}")
    return services

def get_profiles():
    """Combine live Docker containers with profiles only defined in docker-compose.yml
    so undeployed profiles show up instead of silently disappearing."""
    profiles = []
    deployed_names = set()
    try:
        containers = client.containers.list(all=True, filters={"label": "toast.role=browser"})
        for c in containers:
            labels = c.labels
            env = parse_env(c.attrs.get("Config", {}).get("Env", []))
            running = c.status == "running"
            name = labels.get("toast.profile", "unknown")
            deployed_names.add(name)
            networks = c.attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
            ip = next(iter(networks.values()), {}).get("IPAddress", "")
            profiles.append({
                "name": name,
                "browser": labels.get("toast.browser", "chrome"),
                "color": labels.get("toast.color", "#6B7280"),
                "icon": labels.get("toast.icon", "🌐"),
                "port": labels.get("toast.port", "6901"),
                "container": c.name,
                "ip": ip,
                "deployed": True,
                "running": running,
                "status": c.status,
                "lang": env.get("LANG", "en-US"),
                "tz": env.get("TZ", "UTC"),
                "vnc_pw": env.get("VNC_PW", ""),
            })
    except Exception as e:
        print(f"Docker error: {e}")

    for name, spec in get_compose_browser_services().items():
        if name in deployed_names:
            continue
        profiles.append({
            "name": name,
            "browser": spec["browser"],
            "color": spec["color"],
            "icon": spec["icon"],
            "port": spec["port"],
            "container": None,
            "ip": "",
            "deployed": False,
            "running": False,
            "status": "not deployed",
            "lang": "-",
            "tz": "-",
            "vnc_pw": "",
            "deploy_cmd": f"docker compose up -d {spec['service']}",
        })

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

def require_auth(fn):
    """Gate an endpoint behind Authorization: Bearer <TOAST_SECRET>.
    No-op (auth disabled) if TOAST_SECRET is unset/still the placeholder —
    see the startup warning printed above."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if TOAST_SECRET and TOAST_SECRET != DEFAULT_SECRET_PLACEHOLDER:
            supplied = request.headers.get("Authorization", "")
            if supplied != f"Bearer {TOAST_SECRET}":
                return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

def get_profile_by_name(name):
    for p in get_profiles():
        if p["name"] == name:
            return p
    return None

def require_running_profile(name):
    """Returns (profile, None) or (None, (response, status)) for route handlers."""
    p = get_profile_by_name(name)
    if p is None:
        return None, (jsonify({"ok": False, "error": "no such profile"}), 404)
    if not p["running"]:
        return None, (jsonify({"ok": False, "error": "profile is not running"}), 409)
    return p, None

class VNCError(Exception):
    pass

class WSBridge:
    """KasmVNC only exposes RFB tunneled inside a WebSocket at /websockify on
    its HTTPS port (verified — no separate raw VNC TCP port is listening in
    this image) — the exact same endpoint the browser's own noVNC client
    connects to, self-signed cert, HTTP Basic Auth on the upgrade request,
    'binary' subprotocol. vncdotool only speaks plain TCP RFB, so this runs a
    local per-profile TCP listener that transparently forwards bytes to/from
    that same WebSocket endpoint — a protocol shim, not an alternate path.

    NOTE: each browser_* service needs VNCOPTIONS=-SecurityTypes None (see
    docker-compose.yml) because this image's legacy classic-VNC-Auth
    challenge doesn't validate against VNC_PW (its password file isn't
    derived from it) — without disabling it, RFB auth fails outright and
    nothing works, not even screenshots. With it disabled, screenshot
    capture is fully verified working; mouse/keyboard control endpoints
    exist and report success but have NOT been confirmed to actually land
    input on the browser in testing — likely because that same legacy auth
    layer is also how KasmVNC grants the "owner" (write/input) role from
    its .kasmpasswd permission model, which a bare RFB connection bypassing
    it never acquires. Fixing this needs figuring out how KasmVNC ties
    input authorization to a WS-tunneled RFB session outside that legacy
    challenge — not yet solved here."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._ports = {}
        self._lock = threading.Lock()

    def get_local_port(self, name, ip, password):
        with self._lock:
            cached = self._ports.get(name)
        if cached:
            return cached
        fut = asyncio.run_coroutine_threadsafe(self._start_server(ip, password), self._loop)
        local_port = fut.result(timeout=VNC_CONNECT_TIMEOUT)
        with self._lock:
            self._ports[name] = local_port
        return local_port

    def invalidate(self, name):
        with self._lock:
            self._ports.pop(name, None)

    async def _start_server(self, ip, password):
        async def handle(reader, writer):
            url = f"wss://{ip}:{KASM_INTERNAL_PORT}{KASM_WS_PATH}"
            auth = base64.b64encode(f"{KASM_USER}:{password}".encode()).decode()
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            try:
                async with websockets.connect(
                    url, subprotocols=["binary"],
                    additional_headers={
                        "Authorization": f"Basic {auth}",
                        "Origin": f"https://{ip}:{KASM_INTERNAL_PORT}",
                    },
                    ssl=ssl_ctx, max_size=None,
                ) as ws:
                    async def tcp_to_ws():
                        try:
                            while True:
                                data = await reader.read(65536)
                                if not data:
                                    break
                                await ws.send(data)
                        except Exception:
                            pass
                    async def ws_to_tcp():
                        try:
                            async for msg in ws:
                                writer.write(msg)
                                await writer.drain()
                        except Exception:
                            pass
                    await asyncio.gather(tcp_to_ws(), ws_to_tcp())
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        return server.sockets[0].getsockname()[1]

ws_bridge = WSBridge()

class VNCPool:
    """Pools RFB (raw VNC, port 5901) connections to each profile's container,
    reached directly over the toast_net bridge — the same session KasmVNC's own
    web UI streams over websockets, not a separate automation channel.

    One RLock per profile serializes all operations against that profile's
    session (screenshot/move/click/type must not interleave on one RFB
    connection); different profiles run fully concurrently. A daemon thread
    reaps connections idle past VNC_IDLE_TIMEOUT."""

    def __init__(self, idle_timeout=VNC_IDLE_TIMEOUT):
        self._idle_timeout = idle_timeout
        self._dict_lock = threading.Lock()
        self._locks = {}
        self._clients = {}
        threading.Thread(target=self._reap_loop, daemon=True).start()

    def _get_lock(self, name):
        with self._dict_lock:
            if name not in self._locks:
                self._locks[name] = threading.RLock()
            return self._locks[name]

    def _connect(self, name, host, password):
        local_port = ws_bridge.get_local_port(name, host, password)
        vnc_client = vnc_api.connect(f"127.0.0.1::{local_port}", password=password,
                                      timeout=VNC_CONNECT_TIMEOUT)
        self._clients[name] = {"client": vnc_client, "last_used": time.time()}
        return vnc_client

    def invalidate(self, name):
        entry = self._clients.pop(name, None)
        if entry:
            try:
                entry["client"].disconnect()
            except Exception:
                pass
        ws_bridge.invalidate(name)

    def execute(self, name, host, password, op):
        lock = self._get_lock(name)
        with lock:
            entry = self._clients.get(name)
            vnc_client = entry["client"] if entry else self._connect(name, host, password)
            try:
                result = op(vnc_client)
                self._clients[name]["last_used"] = time.time()
                return result
            except Exception:
                self.invalidate(name)
                try:
                    vnc_client = self._connect(name, host, password)
                    result = op(vnc_client)
                    self._clients[name]["last_used"] = time.time()
                    return result
                except Exception as e:
                    raise VNCError(str(e))

    def _reap_loop(self):
        while True:
            time.sleep(30)
            now = time.time()
            with self._dict_lock:
                stale = [n for n, e in self._clients.items()
                         if now - e["last_used"] > self._idle_timeout]
            for n in stale:
                print(f"[vnc-pool] idle timeout, disconnecting '{n}'")
                self.invalidate(n)

vnc_pool = VNCPool()

def _capture_png(vnc_client):
    fd, path = tempfile.mkstemp(suffix=".png", dir="/tmp")
    os.close(fd)
    try:
        vnc_client.captureScreen(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def _type_text(vnc_client, text):
    for ch in text:
        vnc_client.keyPress("minus" if ch == "-" else ch)

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Toast Browser</title>
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

  .badge-undeployed {
    background: rgba(244,71,107,0.08);
    color: var(--red);
    border: 1px dashed rgba(244,71,107,0.3);
  }

  .card-undeployed { opacity: 0.75; }

  .deploy-hint {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    margin: 16px 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    word-break: break-all;
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

  .login-block {
    background: color-mix(in srgb, var(--accent) 6%, var(--surface2));
    border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
    border-radius: 10px;
    padding: 12px 14px;
    margin: 0 0 16px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    line-height: 1.8;
  }

  .pw-mask {
    cursor: pointer;
    user-select: none;
    letter-spacing: 1px;
  }

  .copy-btn {
    background: transparent;
    border: none;
    color: var(--muted);
    cursor: pointer;
    font-size: 11px;
    padding: 0 0 0 8px;
    line-height: 1;
  }

  .copy-btn:hover { color: var(--accent); }

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

  .switcher { margin-bottom: 40px; }

  .switcher-strip {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 8px;
    margin-bottom: 10px;
    position: sticky;
    top: 88px;
    z-index: 90;
    overflow-x: auto;
  }

  .switcher-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    width: 52px;
    flex-shrink: 0;
    padding: 8px 0;
    border-radius: 8px;
    border: 1px solid transparent;
    background: transparent;
    cursor: pointer;
    color: var(--text);
    font-family: var(--sans);
  }

  .switcher-item:hover { background: rgba(255,255,255,0.05); }

  .switcher-item.active {
    border-color: color-mix(in srgb, var(--profile-color) 40%, transparent);
    background: color-mix(in srgb, var(--profile-color) 14%, transparent);
  }

  .switcher-item-disabled { opacity: 0.3; cursor: not-allowed; }

  .switcher-icon { font-size: 18px; }
  .switcher-key { font-family: var(--mono); font-size: 9px; color: var(--muted); }
  .switcher-spacer { flex: 1; }

  .workspace-frame-wrap {
    position: relative;
    min-height: 640px;
    border-radius: 16px;
    overflow: hidden;
    border: 1px solid var(--border);
    background: var(--surface);
  }

  .workspace-iframe {
    width: 100%;
    height: 640px;
    border: 0;
    display: block;
  }

  .workspace-empty, .workspace-fallback {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 16px;
    height: 640px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: 12px;
    text-align: center;
    padding: 40px;
  }

  .workspace-fallback p { max-width: 420px; line-height: 1.6; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-mark">🍞</div>
    <div>
      <div class="logo-text">TOAST BROWSER</div>
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

  {% set switchable = profiles|selectattr('deployed')|list %}
  {% if switchable %}
  <div class="switcher" id="switcher">
    <div class="switcher-strip" id="switcher-strip">
      {% for p in switchable %}
      <button class="switcher-item {{ '' if p.running else 'switcher-item-disabled' }}"
              style="--profile-color: {{ p.color }}"
              data-name="{{ p.name }}"
              {{ 'disabled' if not p.running }}
              onclick="switchProfile('{{ p.name }}')"
              title="{{ p.name }} (press {{ loop.index }})">
        <span class="switcher-icon">{{ p.icon }}</span>
        <span class="switcher-key">{{ loop.index }}</span>
      </button>
      {% endfor %}
      <div class="switcher-spacer"></div>
      <a id="switcher-newtab" class="btn" target="_blank" style="max-width:160px; flex:none;">↗ Open in new tab</a>
    </div>

    <div class="workspace-frame-wrap" id="workspace-wrap">
      <div class="workspace-empty" id="workspace-empty">
        Select a profile above (or press 1–{{ switchable|length }}) to view it here.
      </div>
      <iframe id="workspace-frame" class="workspace-iframe" style="display:none;"
              allow="clipboard-read; clipboard-write"></iframe>
      <div class="workspace-fallback" id="workspace-fallback" style="display:none;">
        <p>This profile hasn't loaded here yet — likely its self-signed certificate hasn't
        been trusted in this browser, or KasmVNC's login hasn't been accepted once.</p>
        <a id="workspace-fallback-link" class="btn btn-open" target="_blank">↗ Open once in a new tab to trust it, then come back</a>
      </div>
    </div>
  </div>
  {% endif %}

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
        <div class="principle-text">Each container gets its own IP on the toast_net bridge. No shared sockets.</div>
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
    <div class="card {{ 'card-undeployed' if not p.deployed }}" style="--profile-color: {{ p.color }}">
      <div class="card-top">
        <div class="profile-icon">{{ p.icon }}</div>
        {% if not p.deployed %}
        <span class="badge badge-undeployed">⚠ not deployed</span>
        {% else %}
        <span class="badge {{ 'badge-running' if p.running else 'badge-stopped' }}">
          {{ '● running' if p.running else '○ stopped' }}
        </span>
        {% endif %}
      </div>

      <div class="profile-name">{{ p.name }}</div>
      <div class="profile-meta">
        <span>🌍 {{ p.browser }}</span>
        <span>⚡ :{{ p.port }}</span>
        <span class="tag">isolated</span>
      </div>

      {% if not p.deployed %}
      <div class="deploy-hint">
        <span id="cmd-{{ p.name }}">{{ p.deploy_cmd }}</span>
        <button class="copy-btn" onclick="copyPw(this, '{{ p.deploy_cmd }}')" title="copy command">📋</button>
      </div>
      <div class="fingerprint-block">
        <div class="fp-row"><span class="fp-key">status</span><span class="fp-val">defined in docker-compose.yml, container not created yet</span></div>
      </div>
      {% else %}
      <div class="fingerprint-block">
        <div class="fp-row"><span class="fp-key">locale</span><span class="fp-val">{{ p.lang }}</span></div>
        <div class="fp-row"><span class="fp-key">timezone</span><span class="fp-val">{{ p.tz }}</span></div>
        <div class="fp-row"><span class="fp-key">storage</span><span class="fp-val">isolated volume</span></div>
      </div>

      <div class="login-block">
        <div class="fp-row"><span class="fp-key">user</span><span class="fp-val">kasm_user</span></div>
        <div class="fp-row">
          <span class="fp-key">pass</span>
          <span class="fp-val pw-mask" data-pw="{{ p.vnc_pw }}" onclick="togglePw(this)" title="click to reveal">••••••••••••</span>
          <button class="copy-btn" onclick="copyPw(this, '{{ p.vnc_pw }}')" title="copy password">📋</button>
        </div>
      </div>
      {% endif %}

      <div class="actions">
        {% if not p.deployed %}
        <span class="btn" style="opacity:0.5; cursor:default;" title="run the command above on the host">not deployed</span>
        {% elif p.running %}
        <a href="https://{{ request.host.split(':')[0] }}:{{ p.port }}" target="_blank" class="btn btn-open">↗ Open</a>
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
  <span>Toast Browser — Self-Hosted · Open Source</span>
  <span>All browsing isolated in Docker containers · No telemetry · No cloud · No subscription</span>
</footer>

<script>
const TOAST_PROFILES = {{ profiles_json|safe }};
const TOAST_TOKEN = {{ toast_token|tojson }};

function apiFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers, TOAST_TOKEN ? {"Authorization": `Bearer ${TOAST_TOKEN}`} : {});
  return fetch(url, opts);
}

async function containerAction(name, action) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const res = await apiFetch(`/api/container/${name}/${action}`, { method: 'POST' });
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

function togglePw(el) {
  el.textContent = el.textContent.includes('•') ? el.dataset.pw : '••••••••••••';
}

function copyPw(btn, pw) {
  navigator.clipboard.writeText(pw).then(() => {
    const orig = btn.textContent;
    btn.textContent = '✓';
    setTimeout(() => btn.textContent = orig, 1200);
  });
}

let activeProfile = null;
let loadWatchdog = null;

function profileByName(name) { return TOAST_PROFILES.find(p => p.name === name); }

function switchProfile(name) {
  const p = profileByName(name);
  if (!p || !p.running) return;
  activeProfile = name;
  localStorage.setItem('toast_last_profile', name);

  document.querySelectorAll('.switcher-item').forEach(el =>
    el.classList.toggle('active', el.dataset.name === name));

  const host = location.hostname;
  const url = `https://${host}:${p.port}/`;
  document.getElementById('switcher-newtab').href = url;
  document.getElementById('workspace-fallback-link').href = url;

  const frame = document.getElementById('workspace-frame');
  const empty = document.getElementById('workspace-empty');
  const fallback = document.getElementById('workspace-fallback');

  empty.style.display = 'none';
  fallback.style.display = 'none';
  frame.style.display = 'block';
  frame.dataset.loaded = 'false';
  frame.src = url;

  clearTimeout(loadWatchdog);
  loadWatchdog = setTimeout(() => {
    if (frame.dataset.loaded !== 'true') {
      frame.style.display = 'none';
      fallback.style.display = 'flex';
    }
  }, 4000);
}

document.addEventListener('DOMContentLoaded', () => {
  const frame = document.getElementById('workspace-frame');
  if (!frame) return;
  frame.addEventListener('load', () => { frame.dataset.loaded = 'true'; });

  const last = localStorage.getItem('toast_last_profile');
  const lastProfile = last ? profileByName(last) : null;
  if (lastProfile && lastProfile.running) switchProfile(last);
});

window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  const deployed = TOAST_PROFILES.filter(p => p.deployed);
  const n = parseInt(e.key, 10);
  if (!Number.isNaN(n) && n >= 1 && n <= deployed.length) {
    switchProfile(deployed[n - 1].name);
  }
});
</script>

</body>
</html>"""

@app.route("/")
def index():
    profiles = get_profiles()
    profiles_json = json.dumps([
        {"name": p["name"], "port": p["port"], "running": p["running"], "deployed": p["deployed"]}
        for p in profiles
    ])
    toast_token = TOAST_SECRET if TOAST_SECRET and TOAST_SECRET != DEFAULT_SECRET_PLACEHOLDER else ""
    return render_template_string(DASHBOARD_HTML, profiles=profiles,
                                   profiles_json=profiles_json, toast_token=toast_token)

@app.route("/api/profiles")
def api_profiles():
    return jsonify(get_profiles())

@app.route("/api/container/<name>/<action>", methods=["POST"])
@require_auth
def api_container(name, action):
    if action not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "error": "invalid action"}), 400
    result = container_action(name, action)
    return jsonify(result)

@app.route("/api/control/<name>/screenshot", methods=["GET"])
@require_auth
def api_control_screenshot(name):
    p, err = require_running_profile(name)
    if err:
        return err
    try:
        png_bytes = vnc_pool.execute(name, p["ip"], p["vnc_pw"], _capture_png)
    except VNCError as e:
        return jsonify({"ok": False, "error": f"vnc error: {e}"}), 502
    return Response(png_bytes, mimetype="image/png")

@app.route("/api/control/<name>/move", methods=["POST"])
@require_auth
def api_control_move(name):
    p, err = require_running_profile(name)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    x, y = data.get("x"), data.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return jsonify({"ok": False, "error": "x and y (int) required"}), 400
    try:
        vnc_pool.execute(name, p["ip"], p["vnc_pw"], lambda c: c.mouseMove(x, y))
    except VNCError as e:
        return jsonify({"ok": False, "error": f"vnc error: {e}"}), 502
    return jsonify({"ok": True})

@app.route("/api/control/<name>/click", methods=["POST"])
@require_auth
def api_control_click(name):
    p, err = require_running_profile(name)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    x, y, button = data.get("x"), data.get("y"), data.get("button", 1)
    if not isinstance(x, int) or not isinstance(y, int):
        return jsonify({"ok": False, "error": "x and y (int) required"}), 400
    def op(c):
        c.mouseMove(x, y)
        c.mousePress(button)
    try:
        vnc_pool.execute(name, p["ip"], p["vnc_pw"], op)
    except VNCError as e:
        return jsonify({"ok": False, "error": f"vnc error: {e}"}), 502
    return jsonify({"ok": True})

@app.route("/api/control/<name>/type", methods=["POST"])
@require_auth
def api_control_type(name):
    p, err = require_running_profile(name)
    if err:
        return err
    text = (request.get_json(silent=True) or {}).get("text", "")
    if not isinstance(text, str) or not text:
        return jsonify({"ok": False, "error": "text (str) required"}), 400
    try:
        vnc_pool.execute(name, p["ip"], p["vnc_pw"], lambda c: _type_text(c, text))
    except VNCError as e:
        return jsonify({"ok": False, "error": f"vnc error: {e}"}), 502
    return jsonify({"ok": True})

@app.route("/api/control/<name>/keypress", methods=["POST"])
@require_auth
def api_control_keypress(name):
    p, err = require_running_profile(name)
    if err:
        return err
    key = (request.get_json(silent=True) or {}).get("key", "")
    if not key:
        return jsonify({"ok": False, "error": "key (str) required, e.g. Return, Tab, ctrl-a"}), 400
    try:
        vnc_pool.execute(name, p["ip"], p["vnc_pw"], lambda c: c.keyPress(key))
    except VNCError as e:
        return jsonify({"ok": False, "error": f"vnc error: {e}"}), 502
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
