#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# compose.sh — Interactive Docker Compose manager for AgentiBridge
#
# Detects current stack state, validates the .env file,
# and offers contextual actions (up / down / restart / logs).
#
# Usage:
#   ./automation/compose.sh                  # uses .env next to docker-compose.yml
#   ./automation/compose.sh /path/to/.env    # explicit env file
# ─────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colors ───────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ok()   { printf "${GREEN}✔${NC} %s\n" "$*"; }
info() { printf "${YELLOW}▶${NC} %s\n" "$*"; }
err()  { printf "${RED}✖${NC} %s\n" "$*" >&2; }
hdr()  { printf "\n${CYAN}${BOLD}── %s ──${NC}\n" "$*"; }

# ── Resolve project root (where docker-compose.yml lives) ───
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    err "docker-compose.yml not found at $PROJECT_ROOT"
    exit 1
fi

# ── Resolve .env file ───────────────────────────────────────
ENV_FILE="${1:-$PROJECT_ROOT/.env}"

hdr "Environment file"

if [[ ! -f "$ENV_FILE" ]]; then
    err "No .env file found at: $ENV_FILE"
    printf "\n"
    info "Docker Compose needs a .env file to inject configuration."
    info "Copy the example and fill in your values:"
    printf "\n"
    printf "  ${BOLD}cp %s/.env.example %s/.env${NC}\n" "$PROJECT_ROOT" "$PROJECT_ROOT"
    printf "  ${BOLD}nano %s/.env${NC}\n" "$PROJECT_ROOT"
    printf "\n"
    info "Or pass an explicit path:"
    printf "  ${BOLD}%s /path/to/your/.env${NC}\n" "${BASH_SOURCE[0]}"
    printf "\n"
    exit 1
fi

# Resolve to absolute path
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
ok "Using env file: $ENV_FILE"

# ── Validate .env has minimum required vars ─────────────────
REQUIRED_VARS=(
    "REDIS_URL"
    "POSTGRES_URL"
    "POSTGRES_USER"
    "POSTGRES_PASSWORD"
    "POSTGRES_DB"
    "AGENTIBRIDGE_TRANSPORT"
    "AGENTIBRIDGE_PORT"
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    # Check that the var exists and is not commented out
    if ! grep -qE "^\s*${var}=" "$ENV_FILE"; then
        MISSING_VARS+=("$var")
    fi
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
    err "The .env file is missing required variables:"
    for v in "${MISSING_VARS[@]}"; do
        printf "  ${RED}•${NC} %s\n" "$v"
    done
    printf "\n"
    info "Check .env.example for reference: $PROJECT_ROOT/.env.example"
    exit 1
fi

ok "Required variables present"

# ── Detect dispatch bridge state ──────────────────────────────
detect_bridge() {
    # Check if dispatch bridge is running on the host
    if curl -sf http://127.0.0.1:${DISPATCH_BRIDGE_PORT:-8101}/health >/dev/null 2>&1; then
        echo "running"
    else
        echo "stopped"
    fi
}

start_bridge() {
    local secret
    secret=$(grep -E '^\s*DISPATCH_SECRET=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
    if [[ -z "$secret" ]]; then
        err "DISPATCH_SECRET not found in .env — cannot start bridge"
        return 1
    fi

    local port
    port=$(grep -E '^\s*DISPATCH_BRIDGE_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo "8101")

    info "Starting dispatch bridge on 127.0.0.1:${port}..."
    DISPATCH_BRIDGE_SECRET="$secret" DISPATCH_BRIDGE_PORT="$port" \
        nohup python -m agentibridge.dispatch_bridge > /tmp/dispatch_bridge.log 2>&1 &
    local pid=$!
    sleep 1

    if kill -0 "$pid" 2>/dev/null; then
        ok "Dispatch bridge started (PID $pid, port $port)"
        ok "Logs: /tmp/dispatch_bridge.log"
    else
        err "Dispatch bridge failed to start — check /tmp/dispatch_bridge.log"
        return 1
    fi
}

stop_bridge() {
    local pids
    pids=$(pgrep -f "agentibridge.dispatch_bridge" 2>/dev/null || true)
    if [[ -z "$pids" ]]; then
        info "No dispatch bridge process found"
        return 0
    fi
    info "Stopping dispatch bridge (PID: $pids)..."
    kill $pids 2>/dev/null || true
    sleep 1
    ok "Dispatch bridge stopped"
}

# ── Detect current stack state ───────────────────────────────
hdr "Stack status"

detect_state() {
    # Returns: "running", "partial", or "stopped"
    local total=0
    local running=0

    while IFS= read -r line; do
        total=$((total + 1))
        if echo "$line" | grep -qE "running|Up"; then
            running=$((running + 1))
        fi
    done < <(docker compose -f "$COMPOSE_FILE" ps --format "{{.State}}" 2>/dev/null || true)

    if [[ $total -eq 0 || $running -eq 0 ]]; then
        echo "stopped"
    elif [[ $running -eq $total ]]; then
        echo "running"
    else
        echo "partial"
    fi
}

STATE=$(detect_state)

show_status() {
    docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
}

case "$STATE" in
    running)
        ok "Stack is ${GREEN}running${NC}"
        printf "\n"
        show_status
        ;;
    partial)
        info "Stack is ${YELLOW}partially running${NC} (some services down)"
        printf "\n"
        show_status
        ;;
    stopped)
        info "Stack is ${DIM}stopped${NC}"
        ;;
esac

# ── Detect dispatch bridge ────────────────────────────────────
BRIDGE_STATE=$(detect_bridge)
printf "\n"
if [[ "$BRIDGE_STATE" == "running" ]]; then
    ok "Dispatch bridge is ${GREEN}running${NC}"
else
    info "Dispatch bridge is ${DIM}stopped${NC}"
fi

# ── Build menu based on state ───────────────────────────────
hdr "Actions"

declare -a MENU_OPTIONS=()
declare -a MENU_LABELS=()

add_option() {
    MENU_OPTIONS+=("$1")
    MENU_LABELS+=("$2")
}

case "$STATE" in
    running)
        add_option "stop-all"  "Stop everything (stack + bridge)"
        add_option "down"      "Stop the stack"
        add_option "restart"   "Restart the stack"
        add_option "rebuild"   "Rebuild & restart (docker compose up --build)"
        add_option "logs"      "View logs (follow)"
        add_option "status"    "Show detailed status"
        ;;
    partial)
        add_option "start-all" "Start everything (stack + bridge)"
        add_option "stop-all"  "Stop everything (stack + bridge)"
        add_option "up"        "Start all services"
        add_option "down"      "Stop everything"
        add_option "rebuild"   "Rebuild & restart (docker compose up --build)"
        add_option "logs"      "View logs (follow)"
        add_option "status"    "Show detailed status"
        ;;
    stopped)
        add_option "start-all" "Start everything (stack + bridge)"
        add_option "up"        "Start the stack"
        add_option "rebuild"   "Build & start (docker compose up --build)"
        ;;
esac

# Dispatch bridge options
if [[ "$BRIDGE_STATE" == "running" ]]; then
    add_option "bridge-stop"    "Stop dispatch bridge"
    add_option "bridge-logs"    "View dispatch bridge logs"
else
    add_option "bridge-start"   "Start dispatch bridge (host-side Claude CLI proxy)"
fi

add_option "quit"  "Exit"

# Print menu
for i in "${!MENU_OPTIONS[@]}"; do
    idx=$((i + 1))
    printf "  ${BOLD}%d)${NC} %s\n" "$idx" "${MENU_LABELS[$i]}"
done
printf "\n"

# Prompt
read -rp "Select an option [1-${#MENU_OPTIONS[@]}]: " CHOICE

# Validate
if [[ -z "$CHOICE" ]] || ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || (( CHOICE < 1 || CHOICE > ${#MENU_OPTIONS[@]} )); then
    err "Invalid selection"
    exit 1
fi

ACTION="${MENU_OPTIONS[$((CHOICE - 1))]}"

# ── Execute action ───────────────────────────────────────────
hdr "Executing: $ACTION"

COMPOSE_CMD="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

case "$ACTION" in
    start-all)
        info "Starting stack..."
        $COMPOSE_CMD up -d
        printf "\n"
        ok "Stack started"
        show_status
        printf "\n"
        start_bridge
        ;;
    stop-all)
        stop_bridge
        printf "\n"
        info "Stopping stack..."
        $COMPOSE_CMD down
        printf "\n"
        ok "Stack stopped"
        ;;
    up)
        info "Starting stack..."
        $COMPOSE_CMD up -d
        printf "\n"
        ok "Stack started"
        show_status
        ;;
    down)
        info "Stopping stack..."
        $COMPOSE_CMD down
        printf "\n"
        ok "Stack stopped"
        ;;
    restart)
        info "Restarting stack..."
        $COMPOSE_CMD restart
        printf "\n"
        ok "Stack restarted"
        show_status
        ;;
    rebuild)
        info "Rebuilding & starting stack..."
        $COMPOSE_CMD up --build -d
        printf "\n"
        ok "Stack rebuilt and started"
        show_status
        ;;
    logs)
        info "Streaming logs (Ctrl+C to stop)..."
        printf "\n"
        $COMPOSE_CMD logs -f --tail 100
        ;;
    status)
        show_status
        printf "\n"
        hdr "Health checks"
        for svc in agentibridge redis postgres; do
            health=$(docker inspect --format='{{.State.Health.Status}}' "agentibridge${svc:+$([ "$svc" != "agentibridge" ] && echo "-$svc")}" 2>/dev/null || echo "n/a")
            container="agentibridge$([ "$svc" != "agentibridge" ] && echo "-$svc" || true)"
            health=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}no healthcheck{{end}}' "$container" 2>/dev/null || echo "not found")
            case "$health" in
                healthy)        printf "  ${GREEN}●${NC} %-20s %s\n" "$container" "$health" ;;
                unhealthy)      printf "  ${RED}●${NC} %-20s %s\n" "$container" "$health" ;;
                *)              printf "  ${YELLOW}●${NC} %-20s %s\n" "$container" "$health" ;;
            esac
        done
        ;;
    bridge-start)
        start_bridge
        ;;
    bridge-stop)
        stop_bridge
        ;;
    bridge-logs)
        if [[ -f /tmp/dispatch_bridge.log ]]; then
            info "Showing dispatch bridge logs (Ctrl+C to stop)..."
            printf "\n"
            tail -f /tmp/dispatch_bridge.log
        else
            err "No dispatch bridge log found at /tmp/dispatch_bridge.log"
        fi
        ;;
    quit)
        ok "Bye!"
        exit 0
        ;;
esac

printf "\n"
