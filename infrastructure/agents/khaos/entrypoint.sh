#!/bin/bash
set -e

echo "🤖 Initializing Khaos Containerized Agent"

# Ensure Hermes is available
if ! command -v hermes &> /dev/null; then
    echo "❌ Hermes Agent not found in PATH"
    exit 1
fi

# Setup Paperclip integration
export PAPERCLIP_API_URL="${PAPERCLIP_API_URL:-http://paperclip:3100/api}"
export PAPERCLIP_AGENT_ID="${AGENT_ID:-khaos}"

# Run secrets setup if available
if [ -f /usr/local/bin/setup-secrets.py ]; then
    echo "🔐 Setting up secrets..."
    python3 /usr/local/bin/setup-secrets.py "${AGENT_ID:-khaos}" || true
fi

# Copy agent config from mounted volume if present
AGENT_CONFIG_DIR="/opt/data/agent-config"
if [ -d "$AGENT_CONFIG_DIR" ]; then
    echo "📋 Copying agent config from $AGENT_CONFIG_DIR..."
    for f in SOUL.md USER.md IDENTITY.md AGENTS.md TOOLS.md MEMORY.md HEARTBEAT.md; do
        if [ -f "$AGENT_CONFIG_DIR/$f" ] && [ ! -f "/opt/data/$f" ]; then
            cp "$AGENT_CONFIG_DIR/$f" "/opt/data/$f"
            echo "  ✅ Copied $f"
        fi
    done
    for d in memory journal; do
        if [ -d "$AGENT_CONFIG_DIR/$d" ] && [ ! -d "/opt/data/$d" ]; then
            cp -r "$AGENT_CONFIG_DIR/$d" "/opt/data/$d"
            echo "  ✅ Copied $d/"
        fi
    done
fi

# Always write baked-in agent config from /etc/hermes/ (image-baked is
# source of truth — overwrites any stale host-side copy).
if [ -f /etc/hermes/agent-config.yaml ]; then
    cp /etc/hermes/agent-config.yaml /opt/data/config.yaml
    echo "  ✅ Wrote baked /etc/hermes/agent-config.yaml → /opt/data/config.yaml"
fi

# Start Hermes gateway in background (for WhatsApp integration)
echo "🌐 Starting Hermes gateway..."
hermes gateway run &
GATEWAY_PID=$!
sleep 3

# Start dashboard in background (for desktop app)
echo "📊 Starting dashboard on port 9120..."
hermes dashboard --host 0.0.0.0 --port 9120 --no-open &
DASHBOARD_PID=$!
sleep 2

# Start WhatsApp bridge in background
if [ -d /opt/data/whatsapp/bridge ]; then
    echo "📱 Starting WhatsApp bridge..."
    cd /opt/data/whatsapp/bridge
    node bridge.js --port 3000 --session /opt/data/whatsapp/session --mode personal &
    BRIDGE_PID=$!
    sleep 2
fi

# Start container API server (main process)
echo "🚀 Starting container API server..."
exec python3 /usr/local/bin/container-api-server.py
