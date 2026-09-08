# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""stdio MCP adapter for the first-party Defold agent CLI. No game plugin."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from agent_ops import dispatch_command

_EXCLUDE_DOMAINS: List[str] = []


PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
PROTOCOL_VERSION = PROTOCOL_VERSIONS[0]
INSTRUCTIONS = (
    "First-party Defold stdio MCP. Use tools and defold:// resources. "
    "Observe with runtime_observe then runtime_snapshot_query. "
    "Do not read snapshot JSON via filesystem_manage. "
    "Do not configure an HTTP MCP URL."
)

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
    ("batch_execute", "Run commands[]. Editor closed: one disk rollback (atomic=true). Editor open: sequential graph edits (atomic=false)."),
    ("collection_manage", "op: create | add_instance | remove_instance | get_roots"),
    ("gameobject_manage", "op: delete | rename | set_property | find"),
    ("component_manage", "op: remove | set_property"),
    ("script_manage", "op: read | detach"),
    ("filesystem_manage", "op: read_text | write_text | list | exists | mkdir | copy | move | delete | search. Do not read or delete snapshot JSON."),
    ("project_manage", "op: settings_get | settings_set | stop"),
    ("editor_manage", "op: state | selection_get | quit | mcp_config"),
    ("session_manage", "op: list"),
    ("api_manage", "op: get — editor GET /ref, or engine /*# docs when the editor is closed."),
    ("runtime_observe", "Write a scene_graph snapshot file and return a summary. Uses the live engine if project_run is up; otherwise a batch run. Default does not screenshot."),
    ("runtime_snapshot_query", "Read a precise slice from a snapshot file. Engine may already be dead."),
    ("runtime_get_hierarchy", "Runtime tree from the latest snapshot file (not the authoring collection)."),
    ("runtime_get_properties", "One runtime GO/component from the latest snapshot file."),
    ("runtime_state", "Latest snapshot handle and whether the CLI-owned live engine is alive."),
    ("runtime_diff", "Compare two snapshot files (default previous vs latest). Not HTTP."),
    ("runtime_screenshot", "Optional live PNG via screenshot.request files. Not core. Not HTTP."),
    ("project_run", "Start dmengine. mode=live keeps it running and dumps via control files, not HTTP."),
    ("atlas_manage", "Create/get/list atlas images and animations. Disk when the editor is closed."),
    ("tilemap_manage", "Create/get/list tilemaps, layers, tile_set, set_tile / get_tile."),
    ("gui_manage", "Create/get/list gui scenes. add_box/add_text, set_node/get_node."),
    ("input_binding_manage", "Create/get/list key/mouse/gamepad/touch bindings."),
    ("particlefx_manage", "Create/get/list particlefx emitters."),
    ("material_manage", "Create/get/list materials and set programs."),
    ("camera_manage", "Add/get/remove an embedded camera on a .go."),
    ("render_manage", "Create/get/list .render files and set the render script."),
    ("tilesource_manage", "Create/get/list tilesources, images, and animations."),
    ("font_manage", "Create/get/list .font files and set the TTF."),
    ("sound_manage", "Create/get/list .sound files and set the sample."),
    ("gamepads_manage", "Create/get/list .gamepads driver maps."),
    ("display_profiles_manage", "Create/get/list display profile files."),
    ("model_manage", "Create/get/list .model files and set the mesh."),
    ("factory_manage", "Create/get/list game object factories."),
    ("collectionfactory_manage", "Create/get/list collection factories."),
    ("collectionproxy_manage", "Create/get/list collection proxies."),
    ("collisionobject_manage", "Create/get/list collision object files."),
    ("cubemap_manage", "Create/get/list cubemap face images."),
    ("mesh_manage", "Create/get/list .mesh files."),
    ("texture_profiles_manage", "Create/get/list texture profile files."),
    ("compute_manage", "Create/get/list compute shader program files."),
    ("appmanifest_manage", "Create/get/list app manifest files."),
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
            "nested": {"type": "boolean", "description": "If true, children are nested under parents. Default is a flat list with parent ids."},
            "tree": {"type": "boolean"},
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
            "op": {
                "type": "string",
                "enum": ["create", "get", "list", "remove", "set_property", "add_layer", "set_tile_set", "set_tile", "get_tile"],
            },
            "path": {"type": "string"},
            "id": {"type": "string"},
            "layer": {"type": "string"},
            "tile_set": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "tile": {"type": "integer"},
            "h_flip": {"type": "integer"},
            "v_flip": {"type": "integer"},
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
                "enum": [
                    "create",
                    "get",
                    "list",
                    "remove",
                    "set_property",
                    "add_box",
                    "add_text",
                    "add_texture",
                    "add_font",
                    "set_script",
                    "set_node",
                    "get_node",
                ],
            },
            "path": {"type": "string"},
            "id": {"type": "string"},
            "node": {"type": "string"},
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
    "tilesource_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {
                "type": "string",
                "enum": ["create", "get", "list", "remove", "set_property", "add_animation", "set_image"],
            },
            "path": {"type": "string"},
            "id": {"type": "string"},
            "image": {"type": "string"},
            "name": {"type": "string"},
            "start_tile": {"type": "integer"},
            "end_tile": {"type": "integer"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "font_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property", "set_font"]},
            "path": {"type": "string"},
            "font": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "sound_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property", "set_sound"]},
            "path": {"type": "string"},
            "sound": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "gamepads_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "display_profiles_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "model_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "mesh": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "factory_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "prototype": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "collectionfactory_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "prototype": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "collectionproxy_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "collection": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "collisionobject_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "collision_shape": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "cubemap_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "mesh_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "texture_profiles_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "compute_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "appmanifest_manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": ["create", "get", "list", "remove", "set_property"]},
            "path": {"type": "string"},
            "name": {"type": "string"},
            "content": {"type": "string"},
            "property": {"type": "string"},
            "value": {},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
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
            "rotation": {
                "description": "Quaternion [x,y,z,w] or z degrees.",
            },
            "scale": {
                "description": "Uniform number or [x,y,z].",
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
            "tile_set": {"type": "string"},
            "atlas": {"type": "string"},
            "animation": {"type": "string"},
            "default_animation": {"type": "string"},
            "text": {"type": "string"},
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
            "source": {"type": "string", "enum": ["all", "editor", "engine"], "default": "all"},
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
            "op": {
                "type": "string",
                "enum": ["read_text", "write_text", "list", "exists", "mkdir", "copy", "move", "delete", "search"],
            },
            "path": {"type": "string"},
            "dest": {"type": "string"},
            "text": {"type": "string"},
            "query": {"type": "string"},
            "ext": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
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
        "properties": {
            "op": {"type": "string", "enum": ["state", "selection_get", "quit", "mcp_config"]},
            "format": {"type": "string", "enum": ["cursor", "codex"], "default": "cursor"},
        },
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


RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "defold://sessions",
        "name": "sessions",
        "description": "Connected editor / CLI sessions.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://editor/state",
        "name": "editor-state",
        "description": "Authoring editor or disk snapshot.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://collection/current",
        "name": "current-collection",
        "description": "Active or bootstrap collection path.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://collection/hierarchy{?path}",
        "name": "collection-hierarchy",
        "description": "Authoring collection tree. Query path=/main/main.collection",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://gameobject/{id}/properties{?collection}",
        "name": "gameobject-properties",
        "description": "Authoring GO properties.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://script/{+path}",
        "name": "script",
        "description": "Lua script text. Path is project-relative without a leading defold host.",
        "mimeType": "text/plain",
    },
    {
        "uriTemplate": "defold://project/info",
        "name": "project-info",
        "description": "Doctor + game.project readiness.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://project/logs",
        "name": "project-logs",
        "description": "Editor console or engine.log tail, plus parsed issues.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://project/mcp-config",
        "name": "mcp-config",
        "description": "Cursor/Codex stdio command+args. Never an HTTP URL.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://ref/{query}",
        "name": "api-ref",
        "description": "Lua/API reference search.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "defold://runtime/snapshot/{id}",
        "name": "runtime-snapshot",
        "description": "Snapshot handle. Prefer runtime_snapshot_query over raw JSON.",
        "mimeType": "application/json",
    },
]

PROMPTS = [
    {
        "name": "defold-doctor",
        "description": "Check bob, dmengine, editor, and project readiness.",
        "arguments": [],
    },
    {
        "name": "defold-observe",
        "description": "Batch or live observe, then query a node.",
        "arguments": [
            {"name": "id", "description": "Game object id to query after observe.", "required": False},
            {"name": "frames", "description": "Batch frame count if no live engine.", "required": False},
        ],
    },
    {
        "name": "defold-live",
        "description": "Start a live engine, observe via control files, stop.",
        "arguments": [{"name": "id", "description": "Game object id.", "required": False}],
    },
    {
        "name": "defold-loop",
        "description": "check then observe. No screenshot unless asked.",
        "arguments": [{"name": "frames", "required": False}],
    },
    {
        "name": "defold-author",
        "description": "Author on disk without the editor: collection, GO parent/transform, tile, GUI, API docs.",
        "arguments": [
            {"name": "collection", "description": "Collection path.", "required": False},
            {"name": "id", "description": "Game object id.", "required": False},
        ],
    },
    {
        "name": "defold-check",
        "description": "Compile only, then read logs/issues. Never launch.",
        "arguments": [],
    },
]


def normalize_domains(raw: Any) -> List[str]:
    if raw is None:
        raw = os.environ.get("DEFOLD_MCP_EXCLUDE_DOMAINS", "")
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


def configure_mcp(exclude_domains: Optional[Sequence[str]] = None) -> None:
    global _EXCLUDE_DOMAINS
    _EXCLUDE_DOMAINS = normalize_domains(exclude_domains)


def tool_excluded(name: str) -> bool:
    for domain in _EXCLUDE_DOMAINS:
        if name == domain or name.startswith(f"{domain}_"):
            return True
    return False


def listed_tools() -> List[Tuple[str, str]]:
    return [(name, description) for name, description in TOOLS if not tool_excluded(name)]


def _tool_schema(name: str, description: str) -> Dict[str, Any]:
    schema = TOOL_SCHEMAS.get(name)
    if schema is None:
        raise KeyError(f"Missing closed MCP schema for {name}")
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
    }


def _json_resource(uri: str, payload: Any) -> Dict[str, Any]:
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ]
    }


def list_mcp_resources(project: Path) -> List[Dict[str, Any]]:
    from agent_runtime import list_snapshot_records

    resources = [
        {
            "uri": "defold://sessions",
            "name": "sessions",
            "mimeType": "application/json",
            "description": "Editor / CLI sessions.",
        },
        {
            "uri": "defold://editor/state",
            "name": "editor-state",
            "mimeType": "application/json",
            "description": "Authoring state.",
        },
        {
            "uri": "defold://collection/current",
            "name": "current-collection",
            "mimeType": "application/json",
            "description": "Active or bootstrap collection.",
        },
        {
            "uri": "defold://project/info",
            "name": "project-info",
            "mimeType": "application/json",
            "description": "Doctor and game.project.",
        },
        {
            "uri": "defold://project/logs",
            "name": "project-logs",
            "mimeType": "application/json",
            "description": "Console or engine.log tail.",
        },
        {
            "uri": "defold://project/mcp-config",
            "name": "mcp-config",
            "mimeType": "application/json",
            "description": "stdio MCP client snippet. Never an HTTP URL.",
        },
    ]
    for path in sorted(project.rglob("*.collection")):
        if any(part in {".internal", "build", ".git", ".editor"} for part in path.parts):
            continue
        rel = "/" + path.relative_to(project).as_posix()
        resources.append(
            {
                "uri": f"defold://collection/hierarchy?path={rel}",
                "name": rel,
                "mimeType": "application/json",
                "description": "Authoring hierarchy for this collection.",
            }
        )
        if len(resources) >= 28:
            break
    for record in list_snapshot_records(project):
        resources.append(
            {
                "uri": f"defold://runtime/snapshot/{record['id']}",
                "name": record["id"],
                "mimeType": "application/json",
                "description": "Snapshot handle. Use runtime_snapshot_query; do not read_text the JSON.",
            }
        )
    return resources


def _uri_parts(uri: str) -> Tuple[str, List[str], Dict[str, str]]:
    parsed = urlparse(uri)
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    host = unquote(parsed.netloc or "")
    path = unquote(parsed.path or "").strip("/")
    segments = [part for part in ([host] if host else []) + (path.split("/") if path else []) if part]
    return host, segments, query


def read_mcp_resource(project: Path, uri: str, timeout: float) -> Dict[str, Any]:
    from agent_runtime import query_snapshot

    if not uri.startswith("defold://"):
        raise ValueError("URI must start with defold://")
    _host, segments, query = _uri_parts(uri)
    if uri.startswith("defold://runtime/snapshot/") or (segments[:2] == ["runtime", "snapshot"] and len(segments) >= 3):
        snap_id = uri.split("defold://runtime/snapshot/", 1)[-1]
        return query_snapshot(project, {"op": "summary", "snapshot": snap_id})
    if segments[:1] == ["sessions"] or uri.rstrip("/") == "defold://sessions":
        return dispatch_command(project, "session_manage", {"op": "list"}, timeout)
    if segments[:2] == ["editor", "state"] or uri.rstrip("/") == "defold://editor/state":
        return dispatch_command(project, "editor_state", {}, timeout)
    if segments[:2] == ["collection", "current"]:
        state = dispatch_command(project, "editor_state", {}, timeout)
        data = state.get("data") or {}
        return {
            "status": state.get("status"),
            "readiness": state.get("readiness"),
            "data": {
                "path": data.get("active_resource") or data.get("main_collection"),
                "main_collection": data.get("main_collection"),
                "active_resource": data.get("active_resource"),
                "source": data.get("source"),
            },
        }
    if segments[:2] == ["collection", "hierarchy"]:
        path = query.get("path") or query.get("collection")
        if not path and len(segments) > 2:
            path = "/" + "/".join(segments[2:])
        return dispatch_command(project, "collection_get_hierarchy", {"path": path}, timeout)
    if segments[:1] == ["gameobject"] and "properties" in segments:
        go_id = query.get("id") or (segments[1] if len(segments) > 1 and segments[1] != "properties" else None)
        collection = query.get("collection") or query.get("path")
        return dispatch_command(
            project,
            "gameobject_get_properties",
            {"id": go_id, "collection": collection, "path": collection},
            timeout,
        )
    if segments[:1] == ["script"]:
        path = query.get("path")
        if not path:
            path = "/" + "/".join(segments[1:])
        if not path.startswith("/"):
            path = "/" + path
        return dispatch_command(project, "script_manage", {"op": "read", "path": path}, timeout)
    if segments[:2] == ["project", "info"] or uri.rstrip("/") == "defold://project/info":
        return dispatch_command(project, "project_doctor", {}, timeout)
    if segments[:2] == ["project", "logs"] or uri.rstrip("/") == "defold://project/logs":
        return dispatch_command(project, "logs_read", {"limit": 80}, timeout)
    if segments[:2] == ["project", "mcp-config"] or uri.rstrip("/") == "defold://project/mcp-config":
        return dispatch_command(project, "editor_manage", {"op": "mcp_config"}, timeout)
    if segments[:1] == ["ref"]:
        q = query.get("q") or query.get("query") or "/".join(segments[1:])
        return dispatch_command(project, "api_manage", {"op": "get", "q": q}, timeout)
    raise ValueError(f"Unknown resource: {uri}")


def prompt_messages(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    go_id = arguments.get("id") or "cube"
    frames = arguments.get("frames") or "30"
    collection = arguments.get("collection") or "/main/main.collection"
    texts = {
        "defold-doctor": "Call project_doctor. Report bob, dmengine, editor, and game.project readiness. Do not use an HTTP MCP URL.",
        "defold-observe": (
            f"Call runtime_observe (inline=summary, no screenshot). Then runtime_snapshot_query op=get_node id={go_id}. "
            f"If no live engine, batch frames={frames}. Do not read the snapshot file as text."
        ),
        "defold-live": (
            f"Call project_run mode=live, then runtime_observe, then runtime_snapshot_query op=get_node id={go_id}, "
            "then project_stop. Observation is file handshake, not HTTP."
        ),
        "defold-loop": (
            f"Call project_check / project_build (check only), then runtime_observe frames={frames}. "
            "Do not screenshot unless the user asks how it looks."
        ),
        "defold-author": (
            f"Edit {collection} on disk. Create or parent id={go_id} with gameobject_create "
            "(parent/position/rotation/scale work without the editor). Use tilemap_manage set_tile and "
            "gui_manage set_node for tiles and HUD. Look up Lua with api_manage. "
            "batch_execute is atomic on disk. Do not use an HTTP MCP URL. Do not screenshot."
        ),
        "defold-check": (
            "Call project_check (launched=false). Then logs_read source=all. "
            "Fix issues[].resource using script_patch. Do not run or screenshot."
        ),
    }
    if name not in texts:
        raise KeyError(name)
    return {
        "description": next(item["description"] for item in PROMPTS if item["name"] == name),
        "messages": [{"role": "user", "content": {"type": "text", "text": texts[name]}}],
    }


def mcp_client_config(
    agent_py: Path,
    project: Optional[Path],
    kind: str,
    exclude_domains: Optional[Sequence[str]] = None,
) -> str:
    args = [str(agent_py.resolve()), "mcp"]
    if project:
        args.extend(["--project", str(project.resolve())])
    excluded = normalize_domains(exclude_domains) if exclude_domains is not None else []
    if excluded:
        args.extend(["--exclude-domains", ",".join(excluded)])
    if kind == "codex":
        quoted = ", ".join(json.dumps(item) for item in args)
        return (
            "[mcp_servers.\"defold-agent\"]\n"
            "command = \"python\"\n"
            f"args = [{quoted}]\n"
            "enabled = true\n"
            "startup_timeout_sec = 60\n"
            "tool_timeout_sec = 360\n"
        )
    return json.dumps(
        {"mcpServers": {"defold-agent": {"command": "python", "args": args}}},
        indent=2,
    ) + "\n"


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
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "defold-agent", "version": "1.13.1"},
                "instructions": INSTRUCTIONS,
            },
        }
    if method == "notifications/initialized" or method == "initialized" or method == "notifications/cancelled":
        return None
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": list_mcp_resources(project)}}
    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resourceTemplates": RESOURCE_TEMPLATES}}
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        try:
            payload = read_mcp_resource(project, uri, timeout)
        except ValueError as error:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": str(error)}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": _json_resource(uri, payload)}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": PROMPTS}}
    if method == "prompts/get":
        name = params.get("name")
        try:
            result = prompt_messages(str(name), params.get("arguments") or {})
        except KeyError:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": f"Unknown prompt: {name}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method == "logging/setLevel":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": [_tool_schema(name, description) for name, description in listed_tools()]},
        }
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if tool_excluded(str(name)):
            result = {
                "status": "error",
                "readiness": "ready",
                "error": {
                    "code": "NOT_ALLOWED",
                    "message": f"Tool '{name}' is excluded by --exclude-domains",
                },
            }
        else:
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


def serve_stdio(project: Path, timeout: float, exclude_domains: Optional[Sequence[str]] = None) -> int:
    configure_mcp(exclude_domains)
    while True:
        message = read_message()
        if message is None:
            return 0
        response = handle_rpc(message, project, timeout)
        if response is not None:
            write_message(response)
