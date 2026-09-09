# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Disk fallbacks for atlas / tilemap / tilesource / font / sound / gui / input / particlefx / material / camera / render."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_ops import (
    error_envelope,
    match_brace,
    note_file_write,
    ok_envelope,
    project_file,
    sanitize_proj_path,
    set_position_in_block,
)


SKIP_DIRS = {".internal", "build", ".git", ".editor"}

DOMAIN_EXT = {
    "atlas_manage": "atlas",
    "tilemap_manage": "tilemap",
    "tilesource_manage": "tilesource",
    "font_manage": "font",
    "sound_manage": "sound",
    "gui_manage": "gui",
    "particlefx_manage": "particlefx",
    "material_manage": "material",
    "input_binding_manage": "input_binding",
    "render_manage": "render",
    "gamepads_manage": "gamepads",
    "display_profiles_manage": "display_profiles",
    "model_manage": "model",
    "factory_manage": "factory",
    "collectionfactory_manage": "collectionfactory",
    "collectionproxy_manage": "collectionproxy",
    "collisionobject_manage": "collisionobject",
    "cubemap_manage": "cubemap",
    "mesh_manage": "mesh",
    "texture_profiles_manage": "texture_profiles",
    "compute_manage": "compute",
    "appmanifest_manage": "appmanifest",
}

FALLBACK_TEMPLATES = {
    "atlas": "extrude_borders: 2\n",
    "tilemap": (
        'tile_set: "{tile_set}"\n'
        "layers {{\n"
        '  id: "layer1"\n'
        "  z: 0.0\n"
        "  is_visible: 1\n"
        "}}\n"
        'material: "/builtins/materials/tile_map.material"\n'
    ),
    "gui": (
        'script: ""\n'
        "adjust_reference: ADJUST_REFERENCE_PARENT\n"
        "background_color {\n"
        "  x: 1.0\n"
        "  y: 1.0\n"
        "  z: 1.0\n"
        "  w: 1.0\n"
        "}\n"
    ),
    "particlefx": (
        "emitters {\n"
        '  id: "emitter"\n'
        "  mode: PLAY_MODE_LOOP\n"
        "  duration: 1.0\n"
        "  space: EMISSION_SPACE_WORLD\n"
        '  tile_source: "/builtins/graphics/particle_blob.tilesource"\n'
        '  animation: "anim"\n'
        '  material: "/builtins/materials/particlefx.material"\n'
        "  blend_mode: BLEND_MODE_ADD\n"
        "  max_particle_count: 128\n"
        "  type: EMITTER_TYPE_2DCONE\n"
        "}\n"
    ),
    "material": (
        'name: "{name}"\n'
        'vertex_program: ""\n'
        'fragment_program: ""\n'
        "vertex_space: VERTEX_SPACE_WORLD\n"
        "max_page_count: 0\n"
    ),
    "render": 'script: ""\n',
    "input_binding": "",
    "tilesource": (
        'image: "{image}"\n'
        "tile_width: 16\n"
        "tile_height: 16\n"
        "tile_margin: 0\n"
        "tile_spacing: 0\n"
        'collision: ""\n'
        'material_tag: "tile"\n'
        'collision_groups: "default"\n'
        "animations {\n"
        '  id: "anim"\n'
        "  start_tile: 1\n"
        "  end_tile: 1\n"
        "}\n"
        "extrude_borders: 2\n"
        "sprite_trim_mode: SPRITE_TRIM_MODE_OFF\n"
    ),
    "font": (
        'font: "{font}"\n'
        'material: "/builtins/fonts/font.material"\n'
        "size: 15\n"
    ),
    "sound": 'sound: "{sound}"\nlooping: 0\ngroup: "master"\ngain: 1.0\n',
    "gamepads": "driver {\n    device: \"Controller\"\n    platform: \"windows\"\n    dead_zone: 0.2\n}\n",
    "display_profiles": (
        "profiles {\n"
        '  name: "Landscape"\n'
        "  qualifiers {\n"
        "    width: 1280\n"
        "    height: 720\n"
        "  }\n"
        "}\n"
    ),
    "model": 'mesh: "{mesh}"\nname: "{name}"\n',
    "factory": 'prototype: "{prototype}"\n',
    "collectionfactory": 'prototype: "{prototype}"\n',
    "collectionproxy": 'collection: "{collection}"\n',
    "collisionobject": (
        'collision_shape: "{collision_shape}"\n'
        "type: COLLISION_OBJECT_TYPE_DYNAMIC\n"
        "mass: 1.0\n"
        "friction: 0.1\n"
        "restitution: 0.5\n"
        'group: "default"\n'
        'mask: "default"\n'
    ),
    "cubemap": 'right: ""\nleft: ""\ntop: ""\nbottom: ""\nfront: ""\nback: ""\n',
    "mesh": 'material: "/builtins/materials/model_lit.material"\nvertices: ""\n',
    "texture_profiles": (
        "path_settings {\n"
        '  path: "**"\n'
        '  profile: "Default"\n'
        "}\n"
        "profiles {\n"
        '  name: "Default"\n'
        "}\n"
    ),
    "compute": 'compute_program: ""\n',
    "appmanifest": "# App manifest\n",
}

CAMERA_BLOCK = """
embedded_components {{
  id: "{id}"
  type: "camera"
  data: "aspect_ratio: 1.0\\n"
  "fov: 0.785\\n"
  "near_z: 0.1\\n"
  "far_z: 1000.0\\n"
  ""
}}
"""


def _stem(path: str) -> str:
    return Path(path).stem


def load_template(ext: str, name: str, extras: Optional[Dict[str, str]] = None) -> str:
    extras = extras or {}
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "editor" / "resources" / "templates" / f"template.{ext}"
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            return (
                text.replace("{{NAME}}", name)
                .replace("{name}", name)
                .replace("{tile_set}", extras.get("tile_set", ""))
                .replace("{image}", extras.get("image", ""))
                .replace("{font}", extras.get("font", "/builtins/fonts/vera_mo_bd.ttf"))
                .replace("{sound}", extras.get("sound", ""))
                .replace("{mesh}", extras.get("mesh", ""))
                .replace("{prototype}", extras.get("prototype", ""))
                .replace("{collection}", extras.get("collection", extras.get("tile_set", "")))
                .replace("{collision_shape}", extras.get("collision_shape", ""))
            )
    text = FALLBACK_TEMPLATES[ext]
    return text.format(
        name=name,
        tile_set=extras.get("tile_set", ""),
        image=extras.get("image", ""),
        font=extras.get("font", "/builtins/fonts/vera_mo_bd.ttf"),
        sound=extras.get("sound", ""),
        mesh=extras.get("mesh", ""),
        prototype=extras.get("prototype", ""),
        collection=extras.get("collection", ""),
        collision_shape=extras.get("collision_shape", ""),
    )


def list_files(project: Path, ext: str) -> List[str]:
    matches: List[str] = []
    suffix = f".{ext}"
    for root, dirs, files in __import__("os").walk(project):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name.endswith(suffix):
                rel = "/" + Path(root, name).relative_to(project).as_posix()
                matches.append(rel)
    return matches


def quoted(text: str, key: str) -> List[str]:
    return re.findall(rf'{key}:\s*"([^"]*)"', text)


def scalar(text: str, key: str) -> Optional[str]:
    match = re.search(rf'(?:^|\n){key}:\s*("([^"]*)"|[^\s\n]+)', text)
    if not match:
        return None
    return match.group(2) if match.group(2) is not None else match.group(1)


def write_new(project: Path, path: str, text: str) -> Path:
    dest = project_file(project, path)
    if dest.exists():
        raise FileExistsError(path)
    note_file_write(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def rewrite(project: Path, path: str, text: str) -> Path:
    dest = project_file(project, path)
    note_file_write(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def require_path(params: Dict[str, Any]) -> str:
    path = params.get("path")
    if not path:
        raise KeyError("path")
    return sanitize_proj_path(str(path))


def read_resource(project: Path, path: str) -> str:
    return project_file(project, path).read_text(encoding="utf-8")


def append_block(project: Path, path: str, block: str) -> str:
    text = read_resource(project, path)
    if not text.endswith("\n"):
        text += "\n"
    text += block if block.endswith("\n") else block + "\n"
    rewrite(project, path, text)
    return text


def remove_block_containing(text: str, needle: str) -> str:
    parts = re.split(r"\n(?=\w)", text)
    kept = [part for part in parts if needle not in part]
    if len(kept) == len(parts):
        raise FileNotFoundError(needle)
    return "\n".join(kept).rstrip() + "\n"


def format_proto_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    text = str(value)
    if text.startswith('"'):
        return text
    return f'"{text}"'


def set_scalar(text: str, key: str, value: Any) -> str:
    formatted = format_proto_scalar(value)
    pattern = re.compile(rf'(^|\n)([ \t]*{re.escape(key)}:\s*)(?:"[^"]*"|[^\s\n]+)')
    if pattern.search(text):
        return pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}{formatted}", text, count=1)
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}{key}: {formatted}\n"


def parse_bindings(text: str) -> List[Dict[str, str]]:
    bindings: List[Dict[str, str]] = []
    for kind, body in re.findall(r"(key_trigger|mouse_trigger|gamepad_trigger|touch_trigger)\s*\{([^}]*)\}", text):
        input_name = scalar("\n" + body, "input") or ""
        action = (quoted("\n" + body, "action") or [""])[0]
        bindings.append({"kind": kind, "input": input_name, "action": action})
    return bindings


def summarize(command: str, path: str, text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"path": path, "source": "disk"}
    if command == "atlas_manage":
        data["images"] = quoted(text, "image")
        data["animations"] = quoted(text, "id")
    elif command == "tilemap_manage":
        data["tile_set"] = scalar(text, "tile_set")
        data["layers"] = quoted(text, "id")
        data["material"] = scalar(text, "material")
    elif command == "gui_manage":
        data["script"] = scalar(text, "script")
        data["textures"] = quoted(text, "texture")
        data["fonts"] = quoted(text, "font")
        data["nodes"] = quoted(text, "id")
    elif command == "particlefx_manage":
        data["emitters"] = quoted(text, "id")
    elif command == "material_manage":
        data["name"] = scalar(text, "name")
        data["vertex_program"] = scalar(text, "vertex_program")
        data["fragment_program"] = scalar(text, "fragment_program")
    elif command == "render_manage":
        data["script"] = scalar(text, "script")
    elif command == "input_binding_manage":
        data["bindings"] = parse_bindings(text)
    elif command == "tilesource_manage":
        data["image"] = scalar(text, "image")
        data["animations"] = quoted(text, "id")
        data["tile_width"] = scalar(text, "tile_width")
        data["tile_height"] = scalar(text, "tile_height")
    elif command == "font_manage":
        data["font"] = scalar(text, "font")
        data["material"] = scalar(text, "material")
        data["size"] = scalar(text, "size")
    elif command == "sound_manage":
        data["sound"] = scalar(text, "sound")
        data["group"] = scalar(text, "group")
    elif command == "gamepads_manage":
        data["devices"] = quoted(text, "device")
    elif command == "display_profiles_manage":
        data["profiles"] = quoted(text, "name")
    elif command == "model_manage":
        data["mesh"] = scalar(text, "mesh")
        data["name"] = scalar(text, "name")
    elif command in {"factory_manage", "collectionfactory_manage"}:
        data["prototype"] = scalar(text, "prototype")
    elif command == "collectionproxy_manage":
        data["collection"] = scalar(text, "collection")
    elif command == "collisionobject_manage":
        data["collision_shape"] = scalar(text, "collision_shape")
        data["group"] = scalar(text, "group")
        data["type"] = scalar(text, "type")
    elif command == "cubemap_manage":
        data["right"] = scalar(text, "right")
        data["left"] = scalar(text, "left")
    elif command == "mesh_manage":
        data["material"] = scalar(text, "material")
        data["vertices"] = scalar(text, "vertices")
    elif command == "texture_profiles_manage":
        data["profiles"] = quoted(text, "name")
        data["paths"] = quoted(text, "path")
    elif command == "compute_manage":
        data["compute_program"] = scalar(text, "compute_program")
    elif command == "appmanifest_manage":
        data["bytes"] = len(text)
    return data


def handle_file_domain(project: Path, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    ext = DOMAIN_EXT[command]
    op = params.get("op")
    if not op:
        return error_envelope("MISSING_PARAM", f"{command} needs op")
    try:
        if op == "list":
            paths = list_files(project, ext)
            offset = max(int(params.get("offset") or 0), 0)
            limit = max(int(params.get("limit") or 100), 1)
            sliced = paths[offset : offset + limit]
            return ok_envelope(
                {
                    "paths": sliced,
                    "total": len(paths),
                    "offset": offset,
                    "limit": limit,
                    "truncated": offset + len(sliced) < len(paths),
                    "source": "disk",
                }
            )
        if op == "create":
            path = require_path(params)
            extras = {
                "tile_set": str(params.get("tile_set") or params.get("tilesource") or ""),
                "image": str(params.get("image") or ""),
                "font": str(params.get("font") or "/builtins/fonts/vera_mo_bd.ttf"),
                "sound": str(params.get("sound") or ""),
                "mesh": str(params.get("mesh") or ""),
                "prototype": str(params.get("prototype") or ""),
                "collection": str(params.get("collection") or ""),
                "collision_shape": str(params.get("collision_shape") or ""),
            }
            text = params.get("content") or load_template(ext, params.get("name") or _stem(path), extras)
            for key in ("image", "font", "sound", "tile_set", "mesh", "prototype", "collection", "collision_shape"):
                value = extras.get(key)
                if value:
                    text = set_scalar(text, key, sanitize_proj_path(value) if key != "font" or value.startswith("/") else value)
            write_new(project, path, text)
            return ok_envelope({"path": path, "created": True, "undoable": False, "source": "disk"})
        path = require_path(params)
        text = read_resource(project, path)
        if op == "get":
            return ok_envelope(summarize(command, path, text))
        if op == "set_property":
            key = params.get("property") or params.get("key")
            if not key:
                return error_envelope("MISSING_PARAM", "set_property needs property")
            rewrite(project, path, set_scalar(text, str(key), str(params.get("value", ""))))
            return ok_envelope({"path": path, "property": key, "value": params.get("value"), "undoable": False, "source": "disk"})
        if op == "remove":
            needle = params.get("id") or params.get("action") or params.get("image") or params.get("name")
            if not needle:
                return error_envelope("MISSING_PARAM", "remove needs id, action, image, or name")
            rewrite(project, path, remove_block_containing(text, str(needle)))
            return ok_envelope({"path": path, "removed": needle, "undoable": False, "source": "disk"})
        if command == "gui_manage" and op in {"set_node", "get_node"}:
            ident = params.get("id") or params.get("node")
            if not ident:
                return error_envelope("MISSING_PARAM", f"{op} needs id")
            found = find_named_block(text, "nodes", str(ident))
            if found is None:
                return error_envelope("NOT_FOUND", f"GUI node '{ident}' was not found")
            start, end, block = found
            if op == "get_node":
                pos = re.search(
                    r"position\s*\{\s*x:\s*([-\d.]+)\s*y:\s*([-\d.]+)\s*z:\s*([-\d.]+)",
                    block,
                )
                return ok_envelope(
                    {
                        "path": path,
                        "id": ident,
                        "type": scalar(block, "type"),
                        "text": (quoted(block, "text") or [None])[0],
                        "texture": (quoted(block, "texture") or [None])[0],
                        "position": (
                            [float(pos.group(1)), float(pos.group(2)), float(pos.group(3))]
                            if pos
                            else None
                        ),
                        "source": "disk",
                    }
                )
            key = params.get("property") or params.get("key")
            if not key:
                return error_envelope("MISSING_PARAM", "set_node needs property")
            if str(key) == "position":
                new_block = set_position_in_block(block, params.get("value"))
            else:
                new_block = set_scalar(block, str(key), str(params.get("value", "")))
            rewrite(project, path, text[:start] + new_block + text[end:])
            return ok_envelope(
                {
                    "path": path,
                    "id": ident,
                    "property": key,
                    "value": params.get("value"),
                    "undoable": False,
                    "source": "disk",
                }
            )
        if command == "tilemap_manage" and op in {"set_tile", "get_tile"}:
            layer = str(params.get("layer") or params.get("id") or "layer")
            if params.get("x") is None or params.get("y") is None:
                return error_envelope("MISSING_PARAM", f"{op} needs x and y")
            x = int(params.get("x"))
            y = int(params.get("y"))
            if op == "get_tile":
                cell = get_tile_cell(text, layer, x, y)
                if cell is None:
                    return error_envelope("NOT_FOUND", f"No tile at {layer} ({x},{y})")
                return ok_envelope({"path": path, "layer": layer, **cell, "source": "disk"})
            if params.get("tile") is None:
                return error_envelope("MISSING_PARAM", "set_tile needs tile")
            tile = int(params.get("tile"))
            h_flip = int(params.get("h_flip") or 0)
            v_flip = int(params.get("v_flip") or 0)
            rewrite(project, path, set_tile_cell(text, layer, x, y, tile, h_flip, v_flip))
            return ok_envelope(
                {
                    "path": path,
                    "layer": layer,
                    "x": x,
                    "y": y,
                    "tile": tile,
                    "h_flip": h_flip,
                    "v_flip": v_flip,
                    "undoable": False,
                    "source": "disk",
                }
            )
        block = add_block(command, op, params)
        if block is None:
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=known_ops(command))
        append_block(project, path, block)
        return ok_envelope({"path": path, "op": op, "undoable": False, "source": "disk"})
    except KeyError as error:
        return error_envelope("MISSING_PARAM", f"Missing {error}")
    except FileExistsError as error:
        return error_envelope("INVALID_PARAM", f"File already exists: {error}")
    except FileNotFoundError as error:
        return error_envelope("NOT_FOUND", f"Not found: {error}")
    except ValueError as error:
        return error_envelope("INVALID_PARAM", str(error))


def find_named_block(text: str, header: str, ident: str) -> Optional[tuple]:
    for match in re.finditer(rf"{header}\s*\{{", text):
        open_at = text.find("{", match.start())
        close_at = match_brace(text, open_at) if open_at >= 0 else None
        if close_at is None:
            continue
        block = text[match.start() : close_at + 1]
        if re.search(rf'id:\s*"{re.escape(ident)}"', block):
            return match.start(), close_at + 1, block
    return None


def parse_cell(block: str) -> Dict[str, int]:
    def number(key: str, default: int = 0) -> int:
        match = re.search(rf"{key}:\s*(-?\d+)", block)
        return int(match.group(1)) if match else default

    return {
        "x": number("x"),
        "y": number("y"),
        "tile": number("tile", -1),
        "h_flip": number("h_flip"),
        "v_flip": number("v_flip"),
    }


def iter_layer_cells(layer_block: str) -> List[tuple]:
    cells: List[tuple] = []
    for match in re.finditer(r"cell\s*\{", layer_block):
        open_at = layer_block.find("{", match.start())
        close_at = match_brace(layer_block, open_at) if open_at >= 0 else None
        if close_at is None:
            continue
        start = match.start()
        end = close_at + 1
        cells.append((start, end, parse_cell(layer_block[start:end])))
    return cells


def format_cell(x: int, y: int, tile: int, h_flip: int, v_flip: int) -> str:
    return (
        f"  cell {{\n"
        f"    x: {int(x)}\n"
        f"    y: {int(y)}\n"
        f"    tile: {int(tile)}\n"
        f"    h_flip: {int(h_flip)}\n"
        f"    v_flip: {int(v_flip)}\n"
        f"  }}\n"
    )


def set_tile_cell(text: str, layer: str, x: int, y: int, tile: int, h_flip: int = 0, v_flip: int = 0) -> str:
    found = find_named_block(text, "layers", layer)
    if found is None:
        raise FileNotFoundError(layer)
    start, end, block = found
    replacement = format_cell(x, y, tile, h_flip, v_flip)
    for cell_start, cell_end, cell in iter_layer_cells(block):
        if cell["x"] == int(x) and cell["y"] == int(y):
            new_block = block[:cell_start] + replacement + block[cell_end:]
            return text[:start] + new_block + text[end:]
    close_at = block.rfind("}")
    new_block = block[:close_at] + replacement + block[close_at:]
    return text[:start] + new_block + text[end:]


def get_tile_cell(text: str, layer: str, x: int, y: int) -> Optional[Dict[str, int]]:
    found = find_named_block(text, "layers", layer)
    if found is None:
        raise FileNotFoundError(layer)
    _start, _end, block = found
    for _cs, _ce, cell in iter_layer_cells(block):
        if cell["x"] == int(x) and cell["y"] == int(y):
            return cell
    return None


def known_ops(command: str) -> List[str]:
    extra = {
        "atlas_manage": ["add_image", "add_animation"],
        "tilemap_manage": ["add_layer", "set_tile_set", "set_tile", "get_tile"],
        "gui_manage": ["add_box", "add_text", "add_texture", "add_font", "set_script", "set_node", "get_node"],
        "input_binding_manage": ["add_key", "add_mouse", "add_gamepad", "add_touch"],
        "particlefx_manage": ["add_emitter"],
        "material_manage": ["set_program"],
        "render_manage": ["set_script"],
        "tilesource_manage": ["add_animation", "set_image"],
        "font_manage": ["set_font"],
        "sound_manage": ["set_sound"],
    }
    return ["create", "get", "list", "remove", "set_property", *extra.get(command, [])]


def add_block(command: str, op: str, params: Dict[str, Any]) -> Optional[str]:
    if command == "atlas_manage":
        if op == "add_image":
            image = params.get("image") or params.get("path")
            if not image:
                raise KeyError("image")
            ident = params.get("id")
            if ident:
                return (
                    "animations {\n"
                    f'  id: "{ident}"\n'
                    "  images {\n"
                    f'    image: "{sanitize_proj_path(str(image))}"\n'
                    "  }\n"
                    "}\n"
                )
            return f'images {{\n  image: "{sanitize_proj_path(str(image))}"\n}}\n'
        if op == "add_animation":
            ident = params.get("id") or "anim"
            return f'animations {{\n  id: "{ident}"\n  playback: PLAYBACK_LOOP_FORWARD\n  fps: 30\n}}\n'
    if command == "tilemap_manage":
        if op == "add_layer":
            ident = params.get("id") or params.get("name") or "layer"
            return f'layers {{\n  id: "{ident}"\n  z: 0.0\n  is_visible: 1\n}}\n'
    if command == "gui_manage":
        if op == "add_box":
            ident = params.get("id") or "box"
            return (
                "nodes {\n"
                f'  id: "{ident}"\n'
                "  type: TYPE_BOX\n"
                f'  texture: "{params.get("texture") or ""}"\n'
                "}\n"
            )
        if op == "add_text":
            ident = params.get("id") or "text"
            return (
                "nodes {\n"
                f'  id: "{ident}"\n'
                "  type: TYPE_TEXT\n"
                f'  text: "{params.get("text") or ident}"\n'
                "}\n"
            )
        if op == "add_texture":
            name = params.get("name") or "main"
            texture = params.get("texture") or params.get("path")
            if not texture:
                raise KeyError("texture")
            return f'textures {{\n  name: "{name}"\n  texture: "{sanitize_proj_path(str(texture))}"\n}}\n'
        if op == "add_font":
            name = params.get("name") or "default_font"
            font = params.get("font") or params.get("path") or "/builtins/fonts/default.font"
            return f'fonts {{\n  name: "{name}"\n  font: "{sanitize_proj_path(str(font))}"\n}}\n'
    if command == "input_binding_manage":
        kind = {
            "add_key": "key_trigger",
            "add_mouse": "mouse_trigger",
            "add_gamepad": "gamepad_trigger",
            "add_touch": "touch_trigger",
        }.get(op)
        if kind:
            input_name = params.get("input")
            action = params.get("action")
            if not input_name or not action:
                raise KeyError("input/action")
            return f'{kind} {{\n  input: {input_name}\n  action: "{action}"\n}}\n'
    if command == "tilesource_manage" and op == "add_animation":
        ident = params.get("id") or "anim"
        start_tile = params.get("start_tile") or 1
        end_tile = params.get("end_tile") or start_tile
        return (
            "animations {\n"
            f'  id: "{ident}"\n'
            f"  start_tile: {int(start_tile)}\n"
            f"  end_tile: {int(end_tile)}\n"
            "}\n"
        )
    if command == "particlefx_manage" and op == "add_emitter":
        ident = params.get("id") or "emitter"
        return (
            "emitters {\n"
            f'  id: "{ident}"\n'
            "  mode: PLAY_MODE_LOOP\n"
            "  duration: 1.0\n"
            '  tile_source: "/builtins/graphics/particle_blob.tilesource"\n'
            '  animation: "anim"\n'
            '  material: "/builtins/materials/particlefx.material"\n'
            "  max_particle_count: 64\n"
            "  type: EMITTER_TYPE_CIRCLE\n"
            "}\n"
        )
    return None


def apply_special_set(project: Path, command: str, op: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    path = require_path(params)
    text = read_resource(project, path)
    if command == "tilemap_manage" and op == "set_tile_set":
        tile_set = params.get("tile_set") or params.get("tilesource") or params.get("value")
        if not tile_set:
            return error_envelope("MISSING_PARAM", "set_tile_set needs tile_set")
        rewrite(project, path, set_scalar(text, "tile_set", sanitize_proj_path(str(tile_set))))
        return ok_envelope({"path": path, "tile_set": sanitize_proj_path(str(tile_set)), "undoable": False, "source": "disk"})
    if command in {"gui_manage", "render_manage"} and op == "set_script":
        script = params.get("script") or params.get("value")
        if not script:
            return error_envelope("MISSING_PARAM", "set_script needs script")
        rewrite(project, path, set_scalar(text, "script", sanitize_proj_path(str(script))))
        return ok_envelope({"path": path, "script": sanitize_proj_path(str(script)), "undoable": False, "source": "disk"})
    if command == "tilesource_manage" and op == "set_image":
        image = params.get("image") or params.get("value")
        if not image:
            return error_envelope("MISSING_PARAM", "set_image needs image")
        rewrite(project, path, set_scalar(text, "image", sanitize_proj_path(str(image))))
        return ok_envelope({"path": path, "image": sanitize_proj_path(str(image)), "undoable": False, "source": "disk"})
    if command == "font_manage" and op == "set_font":
        font = params.get("font") or params.get("value")
        if not font:
            return error_envelope("MISSING_PARAM", "set_font needs font")
        rewrite(project, path, set_scalar(text, "font", sanitize_proj_path(str(font))))
        return ok_envelope({"path": path, "font": sanitize_proj_path(str(font)), "undoable": False, "source": "disk"})
    if command == "sound_manage" and op == "set_sound":
        sound = params.get("sound") or params.get("value")
        if not sound:
            return error_envelope("MISSING_PARAM", "set_sound needs sound")
        rewrite(project, path, set_scalar(text, "sound", sanitize_proj_path(str(sound))))
        return ok_envelope({"path": path, "sound": sanitize_proj_path(str(sound)), "undoable": False, "source": "disk"})
    if command == "material_manage" and op == "set_program":
        text = text
        if params.get("vertex_program"):
            text = set_scalar(text, "vertex_program", sanitize_proj_path(str(params["vertex_program"])))
        if params.get("fragment_program"):
            text = set_scalar(text, "fragment_program", sanitize_proj_path(str(params["fragment_program"])))
        rewrite(project, path, text)
        return ok_envelope({"path": path, "undoable": False, "source": "disk"})
    return None


def handle_camera(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    op = params.get("op")
    if not op:
        return error_envelope("MISSING_PARAM", "camera_manage needs op")
    path = params.get("path") or params.get("gameobject") or params.get("go")
    collection = params.get("collection")
    ident = params.get("component") or "camera"
    if not collection:
        ident = params.get("id") or ident
    try:
        if op == "add":
            if collection and params.get("id"):
                from agent_ops import disk_command

                return disk_command(
                    project,
                    "component_add",
                    {
                        "collection": collection,
                        "id": params.get("id"),
                        "type": "camera",
                    },
                )
            if not path:
                return error_envelope(
                    "MISSING_PARAM",
                    "camera_manage add needs a .go path, or collection and id",
                )
            path = sanitize_proj_path(str(path))
            text = read_resource(project, path)
            if f'id: "{ident}"' in text and "type: \"camera\"" in text:
                return error_envelope("INVALID_PARAM", f"Camera '{ident}' already exists")
            rewrite(project, path, text.rstrip() + CAMERA_BLOCK.format(id=ident))
            return ok_envelope({"path": path, "id": ident, "undoable": False, "source": "disk"})
        if op == "get":
            if not path:
                return error_envelope("MISSING_PARAM", "camera_manage get needs path")
            path = sanitize_proj_path(str(path))
            text = read_resource(project, path)
            present = f'id: "{ident}"' in text
            return ok_envelope({"path": path, "id": ident, "present": present, "source": "disk"})
        if op == "remove":
            if collection and params.get("id"):
                from agent_ops import disk_command

                return disk_command(
                    project,
                    "component_manage",
                    {
                        "op": "remove",
                        "collection": collection,
                        "id": params.get("id"),
                        "component": ident,
                    },
                )
            if not path:
                return error_envelope(
                    "MISSING_PARAM",
                    "camera_manage remove needs a .go path, or collection and id",
                )
            path = sanitize_proj_path(str(path))
            rewrite(project, path, remove_block_containing(read_resource(project, path), f'id: "{ident}"'))
            return ok_envelope({"path": path, "id": ident, "removed": True, "undoable": False, "source": "disk"})
        return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=["add", "get", "remove"])
    except FileNotFoundError as error:
        return error_envelope("NOT_FOUND", f"Not found: {error}")


def handle_domain_command(project: Path, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    params = params or {}
    if command == "camera_manage":
        return handle_camera(project, params)
    if command not in DOMAIN_EXT:
        return error_envelope("UNKNOWN_COMMAND", f"Unknown domain command: {command}")
    op = params.get("op")
    if op in {"set_tile_set", "set_script", "set_program", "set_image", "set_font", "set_sound"}:
        try:
            special = apply_special_set(project, command, op, params)
        except KeyError as error:
            return error_envelope("MISSING_PARAM", f"Missing {error}")
        except FileNotFoundError as error:
            return error_envelope("NOT_FOUND", f"Not found: {error}")
        if special is not None:
            return special
    return handle_file_domain(project, command, params)


DOMAIN_COMMANDS = set(DOMAIN_EXT) | {"camera_manage"}
