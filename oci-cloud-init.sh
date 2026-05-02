#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  toast browser — OCI Free Tier Cloud-Init                       ║
# ║  Oracle Cloud Infrastructure — Always Free VM                   ║
# ║  Shape: VM.Standard.A1.Flex (ARM) or VM.Standard.E2.1.Micro     ║
# ║  Image: Oracle Linux 8 or Ubuntu 22.04                          ║
# ║  Resources: 1 OCPU / 6GB RAM                                    ║
# ║                                                                  ║
# ║  Installs:                                                       ║
# ║    - System updates + essential deps                             ║
# ║    - Docker CE (latest stable)                                   ║
# ║    - Docker Compose v2 plugin                                    ║
# ║    - UFW firewall (80, 443, 22 only)                             ║
# ║    - Swap file (2GB — critical on 6GB RAM with 6 containers)     ║
# ║    - OCI iptables rules cleared (OCI blocks ports by default)    ║
# ║    - toast browser repo cloned + ready to configure              ║
# ╚══════════════════════════════════════════════════════════════════╝

#cloud-config
# NOTE: This file uses cloud-init's "runcmd" via a bash heredoc approach.
# Paste the entire content into OCI's "Cloud-Init Script" field under
# "Advanced Options" when creating your instance.

# ── The actual script runs as root at first boot ─────────────────────

set -euo pipefail

LOG=/var/log/toast-browser-init.log
exec > >(tee -a "$LOG") 2>&1

echo "======================================================"
echo " toast browser — OCI Cloud-Init Starting"
echo " $(date -u)"
echo "======================================================"

# ─────────────────────────────────────────────────────────────────────
# 0. Detect OS (Ubuntu vs Oracle Linux / RHEL)
# ─────────────────────────────────────────────────────────────────────
if [ -f /etc/os-release ]; then
  . /etc/os-release
  OS_ID="${ID}"          # ubuntu / ol / rhel / centos
  OS_VER="${VERSION_ID}" # 22.04 / 8 / 9 etc
else
  echo "ERROR: Cannot detect OS. Exiting."
  exit 1
fi

echo "Detected OS: ${OS_ID} ${OS_VER}"

# ─────────────────────────────────────────────────────────────────────
# 1. System update
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 1: System update"

if [[ "$OS_ID" == "ubuntu" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"
  apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    htop \
    vim \
    ufw \
    fail2ban \
    unattended-upgrades \
    apt-transport-https \
    software-properties-common \
    net-tools \
    jq \
    openssl

elif [[ "$OS_ID" == "ol" || "$OS_ID" == "rhel" || "$OS_ID" == "centos" || "$OS_ID" == "almalinux" ]]; then
  dnf update -y
  dnf install -y \
    ca-certificates \
    curl \
    gnupg2 \
    git \
    htop \
    vim \
    firewalld \
    fail2ban \
    net-tools \
    jq \
    openssl \
    dnf-automatic
fi

echo ">>> System update complete"

# ─────────────────────────────────────────────────────────────────────
# 2. Swap file — 2GB
#    OCI free tier RAM is shared with the OS. 6 browser containers
#    each using 300-600MB means swap is a safety net, not optional.
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 2: Creating 2GB swap"

SWAPFILE=/swapfile
if [ ! -f "$SWAPFILE" ]; then
  fallocate -l 2G "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count=2048
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
  swapon "$SWAPFILE"
  echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
  echo "vm.swappiness=10"  >> /etc/sysctl.d/99-toast.conf
  echo "vm.vfs_cache_pressure=50" >> /etc/sysctl.d/99-toast.conf
  sysctl -p /etc/sysctl.d/99-toast.conf
  echo ">>> Swap created: $(swapon --show)"
else
  echo ">>> Swap already exists, skipping"
fi

# ─────────────────────────────────────────────────────────────────────
# 3. Install Docker CE
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 3: Installing Docker CE"

if command -v docker &>/dev/null; then
  echo ">>> Docker already installed: $(docker --version)"
else

  if [[ "$OS_ID" == "ubuntu" ]]; then
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    # Add Docker repo
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update -y
    apt-get install -y \
      docker-ce \
      docker-ce-cli \
      containerd.io \
      docker-buildx-plugin \
      docker-compose-plugin

  elif [[ "$OS_ID" == "ol" || "$OS_ID" == "rhel" || "$OS_ID" == "centos" || "$OS_ID" == "almalinux" ]]; then
    # Oracle Linux / RHEL family
    dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo 2>/dev/null || \
    dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    dnf install -y \
      docker-ce \
      docker-ce-cli \
      containerd.io \
      docker-buildx-plugin \
      docker-compose-plugin
  fi

fi

# Enable + start Docker
systemctl enable docker
systemctl start docker

echo ">>> Docker installed: $(docker --version)"
echo ">>> Docker Compose: $(docker compose version)"

# ─────────────────────────────────────────────────────────────────────
# 4. Docker daemon tuning
#    - log rotation (containers log to json, unbounded by default)
#    - live-restore (containers survive daemon restart)
#    - no-new-privileges by default
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 4: Tuning Docker daemon"

mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'DOCKERD'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true,
  "userland-proxy": false,
  "no-new-privileges": true,
  "storage-driver": "overlay2"
}
DOCKERD

systemctl reload docker || systemctl restart docker
echo ">>> Docker daemon configured"

# ─────────────────────────────────────────────────────────────────────
# 5. Create deploy user
#    Running everything as root is bad practice.
#    'toast' user owns the app and is in the docker group.
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 5: Creating deploy user 'toast'"

if ! id "toast" &>/dev/null; then
  useradd -m -s /bin/bash toast
  usermod -aG docker toast
  echo ">>> User 'toast' created and added to docker group"
else
  usermod -aG docker toast
  echo ">>> User 'toast' already exists, ensured docker group membership"
fi

# Also add default cloud user to docker group (ubuntu / opc)
for u in ubuntu opc ec2-user; do
  if id "$u" &>/dev/null; then
    usermod -aG docker "$u"
    echo ">>> Added $u to docker group"
  fi
done

# ─────────────────────────────────────────────────────────────────────
# 6. Firewall configuration
#    OCI has TWO layers of firewalling:
#      a) OCI Security Lists / NSGs (cloud-level, configure in Console)
#      b) OS-level firewall (iptables / ufw / firewalld)
#
#    OCI's default Oracle Linux image also has very restrictive
#    iptables rules. We clear those and use ufw/firewalld instead.
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 6: Configuring firewall"

if [[ "$OS_ID" == "ubuntu" ]]; then
  # UFW setup
  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow 22/tcp   comment "SSH"
  ufw allow 80/tcp   comment "HTTP (Let's Encrypt challenge)"
  ufw allow 443/tcp  comment "HTTPS"
  ufw allow 443/udp  comment "HTTP/3 QUIC"
  # Allow Docker internal traffic
  ufw allow in on docker0
  ufw allow in on br-+
  ufw --force enable
  echo ">>> UFW configured"

elif [[ "$OS_ID" == "ol" || "$OS_ID" == "rhel" || "$OS_ID" == "centos" || "$OS_ID" == "almalinux" ]]; then
  # OCI's Oracle Linux has very tight iptables by default — flush them
  # so Caddy can bind to 80/443
  iptables  -F INPUT
  iptables  -F FORWARD
  iptables  -P INPUT   ACCEPT
  iptables  -P FORWARD ACCEPT
  iptables  -P OUTPUT  ACCEPT
  ip6tables -F INPUT  2>/dev/null || true
  ip6tables -P INPUT ACCEPT 2>/dev/null || true

  # Persist the flushed rules
  if command -v iptables-save &>/dev/null; then
    iptables-save  > /etc/iptables/rules.v4  2>/dev/null || \
    iptables-save  > /etc/sysconfig/iptables 2>/dev/null || true
  fi

  # Use firewalld for clean rule management
  systemctl enable firewalld
  systemctl start firewalld
  firewall-cmd --permanent --add-service=ssh
  firewall-cmd --permanent --add-service=http
  firewall-cmd --permanent --add-service=https
  firewall-cmd --permanent --add-port=443/udp
  # Allow Docker masquerade
  firewall-cmd --permanent --zone=trusted --add-interface=docker0
  firewall-cmd --reload
  echo ">>> firewalld configured + OCI iptables flushed"
fi

# ─────────────────────────────────────────────────────────────────────
# 7. Kernel tuning for container workloads
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 7: Kernel / sysctl tuning"

cat > /etc/sysctl.d/99-toast-containers.conf << 'SYSCTL'
# Allow more simultaneous connections (browser containers open many)
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535

# Increase file descriptor limits for KasmVNC streaming
fs.file-max = 1000000

# IP forwarding required for Docker networking
net.ipv4.ip_forward = 1

# Reduce TIME_WAIT — KasmVNC opens many short-lived connections
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15

# Shared memory — required for browser containers (shm_size: 512m)
kernel.shmmax = 536870912
kernel.shmall = 131072
SYSCTL

sysctl -p /etc/sysctl.d/99-toast-containers.conf
echo ">>> sysctl tuned"

# ─────────────────────────────────────────────────────────────────────
# 8. System limits — open files / processes for Docker + KasmVNC
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 8: System limits"

cat > /etc/security/limits.d/99-toast.conf << 'LIMITS'
*         soft  nofile    100000
*         hard  nofile    100000
root      soft  nofile    100000
root      hard  nofile    100000
toast     soft  nofile    100000
toast     hard  nofile    100000
LIMITS

# systemd unit override for Docker
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/limits.conf << 'DLIMITS'
[Service]
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
DLIMITS

systemctl daemon-reload
systemctl restart docker
echo ">>> System limits configured"

# ─────────────────────────────────────────────────────────────────────
# 9. Fail2ban — basic SSH brute force protection
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 9: Configuring fail2ban"

cat > /etc/fail2ban/jail.local << 'F2B'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled = true
port    = ssh
logpath = %(sshd_log)s
F2B

systemctl enable fail2ban
systemctl start fail2ban || systemctl restart fail2ban
echo ">>> fail2ban enabled"

# ─────────────────────────────────────────────────────────────────────
# 10. Unattended security upgrades
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 10: Unattended security upgrades"

if [[ "$OS_ID" == "ubuntu" ]]; then
  cat > /etc/apt/apt.conf.d/20auto-upgrades << 'AU'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
AU

elif [[ "$OS_ID" == "ol" || "$OS_ID" == "rhel" || "$OS_ID" == "centos" || "$OS_ID" == "almalinux" ]]; then
  sed -i 's/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf 2>/dev/null || true
  systemctl enable dnf-automatic.timer 2>/dev/null || true
fi

echo ">>> Auto security updates configured"

# ─────────────────────────────────────────────────────────────────────
# 11. Clone toast browser repository
#     Clones into /opt/cloudbrowser, owned by the toast user
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 11: Setting up toast browser directory"

toast_DIR=/opt/cloudbrowser

mkdir -p "$toast_DIR"
chown toast:toast "$toast_DIR"

# If you have a git repo, uncomment and replace URL:
# su - toast -c "git clone https://github.com/YOUR_USER/cloudbrowser.git $toast_DIR"

# For now, create the directory structure ready for manual file upload
# or scp from your local machine
mkdir -p "$toast_DIR"/{caddy,ddclient,dashboard}
chown -R toast:toast "$toast_DIR"

# Create a README for the toast user
cat > "$toast_DIR/READY.txt" << 'READY'
toast browser — OCI Instance Ready
===================================

This instance has been configured with:
  ✓ Docker CE (latest)
  ✓ Docker Compose v2 plugin
  ✓ 2GB swap
  ✓ Firewall (ports 22, 80, 443 open)
  ✓ Kernel tuning for container workloads
  ✓ fail2ban SSH protection
  ✓ Auto security updates

NEXT STEPS:
-----------
1. Copy your toast browser files to this directory:
     scp -r ./cloudbrowser/* toast@YOUR_IP:/opt/cloudbrowser/

2. Edit ddclient/ddclient.conf with your dedyn.io credentials

3. Copy .env.example to .env and fill in your values:
     cp .env.example .env && vim .env

4. Run setup:
     chmod +x setup.sh toast.sh
     ./setup.sh

5. IMPORTANT — OCI Security List:
   In the OCI Console, go to:
     Networking → Virtual Cloud Networks → your VCN
     → Security Lists → Default Security List
   Add Ingress Rules for:
     TCP port 80   (source: 0.0.0.0/0)
     TCP port 443  (source: 0.0.0.0/0)
     UDP port 443  (source: 0.0.0.0/0)
   Without this, internet traffic cannot reach Caddy
   even though the OS firewall allows it.

READY

chown toast:toast "$toast_DIR/READY.txt"
echo ">>> toast browser directory ready at $toast_DIR"

# ─────────────────────────────────────────────────────────────────────
# 12. Pull KasmVNC images in background
#     Pulls while you're logging in and configuring — saves time.
#     Runs as a systemd oneshot so it doesn't block boot.
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 12: Scheduling background image pull"

cat > /etc/systemd/system/toast-image-pull.service << 'UNIT'
[Unit]
Description=toast browser — Pre-pull KasmVNC images
After=docker.service network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/toast-images-pulled

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
ExecStart=/bin/bash -c '\
  for img in \
    "lscr.io/linuxserver/ddclient:latest" \
    "caddy:2-alpine" \
    "kasmweb/chrome:1.16.0" \
    "kasmweb/firefox:1.16.0" \
    "kasmweb/chromium:1.16.0" \
    "kasmweb/tor-browser:1.16.0"; do \
      echo "Pulling $img..." >> /var/log/toast-pull.log 2>&1; \
      docker pull "$img" >> /var/log/toast-pull.log 2>&1 || true; \
  done; \
  touch /var/lib/toast-images-pulled; \
  echo "All images pulled at $(date)" >> /var/log/toast-pull.log'
TimeoutStartSec=900
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable toast-image-pull.service
systemctl start toast-image-pull.service &   # background — don't block boot
echo ">>> Image pre-pull started in background (check: journalctl -u toast-image-pull -f)"

# ─────────────────────────────────────────────────────────────────────
# 13. Set hostname
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 13: Setting hostname"
hostnamectl set-hostname toast-browser 2>/dev/null || hostname toast-browser
echo ">>> Hostname set to toast-browser"

# ─────────────────────────────────────────────────────────────────────
# 14. SSH hardening (optional but recommended)
# ─────────────────────────────────────────────────────────────────────
echo ">>> Step 14: SSH hardening"

# Disable password auth — key-only (OCI uses keys by default anyway)
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?X11Forwarding.*/X11Forwarding no/' /etc/ssh/sshd_config

# Add keep-alive to prevent OCI's idle connection drops
grep -q "ClientAliveInterval" /etc/ssh/sshd_config || \
  echo "ClientAliveInterval 60" >> /etc/ssh/sshd_config
grep -q "ClientAliveCountMax" /etc/ssh/sshd_config || \
  echo "ClientAliveCountMax 3" >> /etc/ssh/sshd_config

systemctl reload sshd || systemctl reload ssh
echo ">>> SSH hardened (key-only, no root login)"

# ─────────────────────────────────────────────────────────────────────
# 15. Final summary
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo " toast browser OCI Init — COMPLETE"
echo " $(date -u)"
echo "======================================================"
echo ""
echo " OS:           ${OS_ID} ${OS_VER}"
echo " Docker:       $(docker --version)"
echo " Compose:      $(docker compose version)"
echo " Swap:         $(swapon --show --noheadings | awk '{print $3}')"
echo " Firewall:     active (22, 80, 443 open)"
echo " App dir:      /opt/cloudbrowser"
echo " Pull log:     /var/log/toast-pull.log"
echo " Init log:     /var/log/toast-browser-init.log"
echo ""
echo " NEXT: scp your files, then ./setup.sh"
echo " See:  /opt/cloudbrowser/READY.txt"
echo "======================================================"
