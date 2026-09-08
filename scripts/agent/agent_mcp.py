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
    ("session_activate", "Pin a runtime target url, or no-op when the editor is closed."),
    ("project_doctor", "Report bob / dmengine / editor / java readiness. Same as CLI doctor."),
    ("collection_open", "Resolve and open a collection in the editor."),
    ("collection_save", "Write the collection save data to disk."),
    ("gameobject_create", "Add an embedded or referenced game object. params: collection, id, position, optional path."),
    ("component_add", "Add a component. params: collection, id, type or path."),
    ("script_create", "Create a Lua script from the editor template."),
    ("script_attach", "Attach a .script to a game object."),
    ("script_patch", "Replace a unique old_text with new_text."),
    ("project_build", "Compile only (bob or editor check). Does not launch the game. Reply always has launched=false."),
    ("project_check", "Alias of project_build: compile only, never launch."),
    ("logs_read", "Read GET /console when the editor is open, or the last engine log."),
    ("editor_preview", "Authoring preview PNG via GET /preview/{path}. Not a runtime screenshot."),
    ("batch_execute", "Run commands[] sequentially. Not one undo. Reply has atomic=false."),
    ("collection_manage", "op: create | add_instance | remove_instance | get_roots"),
    ("gameobject_manage", "op: delete | rename | set_property | find"),
    ("component_manage", "op: remove | set_property"),
    ("script_manage", "op: read | detach"),
    ("filesystem_manage", "op: read_text | write_text | search. Do not read snapshot JSON."),
    ("project_manage", "op: settings_get | settings_set | stop"),
    ("editor_manage", "op: state | selection_get | quit"),
    ("session_manage", "op: list"),
    ("api_manage", "op: get — forwards GET /ref?q="),
    ("runtime_observe", "Write a scene_graph snapshot file and return a summary. Uses the live engine if project_run is up; otherwise a batch run. Default does not screenshot."),
    ("runtime_snapshot_query", "Read a precise slice from a snapshot file. Engine may already be dead."),
    ("runtime_get_hierarchy", "Runtime tree from the latest snapshot file (not the authoring collection)."),
    ("runtime_get_properties", "One runtime GO/component from the latest snapshot file."),
    ("runtime_state", "Latest snapshot handle and whether the CLI-owned live engine is alive."),
    ("runtime_diff", "Compare two snapshot files (default previous vs latest). Not HTTP."),
    ("runtime_screenshot", "Optional live PNG via screenshot.request files. Not core. Not HTTP."),
    ("project_run", "Start dmengine. mode=live keeps it running and dumps via control files, not HTTP."),
    ("atlas_manage", "Create/get/list atlas images and animations. Disk when the editor is closed."),
    ("tilemap_manage", "Create/get/list tilemaps, layers, tile_set."),
    ("gui_manage", "Create/get/list gui nodes, textures, fonts, script."),
    ("input_binding_manage", "Create/get/list key/mouse/gamepad/touch bindings."),
    ("particlefx_manage", "Create/get/list particlefx emitters."),
    ("material_manage", "Create/get/list materials and set programs."),
    ("camera_manage", "Add/get/remove an embedded camera on a .go."),
    ("render_manage", "Create/get/list .render files and set the render script."),
    ("project_stop", "Stop the CLI-owned live dmengine."),
]

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "runtime_observe": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "frames": {"type": "integer", "default": 30, "description": "Batch only. Ignored when a live engine is running."},
            "inline": {"type": "string", "enum": ["summary", "preview", "full"], "default": "summary"},
            "mode": {"type": "string", "enum": ["live", "batch"], "description": "Force batch even if a live engine is running."},
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
            "snapshot": {
                "type": "string",
                "default": "latest",
                "description": "latest, snapshot id, path, or live to refresh via file handshake.",
            },
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
            "snapshot": {
                "type": "string",
                "default": "latest",
                "description": "latest, snapshot id, path, or live to refresh via file handshake.",
            },
        },
    },
    "runtime_state": {"type": "object", "additionalProperties": False, "properties": {}},
    "runtime_diff": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "a": {"type": "string", "default": "previous", "description": "Older snapshot id, path, or previous."},
            "b": {"type": "string", "default": "latest", "description": "Newer snapshot id, path, or latest."},
            "limit": {"type": "integer", "default": 80},
        },
    },
    "project_run": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mode": {"type": "string", "enum": ["live", "batch"], "default": "live"},
            "no_build": {"type": "boolean", "default": False},
            "frames": {"type": "integer", "default": 30, "description": "Batch only."},
        },
    },
    "project_stop": {"type": "object", "additionalProperties": False, "properties": {}},
    "editor_state": {"type": "object", "additionalProperties": False, "properties": {}},
    "collection_get_hierarchy": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Project path, e.g. /main/main.collection."},
            "collection": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 200},
        },
    },
    "gameobject_get_properties": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "collection": {"type": "string"},
            "path": {"type": "string"},
            "id": {"type": "string"},
            "component": {"type": "string"},
        },
    },
    "session_activate": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "Pin a discovered runtime target. Omit for the editor no-op."},
        },
    },
    "project_doctor": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"bob": {"type": "string"}, "engine": {"type": "string"}},
    },
    "runtime_screenshot": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"dest": {"type": "string", "description": "PNG path. Default latest.png in the snapshot dir."}},
    },
    "atlas_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {
                "type": "string",
                "enum": ["create", "get", "list", "remove", "set_property", "add_image", "add_animation"],
            },
            "path": {"type": "string"},
            "id": {"type": "string"},
            "image": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "tilemap_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property", "add_layer", "set_tile_set"]},
            "path": {"type": "string"},
            "id": {"type": "string"},
            "tile_set": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "gui_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {
                "type": "string",
                "enum": ["create", "get", "list", "remove", "set_property", "add_box", "add_text", "add_texture", "add_font", "set_script"],
            },
            "path": {"type": "string"},
            "id": {"type": "string"},
            "texture": {"type": "string"},
            "font": {"type": "string"},
            "name": {"type": "string"},
            "text": {"type": "string"},
            "script": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
        },
    },
    "input_binding_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {
                "type": "string",
                "enum": ["create", "get", "list", "remove", "set_property", "add_key", "add_mouse", "add_gamepad", "add_touch"],
            },
            "path": {"type": "string"},
            "input": {"type": "string"},
            "action": {"type": "string"},
            "id": {"type": "string"},
        },
    },
    "particlefx_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property", "add_emitter"]},
            "path": {"type": "string"},
            "id": {"type": "string"},
        },
    },
    "material_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property", "set_program"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "vertex_program": {"type": "string"},
            "fragment_program": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
        },
    },
    "camera_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["add", "get", "remove", "set_property"]},
            "path": {"type": "string"},
            "collection": {"type": "string"},
            "id": {"type": "string"},
            "component": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
        },
    },
    "render_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property", "set_script"]},
            "path": {"type": "string"},
            "script": {"type": "string"},
        },
    },
    "collection_open": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"path": {"type": "string"}, "collection": {"type": "string"}},
    },
    "collection_save": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"path": {"type": "string"}, "collection": {"type": "string"}},
    },
    "gameobject_create": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "collection": {"type": "string"},
            "path": {"type": "string", "description": "Referenced .go path, or collection when used as alias."},
            "id": {"type": "string"},
            "parent": {"type": "string"},
            "position": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 3,
            },
        },
    },
    "component_add": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "collection": {"type": "string"},
            "id": {"type": "string"},
            "type": {"type": "string"},
            "component_type": {"type": "string"},
            "path": {"type": "string"},
        },
    },
    "script_create": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "name": {"type": "string"},
            "content": {"type": "string"},
        },
    },
    "script_attach": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "path"],
        "properties": {
            "collection": {"type": "string"},
            "id": {"type": "string"},
            "path": {"type": "string"},
        },
    },
    "script_patch": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "old_text", "new_text"],
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
    },
    "project_build": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"bob": {"type": "string", "description": "Optional bob.jar path."}},
    },
    "project_check": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"bob": {"type": "string"}},
    },
    "logs_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "offset": {"type": "integer", "default": 0, "description": "Skip this many lines from the end."},
            "limit": {"type": "integer", "default": 200},
        },
    },
    "editor_preview": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string"},
            "resource": {"type": "string"},
            "dest": {"type": "string"},
            "width": {"type": "integer", "default": 1280},
            "height": {"type": "integer", "default": 720},
        },
    },
    "batch_execute": {
        "type": "object",
        "additionalProperties": False,
        "required": ["commands"],
        "properties": {
            "commands": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string"},
                        "params": {"type": "object"},
                    },
                },
            },
        },
    },
    "collection_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "add_instance", "remove_instance", "get_roots"]},
            "path": {"type": "string"},
            "collection": {"type": "string"},
            "name": {"type": "string"},
            "id": {"type": "string"},
        },
    },
    "gameobject_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["delete", "rename", "set_property", "find"]},
            "collection": {"type": "string"},
            "id": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
        },
    },
    "component_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", "id", "component"],
        "properties": {
            "op": {"type": "string", "enum": ["remove", "set_property"]},
            "collection": {"type": "string"},
            "id": {"type": "string"},
            "component": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
        },
    },
    "script_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["read", "detach"]},
            "path": {"type": "string"},
            "collection": {"type": "string"},
            "id": {"type": "string"},
            "component": {"type": "string"},
        },
    },
    "filesystem_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["read_text", "write_text", "search"]},
            "path": {"type": "string"},
            "text": {"type": "string"},
            "query": {"type": "string"},
            "ext": {"type": "string"},
        },
    },
    "project_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["settings_get", "settings_set", "stop"]},
            "key": {"type": "string"},
            "path": {},
            "value": {},
        },
    },
    "editor_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {"op": {"type": "string", "enum": ["state", "selection_get", "quit"]}},
    },
    "session_manage": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"op": {"type": "string", "enum": ["list"], "default": "list"}},
    },
    "api_manage": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "op": {"type": "string", "enum": ["get"], "default": "get"},
            "q": {"type": "string"},
            "query": {"type": "string"},
            "environment": {"type": "string", "default": "runtime"},
            "language": {"type": "string", "default": "Lua"},
        },
    },
}


def _tool_schema(name: str, description: str) -> Dict[str, Any]:
    schema = TOOL_SCHEMAS.get(name)
    if schema is None:
        raise KeyError(f"Missing closed MCP schema for {name}")
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
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
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "defold-agent", "version": "1.13.1"},
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "resources/list":
        from agent_runtime import list_snapshot_records

        resources = [
            {
                "uri": f"defold://runtime/snapshot/{record['id']}",
                "name": record["id"],
                "mimeType": "application/json",
                "description": "Snapshot file handle. Use runtime_snapshot_query; do not read_text the JSON.",
            }
            for record in list_snapshot_records(project)
        ]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": resources}}
    if method == "resources/read":
        from agent_runtime import query_snapshot

        uri = str(params.get("uri") or "")
        prefix = "defold://runtime/snapshot/"
        if not uri.startswith(prefix):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Unknown resource. Use defold://runtime/snapshot/{id}."},
            }
        summary = query_snapshot(project, {"op": "summary", "snapshot": uri[len(prefix) :]})
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(summary, ensure_ascii=False),
                    }
                ]
            },
        }
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
