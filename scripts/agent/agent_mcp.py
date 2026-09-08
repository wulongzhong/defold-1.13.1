# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""stdio MCP adapter for the first-party Defold agent CLI. No game plugin."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from agent_ops import dispatch_command


PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    ("editor_state", "Open editor or disk snapshot: version, title, current resource, readiness."),
    ("collection_get_hierarchy", "Authoring collection tree. params: path."),
    ("gameobject_get_properties", "Authoring GO or component properties. params: collection, id, optional component."),
    ("session_activate", "Single local session. No-op success when the editor is closed."),
    ("collection_open", "Resolve and open a collection in the editor."),
    ("collection_save", "Write the collection save data to disk."),
    ("gameobject_create", "Add an embedded or referenced game object. params: collection, id, position, optional path."),
    ("component_add", "Add a component. params: collection, id, type or path."),
    ("script_create", "Create a Lua script from the editor template."),
    ("script_attach", "Attach a .script to a game object."),
    ("script_patch", "Replace a unique old_text with new_text."),
    ("project_build", "Prefer defold_agent.py check. Editor uses POST /command/check."),
    ("logs_read", "Read GET /console when the editor is open, or the last engine log."),
    ("editor_preview", "Authoring preview PNG via GET /preview/{path}. Not a runtime screenshot."),
    ("batch_execute", "Run commands[] sequentially. Each step is its own undo."),
    ("collection_manage", "op: create | add_instance | remove_instance | get_roots"),
    ("gameobject_manage", "op: delete | rename | set_property | find"),
    ("component_manage", "op: remove | set_property"),
    ("script_manage", "op: read | detach"),
    ("filesystem_manage", "op: read_text | write_text | search. Do not read snapshot JSON."),
    ("project_manage", "op: settings_get | settings_set | stop"),
    ("editor_manage", "op: state | selection_get | quit"),
    ("session_manage", "op: list"),
    ("api_manage", "op: get — forwards GET /ref?q="),
    ("runtime_observe", "Batch-run the game, write a full scene_graph snapshot file, return a summary. Default does not screenshot."),
    ("runtime_snapshot_query", "Read a precise slice from a snapshot file. Engine may already be dead."),
    ("runtime_get_hierarchy", "Runtime tree from the latest snapshot file (not the authoring collection)."),
    ("runtime_get_properties", "One runtime GO/component from the latest snapshot file."),
    ("runtime_state", "Latest snapshot handle and last known engine service url."),
]

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "runtime_observe": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "frames": {"type": "integer", "default": 30},
            "inline": {"type": "string", "enum": ["summary", "preview", "full"], "default": "summary"},
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["screenshot"]},
                "description": "Optional extras. Screenshot is not default.",
            },
            "dest": {"type": "string", "description": "Optional PNG path when include contains screenshot."},
            "no_build": {"type": "boolean", "default": False},
            "debug_collisions": {"type": "boolean", "default": False},
        },
    },
    "runtime_snapshot_query": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "op": {
                "type": "string",
                "enum": ["list", "summary", "list_ids", "get_node", "get_subtree", "find", "get_path"],
                "default": "summary",
            },
            "snapshot": {"type": "string", "default": "latest", "description": "latest, snapshot id, or path."},
            "id": {"type": "string"},
            "component": {"type": "string"},
            "path": {"type": "string", "description": "JSON Pointer for get_path, e.g. /scene_graph/children/0/id."},
            "type": {"type": "string"},
            "id_glob": {"type": "string"},
            "has_property": {"type": "string"},
            "depth": {"type": "integer"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
    },
    "runtime_get_hierarchy": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "snapshot": {"type": "string", "default": "latest"},
            "id": {"type": "string"},
            "depth": {"type": "integer", "default": 8},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 200},
        },
    },
    "runtime_get_properties": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": {"type": "string"},
            "component": {"type": "string"},
            "snapshot": {"type": "string", "default": "latest"},
        },
    },
    "runtime_state": {"type": "object", "additionalProperties": False, "properties": {}},
}


def _tool_schema(name: str, description: str) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": TOOL_SCHEMAS.get(
            name,
            {"type": "object", "additionalProperties": True},
        ),
    }


def write_message(payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def read_message() -> Optional[Dict[str, Any]]:
    buf = sys.stdin.buffer
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        chunk = buf.read(1)
        if not chunk:
            return None
        header += chunk
    length = 0
    for line in header.decode("ascii", errors="replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length <= 0:
        return None
    body = buf.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def handle_rpc(message: Dict[str, Any], project: Path, timeout: float) -> Optional[Dict[str, Any]]:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "defold-agent", "version": "1.13.1"},
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": [_tool_schema(name, description) for name, description in TOOLS]},
        }
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        result = dispatch_command(project, name, arguments, timeout)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": result.get("status") == "error",
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def serve_stdio(project: Path, timeout: float) -> int:
    while True:
        message = read_message()
        if message is None:
            return 0
        response = handle_rpc(message, project, timeout)
        if response is not None:
            write_message(response)
