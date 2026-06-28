# syntax=docker/dockerfile:1
# ============================================================
# TAYA Agent: Khaos (Jayme's Personal Assistant) — baked-config image
# ============================================================
# Special-case: uses khaos/entrypoint.sh (not shared/entrypoint.sh) to
# bootstrap WhatsApp bridge + dashboard + container API server.
# Config + plugins baked into image. Secrets + WhatsApp creds + OR key
# injected at runtime via docker-compose env.
#
# Build:
#   docker build -f infrastructure/agents/khaos/Dockerfile \
#     --build-arg AGENT_NAME=khaos \
#     --build-arg AGENT_ID=khaos \
#     --build-arg MAIN_MODEL=xiaomi/mimo-v2.5-pro \
#     --build-arg AUX_MODEL=qwen3.5:9b \
#     --build-arg AUX_BASE_URL=http://100.97.181.20:11434/v1 \
#     --build-arg GATEWAY_PORT=8643 \
#     infrastructure/agents/
#
# Runtime via compose:
#   docker compose -f infrastructure/docker-compose.agents.yml up -d khaos
# ============================================================

ARG HERMES_IMAGE=ghcr.io/nousresearch/hermes-agent:latest
FROM ${HERMES_IMAGE}

# ---- Build args (per-agent; baked into image) ----
ARG AGENT_NAME=khaos
ARG AGENT_ID=khaos
ARG MAIN_MODEL=xiaomi/mimo-v2.5-pro
ARG AUX_MODEL=qwen3.5:9b
ARG AUX_BASE_URL=http://100.97.181.20:11434/v1
ARG GATEWAY_PORT=8643

# ---- Identity labels (visible in `docker inspect`) ----
LABEL taya.agent="${AGENT_NAME}"
LABEL taya.agent.id="${AGENT_ID}"
LABEL taya.agent.role="Jayme Personal Assistant"

# ---- System packages (shared across all agents) ----
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        htop tree strace jq \
    && rm -rf /var/lib/apt/lists/*

# ---- Hermes data directories with correct ownership ----
RUN mkdir -p /opt/data/skills /opt/data/memories /opt/data/cron /opt/data/profiles \
             /opt/data/plugins /opt/data/agent-config /opt/data/whatsapp \
    && chown -R hermes:hermes /opt/data

# ---- Shared infrastructure files (entrypoint, secrets, hermes templates) ----
# --chown=hermes:hermes fixes the file-permission bug permanently at COPY time.
COPY --chown=hermes:hermes shared/setup-secrets.py          /usr/local/bin/setup-secrets.py
COPY --chown=hermes:hermes shared/container-api-server.py   /usr/local/bin/container-api-server.py
COPY --chown=hermes:hermes shared/hermes-config-template.yml /etc/hermes/config.template.yml

# ---- Khaos-specific entrypoint (Jayme's bridge orchestration) ----
COPY --chown=hermes:hermes ./${AGENT_NAME}/entrypoint.sh /usr/local/bin/khaos-entrypoint.sh
RUN chmod 0755 /usr/local/bin/khaos-entrypoint.sh \
              /usr/local/bin/setup-secrets.py \
              /usr/local/bin/container-api-server.py

# ---- Shared tool-repair plugin (single source of truth for repair JSONs) ----
COPY --chown=hermes:hermes shared/plugins/tool-repair /opt/data/plugins/tool-repair

# ---- Agent-specific config (baked — replaces the bind mount of ~/.hermes/agents/khaos) ----
COPY --chown=hermes:hermes ./${AGENT_NAME}/config.yaml /etc/hermes/agent-config.yaml

# ---- Agent-specific plugins (openrouter-server-tools, etc.) ----
COPY --chown=hermes:hermes ./${AGENT_NAME}/plugins/ /opt/data/plugins/

# ---- Skills list (per-agent) ----
ARG SKILLS="humanizer songwriting-and-ai-music youtube-content gif-search heartmula songsee"
RUN printf '%s\n' ${SKILLS} > /etc/hermes/skills-${AGENT_NAME}.txt \
    && chown hermes:hermes /etc/hermes/skills-${AGENT_NAME}.txt

# ---- Default identity + model env (compose can override HERMES_MODEL at runtime) ----
ENV AGENT_NAME=${AGENT_NAME} \
    AGENT_ID=${AGENT_ID} \
    AGENT_DISPLAY_NAME="Khaos" \
    AGENT_ROLE="Jayme Personal Assistant" \
    HERMES_GATEWAY_PORT=${GATEWAY_PORT} \
    HERMES_MODEL=${MAIN_MODEL} \
    HERMES_HOME="/opt/data"

# Runtime-only config (in docker-compose.agents.yml, NOT baked):
#   OPENROUTER_API_KEY   per-agent OR key (secret)
#   API_SERVER_KEY       gateway auth (secret-ish)
#   WHATSAPP_*           WhatsApp bridge creds
#   PAPERCLIP_API_URL    host-dependent

WORKDIR /opt/data
EXPOSE ${GATEWAY_PORT}
EXPOSE 9120

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

USER hermes
ENTRYPOINT ["/usr/local/bin/khaos-entrypoint.sh"]