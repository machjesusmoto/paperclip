#!/bin/bash
set -e

AGENT_DISPLAY="${TAYA_AGENT_NAME:-${AGENT_NAME:-Unknown}}"
echo "🤖 Initializing TAYA Containerized Agent: ${AGENT_DISPLAY}"
echo "Agent ID: ${AGENT_ID:-${HERMES_AGENT_ID:-unknown}}"
echo "Role: ${TAYA_AGENT_ROLE:-${AGENT_ROLE:-General}}"

# Ensure Hermes is available
if ! command -v hermes &> /dev/null; then
    echo "❌ Hermes Agent not found in PATH"
    exit 1
fi

# Setup Paperclip integration
echo "🔗 Setting up Paperclip integration..."
export PAPERCLIP_API_URL="${PAPERCLIP_API_URL:-http://paperclip:3100/api}"
export PAPERCLIP_AGENT_ID="${HERMES_AGENT_ID}"

# Run secrets setup if available
if [ -f /usr/local/bin/setup-secrets.py ]; then
    echo "🔐 Setting up secrets..."
    python3 /usr/local/bin/setup-secrets.py "${HERMES_AGENT_ID}" || true
fi

# Load agent profile if available
PROFILE_FILE="/etc/hermes/agent-profile.yml"
if [ -f "$PROFILE_FILE" ]; then
    echo "📋 Loading agent profile: $PROFILE_FILE"
    export HERMES_PROFILE_PATH="$PROFILE_FILE"
fi

# Copy agent config (SOUL.md, config.yaml) from mounted volume if present
# AND not already baked — bind mount takes precedence for state files,
# but baked config.yaml ALWAYS wins (it's the immutable source of truth).
AGENT_CONFIG_DIR="/opt/data/agent-config"
if [ -d "$AGENT_CONFIG_DIR" ]; then
    echo "📋 Copying agent config from $AGENT_CONFIG_DIR..."
    for f in SOUL.md USER.md IDENTITY.md AGENTS.md TOOLS.md MEMORY.md HEARTBEAT.md; do
        if [ -f "$AGENT_CONFIG_DIR/$f" ] && [ ! -f "/opt/data/$f" ]; then
            cp "$AGENT_CONFIG_DIR/$f" "/opt/data/$f"
            echo "  ✅ Copied $f"
        fi
    done
fi

# Always load baked-in agent config from /etc/hermes/ (image-baked).
# Baked config.yaml is the source of truth and overwrites any host-side
# stale copy (fixes mimo.json UID 1000 bug + config drift permanently).
if [ -f "/etc/hermes/agent-config.yaml" ]; then
    cp "/etc/hermes/agent-config.yaml" "/opt/data/config.yaml"
    echo "  ✅ Wrote baked /etc/hermes/agent-config.yaml → /opt/data/config.yaml"
    for d in memory journal; do
        if [ -d "$AGENT_CONFIG_DIR/$d" ] && [ ! -d "/opt/data/$d" ]; then
            cp -r "$AGENT_CONFIG_DIR/$d" "/opt/data/$d"
            echo "  ✅ Copied $d/"
        fi
    done
    # Copy skills directory if present
    if [ -d "$AGENT_CONFIG_DIR/skills" ] && [ ! -d "/opt/data/skills/populated" ]; then
        cp -r "$AGENT_CONFIG_DIR/skills/"* /opt/data/skills/ 2>/dev/null || true
        touch /opt/data/skills/populated
        echo "  ✅ Copied skills"
    fi
    # Copy plugins directory if present
    if [ -d "$AGENT_CONFIG_DIR/plugins" ]; then
        for pdir in "$AGENT_CONFIG_DIR/plugins/"*; do
            pname=$(basename "$pdir")
            if [ ! -d "/opt/data/plugins/$pname" ]; then
                mkdir -p "/opt/data/plugins"
                cp -r "$pdir" "/opt/data/plugins/"
                echo "  ✅ Copied plugin: $pname"
            fi
        done
    fi
fi

# Start Hermes gateway if enabled (for Discord, WhatsApp, etc.)
if [ "$HERMES_GATEWAY_ENABLED" = "true" ]; then
    echo "🌐 Starting Hermes gateway..."
    hermes gateway run &
    GATEWAY_PID=$!
    sleep 3
    
    # Start WhatsApp bridge if configured
    if [ "$WHATSAPP_ENABLED" = "true" ] && [ -d /opt/data/whatsapp/bridge ]; then
        echo "📱 Starting WhatsApp bridge..."
        cd /opt/data/whatsapp/bridge
        node bridge.js --port 3000 --session /opt/data/whatsapp/session --mode personal &
        BRIDGE_PID=$!
        sleep 2
    fi
fi

# Start dashboard if enabled
if [ "$HERMES_DASHBOARD_ENABLED" != "false" ]; then
    DASHBOARD_PORT="${HERMES_DASHBOARD_PORT:-9120}"
    echo "📊 Starting dashboard on port ${DASHBOARD_PORT}..."
    hermes dashboard --host 0.0.0.0 --port "$DASHBOARD_PORT" --no-open &
    DASHBOARD_PID=$!
    sleep 2
fi

# Load skills if available
SKILLS_FILE="/etc/hermes/skills-${HERMES_AGENT_ID}.txt"
if [ -f "$SKILLS_FILE" ]; then
    echo "📚 Loading skills from: $SKILLS_FILE"
    export HERMES_SKILLS_FILE="$SKILLS_FILE"
fi

# Start the container API server
echo "🚀 Starting container API server..."
exec python3 /usr/local/bin/container-api-server.py
