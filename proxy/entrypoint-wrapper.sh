#!/bin/sh
# Bind-mounted over the image's own entrypoint via docker-compose.yml's
# `entrypoint:`/`user: root` override - NOT baked into any image. Applies
# whatever CA cert + browser policy currently sit in the runtime mount (if
# any - both are optional, so a profile with no CA-fallback proxy running
# just gets no policy applied and boots exactly as upstream), then drops
# privileges and execs the image's own, completely unmodified startup
# chain. Root is only needed for this trust step; every real process
# (VNC server, browser, etc.) still ends up running as kasm-user (uid
# 1000), exactly as it does upstream.
#
# Because this runs at every container start (not baked in at build time),
# rotating a profile's CA is just `docker compose restart <profile>` after
# `handibot proxy-ca-rotate <profile>` + `proxy-ca-cert <profile>` refresh
# what's in this mount - no rebuild needed.
set -e

MOUNT=/handibot-proxy-mount/runtime

if [ -f "$MOUNT/ca-cert.pem" ]; then
    cp "$MOUNT/ca-cert.pem" /usr/local/share/ca-certificates/handibot-ca-cert.crt
    update-ca-certificates
fi

if [ -f "$MOUNT/policy.json" ]; then
    case "$HANDIBOT_BROWSER_ENGINE" in
        chrome)
            mkdir -p /etc/opt/chrome/policies/managed
            cp "$MOUNT/policy.json" /etc/opt/chrome/policies/managed/handibot-proxy.json
            ;;
        chromium)
            # Which path Chromium actually reads depends on how it was
            # packaged in this image - write both, harmless if one is unused.
            mkdir -p /etc/opt/chrome/policies/managed /etc/chromium/policies/managed
            cp "$MOUNT/policy.json" /etc/opt/chrome/policies/managed/handibot-proxy.json
            cp "$MOUNT/policy.json" /etc/chromium/policies/managed/handibot-proxy.json
            ;;
        firefox)
            mkdir -p /etc/firefox/policies
            cp "$MOUNT/policy.json" /etc/firefox/policies/policies.json
            ;;
        *)
            echo "entrypoint-wrapper: unknown HANDIBOT_BROWSER_ENGINE=$HANDIBOT_BROWSER_ENGINE, skipping policy" >&2
            ;;
    esac
fi

# Confirmed via `docker inspect`/`docker cp` against the real pulled images:
# ENTRYPOINT was ["/dockerstartup/kasm_default_profile.sh", "/dockerstartup/vnc_startup.sh",
# "/dockerstartup/kasm_startup.sh"], CMD was ["--wait"], USER was 1000.
# Reproduced verbatim below, just with a privilege drop in front of it.
exec setpriv --reuid=1000 --regid=1000 --init-groups \
    /dockerstartup/kasm_default_profile.sh /dockerstartup/vnc_startup.sh /dockerstartup/kasm_startup.sh --wait
