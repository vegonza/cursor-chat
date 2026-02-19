#!/usr/bin/env bash
set -euo pipefail

TERMINAL_USER="${TERMINAL_USER:-admin}"
TERMINAL_PASSWORD="${TERMINAL_PASSWORD:?TERMINAL_PASSWORD env var is required}"
BASE_PATH="${BASE_PATH:-/terminal}"
AGENT_BIN="${AGENT_BIN:-/opt/cursor-agent/cursor-agent}"
CURSOR_API_KEY="${CURSOR_API_KEY:-}"

WORKSPACE="${WORKSPACE:-/workspace}"

mkdir -p "$WORKSPACE/.cursor/rules"
echo "[entrypoint] Syncing workspace config..."
cp /app/workspace/.cursor/rules/*.mdc "$WORKSPACE/.cursor/rules/" 2>/dev/null || true
cp /app/workspace/.cursor/mcp.json "$WORKSPACE/.cursor/mcp.json" 2>/dev/null || true
cp /app/workspace/AGENTS.md "$WORKSPACE/AGENTS.md" 2>/dev/null || true

if [ ! -d "$WORKSPACE/.git" ]; then
    echo "[entrypoint] Initializing git repo in workspace..."
    git -C "$WORKSPACE" init -q
    git -C "$WORKSPACE" add -A 2>/dev/null || true
    git -C "$WORKSPACE" commit -q -m "init" --allow-empty 2>/dev/null || true
fi

mkdir -p /root/.cursor
if [ -f /tmp/host-cli-config.json ]; then
    cp /tmp/host-cli-config.json /root/.cursor/cli-config.json
    echo "[entrypoint] Synced host cli-config.json"
fi
cp /app/workspace/.cursor/mcp.json /root/.cursor/mcp.json 2>/dev/null || true

PROJ_DIR="/root/.cursor/projects/workspace"
mkdir -p "$PROJ_DIR"
if [ ! -f "$PROJ_DIR/.workspace-trusted" ]; then
    echo "[entrypoint] Pre-trusting workspace..."
    cat > "$PROJ_DIR/.workspace-trusted" <<TRUSTEOF
{
  "trustedAt": "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)",
  "workspacePath": "/workspace"
}
TRUSTEOF
fi

AGENT_MODEL="${AGENT_MODEL:-claude-4.6-opus-high}"

AGENT_ARGS=""
if [ -n "$CURSOR_API_KEY" ]; then
    AGENT_ARGS="$AGENT_ARGS --api-key $CURSOR_API_KEY"
fi
AGENT_ARGS="$AGENT_ARGS --workspace $WORKSPACE"
AGENT_ARGS="$AGENT_ARGS --model $AGENT_MODEL"
AGENT_ARGS="$AGENT_ARGS --approve-mcps"
AGENT_CMD="$AGENT_BIN $AGENT_ARGS"

start_agent_session() {
    if ! tmux has-session -t agent 2>/dev/null; then
        echo "[entrypoint] Starting tmux session 'agent'..."
        tmux new-session -d -s agent -x 200 -y 50 -c "$WORKSPACE" "$AGENT_CMD"
    fi
}

start_agent_session

# Watchdog: restart the tmux session if the agent process dies
(
    while true; do
        sleep 5
        if ! tmux has-session -t agent 2>/dev/null; then
            echo "[watchdog] Agent session died, restarting..."
            start_agent_session
        fi
    done
) &

echo "[entrypoint] Starting terminal server on port 7681 (base path: ${BASE_PATH})..."
exec python /app/app.py
