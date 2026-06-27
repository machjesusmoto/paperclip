# TAYA Agent Config — Administration Runbook

**Scope:** how to modify the configuration of the 8 TAYA agents (zephyr, phoenix, orbit, pm, reviewer, researcher, writer, khaos) running on `moto-genai-vm` under `infrastructure/docker-compose.agents.yml`.

**Audience:** operators (Dylan + agents working on agent-platform issues).

**Last updated:** 2026-06-27

---

## 1. Architecture in 90 Seconds

Each agent is a Docker container running `infrastructure-<name>:latest`. The image is built from `infrastructure/agents/<name>/Dockerfile` and contains three things baked in:

1. **`/etc/hermes/agent-config.yaml`** — the agent's full `config.yaml`, baked at build time.
2. **`/etc/hermes/skills-<name>.txt`** — the agent's skill preload list.
3. **`/opt/data/plugins/<plugin>/`** — per-agent plugins (most notably `openrouter-server-tools`).

On container start, the entrypoint (`shared/entrypoint.sh` for standard agents, `khaos/entrypoint.sh` for Khaos) runs:

```sh
cp /etc/hermes/agent-config.yaml /opt/data/config.yaml   # always overwrites
```

This means **the baked image is the source of truth**. Editing `~/.hermes/agents/<name>/config.yaml` directly does NOT persist across container restarts.

The host bind mount `~/.hermes/agents/<name>:/opt/data` exists for **state continuity** (state.db, memory/, journal/, channels/) and for **restic backup visibility**, but it is shadowed by the baked config on every startup.

---

## 2. What's Baked vs What's Runtime

| Setting | Where | Why |
|---|---|---|
| `model.default`, `model.provider`, `model.base_url`, `model.api_mode` | **baked** in `config.yaml` | Source of truth; no need to redeploy secrets |
| `openrouter.server_tools.*` (fusion, advisors, web_search) | **baked** in `config.yaml` | Tunes behavior; baked means consistent across restarts |
| `auxiliary.*` (vision, web_extract, compression, etc.) | **baked** in `config.yaml` | All agents now route aux to local qwen3.5:9b via GB10 Ollama |
| `gateway.platforms.api_server.port` | **baked** in `config.yaml` | Must match `HERMES_GATEWAY_PORT` env to be reachable |
| `gateway.platforms.api_server.key` | **runtime** in `docker-compose.agents.yml` env | Secret; never bake |
| `gateway.platforms.discord.allowed_channels` | **baked** in `config.yaml` | Non-secret allowlist |
| `dashboard.basic_auth.password` | **baked** in `config.yaml` | Dashboard creds; non-secret in our threat model |
| `approvals.mode`, `approvals.timeout` | **baked** in `config.yaml` | Behavior, not secret |
| `OPENROUTER_API_KEY_<NAME>` | **runtime** in `infrastructure/.env` | Vault-sourced per-agent OR key |
| `API_SERVER_KEY` | **runtime** in `docker-compose.agents.yml` | Gateway auth, per-agent |
| `DISCORD_BOT_TOKEN` (zephyr, orbit) | **runtime** in `docker-compose.agents.yml` | Discord secret |
| `PAPERCLIP_API_URL`, `PAPERCLIP_COMPANY_ID` | **runtime** in `x-agent-common` env block | Host-dependent |
| `HERMES_GATEWAY_PORT` | **runtime** in per-service env | Must match baked `gateway.platforms.api_server.port` |
| `WHATSAPP_ENABLED` (khaos only) | **runtime** in khaos env | Feature flag |
| Memory, state.db, journal, channels, audio_cache | **runtime** in `~/.hermes/agents/<name>/` (host bind mount) | Mutable state; backed up hourly by restic |

**Rule of thumb:** if it's a **secret** or a **host-dependent URL**, it goes in compose env. Everything else is baked.

---

## 3. The 3-Step Modification Workflow

### Step 1 — Edit the source-of-truth file

```sh
$EDITOR ~/paperclip/infrastructure/agents/<name>/config.yaml
```

For changes that should apply to multiple standard agents, edit one and **mirror** to the others. The 7 standard agents (everything except Khaos) share a near-identical config skeleton.

### Step 2 — Rebuild the baked image

```sh
cd ~/paperclip/infrastructure

# Single agent
docker compose -f docker-compose.agents.yml build <name>

# All 8 (faster aggregate, same end state)
docker compose -f docker-compose.agents.yml build
```

Build time is ~10s per agent on a warm cache. The `infrastructure-<name>:latest` image is tagged locally; no registry push needed.

### Step 3 — Restart the agent(s)

```sh
cd ~/paperclip/infrastructure

# Single agent
docker compose -f docker-compose.agents.yml up -d <name>

# All 8
docker compose -f docker-compose.agents.yml up -d
```

Compose recreates the container, picking up the new image. Entrypoint copies baked config into `/opt/data/config.yaml`, overwriting any stale host-side copy. State (state.db, memory/, journal/) is preserved on the bind mount.

### Verify it landed

```sh
# 1. Container health
curl -sf http://localhost:8093/health   # 8092=zephyr, 8093=phoenix, ..., 8099=khaos

# 2. Confirm the new config is the active one (inside the container)
docker exec taya-<name> python3 -c \
  "import yaml; print(yaml.safe_load(open('/opt/data/config.yaml'))['model']['default'])"

# 3. Run the 6-test migration harness
python3 ~/bin/phoenix-migration-test.py --agent <name>

# 4. Check the gateway is actually serving the new model
python3 ~/.hermes/scripts/agent-gateway-health.py
```

If the `model.default` print or the harness's Test 6 disagrees with what you baked, your baked image isn't being used — see Pitfall #1.

---

## 4. Common Edits Cookbook

### 4.1 Swap the main model

Edit `<name>/config.yaml`:

```yaml
model:
  default: xiaomi/mimo-v2.5-pro   # ← change this
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
```

Then `docker compose -f docker-compose.agents.yml build <name> && docker compose -f docker-compose.agents.yml up -d <name>`.

Mirror to the other 7 agents if it's a fleet-wide change.

### 4.2 Rotate the OpenRouter API key (no rebuild)

Edit `infrastructure/.env`:

```sh
OPENROUTER_API_KEY_<NAME>=new-key-here
```

Then `docker compose -f docker-compose.agents.yml up -d <name>` (restart only, no build).

The per-agent OR keys live in Vault at `secret/hermes/openrouter-api-keys` and are refreshed via `python3 infrastructure/scripts/sync-env-from-vault.py` whenever the Vault values rotate. To rotate Vault-side:

```sh
# Update Vault via AppRole (preferred — no SSH)
python3 infrastructure/scripts/sync-env-from-vault.py
# Regenerates infrastructure/.env from Vault, mode 600

# Verify .env matches Vault (use in cron health check)
python3 infrastructure/scripts/sync-env-from-vault.py --check
```

After syncing, force-recreate the affected agent to pick up the new key:

```sh
docker compose -f infrastructure/docker-compose.agents.yml up -d <name>
```

**Khaos is special:** its bind-mounted `~/.hermes/agents/khaos/.env` (host file) must be patched in-container. The sync script handles Docker agents but not the host-profile bind mount. To update Khaos:

```sh
# Run the patch script on the host, which fetches key from Vault and patches in-container
docker exec -i taya-khaos sh -c 'cat > /tmp/patch.py' < infrastructure/scripts/_patch-khaos-key.py
docker exec -e NEW_KEY="$(...)" taya-khaos python3 /tmp/patch.py
docker exec taya-khaos rm /tmp/patch.py
```

Or as a one-shot: `docker restart taya-khaos` after the sync script regenerates `.env` and patches the host `~/.hermes/agents/khaos/.env` via the patch script.

**Field name inconsistency:** Zephyr's Discord Vault entry uses `token` (lowercase), Orbit's uses `Token` (capital T). The sync script handles both.

### 4.3 Add or change an auxiliary model

Edit `<name>/config.yaml` `auxiliary:` block. The current fleet standard is local qwen3.5:9b via GB10 Ollama:

```yaml
auxiliary:
  vision:            { provider: custom, model: qwen3.5:9b, base_url: http://100.97.181.20:11434/v1 }
  web_extract:       { provider: custom, model: qwen3.5:9b, base_url: http://100.97.181.20:11434/v1 }
  # ... etc for skills_hub, approval, mcp, title_generation, etc.
```

GB10 is reachable from `moto-genai-vm` at `http://100.97.181.20:11434/v1` (Tailscale IP, stable).

### 4.4 Update Discord channel allowlist (zephyr, orbit)

Edit `<name>/config.yaml`:

```yaml
gateway:
  platforms:
    discord:
      enabled: true
      allowed_channels:
        - "1503671577781211167"   # #dylan-orbit
        - "1503673116545781872"   # #cost-watch
        # ... etc
```

Build + restart. The `DISCORD_BOT_TOKEN` env var is unchanged.

### 4.5 Change resource limits

Edit `infrastructure/docker-compose.agents.yml` per-service `deploy.resources.limits`:

```yaml
deploy:
  resources:
    limits:
      memory: 8G
      cpus: "3.0"
```

Then `docker compose -f docker-compose.agents.yml up -d <name>` (no rebuild needed — runtime config).

Fleet standard is 8GB RAM / 3 cores per agent. Khaos is currently held at 8GB; bump to 10GB if WhatsApp media handling gets tight (todo #11).

### 4.6 Bump the gateway port

Edit `<name>/config.yaml`:

```yaml
gateway:
  platforms:
    api_server:
      port: 8642   # ← change this
```

And update the matching `HERMES_GATEWAY_PORT` env var in `docker-compose.agents.yml` for that service. **They must match** — if they don't, the gateway binds on the baked port but compose port-mapping uses the env port, so the host port won't reach the gateway.

Then `docker compose -f docker-compose.agents.yml build <name> && docker compose -f docker-compose.agents.yml up -d <name>`.

### 4.7 Add a brand-new agent

1. Copy `infrastructure/agents/<closest-existing-agent>/` to `infrastructure/agents/<new-name>/`.
2. Copy `infrastructure/profiles/<closest>.yml` to `infrastructure/profiles/<new-name>.yml`.
3. Edit `<new-name>/config.yaml`: set `model`, `gateway.platforms.api_server.port` (next free), `agent_id`, role.
4. Edit `<new-name>/Dockerfile`: set all `ARG` defaults to match the new agent.
5. Add a service entry in `docker-compose.agents.yml` (copy an existing one, change `image:`, `container_name:`, `hostname:`, `volumes:`, `ports:`, `API_SERVER_KEY`).
6. Add `OPENROUTER_API_KEY_<NEW>` to `infrastructure/.env`.
7. Add the OR key to Vault (`secret/hermes/openrouter-api-keys/<new>`) and add the agent to `~/.hermes/scripts/fetch-agent-or-keys.sh`.
8. Update `_CONTAINER_API_PORT` dict in `~/bin/phoenix-migration-test.py`.
9. Update `AGENT_TO_CONTAINER` and `NAME_MAP` dicts in `~/.hermes/scripts/agent-gateway-health.py`.
10. `mkdir -p ~/.hermes/agents/<new-name> && sudo chown 10000:10000 ~/.hermes/agents/<new-name>`.
11. `docker compose -f docker-compose.agents.yml build <new-name> && docker compose -f docker-compose.agents.yml up -d <new-name>`.

---

## 5. Per-Agent Callouts

### 5.1 Zephyr (Technical Lead)

- **Special:** first agent to use baked images. Has Discord integration; `DISCORD_BOT_TOKEN` set in compose.
- **Gateway port:** 8642 baked, **but config and runtime port have drifted** — `gateway.platforms.api_server.port` says 8642 but actual listening port was 8643 in pre-Phase-5 runs. See Follow-up #10.
- **Skills baked:** `systematic-debugging test-driven-development github-pr-workflow requesting-code-review simplify-code node-inspect-debugger plan hermes-agent`.
- **Auxiliary:** full qwen3.5:9b routing.
- **Watch for:** `model.xiaomi/mimo-v2.5-pro` is set both baked AND via `HERMES_MODEL` env in compose. If they drift, the env wins at runtime.

### 5.2 Phoenix (Migration Specialist)

- **Standard agent.** No special integrations.
- **Gateway port:** 8643 baked.
- **Skills baked:** `systematic-debugging test-driven-development github-pr-workflow simplify-code python-debugpy plan hermes-agent`.
- **Watch for:** was originally using the OLD config schema (`model.primary: deepseek/...`) before Phase 5. After rebuild, must use NEW schema (`model.default + provider + base_url + api_mode`).

### 5.3 Orbit (DevOps Engineer)

- **Special:** Discord integration with the most extensive channel allowlist of any agent (6 channels across `#dylan-orbit`, `#cost-watch`, `#infra-alerts`, `#notifications-and-alerts`, `#health-checkins`, `#briefings-and-interviews`). Baked into config, NOT in shared discord-orbit.yml.
- **Gateway port:** 8644 baked.
- **Skills baked:** `systematic-debugging test-driven-development github-pr-workflow plan hermes-agent`.
- **Watch for:** host cron `2b4ad1e318a8` "Agent Gateway Health" runs as Orbit is one of the smoke-tested agents; if Orbit's gateway is broken, the cron will alert every 10 min.

### 5.4 PM (Project Manager)

- **Standard agent.** No special integrations.
- **Gateway port:** 8645 baked.
- **Skills baked:** `plan hermes-agent` (smallest skill set of any agent).
- **Watch for:** Paperclip `assignee: pm` issues land here. When upgrading Paperclip, verify PM's agent ID still matches `18c3980a-3ded-4cff-8276-28a7bdfd2817`.

### 5.5 Reviewer (Code Reviewer)

- **Standard agent.** No Discord/WhatsApp.
- **Gateway port:** 8646 baked.
- **Skills baked:** `systematic-debugging test-driven-development github-pr-workflow requesting-code-review plan hermes-agent`.
- **Watch for:** was originally using OLD config schema (`model.primary: deepseek/...`) before Phase 5; converted in Phase 5.1.

### 5.6 Researcher

- **Standard agent.** No Discord/WhatsApp.
- **Gateway port:** 8647 baked.
- **Skills baked:** `arxiv youtube-content plan hermes-agent` (research-specific).
- **Watch for:** arxiv + youtube-content skills mean this agent makes external API calls. The OR key (`OPENROUTER_API_KEY_RESEARCHER`) is on the same Vault quota as the others — heavy research tasks can burn quota fast.

### 5.7 Writer (Technical Writer)

- **Standard agent.** No Discord/WhatsApp.
- **Gateway port:** 8648 baked.
- **Skills baked:** `plan hermes-agent obsidian`.
- **Watch for:** obsidian skill reads from a vault path. If the Obsidian vault isn't mounted at `/obsidian` (or wherever the skill expects), Writer's output quality drops.

### 5.8 Khaos (Jayme's Personal Assistant) — **Special Case**

- **Special:** uses its own `khaos/entrypoint.sh` instead of `shared/entrypoint.sh`. Runs WhatsApp bridge + dashboard + container-API server + gateway (4 services in 1 container).
- **Gateway port:** 8643 baked, **but compose port mapping is 8751→8751** (not 8751→8643). Container API is 8099.
- **Skills baked:** `humanizer songwriting-and-ai-music youtube-content gif-search heartmula songsee`.
- **Auxiliary:** full qwen3.5:9b routing.
- **Missing identity files:** only `SOUL.md` + `agent-config/` exist in `~/.hermes/agents/khaos/`. Missing: `AGENTS.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`, `TOOLS.md`. (todo #9)
- **Watch for:**
  - **Config is 9050 lines** — much larger than the standard 80-line config. Don't copy Khaos's config as a template; copy Phoenix's.
  - **WhatsApp bridge directory** at `/opt/data/whatsapp/bridge/` must exist on the bind mount for Khaos's entrypoint to start the bridge. If the bridge dir is missing, Khaos still starts (gateway + container API + dashboard) but WhatsApp is dead.
  - **Open Brain key** is baked into the config (under `mcp_servers.open-brain.url` query string). If the key rotates in Supabase, you MUST rebuild Khaos (no env override exists).
  - **Khaos is the only agent Jayme uses directly** — coordinate changes with Jayme before pushing config that affects user-facing behavior.
  - **Jayme confirmed not actively working with Khaos** as of 2026-06-26, so safe to do infra work without coordinating.

---

## 6. Verify-It-Landed Checklist

After every config change, run this end-to-end:

```sh
# A. Container is up and reporting the new model
curl -sf http://localhost:8093/health | jq '.agent,.role'
docker exec taya-<name> python3 -c \
  "import yaml; cfg=yaml.safe_load(open('/opt/data/config.yaml')); \
   print('model:', cfg['model']['default']); \
   print('port:', cfg['gateway']['platforms']['api_server']['port']); \
   print('aux.vision:', cfg['auxiliary']['vision'])"

# B. Gateway chat actually completes with the new model
python3 ~/bin/phoenix-migration-test.py --agent <name>
# Expect: 5/5 passed, 1 skipped (no expected hash), gateway chat returns MIGRATION_OK

# C. (Optional) Cross-agent fleet health
python3 ~/.hermes/scripts/agent-gateway-health.py
# Expect: "OK @ timestamp (or_keys=10, gateways=8)" or per-agent detail lines

# D. (Optional) Paperclip can still dispatch to the agent
curl -sf http://localhost:3100/api/agents | jq '.[] | select(.name=="<name>") | .status'
```

If A or B fails, see Pitfall #1 (config not loading) or Pitfall #2 (permission denied).

---

## 7. Rollback Procedure

### 7.1 Roll back a single agent to its previous baked config

```sh
cd ~/paperclip

# 1. Restore the previous config.yaml from the pre-baked-config sidecar
cp ~/backups/pre-baked-config/<name>/config.yaml infrastructure/agents/<name>/config.yaml

# 2. Rebuild + restart
cd infrastructure
docker compose -f docker-compose.agents.yml build <name>
docker compose -f docker-compose.agents.yml up -d <name>

# 3. Verify
python3 ~/bin/phoenix-migration-test.py --agent <name>
```

### 7.2 Roll back to a previous Docker image

The current image is `infrastructure-<name>:latest`. Before rebuilding, tag the current one:

```sh
docker tag infrastructure-<name>:latest infrastructure-<name>:rollback-2026-06-27
```

To roll back:

```sh
cd ~/paperclip/infrastructure
docker compose -f docker-compose.agents.yml down <name>
# Edit docker-compose.agents.yml: change `image: infrastructure-<name>:latest` → `:rollback-2026-06-27`
docker compose -f docker-compose.agents.yml up -d <name>
```

### 7.3 Restore state.db from a sidecar

If you need to restore agent state (state.db, memory/, journal/) from before a bad config change:

```sh
# Sidecars are at:
ls ~/backups/pre-baked-config/<name>/
#   state.db, memory/, journal/, channel_directory.json, etc.

# Stop the agent
cd ~/paperclip/infrastructure
docker compose -f docker-compose.agents.yml stop <name>

# Replace state on host (use sudo — UID 10000 dirs)
sudo cp -a ~/backups/pre-baked-config/<name>/state.db /home/dtaylor/.hermes/agents/<name>/
sudo cp -a ~/backups/pre-baked-config/<name>/memory /home/dtaylor/.hermes/agents/<name>/

# Restart
docker compose -f docker-compose.agents.yml up -d <name>
```

### 7.4 Restore state.db from a restic snapshot

```sh
# List available snapshots for ~/.hermes/agents/
restic -r ~/backups/restic --password-file ~/.config/restic/password snapshots --path /home/dtaylor/.hermes/agents/<name>

# Restore a specific snapshot
sudo restic -r ~/backups/restic --password-file ~/.config/restic/password \
  restore <snapshot-id> --target / --include /home/dtaylor/.hermes/agents/<name>

# Or just the state.db
sudo restic -r ~/backups/restic --password-file ~/.config/restic/password \
  restore <snapshot-id> --target / --include /home/dtaylor/.hermes/agents/<name>/state.db
```

---

## 8. Pitfalls

These are real bugs we hit during Phase 5. They will hit you again unless you read this section.

### Pitfall #1 — Entrypoint shadows baked config

**Symptom:** You edit `config.yaml`, rebuild, restart, but `docker exec ... cat /opt/data/config.yaml` still shows the OLD config.

**Cause:** Older entrypoint versions had a `if [ ! -f /opt/data/config.yaml ]; then cp /etc/hermes/agent-config.yaml /opt/data/config.yaml; fi` guard. Since Phase 1-4 bind-mount migration, `/opt/data/config.yaml` ALWAYS exists from the host, so the baked copy was skipped. The host's stale config won.

**Fix already applied:** Both `shared/entrypoint.sh` and `khaos/entrypoint.sh` now unconditionally copy baked config:

```sh
if [ -f /etc/hermes/agent-config.yaml ]; then
  cp /etc/hermes/agent-config.yaml /opt/data/config.yaml
fi
```

If you reintroduce the conditional, you break the whole baked-config architecture.

### Pitfall #2 — `os.path.exists()` lies under UID 10000 dirs

**Symptom:** The health monitor (`agent-gateway-health.py`) reports `no model.default in config` for an agent whose host-side config clearly has it.

**Cause:** After Phase 1-4 migration, agent dirs are owned by UID 10000 (the `hermes` user inside containers). The host ACL mask was set to `---` by `chown`, blocking `dtaylor`'s read access — even when ACLs grant `r--`, `os.path.exists()` returns False because stat() respects the effective mask.

**Fix already applied:** `agent-gateway-health.py` was patched to:
1. Use `$HOME` for `AGENTS_DIR` (was hardcoded to `~/.hermes/agents` which under `sudo` resolves to `/root/.hermes/agents`).
2. Read the full baked config from inside the container via `docker exec python3 -c "yaml.safe_load(open('/etc/hermes/agent-config.yaml'))"`.
3. Pull `API_SERVER_KEY` from container env at runtime (the only thing intentionally NOT baked).
4. chmod on the agent dirs grants `dtaylor` effective read.

If you re-run `sudo chown -R 10000:10000` on an agent dir, re-apply the chmod:

```sh
sudo chmod -R u+rwX,g+rX,o+rX /home/dtaylor/.hermes/agents/<name>/
```

### Pitfall #3 — Symlinks don't survive Docker COPY

**Symptom:** A plugin (e.g., `openrouter-server-tools`) is missing in the built image even though it exists at `infrastructure/agents/<name>/plugins/<plugin>/`.

**Cause:** Docker COPY resolves symlinks at build time. If you create the per-agent plugin dir with `ln -sf ../zephyr/plugins/openrouter-server-tools`, the COPY resolves it back to the source path — but if the source is not under the build context, the COPY fails or copies stale content.

**Fix already applied:** Each per-agent plugin dir uses `cp -a` (real copy), not symlinks.

### Pitfall #4 — mimo.json UID 1000 bug

**Symptom:** Container starts but agent gets stuck in a loop trying to read `/opt/data/mimo.json` owned by UID 1000 (root). Symptom: container healthcheck passes but gateway chat returns "permission denied" or 500.

**Cause:** Pre-Phase-5, the `COPY` steps in agent Dockerfiles didn't `--chown=hermes:hermes`, so the entrypoint's later `cp` operations wrote files as root (UID 1000), not as the `hermes` user (UID 10000). This made `mimo.json` unreadable.

**Fix already applied:** Every COPY in every agent Dockerfile now uses `--chown=hermes:hermes`. If you add a new COPY without `--chown`, the bug returns.

### Pitfall #5 — Port mismatch between baked config and env

**Symptom:** Gateway smoke test reports "Gateway '<name>' (taya-<name>): config port=8646 unreachable, actually listening on 8642."

**Cause:** `gateway.platforms.api_server.port` (baked in config.yaml) and `HERMES_GATEWAY_PORT` (env var in compose) are out of sync. The gateway binds on the baked port, but compose port-maps the env port to the host, so host→container traffic goes to the wrong port.

**Fix:** Pick one as the source of truth (currently the env var wins at runtime), update the baked config to match. This is todo #10.

---

## 9. Cross-References

- **Compose file:** `infrastructure/docker-compose.agents.yml`
- **Env file:** `infrastructure/.env`
- **Agent Dockerfiles:** `infrastructure/agents/<name>/Dockerfile`
- **Agent configs:** `infrastructure/agents/<name>/config.yaml`
- **Agent profiles (skills + paperclip meta):** `infrastructure/profiles/<name>.yml`
- **Shared entrypoint:** `infrastructure/agents/shared/entrypoint.sh`
- **Khaos entrypoint:** `infrastructure/agents/khaos/entrypoint.sh`
- **Migration test harness:** `~/bin/phoenix-migration-test.py --agent <name>`
- **Health monitor:** `~/.hermes/scripts/agent-gateway-health.py` (cron `2b4ad1e318a8`, every 10 min)
- **OR key fetcher:** `~/.hermes/scripts/fetch-agent-or-keys.sh`
- **Pre-baked-config sidecars:** `~/backups/pre-baked-config/<name>/`
- **Pre-migration sidecars (Phase 1-4):** `~/backups/pre-migration/<agent>-anon-vol/`
- **restic repo:** `~/backups/restic/` (encrypted; hourly cron)
- **Container API port mapping:** Phoenix=8093, Orbit=8094, PM=8095, Reviewer=8096, Researcher=8097, Writer=8098, Zephyr=8092, Khaos=8099
- **Vault path for OR keys:** `secret/hermes/openrouter-api-keys` (10 keys: code-reviewer, eos, khaos, mgmt-key, orbit, phoenix, project-manager→pm, researcher, technical-writer→writer, zephyr)
- **GB10 Ollama:** `http://100.97.181.20:11434/v1` (Tailscale IP)

---

## 10. Quick-Reference Card

```sh
# Edit → Rebuild → Restart (the 3-step)
$EDITOR ~/paperclip/infrastructure/agents/<name>/config.yaml
cd ~/paperclip/infrastructure
docker compose -f docker-compose.agents.yml build <name>
docker compose -f docker-compose.agents.yml up -d <name>

# Verify
curl -sf http://localhost:8093/health   # change 8093 to the right port
docker exec taya-<name> python3 -c "import yaml; print(yaml.safe_load(open('/opt/data/config.yaml'))['model']['default'])"
python3 ~/bin/phoenix-migration-test.py --agent <name>

# Roll back
cp ~/backups/pre-baked-config/<name>/config.yaml ~/paperclip/infrastructure/agents/<name>/config.yaml
cd ~/paperclip/infrastructure
docker compose -f docker-compose.agents.yml build <name>
docker compose -f docker-compose.agents.yml up -d <name>

# All 8 at once (when change applies fleet-wide)
cd ~/paperclip/infrastructure
docker compose -f docker-compose.agents.yml build
docker compose -f docker-compose.agents.yml up -d
python3 ~/.hermes/scripts/agent-gateway-health.py
```