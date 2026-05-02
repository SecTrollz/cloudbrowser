#!/usr/bin/env bash
# ╔══════════════════════════════════════════╗
# ║   Toast Browser — Setup Script           ║
# ╚══════════════════════════════════════════╝
set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}"
cat << 'EOF'
  ██████  ██   ██  ██████  ███████ ████████ 
 ██       ██   ██ ██    ██ ██         ██    
 ██   ███ ███████ ██    ██ ███████    ██    
 ██    ██ ██   ██ ██    ██      ██    ██    
  ██████  ██   ██  ██████  ███████    ██    
                                            
 BROWSER — Self-Hosted Profile Manager
EOF
echo -e "${RESET}"

# ── Check deps ───────────────────────────────────────────
check_dep() {
  if ! command -v "$1" &>/dev/null; then
    echo -e "${RED}✗ $1 not found. Please install it first.${RESET}"
    exit 1
  fi
  echo -e "${GREEN}✓ $1 found${RESET}"
}

echo -e "\n${BOLD}Checking dependencies...${RESET}"
check_dep docker
check_dep "docker compose" 2>/dev/null || check_dep docker-compose

# ── .env setup ───────────────────────────────────────────
if [ ! -f ".env" ]; then
  echo -e "\n${BOLD}Creating .env from template...${RESET}"
  cp .env.example .env

  # Generate a random secret
  SECRET=$(tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 48 2>/dev/null || echo "please_set_manually_$(date +%s)")
  sed -i "s/replace_this_with_a_long_random_string/$SECRET/" .env

  echo -e "${YELLOW}⚠  .env created with auto-generated secret.${RESET}"
  echo -e "${YELLOW}   IMPORTANT: Change the VNC passwords in .env before use!${RESET}"
  echo ""
  echo -e "   Edit: ${BOLD}.env${RESET}"
  echo ""
  read -p "   Press Enter to continue with defaults, or Ctrl+C to edit first... "
fi

# ── Pull images ──────────────────────────────────────────
echo -e "\n${BOLD}Pulling browser images (this may take a few minutes)...${RESET}"
echo -e "${CYAN}Pulling Chrome...${RESET}"
docker pull kasmweb/chrome:1.16.0

echo -e "${CYAN}Pulling Firefox...${RESET}"
docker pull kasmweb/firefox:1.16.0

echo -e "${CYAN}Pulling Chromium...${RESET}"
docker pull kasmweb/chromium:1.16.0

echo -e "${CYAN}Pulling Tor Browser...${RESET}"
docker pull kasmweb/tor-browser:1.16.0

# ── Create profiles dir ──────────────────────────────────
mkdir -p profiles

# ── Build & start ────────────────────────────────────────
echo -e "\n${BOLD}Building dashboard...${RESET}"
docker compose build dashboard

echo -e "\n${BOLD}Starting Toast Browser stack...${RESET}"
docker compose up -d

# ── Wait for dashboard ───────────────────────────────────
echo -e "\n${BOLD}Waiting for dashboard to be ready...${RESET}"
for i in {1..20}; do
  if curl -sf http://localhost:8080 &>/dev/null; then
    break
  fi
  echo -n "."
  sleep 1
done

echo -e "\n\n${GREEN}${BOLD}✓ Toast Browser is running!${RESET}\n"
echo -e "  Dashboard:  ${BOLD}http://localhost:8080${RESET}"
echo ""
echo -e "  Profiles:"
echo -e "    💼 Work      → ${BOLD}http://localhost:6901${RESET}"
echo -e "    🏠 Personal  → ${BOLD}http://localhost:6902${RESET}"
echo -e "    🔬 Research  → ${BOLD}http://localhost:6903${RESET}"
echo -e "    📱 Social    → ${BOLD}http://localhost:6904${RESET}"
echo -e "    🏦 Banking   → ${BOLD}http://localhost:6905${RESET}"
echo -e "    💻 Dev       → ${BOLD}http://localhost:6906${RESET}"
echo ""
echo -e "${YELLOW}  VNC passwords are set in your .env file${RESET}"
echo -e "${CYAN}  Run './toast.sh help' for management commands${RESET}\n"
