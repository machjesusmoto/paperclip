"""
openrouter-server-tools — Hermes plugin for OpenRouter server tools.

Injects OpenRouter server tools (subagent, web_search, web_fetch, datetime)
into API requests when the provider is OpenRouter. Configure via config.yaml:

  openrouter:
    server_tools:
      subagent:
        enabled: true
        worker_model: xiaomi/mimo-v2.5
      web_search:
        enabled: true
      web_fetch:
        enabled: false
      datetime:
        enabled: false

All server tools billing goes through BYOK — verified working.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Default tool definitions ───────────────────────────────────────────
_SUBAGENT_TOOL = {
    "type": "openrouter:subagent",
    "parameters": {
        "model": "xiaomi/mimo-v2.5",
        "instructions": (
            "You are a fast, focused worker agent. Complete the task "
            "exactly as described. Return only the requested output."
        ),
    },
}

_WEB_SEARCH_TOOL = {
    "type": "openrouter:web_search",
    "parameters": {"max_results": 5},
}

_WEB_FETCH_TOOL = {
    "type": "openrouter:web_fetch",
}

_DATETIME_TOOL = {
    "type": "openrouter:datetime",
}

_TOOL_BUILDERS = {
    "subagent": lambda cfg: {
        **_SUBAGENT_TOOL,
        "parameters": {
            **_SUBAGENT_TOOL["parameters"],
            **(cfg.get("worker_model", _SUBAGENT_TOOL["parameters"]["model"]) != _SUBAGENT_TOOL["parameters"]["model"]
                and {"model": cfg["worker_model"]}
                or {}),
        },
    },
    "web_search": lambda cfg: _WEB_SEARCH_TOOL,
    "web_fetch": lambda cfg: _WEB_FETCH_TOOL,
    "datetime": lambda cfg: _DATETIME_TOOL,
}

# ── Global state ───────────────────────────────────────────────────────
_patch_applied = False
_original_build_api_kwargs = None
_extra_tools_cache: list[dict[str, Any]] | None = None


def _get_extra_tools(config: dict) -> list[dict[str, Any]]:
    """Read server_tools config and build the extra tools list."""
    global _extra_tools_cache
    if _extra_tools_cache is not None:
        return _extra_tools_cache

    server_tools_cfg = config.get("openrouter", {}).get("server_tools", {})
    if not server_tools_cfg:
        _extra_tools_cache = []
        return _extra_tools_cache

    tools = []
    for tool_name, builder in _TOOL_BUILDERS.items():
        tool_cfg = server_tools_cfg.get(tool_name, {})
        if isinstance(tool_cfg, dict):
            if tool_cfg.get("enabled", False):
                tools.append(builder(tool_cfg))
        elif tool_cfg is True:  # bare boolean
            tools.append(builder({}))

    _extra_tools_cache = tools
    return _extra_tools_cache


def _patched_build_api_kwargs(agent, api_messages: list) -> dict:
    """Wraps the original build_api_kwargs to inject OR server tools."""
    global _original_build_api_kwargs

    # Call original
    kwargs = _original_build_api_kwargs(agent, api_messages)

    # Only inject for OpenRouter
    provider = getattr(agent, "provider", "")
    base_url = getattr(agent, "base_url", "")
    if provider != "openrouter" and "openrouter" not in str(base_url).lower():
        return kwargs

    # Load config and get extra tools
    try:
        from hermes_constants import get_hermes_home
        import yaml

        config_path = get_hermes_home() / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        extra_tools = _get_extra_tools(config)
        logger.info(
            "openrouter-server-tools: config read, %d tools from server_tools cfg",
            len(extra_tools),
        )
    except Exception as exc:
        logger.warning(
            "openrouter-server-tools: config read failed: %s", exc
        )
        extra_tools = []

    if not extra_tools:
        return kwargs

    # Inject extra tools alongside existing tools
    existing_tools = kwargs.get("tools") or []
    kwargs["tools"] = list(existing_tools) + list(extra_tools)

    logger.info(
        "openrouter-server-tools: injected %d server tools (%s) into %d existing",
        len(extra_tools),
        ", ".join(t.get("type", "?") for t in extra_tools),
        len(existing_tools),
    )

    return kwargs


def on_pre_api_request(
    *,
    provider: str = "",
    base_url: str = "",
    tool_count: int = 0,
    **kwargs: Any,
) -> None:
    """Hook handler — verify the patch is active.

    The actual tool injection happens in the patched build_api_kwargs
    (applied in register()). This hook is for debugging/observability.
    """
    global _patch_applied
    if not _patch_applied and provider:
        # Safety net: if register() didn't patch, try now
        try:
            import agent.chat_completion_helpers as _ch
            global _original_build_api_kwargs
            _original_build_api_kwargs = _ch.build_api_kwargs
            _ch.build_api_kwargs = _patched_build_api_kwargs
            _patch_applied = True
            logger.info("openrouter-server-tools: patched build_api_kwargs (late)")
        except Exception:
            pass


def register(ctx: Any) -> None:
    """Plugin entry point — applies the monkey-patch and registers hooks.

    The monkey-patch must be applied BEFORE any API calls happen,
    so we do it here at plugin load time rather than in a hook.
    """
    global _patch_applied, _original_build_api_kwargs
    try:
        import agent.chat_completion_helpers as _ch

        _original_build_api_kwargs = _ch.build_api_kwargs
        _ch.build_api_kwargs = _patched_build_api_kwargs
        _patch_applied = True
        logger.info(
            "openrouter-server-tools: patched build_api_kwargs "
            "(subagent → %s, web_search enabled)",
            _SUBAGENT_TOOL["parameters"]["model"],
        )
    except Exception:
        logger.warning(
            "openrouter-server-tools: failed to patch build_api_kwargs",
            exc_info=True,
        )

    # Register the hook for observability
    ctx.register_hook("pre_api_request", on_pre_api_request)


def _reset_patch():
    """Restore original function (for testing/unload)."""
    global _patch_applied, _original_build_api_kwargs, _extra_tools_cache
    if _original_build_api_kwargs is not None:
        try:
            import agent.chat_completion_helpers as _ch

            _ch.build_api_kwargs = _original_build_api_kwargs
        except Exception:
            pass
    _patch_applied = False
    _original_build_api_kwargs = None
    _extra_tools_cache = None
