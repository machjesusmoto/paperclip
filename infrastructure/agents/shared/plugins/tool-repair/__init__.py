"""
tool-repair plugin — deterministic tool-call argument repair harness.

Broadly mirrors the Command Code repair harness described by Ahmad Awais:
1. Validates tool-call args against the registered JSON Schema before dispatch.
2. Applies deterministic, ordered repairs for the common failure modes of
   open-weight models (DeepSeek, Qwen, GLM, etc.).
3. Appends a compact repair note to the tool result so the model *learns*
   from the correction and stops repeating the mistake.

**Middleware** (``tool_request``): runs *after* ``coerce_tool_args`` in
``handle_function_call``. Receives the already-coerced args and the tool's
registered schema. Applies:
  - null-strip:      remove keys whose value is ``None`` when the property
                     is not listed in the schema's ``required`` array
                     (model sent ``null`` when it should have omitted)
  - object-unwrap:   when schema expects ``array`` but model sends a bare
                     object ``{"x":"y"}``, wrap as ``[{"x":"y"}]``
  - model-specific:  parameter-name mapping, enum coercion, etc. from
                     per-model repair rules in ``repairs/*.json``

**Hook** (``transform_tool_result``): when repairs were applied, appends a
``[🔧 tool-repair]`` note explaining what was fixed.  The note is terse
(≤ 80 chars) to minimise token overhead while still giving the model a
learning signal.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PLUGIN_DIR = Path(__file__).resolve().parent
_REPAIRS_DIR = _PLUGIN_DIR / "repairs"

# ---------------------------------------------------------------------------
# Shared state between middleware and hook
# ---------------------------------------------------------------------------
# Keyed by (tool_call_id, turn_id) → list of repair-description strings.
# The middleware writes here; the hook reads, clears, and appends notes.
_PENDING_REPAIRS: Dict[Tuple[str, str], List[str]] = {}


def _repair_key(tool_call_id: str, turn_id: str) -> Tuple[str, str]:
    return (tool_call_id or "", turn_id or "")


# ---------------------------------------------------------------------------
# Registry helpers — resolve the tool's JSON Schema at repair time
# ---------------------------------------------------------------------------


def _get_tool_schema(tool_name: str) -> Optional[Dict[str, Any]]:
    """Look up the registered JSON Schema for *tool_name*."""
    try:
        from tools.registry import registry
        return registry.get_schema(tool_name)
    except Exception:
        return None


def _get_param_schema(schema: Dict[str, Any], param: str) -> Optional[Dict[str, Any]]:
    """Return the property schema for *param* inside *schema*, or None."""
    params = (schema.get("parameters") or {})
    props = params.get("properties") if isinstance(params, dict) else None
    if not isinstance(props, dict):
        return None
    return props.get(param)


def _get_required_params(schema: Dict[str, Any]) -> Set[str]:
    """Return the set of required parameter names for *schema*."""
    params = (schema.get("parameters") or {})
    if not isinstance(params, dict):
        return set()
    required = params.get("required")
    if isinstance(required, list):
        return {r for r in required if isinstance(r, str)}
    return set()


# ---------------------------------------------------------------------------
# Repair rules
# ---------------------------------------------------------------------------

# Per-model repair rules, loaded lazily.
_RULES_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_rules() -> Dict[str, Dict[str, Any]]:
    """Load all repair rules from ``repairs/*.json``, keyed by model slug."""
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE

    _RULES_CACHE = {}
    if not _REPAIRS_DIR.is_dir():
        return _RULES_CACHE

    for f in sorted(_REPAIRS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            model_slug = data.get("model", f.stem)
            _RULES_CACHE[model_slug] = data
        except Exception as exc:
            logger.warning("tool-repair: failed to load %s: %s", f.name, exc)

    return _RULES_CACHE


def _match_model_rules(active_model: str) -> List[Dict[str, Any]]:
    """Return the model-specific repair rules matching *active_model*."""
    rules = _load_rules()
    model_lower = active_model.lower()

    # Direct match first
    for slug, rule_set in rules.items():
        if slug.lower() == model_lower:
            # Return the "repairs" list, or empty list
            repairs = rule_set.get("repairs")
            if isinstance(repairs, list):
                return repairs
            return []

    # Substring / prefix match (e.g. "deepseek" matches "deepseek/deepseek-v4")
    for slug, rule_set in rules.items():
        if slug.lower() in model_lower or model_lower in slug.lower():
            repairs = rule_set.get("repairs")
            if isinstance(repairs, list):
                return repairs
            return []

    return []


def _resolve_active_model() -> str:
    """Best-effort guess at the active model name for rule matching."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        model_cfg = cfg.get("model")
        if isinstance(model_cfg, dict):
            return (model_cfg.get("model") or model_cfg.get("default") or "").strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Repair operations
# ---------------------------------------------------------------------------

def _apply_null_strip(
    args: Dict[str, Any],
    schema: Dict[str, Any],
) -> List[str]:
    """Remove keys whose value is None and which are not required."""
    required = _get_required_params(schema)
    notes: List[str] = []
    for key in list(args.keys()):
        if args[key] is None and key not in required:
            del args[key]
            notes.append(f"null-{key}")
    return notes


def _apply_object_unwrap_for_array(
    args: Dict[str, Any],
    schema: Dict[str, Any],
) -> List[str]:
    """When schema expects array but value is a dict, wrap it in a list."""
    notes: List[str] = []
    for key, value in list(args.items()):
        if not isinstance(value, dict):
            continue
        prop_schema = _get_param_schema(schema, key)
        if prop_schema is None:
            continue
        expected_type = prop_schema.get("type")
        if expected_type != "array":
            continue
        # Wrap the object as a single-element array
        args[key] = [value]
        notes.append(f"unwrap-obj-{key}")
    return notes


def _apply_json_array_parse(
    args: Dict[str, Any],
    schema: Dict[str, Any],
) -> List[str]:
    """When schema expects array but value is a JSON-encoded string, parse it.

    Note: ``coerce_tool_args`` in ``model_tools`` already handles this, but
    it runs *before* the middleware in some paths.  This is defense-in-depth
    — if coercion missed it, we catch it here.
    """
    notes: List[str] = []
    for key, value in list(args.items()):
        if not isinstance(value, str):
            continue
        if not value.strip().startswith("["):
            continue
        prop_schema = _get_param_schema(schema, key)
        if prop_schema is None:
            continue
        expected_type = prop_schema.get("type")
        if expected_type != "array":
            continue
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                args[key] = parsed
                notes.append(f"json-parse-{key}")
        except (json.JSONDecodeError, TypeError):
            pass
    return notes


def _apply_model_specific_repairs(
    args: Dict[str, Any],
    schema: Dict[str, Any],
    active_model: str,
) -> List[str]:
    """Apply model-specific repair rules from ``repairs/*.json``."""
    rules = _match_model_rules(active_model)
    notes: List[str] = []
    for rule in rules:
        note = _apply_single_model_rule(args, schema, rule)
        if note:
            notes.append(note)
    return notes


def _apply_single_model_rule(
    args: Dict[str, Any],
    schema: Dict[str, Any],
    rule: Dict[str, Any],
) -> Optional[str]:
    """Apply one model-specific repair rule. Returns a note string or None."""
    rule_type = rule.get("type", "")

    if rule_type == "param_rename":
        # When the model uses the wrong parameter name for a known schema key,
        # remap it.  Example: DeepSeek sometimes sends "path" when the tool
        # expects "file_path".
        wrong = rule.get("wrong", "")
        correct = rule.get("correct", "")
        if wrong in args and correct not in args:
            args[correct] = args.pop(wrong)
            return f"rename-{wrong}→{correct}"

    if rule_type == "enum_coerce":
        # When the schema has an enum and the model sends a near-miss value,
        # coerce to the closest enum member.
        param = rule.get("param", "")
        if param not in args:
            return None
        prop_schema = _get_param_schema(schema, param)
        if prop_schema is None:
            return None
        enum_values = prop_schema.get("enum")
        if not isinstance(enum_values, list):
            return None
        value = args[param]
        if not isinstance(value, str):
            return None
        # Case-insensitive match
        for ev in enum_values:
            if isinstance(ev, str) and ev.lower() == value.lower() and ev != value:
                args[param] = ev
                return f"case-{param}:{value}→{ev}"

    if rule_type == "string_num_coerce":
        # Specific numeric fields the model stringifies despite coercion
        param = rule.get("param", "")
        if param in args and isinstance(args[param], str):
            try:
                args[param] = int(args[param])
                return f"str→int-{param}"
            except ValueError:
                try:
                    args[param] = float(args[param])
                    return f"str→float-{param}"
                except ValueError:
                    pass

    return None


# ---------------------------------------------------------------------------
# Middleware: tool_request
# ---------------------------------------------------------------------------

def _on_tool_request(
    tool_name: str = "",
    args: Any = None,
    original_args: Any = None,
    tool_call_id: str = "",
    turn_id: str = "",
    **__: Any,
) -> Optional[Dict[str, Any]]:
    """Repair tool-call args before dispatch.

    Returns ``{"args": repaired_dict}`` to rewrite the args, or ``None``
    (pass through) when no repairs are needed or args can't be repaired.
    """
    if not isinstance(args, dict) or not args:
        return None

    schema = _get_tool_schema(tool_name)
    if schema is None:
        return None

    all_notes: List[str] = []

    # 1. Strip null-valued optional keys
    all_notes.extend(_apply_null_strip(args, schema))

    # 2. Unwrap bare objects when schema expects array
    all_notes.extend(_apply_object_unwrap_for_array(args, schema))

    # 3. JSON-array parse (defense-in-depth)
    all_notes.extend(_apply_json_array_parse(args, schema))

    # 4. Model-specific repairs
    active_model = _resolve_active_model()
    if active_model:
        all_notes.extend(_apply_model_specific_repairs(args, schema, active_model))

    if not all_notes:
        return None

    # Store repair notes for the hook to pick up
    key = _repair_key(tool_call_id, turn_id)
    _PENDING_REPAIRS[key] = all_notes

    logger.debug(
        "tool-repair: %d repair(s) applied to %s: %s",
        len(all_notes), tool_name, ", ".join(all_notes),
    )

    return {"args": args, "source": "tool-repair", "reason": f"applied {len(all_notes)} repair(s)"}


# ---------------------------------------------------------------------------
# Hook: transform_tool_result
# ---------------------------------------------------------------------------

_REPAIR_NOTE_MAX_LEN = 200


def _on_transform_tool_result(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    tool_call_id: str = "",
    turn_id: str = "",
    **__: Any,
) -> Optional[str]:
    """Append a repair note to the tool result when repairs were applied.

    The note is compact (< 200 chars) and tells the model exactly what was
    fixed so it can learn and stop making the same mistake.
    """
    key = _repair_key(tool_call_id, turn_id)
    notes = _PENDING_REPAIRS.pop(key, None)
    if not notes:
        return None

    if not isinstance(result, str):
        return None

    # Don't decorate error results — the model has bigger problems.
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2:
            return None
    except (ValueError, TypeError):
        pass

    note_text = ", ".join(notes)
    if len(note_text) > _REPAIR_NOTE_MAX_LEN:
        note_text = note_text[:_REPAIR_NOTE_MAX_LEN - 3] + "..."

    repair_block = (
        f"\n\n[🔧 tool-repair: {len(notes)} fix(es) — {note_text}]\n"
        "This tool call's arguments were repaired. "
        "The fixes above describe what was changed; adjust your next calls accordingly."
    )

    return result + repair_block


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the tool-repair middleware and hooks with the plugin system."""
    ctx.register_middleware("tool_request", _on_tool_request)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    logger.info("tool-repair plugin registered (middleware + hook)")
