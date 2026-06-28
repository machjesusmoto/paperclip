#!/usr/bin/env python3
"""Setup secrets from Vault for containerized TAYA agents.

Called by entrypoint.sh during container startup. Retrieves agent-specific
secrets from HashiCorp Vault and writes them to the Hermes .env file.

Usage: setup-secrets.py <agent-paperclip-id>
"""
import os
import sys
import urllib.request
import json


def _vault_get(path: str) -> dict:
    """Fetch a single secret path from Vault KV v2. Returns {} on any error."""
    vault_addr = os.getenv("VAULT_ADDR", "http://vault:8200")
    vault_token = os.getenv("VAULT_TOKEN", "")
    if not vault_token:
        return {}
    try:
        url = f"{vault_addr}/v1/secret/data/{path.lstrip('/')}"
        req = urllib.request.Request(url)
        req.add_header("X-Vault-Token", vault_token)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("data", {}).get("data", {})
    except Exception as e:
        print(f"⚠️  Vault fetch failed for {path}: {e}")
        return {}


def get_vault_secret(agent_id: str) -> dict:
    """Retrieve agent-scoped secrets from Vault (per-agent OR keys, etc)."""
    return _vault_get(f"agents/{agent_id}")


def get_brain_key(agent_id: str) -> str:
    """Retrieve Open-Brain MCP access key from `secret/paperclip/services/<id>-brain-supabase`.

    Returns the `mcp-access-key` field if present, else "".
    """
    data = _vault_get(f"paperclip/services/{agent_id}-brain-supabase")
    return data.get("mcp-access-key", "")


def write_env_file(secrets: dict, hermes_home: str) -> None:
    """Write merged secrets to Hermes .env file."""
    env_file = os.path.join(hermes_home, ".env")

    # Start with env-var defaults (secrets passed via docker-compose take precedence)
    defaults = {
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY", ""),
        "PAPERCLIP_API_KEY": os.getenv("PAPERCLIP_API_KEY", ""),
        "FAL_KEY": os.getenv("FAL_KEY", ""),
    }

    merged = {**defaults, **{k: v for k, v in secrets.items() if v}}

    with open(env_file, "w") as f:
        for key, value in merged.items():
            if value:
                f.write(f"{key}={value}\n")

    os.chmod(env_file, 0o600)
    print(f"✅ Secrets written to {env_file} ({len(merged)} keys)")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: setup-secrets.py <agent-paperclip-id>")
        sys.exit(1)

    agent_id = sys.argv[1]
    hermes_home = os.getenv("HERMES_HOME", "/opt/data")
    os.makedirs(hermes_home, exist_ok=True)

    secrets = get_vault_secret(agent_id)

    # Open-Brain MCP access key (per-agent isolated Supabase table).
    brain_key = get_brain_key(agent_id)
    if brain_key:
        secrets["OPEN_BRAIN_KEY"] = brain_key
        print(f"✅ Open-Brain key loaded for {agent_id}")
    else:
        print(f"ℹ️  No Open-Brain key for {agent_id} (this is fine if the agent doesn't use Open-Brain)")

    if secrets:
        write_env_file(secrets, hermes_home)
    else:
        print("ℹ️  No secrets retrieved — agents will use env-var credentials")


if __name__ == "__main__":
    main()
