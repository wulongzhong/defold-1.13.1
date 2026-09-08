# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Shared agent error codes. Keep in sync with editor.agent fail! codes."""

from typing import Any, Dict, Optional, Tuple

ERROR_CODES = {
    "MISSING_PARAM": "A required parameter is missing.",
    "INVALID_PARAM": "A parameter has the wrong type or range.",
    "NOT_FOUND": "Resource, game object, or path does not exist.",
    "NOT_ALLOWED": "The current editor or engine state does not allow this.",
    "EDITOR_NOT_READY": "Readiness gate. data.sub_code has the concrete state.",
    "INVALID_PATH": "Path is outside the project or malformed.",
    "OLD_TEXT_NOT_FOUND": "script_patch anchor matched 0 times.",
    "MULTIPLE_MATCHES": "script_patch anchor matched more than once.",
    "UNKNOWN_OP": "manage op is not recognized.",
    "UNKNOWN_COMMAND": "Dispatcher does not know this command.",
    "UNAUTHORIZED": "Editor Bearer token was rejected.",
    "HANDLER_ERROR": "Handler failed.",
    "EDITOR_UNREACHABLE": "No .internal/editor.port or the editor is closed.",
    "ENGINE_UNREACHABLE": "dmengine executable was not found.",
    "ENGINE_NOT_RUNNING": "Live observe needs a running engine.",
    "RUNTIME_DUMP_MISSING": "Snapshot JSON was not written.",
    "SNAPSHOT_NOT_FOUND": "Snapshot id or path is not a project snapshot.",
    "INLINE_TOO_LARGE": "inline=full or get_path exceeded 48 KB.",
    "UNKNOWN_TARGET": "session_activate url is not in the discovered list.",
}

COMMAND_ALIASES = {
    "add_component": "component_add",
    "add_instance": "gameobject_create",
    "create_gameobject": "gameobject_create",
    "create_node": "gameobject_create",
    "create_script": "script_create",
    "node_create": "gameobject_create",
    "node_get_properties": "gameobject_get_properties",
    "node_set_property": "gameobject_manage",
    "patch_script": "script_patch",
    "scene_get_hierarchy": "collection_get_hierarchy",
    "scene_open": "collection_open",
    "scene_save": "collection_save",
}

READ_OPS = {
    "find",
    "get",
    "get_roots",
    "list",
    "read",
    "read_text",
    "search",
    "selection_get",
    "settings_get",
    "state",
    "stop",
}

ALWAYS_READ_COMMANDS = {
    "api_manage",
    "collection_get_hierarchy",
    "collection_open",
    "doctor",
    "editor_preview",
    "editor_state",
    "gameobject_get_properties",
    "logs_read",
    "project_build",
    "project_check",
    "project_doctor",
    "project_run",
    "project_stop",
    "runtime_diff",
    "runtime_get_hierarchy",
    "runtime_get_properties",
    "runtime_observe",
    "runtime_screenshot",
    "runtime_snapshot_query",
    "runtime_state",
    "session_activate",
    "session_manage",
}


def canonical_command(name: str) -> str:
    return COMMAND_ALIASES.get(name or "", name)


def apply_alias(name: str, params: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    params = dict(params or {})
    canonical = canonical_command(name)
    if name == "node_set_property" and not params.get("op"):
        params["op"] = "set_property"
    return canonical, params


def authoring_write(command: str, params: Optional[Dict[str, Any]] = None) -> bool:
    params = params or {}
    if command in ALWAYS_READ_COMMANDS:
        return False
    op = params.get("op")
    if isinstance(op, str):
        return op not in READ_OPS
    return command not in {"batch_execute"}
