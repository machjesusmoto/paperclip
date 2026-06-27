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


def get_vault_secret(agent_id: str) -> dict:
    """Retrieve secrets from Vault KV v2 for the given agent."""
    vault_addr = os.getenv("VAULT_ADDR", "http://vault:8200")
    vault_token = os.getenv("VAULT_TOKEN", "")

    if not vault_token:
        print("⚠️  No VAULT_TOKEN set, skipping Vault secret retrieval")
        return {}

    try:
        url = f"{vault_addr}/v1/secret/data/agents/{agent_id}"
        req = urllib.request.Request(url)
        req.add_header("X-Vault-Token", vault_token)

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("data", {})
    except Exception as e:
        print(f"⚠️  Vault secret retrieval failed: {e}")
        return {}


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
    if secrets:
        write_env_file(secrets, hermes_home)
    else:
        print("ℹ️  No secrets retrieved — agents will use env-var credentials")


if __name__ == "__main__":
    main()
