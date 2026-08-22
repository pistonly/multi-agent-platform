#!/usr/bin/env bash
# MAP Quick Start Script
#
# One command to get MAP running from a fresh clone:
#   1. Generate Fernet encryption key and write .env
#   2. Start API + Web + MCP via Docker Compose
#   3. Wait for API health check
#   4. Register the first Admin agent
#   5. Print next-step instructions (bootstrap + CLI)
#
# Usage:
#   ./scripts/quickstart.sh                    # default admin name "map-admin"
#   ./scripts/quickstart.sh --admin-name jane  # custom admin name
#   ./scripts/quickstart.sh --api-port 19000   # override API port
#   ./scripts/quickstart.sh --skip-docker      # if services already running
#
# Environment variables (optional):
#   MAP_ADMIN_NAME     Admin agent name (default: map-admin)
#   MAP_API_PORT       API port (default: 18400)
#   MAP_SKIP_DOCKER    If set, skip docker compose up

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Colors ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' CYAN='' BOLD='' NC=''
fi

log()   { echo -e "${GREEN}[quickstart]${NC} $*" >&2; }
warn()  { echo -e "${YELLOW}[quickstart]${NC} $*" >&2; }
error() { echo -e "${RED}[quickstart]${NC} $*" >&2; }
step()  { echo -e "\n${BOLD}${CYAN}── Step $1: $2 ──${NC}" >&2; }

# ── Parse args ──────────────────────────────────────────────────────────────
ADMIN_NAME="${MAP_ADMIN_NAME:-map-admin}"
API_PORT="${MAP_API_PORT:-}"
SKIP_DOCKER="${MAP_SKIP_DOCKER:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin-name) ADMIN_NAME="$2"; shift 2 ;;
    --api-port)   API_PORT="$2";   shift 2 ;;
    --skip-docker) SKIP_DOCKER=1;  shift ;;
    --help|-h)
      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) error "Unknown option: $1"; exit 1 ;;
  esac
done

# ── API port ────────────────────────────────────────────────────────────────
# 默认 18400（不常用端口，避开 8000/8001 冲突重灾区；与 docker-compose.yml 一致）。
if [[ -z "$API_PORT" ]]; then
  API_PORT="18400"
fi
API_URL="http://localhost:${API_PORT}"

# ── Check dependencies ─────────────────────────────────────────────────────
step 1 "Checking dependencies"

missing=()
if [[ -z "$SKIP_DOCKER" ]]; then
  command -v docker >/dev/null 2>&1 || missing+=("docker")
  command -v docker compose >/dev/null 2>&1 || {
    # docker compose v1 fallback
    if ! docker compose version >/dev/null 2>&1; then
      missing+=("docker-compose (plugin v2+)")
    fi
  }
fi
command -v python3 >/dev/null 2>&1 || missing+=("python3")
command -v curl >/dev/null 2>&1 || missing+=("curl")

if [[ ${#missing[@]} -gt 0 ]]; then
  error "Missing dependencies: ${missing[*]}"
  error "Please install them first."
  exit 1
fi
log "Dependencies OK (docker, python3, curl)"

# ── Generate .env with Fernet key ──────────────────────────────────────────
step 2 "Preparing .env"

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  log "Created .env from .env.example"
else
  warn ".env already exists, will update if needed"
fi

# Generate Fernet key if MAP_WEBHOOK_SECRET_ENCRYPTION_KEY is empty
CURRENT_KEY=$(grep '^MAP_WEBHOOK_SECRET_ENCRYPTION_KEY=' .env | cut -d'=' -f2- || true)
if [[ -z "$CURRENT_KEY" ]]; then
  log "Generating Fernet encryption key..."
  FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || true)
  if [[ -z "$FERNET_KEY" ]]; then
    # Fallback: cryptography not installed yet, generate via pip install
    warn "cryptography not installed, installing temporarily..."
    pip install --quiet cryptography --break-system-packages 2>/dev/null || pip install --quiet cryptography
    FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  fi
  # Replace the line in .env
  if grep -q '^MAP_WEBHOOK_SECRET_ENCRYPTION_KEY=' .env; then
    sed -i.bak "s|^MAP_WEBHOOK_SECRET_ENCRYPTION_KEY=.*|MAP_WEBHOOK_SECRET_ENCRYPTION_KEY=${FERNET_KEY}|" .env && rm -f .env.bak
  else
    echo "MAP_WEBHOOK_SECRET_ENCRYPTION_KEY=${FERNET_KEY}" >> .env
  fi
  log "Fernet key generated and written to .env"
else
  log "Fernet key already configured"
fi

# Update MAP_API_URL in .env to match detected port
if grep -q '^MAP_API_URL=' .env; then
  sed -i.bak "s|^MAP_API_URL=.*|MAP_API_URL=${API_URL}|" .env && rm -f .env.bak
fi

# ── Start Docker services ──────────────────────────────────────────────────
if [[ -z "$SKIP_DOCKER" ]]; then
  step 3 "Starting Docker services (API :${API_PORT}, Web :3000, MCP)"

  log "Running: docker compose up --build -d"
  docker compose up --build -d

  # Wait for API health
  log "Waiting for API to become healthy..."
  MAX_WAIT=60
  WAITED=0
  while [[ $WAITED -lt $MAX_WAIT ]]; do
    if curl -sf "${API_URL}/health" >/dev/null 2>&1; then
      log "API is healthy (waited ${WAITED}s)"
      break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    if [[ $((WAITED % 10)) -eq 0 ]]; then
      echo -n "." >&2
    fi
  done

  if [[ $WAITED -ge $MAX_WAIT ]]; then
    echo "" >&2
    error "API did not become healthy within ${MAX_WAIT}s"
    error "Check logs: docker compose logs api"
    exit 1
  fi
  echo "" >&2
else
  step 3 "Skipping Docker (services assumed running)"
  # Still verify health
  if ! curl -sf "${API_URL}/health" >/dev/null 2>&1; then
    error "API at ${API_URL} is not responding. Start it first or remove --skip-docker."
    exit 1
  fi
  log "API is healthy at ${API_URL}"
fi

# ── Register first Admin ───────────────────────────────────────────────────
step 4 "Registering first Admin agent"

# Check if any admin already exists
EXISTING_ADMINS=$(curl -sf "${API_URL}/api/v1/agents?role=admin" 2>/dev/null || echo "[]")
if echo "$EXISTING_ADMINS" | python3 -c "import sys,json; data=json.load(sys.stdin); sys.exit(0 if data else 1)" 2>/dev/null; then
  log "Admin agents already exist, skipping registration"
  warn "If you need the admin token, check your previous registration output or ~/.map/admin.yaml"
else
  log "Registering admin: ${ADMIN_NAME}"
  REGISTER_RESPONSE=$(curl -sf -X POST "${API_URL}/api/v1/agents?name=${ADMIN_NAME}&role=admin" 2>&1) || {
    error "Failed to register admin agent"
    error "Response: ${REGISTER_RESPONSE}"
    error "The API might already have agents. Try with --admin-name <different-name>"
    exit 1
  }

  # Extract token from response
  ADMIN_TOKEN=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_token',''))" 2>/dev/null || true)

  if [[ -z "$ADMIN_TOKEN" ]]; then
    error "Could not extract api_token from registration response:"
    error "$REGISTER_RESPONSE"
    exit 1
  fi

  log "Admin registered successfully"

  # Save admin token to ~/.map/admin.yaml for convenience
  mkdir -p "${HOME}/.map"
  cat > "${HOME}/.map/admin.yaml" <<EOF
token: ${ADMIN_TOKEN}
api_url: ${API_URL}
EOF
  log "Admin token saved to ~/.map/admin.yaml"
fi

# ── Install map CLI (if not already) ───────────────────────────────────────
step 5 "Checking map CLI"

if command -v map >/dev/null 2>&1; then
  log "map CLI is already installed"
  map --help >/dev/null 2>&1 && log "map CLI is working" || warn "map CLI found but may have issues"
else
  warn "map CLI not found in PATH"
  log "Installing map CLI..."
  pip install -e ".[dev]" --break-system-packages 2>/dev/null || pip install -e ".[dev]"
  if command -v map >/dev/null 2>&1; then
    log "map CLI installed successfully"
  else
    warn "map CLI installed but not in PATH. You may need to:"
    warn "  export PATH=\"\$PATH:\$(python3 -m site --user-base)/bin\""
    warn "  or use: python3 -m cli.main instead of map"
  fi
fi

# ── Print next steps ───────────────────────────────────────────────────────
step 6 "Done! Next steps"

echo ""
echo -e "${BOLD}${GREEN}========================================${NC}"
echo -e "${BOLD}${GREEN}  MAP is ready!${NC}"
echo -e "${BOLD}${GREEN}========================================${NC}"
echo ""
MCP_PORT="8080"
if [[ -f "docker-compose.override.yml" ]] && grep -q "18081" docker-compose.override.yml 2>/dev/null; then
  MCP_PORT="18081"
fi
echo -e "Services:"
echo -e "  API+Web: ${CYAN}${API_URL}/${NC}  (board is served from the API origin)"
echo -e "  Web UI:  ${CYAN}http://localhost:3000${NC}  (Docker nginx; same UI)"
echo -e "  MCP:     ${CYAN}http://localhost:${MCP_PORT}/mcp${NC}"
echo ""

if [[ -n "${ADMIN_TOKEN:-}" ]]; then
  echo -e "${BOLD}Admin token (save this!):${NC}"
  echo -e "  ${YELLOW}${ADMIN_TOKEN}${NC}"
  echo ""
  echo -e "  (Also saved to ~/.map/admin.yaml)"
  echo ""
fi

echo -e "${BOLD}To connect your project to MAP:${NC}"
echo ""
echo -e "  ${CYAN}# In your project repo (no admin token needed on new servers):${NC}"
echo -e "  ${CYAN}map bootstrap --key my-project --name \"My Project\" --api-url ${API_URL}${NC}"
echo -e "  ${CYAN}map --persona host persona whoami${NC}"
echo ""
echo -e "${BOLD}To start collaborating:${NC}"
echo ""
echo -e "  ${CYAN}map --persona host status${NC}"
echo -e "  ${CYAN}map --persona host topic list --status open${NC}"
echo ""
echo -e "${BOLD}Full guide:${NC} docs/QUICKSTART.md"
echo ""
