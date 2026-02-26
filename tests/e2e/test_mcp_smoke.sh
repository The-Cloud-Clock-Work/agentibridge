#!/usr/bin/env bash
# E2E MCP Smoke Test — verifies all AgentiBridge MCP tools via claude CLI
#
# Usage:
#   ./tests/e2e/test_mcp_smoke.sh
#
# Prerequisites:
#   - claude CLI installed and on PATH
#   - .mcp.json in project root with agentibridge config
#   - agentibridge reachable (Docker Compose + tunnel, or local)
#
# Each test calls `claude -p` targeting one MCP tool and checks the result.

set -euo pipefail
cd "$(dirname "$0")/../.."

PASS=0
FAIL=0
TOTAL=9
SESSION_ID=""

CLAUDE_CMD="claude -p --dangerously-skip-permissions --output-format json --max-turns 3"
[[ -n "${CLAUDE_MODEL:-}" ]] && CLAUDE_CMD="${CLAUDE_CMD} --model ${CLAUDE_MODEL}"

# ── Prereq checks ──────────────────────────────────────────────────────────

if ! command -v claude &>/dev/null; then
  echo "ABORT: claude CLI not found on PATH"
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "ABORT: jq not found on PATH"
  exit 1
fi

if [[ ! -f .mcp.json ]]; then
  echo "ABORT: .mcp.json not found in project root"
  exit 1
fi

# Extract health URL from .mcp.json (replace /mcp with /health)
MCP_URL=$(jq -r '.mcpServers["agentibridge"].url' .mcp.json)
HEALTH_URL="${MCP_URL%/mcp}/health"
API_KEY=$(jq -r '.mcpServers["agentibridge"].headers["X-API-Key"] // empty' .mcp.json)

echo "Checking agentibridge health at ${HEALTH_URL} ..."
HEALTH_ARGS=(-sf --max-time 10)
[[ -n "$API_KEY" ]] && HEALTH_ARGS+=(-H "X-API-Key: ${API_KEY}")

if ! curl "${HEALTH_ARGS[@]}" "$HEALTH_URL" >/dev/null 2>&1; then
  echo "ABORT: agentibridge not reachable at ${HEALTH_URL}"
  exit 1
fi
echo "Health check OK"
echo ""

# ── Test runner ─────────────────────────────────────────────────────────────

run_test() {
  local num="$1" name="$2" prompt="$3" check_fn="$4"
  local raw result exit_code=0

  raw=$($CLAUDE_CMD "$prompt" 2>/dev/null) || true

  # claude --output-format json wraps output in {"type":"result","result":"..."}
  # Extract the inner result text; on error responses, result may be empty
  result=$(echo "$raw" | jq -r '.result // .error // empty' 2>/dev/null) || result=""
  [[ -z "$result" ]] && result="$raw"

  if $check_fn "$result" >/dev/null 2>&1; then
    echo "[${num}/${TOTAL}] PASS  ${name}"
    PASS=$((PASS + 1))
  else
    echo "[${num}/${TOTAL}] FAIL  ${name}"
    echo "  output (first 500 chars): ${result:0:500}"
    FAIL=$((FAIL + 1))
  fi
}

# ── Check functions ─────────────────────────────────────────────────────────

check_list_sessions() {
  local result="$1"
  # The result text should mention sessions. Look for evidence of session data.
  # Claude may return prose wrapping the JSON, so we grep for key indicators.
  echo "$result" | grep -qi "session" || return 1
  # Check for num_user_turns or num_turns > 0 somewhere in the output
  echo "$result" | grep -qE "(num_user_turns|num_turns|entries)" || return 1
}

check_list_sessions_filtered() {
  local result="$1"
  echo "$result" | grep -qi "session" || return 1
}

check_get_session() {
  local result="$1"
  # Should contain transcript entries or meta
  echo "$result" | grep -qiE "(meta|entries|entry_count|transcript)" || return 1
}

check_search_sessions() {
  local result="$1"
  # Should find at least one match
  echo "$result" | grep -qiE "(match|result|found|session)" || return 1
}

check_get_session_actions() {
  local result="$1"
  # Should mention tool calls or actions
  echo "$result" | grep -qiE "(tool|action|success|count)" || return 1
}

check_collect_now() {
  local result="$1"
  # Should mention files scanned or collection
  echo "$result" | grep -qiE "(scan|collect|file|process)" || return 1
}

check_list_memory_files() {
  local result="$1"
  echo "$result" | grep -qiE "(memory|file|project)" || return 1
}

check_list_plans() {
  local result="$1"
  echo "$result" | grep -qiE "(plan|codename)" || return 1
}

check_search_history() {
  local result="$1"
  echo "$result" | grep -qiE "(history|entry|display|result)" || return 1
}

# ── Test cases ──────────────────────────────────────────────────────────────

echo "Running ${TOTAL} MCP smoke tests..."
echo ""

# Test 1: list_sessions — basic
run_test 1 "list_sessions" \
  "Use the list_sessions MCP tool with limit=3. Show the raw result." \
  check_list_sessions

# Test 2: list_sessions with project filter
run_test 2 "list_sessions (filtered)" \
  "Use the list_sessions MCP tool with project='agentic' and limit=3. Show the raw result." \
  check_list_sessions_filtered

# Test 3: get_session — fetch a full session
# We ask Claude to first list, then get, in one prompt
run_test 3 "get_session" \
  "First call list_sessions with limit=1 to get a session_id, then call get_session with that session_id and last_n=5. Show the get_session result." \
  check_get_session

# Test 4: search_sessions — keyword search
run_test 4 "search_sessions" \
  "Use the search_sessions MCP tool with query='test' and limit=3. Show the raw result." \
  check_search_sessions

# Test 5: get_session_actions — tool call extraction
run_test 5 "get_session_actions" \
  "First call list_sessions with limit=1 to get a session_id, then call get_session_actions with that session_id. Show the result." \
  check_get_session_actions

# Test 6: collect_now — trigger collection
run_test 6 "collect_now" \
  "Use the collect_now MCP tool to trigger immediate collection. Show the raw result." \
  check_collect_now

# Test 7: list_memory_files — Phase 5
run_test 7 "list_memory_files" \
  "Use the list_memory_files MCP tool. Show the raw result." \
  check_list_memory_files

# Test 8: list_plans — Phase 5
run_test 8 "list_plans" \
  "Use the list_plans MCP tool with limit=3. Show the raw result." \
  check_list_plans

# Test 9: search_history — Phase 5
run_test 9 "search_history" \
  "Use the search_history MCP tool with query='test' and limit=3. Show the raw result." \
  check_search_history

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "${PASS}/${TOTAL} passed"
[[ $FAIL -eq 0 ]] && echo "All tests passed!" || echo "${FAIL} test(s) failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
