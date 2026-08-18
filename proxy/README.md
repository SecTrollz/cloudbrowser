# Traffic capture wiring (handibot integration)

Two tiers - see `~/repos/handibot`'s README "Traffic capture and rule
enforcement" for the full picture. This file covers only the cloudbrowser
side of each.

**Status: best-effort, not live-verified end-to-end.** The entrypoint
wrapper below is built against facts confirmed directly against the pulled
`kasmweb/chrome:1.16.0`/`kasmweb/firefox:1.16.0`/`kasmweb/chromium:1.16.0`
images this session (`docker inspect`'s reported ENTRYPOINT/CMD/USER,
`docker cp`'d contents of `/dockerstartup/custom_startup.sh` and
`/dockerstartup/vnc_startup.sh`, confirmed `setpriv` is present, confirmed
`kasm-user` has no passwordless sudo) - but no container has actually been
booted with this wrapper in place, and `tshark` isn't installed anywhere
in the sandbox this was built in, so the sidecar hasn't captured anything
live either. Verify a real `docker compose up` before relying on this.

## Tier 1: SSLKEYLOGFILE passive capture (default, every profile)

Every `browser_*` service in `docker-compose.yml` sets
`SSLKEYLOGFILE=/keylog/keylog.log` and mounts `./data/keylogs/<profile>:/keylog`.
Chrome/Chromium/Firefox all natively write their TLS session secrets there
- no wrapper script needed for this part, confirmed via
`custom_startup.sh`'s `$START_COMMAND $ARGS $URL`: the browser is a direct
child process of the container's own entrypoint chain, so it inherits the
container's environment including `SSLKEYLOGFILE`.

The `traffic_capture` service (`proxy/sidecar/Dockerfile`, plain
`tshark` on `debian:bookworm-slim`) joins `toast_net` with
`NET_RAW`/`NET_ADMIN` and mounts the same `./data/keylogs/` tree read-write,
so it can capture + decrypt each profile's traffic live using that
profile's own keylog file. It doesn't run `tshark` on its own - handibot's
`KeylogCaptureService` launches/stops per-profile `tshark` invocations
inside it via `docker exec` (`handibot proxy-keylog-start/stop <profile> <profile_ip>`).

This tier never blocks anything - nothing is in the traffic path, it's
purely observational.

`browser_research` (Tor) gets `SSLKEYLOGFILE` too (harmless - see its
comment in `docker-compose.yml`), same mechanism, same low-value caveat as
documented there.

## Tier 2: ephemeral, per-profile CA/mitmproxy fallback (opt-in)

Only for `work`/`personal`/`social`/`banking`/`dev` - never `research`
(Tor), see the "Why browser_research is excluded" note below. This tier
only actually does anything once you run `handibot proxy-ca-start <profile>`
for a given profile.

### How it's wired

- `docker-compose.yml`: each of the 5 profiles runs with `user: root` and
  `entrypoint: ["/handibot-proxy-mount/entrypoint-wrapper.sh"]` (bind-mounted
  from `./proxy/entrypoint-wrapper.sh`, **not baked into any image**), plus
  a read-only mount of `./proxy/runtime/<profile>/` at
  `/handibot-proxy-mount/runtime`.
- `entrypoint-wrapper.sh` runs as root at container start: if
  `runtime/ca-cert.pem` exists, trusts it via `update-ca-certificates`; if
  `runtime/policy.json` exists, copies it to the right enterprise-policy
  path for that profile's browser engine (`HANDIBOT_BROWSER_ENGINE` env
  var: `chrome`/`chromium`/`firefox`). Then it drops privileges
  (`setpriv --reuid=1000 --regid=1000 --init-groups`) and execs the
  image's **completely unmodified** original startup chain - confirmed via
  `docker inspect`: `/dockerstartup/kasm_default_profile.sh
  /dockerstartup/vnc_startup.sh /dockerstartup/kasm_startup.sh --wait`.
  Every real process (VNC server, browser, etc.) still ends up running as
  `kasm-user` (uid 1000), exactly as it does upstream.
- **Neither file exists until you turn that profile's proxy on.** handibot's
  `ProxyService.start()`/`stop()` (in `handibot/src/handibot/proxy/service.py`,
  via `handibot/src/handibot/proxy/browser_policy.py`) writes/removes
  `policy.json` directly into `proxy/runtime/<profile>/` on this repo (a
  sibling repo on the same host) as a side effect of `proxy-ca-start`/
  `proxy-ca-stop`. So a profile with no CA-fallback proxy running boots
  with **zero** proxy/CA changes - completely upstream default behavior.
- `ca-cert.pem` is a separate, explicit step: `handibot proxy-ca-cert <profile>`
  exports that profile's freshly-generated mitmproxy CA into this same
  runtime directory.
- **Because none of this is baked in at build time, applying/rotating it
  is just a container restart, not a rebuild.**

### One-time + per-profile setup

```bash
cd ~/repos/handibot
handibot proxy-ca-start banking          # starts banking's own mitmproxy, writes policy.json
handibot proxy-ca-cert banking           # exports banking's CA cert into the runtime mount

cd ~/repos/cloudbrowser
docker compose restart browser_banking   # picks up both files at container start
```

### Rotating a profile's CA

```bash
cd ~/repos/handibot
handibot proxy-ca-rotate banking         # stops, wipes the old CA, starts fresh
handibot proxy-ca-cert banking           # export the new cert

cd ~/repos/cloudbrowser
docker compose restart browser_banking   # trust the new cert, drop the old one
```

### Turning a profile's CA fallback off

```bash
handibot proxy-ca-stop banking           # removes policy.json from the runtime mount
docker compose restart browser_banking   # boots with no forced proxy again
```

(The CA cert itself is left in place on handibot's side - `proxy-ca-start`
reuses it rather than minting a new one unless you explicitly `rotate`.)

## Why each profile gets its own CA/port, never a shared one

Sharing one CA/mitmproxy instance across multiple profiles would give all
of them the same outbound TLS fingerprint (JA3/JA4) to origin servers - a
correlation signal that undermines the isolation the rest of this file's
per-profile setup (separate `VNC_PW`, separate volumes, separate static
IPs, separate `LANG`/`TZ`) already provides. `--set confdir=<dir>` is
mitmproxy's own built-in mechanism for a distinct CA per instance - no
custom crypto involved.

## Why `browser_research` (Tor) is excluded from tier 2

Routing Tor Browser through an external forward proxy on top of Tor's own
circuit-based routing conflicts with the whole point of that profile and
can break or weaken its anonymity properties. `browser_research` gets tier
1 (SSLKEYLOGFILE) only - no `entrypoint-wrapper.sh`, no runtime mount, no
CA, still pulling the upstream `kasmweb/tor-browser` image directly with
its original entrypoint untouched.

## Files

- `entrypoint-wrapper.sh` - the container-start CA-trust/policy-apply
  wrapper, bind-mounted (never baked into an image).
- `runtime/<profile>/` - where `ca-cert.pem`/`policy.json` land at runtime;
  gitignored except the directory structure itself (see `runtime/.gitignore`).
  Both files are entirely absent for a profile whose CA-fallback proxy
  isn't running.
- `sidecar/Dockerfile` - the tshark capture sidecar (tier 1).
