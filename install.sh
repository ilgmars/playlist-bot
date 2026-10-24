#!/usr/bin/env bash
# Discord Playlist Bot installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ilgmars/playlist-bot/main/install.sh | bash

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ilgmars/playlist-bot.git}"
INSTALL_DIR="${INSTALL_DIR:-}"
ENV_FILE="${ENV_FILE:-}"
NON_INTERACTIVE=0
DRY_RUN=0

if [[ -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_GREEN=$'\033[38;5;78m'
  C_CYAN=$'\033[38;5;81m'
  C_YELLOW=$'\033[38;5;221m'
  C_PURPLE=$'\033[38;5;141m'
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_GREEN=""
  C_CYAN=""
  C_YELLOW=""
  C_PURPLE=""
fi

usage() {
  cat <<'EOF'
Usage: install.sh [OPTIONS]

Installer for Discord Playlist Bot (Discord OAuth2, Cloudflare Tunnel,
and Keycloak OIDC with the 'playlist-bot' realm).

Quick install:
  curl -fsSL https://raw.githubusercontent.com/ilgmars/playlist-bot/main/install.sh | bash

Options:
  --install-dir <path>   Target directory to clone or configure (default: ./playlist-bot)
  --env-file <path>      Output path for generated .env file (default: <install-dir>/.env)
  --non-interactive      Use environment variables / defaults without prompting on /dev/tty
  --dry-run              Generate .env and print docker compose command without starting containers
  -h, --help             Show this help message and exit
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

banner() {
  printf "%s%sDiscord Playlist Bot setup%s\n" "$C_GREEN" "$C_BOLD" "$C_RESET"
}

section() {
  printf "\n%s%s[%s]%s\n" "$C_PURPLE" "$C_BOLD" "$1" "$C_RESET"
}

guide_box() {
  printf "%s%s%s\n" "$C_CYAN" "$1" "$C_RESET"
}

prompt_val() {
  local var_name="$1"
  local prompt_text="$2"
  local default_val="${3:-}"
  local current_val="${!var_name:-}"

  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    if [[ -n "$current_val" ]]; then
      printf -v "$var_name" '%s' "$current_val"
    else
      printf -v "$var_name" '%s' "$default_val"
    fi
    return 0
  fi

  local display_default="${current_val:-$default_val}"
  local answer=""
  if [[ -n "$display_default" ]]; then
    printf "%s%s%s [%s%s%s]: " "$C_BOLD" "$prompt_text" "$C_RESET" "$C_YELLOW" "$display_default" "$C_RESET" >/dev/tty
  else
    printf "%s%s%s: " "$C_BOLD" "$prompt_text" "$C_RESET" >/dev/tty
  fi
  read -r answer </dev/tty || true
  if [[ -z "$answer" ]]; then
    answer="$display_default"
  fi
  printf -v "$var_name" '%s' "$answer"
}

prompt_secret() {
  local var_name="$1"
  local prompt_text="$2"
  local default_val="${3:-}"
  local current_val="${!var_name:-}"

  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    if [[ -n "$current_val" ]]; then
      printf -v "$var_name" '%s' "$current_val"
    else
      printf -v "$var_name" '%s' "$default_val"
    fi
    return 0
  fi

  local answer=""
  if [[ -n "$current_val" || -n "$default_val" ]]; then
    printf "%s%s%s [%spress Enter to keep existing/default%s]: " "$C_BOLD" "$prompt_text" "$C_RESET" "$C_DIM" "$C_RESET" >/dev/tty
  else
    printf "%s%s%s (input hidden): " "$C_BOLD" "$prompt_text" "$C_RESET" >/dev/tty
  fi
  read -rs answer </dev/tty || true
  printf "\n" >/dev/tty
  if [[ -z "$answer" ]]; then
    answer="${current_val:-$default_val}"
  fi
  printf -v "$var_name" '%s' "$answer"
}

prompt_yes_no() {
  local var_name="$1"
  local prompt_text="$2"
  local default_ans="${3:-y}"
  local current_val="${!var_name:-}"

  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    local val="${current_val:-$default_ans}"
    printf -v "$var_name" '%s' "$val"
    return 0
  fi

  local hint="Y/n"
  [[ "$default_ans" =~ ^[Nn] ]] && hint="y/N"
  local answer=""
  printf "%s%s%s [%s]: " "$C_BOLD" "$prompt_text" "$C_RESET" "$hint" >/dev/tty
  read -r answer </dev/tty || true
  answer="${answer:-$default_ans}"
  if [[ "$answer" =~ ^[Yy] ]]; then
    printf -v "$var_name" '%s' "y"
  else
    printf -v "$var_name" '%s' "n"
  fi
}

generate_hex_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_hex(32))"
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

banner

if [[ -z "$INSTALL_DIR" ]]; then
  if [[ -f "./docker-compose.yml" && -f "./pyproject.toml" ]]; then
    INSTALL_DIR="$(pwd)"
  else
    INSTALL_DIR="$(pwd)/playlist-bot"
  fi
fi

if [[ -z "$ENV_FILE" ]]; then
  ENV_FILE="${INSTALL_DIR}/.env"
fi

section "1/5 Public URL & Port"
guide_box "Set the public URL where the dashboard will be reachable (for example via
Cloudflare Tunnel: https://music.example.com), or keep http://localhost:8000 for local use."

PUBLIC_URL="${PUBLIC_URL:-http://localhost:8000}"
WEB_BIND_PORT="${WEB_BIND_PORT:-8000}"
prompt_val PUBLIC_URL "Public dashboard URL" "http://localhost:8000"
PUBLIC_URL="${PUBLIC_URL%/}"
prompt_val WEB_BIND_PORT "Local host port to bind web dashboard" "8000"

section "2/5 Discord Bot & OAuth2"
guide_box "Discord setup guide:
  1. Open https://discord.com/developers/applications -> New Application.
  2. Bot tab:
     - Reset Token and copy DISCORD_TOKEN.
     - Enable Message Content Intent (and Server Members Intent).
  3. OAuth2 tab:
     - Copy Client ID (DISCORD_CLIENT_ID) and Client Secret (DISCORD_CLIENT_SECRET).
     - Add Redirect URL: ${PUBLIC_URL}/auth/discord/callback
  4. Admin User ID (BOT_ADMIN_IDS):
     - Discord Settings -> Advanced -> Developer Mode -> right-click your user -> Copy User ID."

prompt_secret DISCORD_TOKEN "Discord Bot Token (DISCORD_TOKEN)" ""
prompt_val DISCORD_CLIENT_ID "Discord OAuth2 Client ID (DISCORD_CLIENT_ID)" ""
prompt_secret DISCORD_CLIENT_SECRET "Discord OAuth2 Client Secret (DISCORD_CLIENT_SECRET)" ""
prompt_val BOT_ADMIN_IDS "Global Bot Admin Discord User IDs (comma-separated)" ""
prompt_val DISCORD_CHANNEL_IDS "Default Discord Channel IDs (optional)" ""
DISCORD_REDIRECT_URI="${DISCORD_REDIRECT_URI:-${PUBLIC_URL}/auth/discord/callback}"

section "3/5 Cloudflare Tunnel (cloudflared)"
guide_box "Cloudflare Tunnel setup guide:
  1. Open https://one.dash.cloudflare.com/ -> Networks -> Tunnels -> Create a tunnel.
  2. Choose Cloudflared, name the tunnel 'playlist-bot', and copy the token ('eyJh...')
     from 'cloudflared service install <CF_TUNNEL_TOKEN>'.
  3. Public Hostname tab -> Add a public hostname:
     - Hostname: matching ${PUBLIC_URL}
     - Service: HTTP -> playlist-bot:8000"

prompt_yes_no ENABLE_CLOUDFLARE_TUNNEL "Enable Cloudflare Tunnel (cloudflared) container?" "n"
CF_TUNNEL_TOKEN="${CF_TUNNEL_TOKEN:-}"
if [[ "$ENABLE_CLOUDFLARE_TUNNEL" == "y" ]]; then
  prompt_secret CF_TUNNEL_TOKEN "Cloudflare Tunnel Token (CF_TUNNEL_TOKEN)" ""
fi

section "4/5 User Authentication & Keycloak Realm (playlist-bot)"
guide_box "Authentication modes:
  - discord: Direct Discord OAuth2 login.
  - keycloak: Keycloak OIDC using deploy/keycloak/playlist-bot-realm.json
    (realm 'playlist-bot', roles 'playlist-admin' & 'playlist-member',
    client 'playlist-bot-web', and Discord identity provider broker).
  - both: Enable both Discord OAuth2 and Keycloak SSO."

prompt_val AUTH_MODE "Authentication mode [discord / keycloak / both]" "both"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-playlist-bot}"
KEYCLOAK_CLIENT_ID="${KEYCLOAK_CLIENT_ID:-}"
KEYCLOAK_CLIENT_SECRET="${KEYCLOAK_CLIENT_SECRET:-}"
KEYCLOAK_ISSUER_URL="${KEYCLOAK_ISSUER_URL:-}"
KEYCLOAK_INTERNAL_ISSUER_URL="${KEYCLOAK_INTERNAL_ISSUER_URL:-}"
KEYCLOAK_REDIRECT_URI="${KEYCLOAK_REDIRECT_URI:-}"
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-}"
KEYCLOAK_BIND_PORT="${KEYCLOAK_BIND_PORT:-8081}"
KEYCLOAK_MODE="${KEYCLOAK_MODE:-none}"

if [[ "$AUTH_MODE" == "keycloak" || "$AUTH_MODE" == "both" ]]; then
  prompt_val KEYCLOAK_MODE "Keycloak deployment [bundled / external]" "bundled"
  prompt_val KEYCLOAK_REALM "Keycloak realm name" "playlist-bot"
  prompt_val KEYCLOAK_CLIENT_ID "Keycloak OIDC Client ID" "playlist-bot-web"
  DEFAULT_KC_SECRET="$(generate_hex_secret)"
  prompt_secret KEYCLOAK_CLIENT_SECRET "Keycloak OIDC Client Secret" "$DEFAULT_KC_SECRET"
  KEYCLOAK_REDIRECT_URI="${PUBLIC_URL}/auth/keycloak/callback"

  if [[ "$KEYCLOAK_MODE" == "bundled" ]]; then
    prompt_val KEYCLOAK_BIND_PORT "Local port for bundled Keycloak HTTP console" "8081"
    prompt_val KEYCLOAK_ADMIN_USER "Bundled Keycloak bootstrap admin username" "admin"
    DEFAULT_KC_ADMIN_PW="$(generate_hex_secret | cut -c1-20)"
    prompt_secret KEYCLOAK_ADMIN_PASSWORD "Bundled Keycloak bootstrap admin password" "$DEFAULT_KC_ADMIN_PW"
    prompt_val KEYCLOAK_ISSUER_URL "Public Keycloak Realm Issuer URL" "http://localhost:${KEYCLOAK_BIND_PORT}/realms/${KEYCLOAK_REALM}"
    KEYCLOAK_INTERNAL_ISSUER_URL="http://keycloak:8080/realms/${KEYCLOAK_REALM}"
  else
    guide_box "External Keycloak setup:
  1. In Keycloak Admin Console -> Create realm -> import deploy/keycloak/playlist-bot-realm.json.
  2. Clients -> playlist-bot-web -> Credentials -> copy Client Secret.
  3. Identity Providers -> discord -> set Discord Client ID & Secret."
    prompt_val KEYCLOAK_ISSUER_URL "External Keycloak Realm Issuer URL (e.g. https://sso.example.com/realms/playlist-bot)" ""
    KEYCLOAK_INTERNAL_ISSUER_URL="$KEYCLOAK_ISSUER_URL"
  fi
fi

section "5/5 Optional Spotify & YouTube API Sync"
guide_box "Optional API credentials for writing to dedicated Spotify/YouTube account playlists:
  - Spotify: https://developer.spotify.com/dashboard
  - YouTube: https://console.cloud.google.com/apis/library/youtube.googleapis.com"

prompt_yes_no CONFIGURE_PLATFORM_SYNC "Configure Spotify / YouTube API refresh tokens now?" "n"
SPOTIFY_CLIENT_ID="${SPOTIFY_CLIENT_ID:-}"
SPOTIFY_CLIENT_SECRET="${SPOTIFY_CLIENT_SECRET:-}"
SPOTIFY_REFRESH_TOKEN="${SPOTIFY_REFRESH_TOKEN:-}"
YOUTUBE_API_KEY="${YOUTUBE_API_KEY:-}"
YOUTUBE_CLIENT_ID="${YOUTUBE_CLIENT_ID:-}"
YOUTUBE_CLIENT_SECRET="${YOUTUBE_CLIENT_SECRET:-}"
YOUTUBE_REFRESH_TOKEN="${YOUTUBE_REFRESH_TOKEN:-}"

if [[ "$CONFIGURE_PLATFORM_SYNC" == "y" ]]; then
  prompt_val SPOTIFY_CLIENT_ID "Spotify Client ID" ""
  prompt_secret SPOTIFY_CLIENT_SECRET "Spotify Client Secret" ""
  prompt_secret SPOTIFY_REFRESH_TOKEN "Spotify Refresh Token" ""
  prompt_secret YOUTUBE_API_KEY "YouTube API Key" ""
  prompt_val YOUTUBE_CLIENT_ID "YouTube OAuth2 Client ID" ""
  prompt_secret YOUTUBE_CLIENT_SECRET "YouTube OAuth2 Client Secret" ""
  prompt_secret YOUTUBE_REFRESH_TOKEN "YouTube OAuth2 Refresh Token" ""
fi

SESSION_SECRET="${SESSION_SECRET:-$(generate_hex_secret)}"

if [[ "$DRY_RUN" -eq 0 && ! -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
  section "Cloning repository into ${INSTALL_DIR}"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

mkdir -p "$(dirname "$ENV_FILE")"
umask 077
cat >"$ENV_FILE" <<EOF
PUBLIC_URL=${PUBLIC_URL}
WEB_BIND_PORT=${WEB_BIND_PORT}
SESSION_SECRET=${SESSION_SECRET}

DISCORD_TOKEN=${DISCORD_TOKEN}
DISCORD_CHANNEL_IDS=${DISCORD_CHANNEL_IDS}
DISCORD_CLIENT_ID=${DISCORD_CLIENT_ID}
DISCORD_CLIENT_SECRET=${DISCORD_CLIENT_SECRET}
DISCORD_REDIRECT_URI=${DISCORD_REDIRECT_URI}
BOT_ADMIN_IDS=${BOT_ADMIN_IDS}

CF_TUNNEL_TOKEN=${CF_TUNNEL_TOKEN}

KEYCLOAK_REALM=${KEYCLOAK_REALM}
KEYCLOAK_ISSUER_URL=${KEYCLOAK_ISSUER_URL}
KEYCLOAK_INTERNAL_ISSUER_URL=${KEYCLOAK_INTERNAL_ISSUER_URL}
KEYCLOAK_CLIENT_ID=${KEYCLOAK_CLIENT_ID}
KEYCLOAK_CLIENT_SECRET=${KEYCLOAK_CLIENT_SECRET}
KEYCLOAK_REDIRECT_URI=${KEYCLOAK_REDIRECT_URI}
KEYCLOAK_ADMIN_ROLE=playlist-admin
KEYCLOAK_BIND_PORT=${KEYCLOAK_BIND_PORT}
KEYCLOAK_ADMIN_USER=${KEYCLOAK_ADMIN_USER}
KEYCLOAK_ADMIN_PASSWORD=${KEYCLOAK_ADMIN_PASSWORD}

SPOTIFY_CLIENT_ID=${SPOTIFY_CLIENT_ID}
SPOTIFY_CLIENT_SECRET=${SPOTIFY_CLIENT_SECRET}
SPOTIFY_REFRESH_TOKEN=${SPOTIFY_REFRESH_TOKEN}
YOUTUBE_API_KEY=${YOUTUBE_API_KEY}
YOUTUBE_CLIENT_ID=${YOUTUBE_CLIENT_ID}
YOUTUBE_CLIENT_SECRET=${YOUTUBE_CLIENT_SECRET}
YOUTUBE_REFRESH_TOKEN=${YOUTUBE_REFRESH_TOKEN}
EOF
chmod 600 "$ENV_FILE"

COMPOSE_PROFILES=()
if [[ "$ENABLE_CLOUDFLARE_TUNNEL" == "y" && -n "$CF_TUNNEL_TOKEN" ]]; then
  COMPOSE_PROFILES+=(--profile cloudflared)
fi
if [[ "$KEYCLOAK_MODE" == "bundled" ]]; then
  COMPOSE_PROFILES+=(--profile keycloak)
fi

section "Summary"
printf "  * Environment file : %s (mode 0600)\n" "$ENV_FILE"
printf "  * Public URL       : %s\n" "$PUBLIC_URL"
printf "  * Auth Mode        : %s (Keycloak mode: %s)\n" "$AUTH_MODE" "$KEYCLOAK_MODE"
printf "  * Cloudflare Tunnel: %s\n" "$ENABLE_CLOUDFLARE_TUNNEL"
printf "  * Compose Command  : docker compose %s up -d --build\n" "${COMPOSE_PROFILES[*]:-}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf "\n%sDry run complete -- skipping container launch.%s\n" "$C_GREEN" "$C_RESET"
  exit 0
fi

LAUNCH_NOW="y"
prompt_yes_no LAUNCH_NOW "Start Docker containers now?" "y"
if [[ "$LAUNCH_NOW" == "y" ]]; then
  cd "$INSTALL_DIR"
  docker compose "${COMPOSE_PROFILES[@]}" up -d --build
  printf "\n%s%sRunning at %s%s\n" "$C_GREEN" "$C_BOLD" "$PUBLIC_URL" "$C_RESET"
fi
