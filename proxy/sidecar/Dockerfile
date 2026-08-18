# tshark sidecar for handibot's default SSLKEYLOGFILE capture path. Runs
# as root with NET_RAW/NET_ADMIN (granted via docker-compose.yml's
# cap_add, not baked in here) so raw packet capture works without any
# host-level sudo/setcap/wireshark-group setup. Stays idle (sleep infinity)
# as the main process - handibot's KeylogCaptureService launches/kills
# actual `tshark` invocations per profile via `docker exec`.
FROM debian:bookworm-slim

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tshark && \
    rm -rf /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
