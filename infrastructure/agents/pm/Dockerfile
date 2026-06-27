# syntax=docker/dockerfile:1
# ============================================================
# TAYA Agent: PM (Project Manager) — baked-config image
# ============================================================
# Agent identity + config + plugins baked into the image.  Secrets
# (API keys, tokens) + host-dependent config (PAPERCLIP_API_URL)
# are injected at runtime via docker-compose env.
#
# Build:
#   docker build -f infrastructure/agents/pm/Dockerfile \
#     --build-arg AGENT_NAME=pm \
#     --build-arg AGENT_ID=18c3980a-3ded-4cff-8276-28a7bdfd2817 \
#     --build-arg MAIN_MODEL=xiaomi/mimo-v2.5-pro \
#     --build-arg AUX_MODEL=qwen3.5:9b \
#     --build-arg AUX_BASE_URL=http://100.97.181.20:11434/v1 \
#     --build-arg GATEWAY_PORT=8645 \
#     infrastructure/agents/
#
# Runtime via compose:
#   docker compose -f infrastructure/docker-compose.agents.yml up -d pm
# ============================================================

ARG HERMES_IMAGE=ghcr.io/nousresearch/hermes-agent:latest
FROM ${HERMES_IMAGE}

# ---- Build args (per-agent; baked into image) ----
ARG AGENT_NAME=pm
ARG AGENT_ID=18c3980a-3ded-4cff-8276-28a7bdfd2817
ARG MAIN_MODEL=xiaomi/mimo-v2.5-pro
ARG AUX_MODEL=qwen3.5:9b
ARG AUX_BASE_URL=http://100.97.181.20:11434/v1
ARG GATEWAY_PORT=8645

# ---- Identity labels (visible in `docker inspect`) ----
LABEL taya.agent="${AGENT_NAME}"
LABEL taya.agent.id="${AGENT_ID}"
LABEL taya.agent.role="Project Manager"

# ---- System packages (shared across all agents) ----
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        htop tree strace jq \
    && rm -rf /var/lib/apt/lists/*

# ---- Hermes data directories with correct ownership ----
RUN mkdir -p /opt/data/skills /opt/data/memories /opt/data/cron /opt/data/profiles \
             /opt/data/plugins /opt/data/agent-config \
    && chown -R hermes:hermes /opt/data

# ---- Shared infrastructure files (entrypoint, secrets, hermes templates) ----
# --chown=hermes:hermes fixes the file-permission bug permanently at COPY time.
COPY --chown=hermes:hermes shared/entrypoint.sh             /usr/local/bin/entrypoint.sh
COPY --chown=hermes:hermes shared/setup-secrets.py          /usr/local/bin/setup-secrets.py
COPY --chown=hermes:hermes shared/container-api-server.py   /usr/local/bin/container-api-server.py
COPY --chown=hermes:hermes shared/hermes-config-template.yml /etc/hermes/config.template.yml
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
              /usr/local/bin/setup-secrets.py \
              /usr/local/bin/container-api-server.py

# ---- Shared tool-repair plugin (single source of truth for repair JSONs) ----
COPY --chown=hermes:hermes shared/plugins/tool-repair /opt/data/plugins/tool-repair

# ---- Agent-specific config (baked — replaces the bind mount of ~/.hermes/agents/pm) ----
# NOTE: copied to /etc/hermes/ (not /opt/data) because /opt/data is shadowed
# by an anonymous Docker volume for runtime state persistence.
# Entrypoint copies this into /opt/data/config.yaml on startup.
COPY --chown=hermes:hermes ./${AGENT_NAME}/config.yaml /etc/hermes/agent-config.yaml

# ---- Agent-specific plugins (openrouter-server-tools, etc.) ----
# Plugins live under /opt/data/plugins — shared/plugins/tool-repair goes there too.
COPY --chown=hermes:hermes ./${AGENT_NAME}/plugins/ /opt/data/plugins/

# ---- Agent profile (personality, skills list, paperclip specialization) ----
COPY --chown=hermes:hermes ./profiles/${AGENT_NAME}.yml /etc/hermes/agent-profile.yml

# ---- Skills list (per-agent) ----
ARG SKILLS="plan hermes-agent"
RUN printf '%s\n' ${SKILLS} > /etc/hermes/skills-${AGENT_NAME}.txt \
    && chown hermes:hermes /etc/hermes/skills-${AGENT_NAME}.txt

# ---- Default identity + model env (compose can override HERMES_MODEL at runtime) ----
ENV AGENT_NAME=${AGENT_NAME} \
    AGENT_ID=${AGENT_ID} \
    AGENT_DISPLAY_NAME="Project Manager" \
    AGENT_ROLE="Project Manager" \
    HERMES_GATEWAY_PORT=${GATEWAY_PORT} \
    HERMES_MODEL=${MAIN_MODEL} \
    HERMES_HOME="/opt/data"

# Runtime-only config (in docker-compose.agents.yml, NOT baked):
#   OPENROUTER_API_KEY   per-agent OR key (secret)
#   API_SERVER_KEY       gateway auth (secret-ish)
#   PAPERCLIP_API_URL    host-dependent

WORKDIR /opt/data
EXPOSE ${GATEWAY_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

USER hermes
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]