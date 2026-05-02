#!/usr/bin/env bash
# Toast Browser management CLI
set -e

BOLD="\033[1m"; GREEN="\033[0;32m"; CYAN="\033[0;36m"
YELLOW="\033[0;33m"; RED="\033[0;31m"; RESET="\033[0m"

PROFILES=(work personal research social banking dev)

usage() {
cat << EOF
${BOLD}Toast Browser CLI${RESET}

Usage: ./toast.sh <command> [profile]

Commands:
  up              Start all profiles
  down            Stop all profiles
  start <name>    Start a specific profile
  stop <name>     Stop a specific profile
  restart <name>  Restart a specific profile
  status          Show status of all containers
  logs <name>     Tail logs for a profile
  nuke <name>     Destroy a profile's volume (wipes all data!)
  open <name>     Open a profile in your browser
  add <name>      Scaffold a new profile in docker-compose.yml
  help            Show this message

Profiles: ${PROFILES[*]}

Examples:
  ./toast.sh start work
  ./toast.sh nuke research   # fresh fingerprint
  ./toast.sh open banking
EOF
}

status() {
  echo -e "\n${BOLD}Toast Browser — Container Status${RESET}\n"
  printf "  %-15s %-12s %-8s %s\n" "PROFILE" "STATUS" "PORT" "BROWSER"
  printf "  %-15s %-12s %-8s %s\n" "───────" "──────" "────" "───────"

  docker ps -a --filter "label=toast.role=browser" \
    --format "{{.Names}}\t{{.Status}}\t{{.Label \"toast.port\"}}\t{{.Label \"toast.browser\"}}" | \
  while IFS=$'\t' read -r name status port browser; do
    profile=$(echo "$name" | sed 's/toast_//')
    if [[ "$status" == *"Up"* ]]; then
      color=$GREEN
    else
      color=$RED
    fi
    printf "  %-15s ${color}%-12s${RESET} %-8s %s\n" "$profile" "${status:0:10}" ":$port" "$browser"
  done
  echo ""
}

open_profile() {
  local name="$1"
  local port
  port=$(docker inspect "toast_${name}" --format '{{index .Config.Labels "toast.port"}}' 2>/dev/null)
  if [ -z "$port" ]; then
    echo -e "${RED}Profile '$name' not found.${RESET}"; exit 1
  fi
  local url="http://localhost:${port}"
  echo -e "Opening ${BOLD}$name${RESET} → $url"
  if command -v xdg-open &>/dev/null; then xdg-open "$url"
  elif command -v open &>/dev/null; then open "$url"
  else echo "Navigate to: $url"
  fi
}

nuke_profile() {
  local name="$1"
  echo -e "${RED}${BOLD}⚠  WARNING: This will permanently destroy all data for '$name'${RESET}"
  read -p "   Type the profile name to confirm: " confirm
  if [ "$confirm" != "$name" ]; then
    echo "Aborted."; exit 0
  fi
  docker compose stop "browser_${name}" 2>/dev/null || true
  docker volume rm "toastbrowser_profile_${name}" 2>/dev/null || true
  echo -e "${GREEN}✓ Profile '$name' nuked. Fresh fingerprint on next start.${RESET}"
}

case "${1:-help}" in
  up)       docker compose up -d ;;
  down)     docker compose down ;;
  start)    docker compose start "browser_${2:?'Profile name required'}" ;;
  stop)     docker compose stop "browser_${2:?'Profile name required'}" ;;
  restart)  docker compose restart "browser_${2:?'Profile name required'}" ;;
  status)   status ;;
  logs)     docker logs -f "toast_${2:?'Profile name required'}" ;;
  open)     open_profile "${2:?'Profile name required'}" ;;
  nuke)     nuke_profile "${2:?'Profile name required'}" ;;
  help|*)   usage ;;
esac
