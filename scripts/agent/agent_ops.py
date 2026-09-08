# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Editor /agent/command client plus disk fallbacks (no game plugin)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_errors import apply_alias, authoring_write


SKIP_DIRS = {".internal", "build", ".git", ".editor"}

RE_COLLECTION_ID = re.compile(
    r"(?:embedded_instances|instances|collection_instances)\s*\{\s*id:\s*\"([^\"]+)\"",
    re.MULTILINE,
)

SCRIPT_TEMPLATE = """function init(self)
end

function update(self, dt)
end
"""

COLLECTION_TEMPLATE = 'name: "{name}"\n'


def envelope(
    status: str,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
    readiness: str = "ready",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": status, "readiness": readiness}
    if data is not None:
        payload["data"] = data
    if error is not None:
        payload["error"] = error
    return payload


def error_envelope(code: str, message: str, hint: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if hint:
        err["hint"] = hint
    if extra:
        err["data"] = extra
    sub = extra.get("sub_code")
    if code == "EDITOR_UNREACHABLE":
        readiness = "no_editor"
    elif sub in {"building", "observing", "running", "no_runtime", "no_editor", "no_collection"}:
        readiness = sub
    else:
        readiness = "ready"
    return envelope("error", error=err, readiness=readiness)


def ok_envelope(data: Dict[str, Any], readiness: str = "ready") -> Dict[str, Any]:
    return envelope("ok", data=data, readiness=readiness)


def sanitize_proj_path(path: str) -> str:
    normalized = (path or "").replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized.lstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Path must stay inside the project")
    return "/" + "/".join(parts)


def project_file(project: Path, proj_path: str) -> Path:
    return project / sanitize_proj_path(proj_path).lstrip("/")


def read_editor_endpoint(project: Path) -> Optional[Tuple[str, str]]:
    port_file = project / ".internal" / "editor.port"
    token_file = project / ".internal" / "editor.token"
    if not port_file.is_file() or not token_file.is_file():
        return None
    port = port_file.read_text(encoding="utf-8").strip()
    token = token_file.read_text(encoding="utf-8").strip()
    if not port or not token:
        return None
    return f"http://127.0.0.1:{port}", token


def http_json(
    url: str,
    token: Optional[str],
    method: str = "GET",
    timeout: float = 120.0,
    body: Any = None,
) -> Tuple[int, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            parsed: Any
            if raw[:1] in (b"{", b"["):
                parsed = json.loads(raw.decode("utf-8"))
            else:
                parsed = raw.decode("utf-8", errors="replace")
            return response.status, parsed
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = raw.decode("utf-8", errors="replace")
        return error.code, parsed


def editor_command(
    project: Path,
    command: str,
    params: Optional[Dict[str, Any]],
    timeout: float,
) -> Optional[Dict[str, Any]]:
    endpoint = read_editor_endpoint(project)
    if not endpoint:
        return None
    url, token = endpoint
    status, body = http_json(
        f"{url}/agent/command",
        token,
        method="POST",
        timeout=timeout,
        body={"command": command, "params": params or {}},
    )
    if status == 404:
        return None
    if status == 401:
        return error_envelope("UNAUTHORIZED", "Editor Bearer token was rejected.")
    if isinstance(body, dict) and "status" in body:
        return body
    return error_envelope(
        "HANDLER_ERROR",
        f"Unexpected /agent/command response ({status}): {body}",
    )


def editor_get(project: Path, path: str, timeout: float) -> Optional[Tuple[int, Any]]:
    endpoint = read_editor_endpoint(project)
    if not endpoint:
        return None
    url, token = endpoint
    return http_json(f"{url}{path}", token, timeout=timeout)


def patch_text(text: str, old_text: str, new_text: str) -> str:
    matches = text.count(old_text)
    if matches == 0:
        raise KeyError("OLD_TEXT_NOT_FOUND")
    if matches > 1:
        raise ValueError("MULTIPLE_MATCHES")
    return text.replace(old_text, new_text, 1)


def list_component_ids(go_text: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for kind in ("embedded_components", "components"):
        for match in re.finditer(rf"{kind}\s*\{{", go_text):
            open_at = go_text.find("{", match.start())
            close_at = match_brace(go_text, open_at) if open_at >= 0 else None
            if close_at is None:
                continue
            block = go_text[match.start() : close_at + 1]
            ident = first_quoted_id(block)
            if not ident:
                continue
            item: Dict[str, str] = {"id": ident, "kind": "referenced" if kind == "components" else "embedded"}
            proto = re.search(r'component:\s*"([^"]+)"', block)
            type_name = re.search(r'type:\s*"([^"]+)"', block)
            if proto:
                item["path"] = proto.group(1)
            if type_name:
                item["type"] = type_name.group(1)
            items.append(item)
    return items


def parse_gameobject_properties(text: str, path: str, go_id: str, project: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    try:
        _start, _end, block = find_instance_span(text, go_id)
    except FileNotFoundError:
        return None
    pos = re.search(r"position\s*\{\s*x:\s*([-\d.]+)\s*y:\s*([-\d.]+)\s*z:\s*([-\d.]+)", block)
    properties: Dict[str, Any] = {}
    if pos:
        properties["position"] = [float(pos.group(1)), float(pos.group(2)), float(pos.group(3))]
    proto = instance_prototype(block)
    go_text = ""
    if proto and project is not None:
        try:
            go_text = _read_text(project, proto)
        except FileNotFoundError:
            go_text = ""
    else:
        go_text = decode_data_field(block) or ""
    return {
        "id": go_id,
        "path": path,
        "source": "disk",
        "kind": "referenced" if proto else "embedded",
        "prototype": proto,
        "components": list_component_ids(go_text),
        "properties": properties,
    }


RE_INSTANCE_HEADER = re.compile(r"(?:embedded_instances|instances|collection_instances)\s*\{")
RE_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')

COMPONENT_EMBEDDED = {
    "camera": (
        'aspect_ratio: 1.0\\n"\n'
        '  "fov: 0.785\\n"\n'
        '  "near_z: 0.1\\n"\n'
        '  "far_z: 1000.0\\n"\n'
        '  "'
    ),
    "sprite": (
        'default_animation: \\"\\"\\n"\n'
        '  "material: \\"/builtins/materials/sprite.material\\"\\n"\n'
        '  "'
    ),
    "label": (
        'text: \\"Label\\"\\n"\n'
        '  "font: \\"/builtins/fonts/default.font\\"\\n"\n'
        '  "'
    ),
    "sound": 'sound: \\"\\"\\n"\n  "',
    "collisionobject": (
        'type: COLLISION_OBJECT_TYPE_KINEMATIC\\n"\n'
        '  "mass: 0.0\\n"\n'
        '  "'
    ),
}


def match_brace(text: str, open_at: int) -> Optional[int]:
    depth = 0
    for index in range(open_at, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def enclosing_span(text: str, pos: int) -> Optional[Tuple[int, int]]:
    depth = 0
    open_at = None
    for index in range(pos, -1, -1):
        char = text[index]
        if char == "}":
            depth += 1
        elif char == "{":
            if depth == 0:
                open_at = index
                break
            depth -= 1
    if open_at is None:
        return None
    close_at = match_brace(text, open_at)
    if close_at is None:
        return None
    header = text.rfind("\n", 0, open_at)
    start = 0 if header < 0 else header + 1
    return start, close_at + 1


def extract_brace_block(text: str, start: int) -> Optional[str]:
    span = enclosing_span(text, start)
    if not span:
        return None
    return text[span[0] : span[1]]


def unescape_proto(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")


def escape_proto(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def decode_data_field(block: str) -> Optional[str]:
    match = re.search(r"\bdata:\s*", block)
    if not match:
        return None
    rest = block[match.end() :]
    parts: List[str] = []
    pos = 0
    while pos < len(rest):
        while pos < len(rest) and rest[pos] in " \t\r\n":
            pos += 1
        if pos >= len(rest) or rest[pos] != '"':
            break
        quoted = RE_QUOTED.match(rest, pos)
        if not quoted:
            break
        parts.append(unescape_proto(quoted.group(1)))
        pos = quoted.end()
    return "".join(parts)


def encode_data_lines(go_text: str) -> str:
    if not go_text:
        return '  data: ""\n'
    lines = go_text.splitlines(keepends=True)
    chunks: List[str] = []
    for index, line in enumerate(lines):
        escaped = escape_proto(line)
        if index == 0:
            chunks.append(f'  data: "{escaped}"')
        else:
            chunks.append(f'  "{escaped}"')
    chunks.append('  ""')
    return "\n".join(chunks) + "\n"


def replace_data_field(block: str, go_text: str) -> str:
    encoded = encode_data_lines(go_text)
    match = re.search(r"\bdata:\s*", block)
    if not match:
        close_at = block.rfind("}")
        return block[:close_at] + encoded + block[close_at:]
    rest = block[match.end() :]
    pos = 0
    while pos < len(rest):
        while pos < len(rest) and rest[pos] in " \t\r\n":
            pos += 1
        if pos >= len(rest) or rest[pos] != '"':
            break
        quoted = RE_QUOTED.match(rest, pos)
        if not quoted:
            break
        pos = quoted.end()
    return block[: match.start()] + encoded + rest[pos:]


def iter_instance_spans(text: str) -> List[Tuple[int, int, str]]:
    spans: List[Tuple[int, int, str]] = []
    for match in RE_INSTANCE_HEADER.finditer(text):
        open_at = text.find("{", match.start())
        close_at = match_brace(text, open_at) if open_at >= 0 else None
        if close_at is None:
            continue
        spans.append((match.start(), close_at + 1, text[match.start() : close_at + 1]))
    return spans


def first_quoted_id(block: str) -> Optional[str]:
    match = re.search(r'id:\s*"([^"]+)"', block)
    return match.group(1) if match else None


def find_instance_span(text: str, go_id: str) -> Tuple[int, int, str]:
    for start, end, block in iter_instance_spans(text):
        if first_quoted_id(block) == go_id:
            return start, end, block
    raise FileNotFoundError(go_id)


def instance_prototype(block: str) -> Optional[str]:
    match = re.search(r'prototype:\s*"([^"]+)"', block)
    return match.group(1) if match else None


def replace_instance_block(text: str, go_id: str, new_block: str) -> str:
    start, end, _block = find_instance_span(text, go_id)
    return text[:start] + new_block + text[end:]


def replace_instance_id(text: str, old_id: str, new_id: str) -> str:
    start, end, block = find_instance_span(text, old_id)
    if f'id: "{new_id}"' in text and new_id != old_id:
        raise ValueError(f"Game object '{new_id}' already exists")
    return text[:start] + block.replace(f'id: "{old_id}"', f'id: "{new_id}"', 1) + text[end:]


def remove_instance_block(text: str, go_id: str) -> str:
    start, end, _block = find_instance_span(text, go_id)
    return text[:start] + text[end:]


def set_position_in_block(block: str, value: Any) -> str:
    if isinstance(value, dict):
        xyz = [value.get("x", 0), value.get("y", 0), value.get("z", 0)]
    elif isinstance(value, (list, tuple)):
        xyz = list(value)
    else:
        raise ValueError("position must be [x, y, z]")
    x, y, z = (xyz + [0, 0, 0])[:3]
    new_pos = f"position {{\n    x: {float(x)}\n    y: {float(y)}\n    z: {float(z)}\n  }}"
    if re.search(r"position\s*\{", block):
        return re.sub(r"position\s*\{[^{}]*\}", new_pos, block, count=1)
    close_at = block.rfind("}")
    return block[:close_at] + "  " + new_pos + "\n" + block[close_at:]


def component_snippet(params: Dict[str, Any]) -> Tuple[str, str]:
    path = params.get("path")
    if path and str(path).endswith(".go"):
        path = None
    type_name = params.get("type") or params.get("component_type")
    ident = params.get("component") or params.get("component_id")
    if path and (not type_name or type_name == "script"):
        ident = ident or Path(str(path)).stem
        return (
            f'components {{\n  id: "{ident}"\n  component: "{sanitize_proj_path(str(path))}"\n}}\n',
            ident,
        )
    type_name = type_name or "sprite"
    ident = ident or type_name
    if path:
        return (
            f'components {{\n  id: "{ident}"\n  component: "{sanitize_proj_path(str(path))}"\n}}\n',
            ident,
        )
    payload = COMPONENT_EMBEDDED.get(type_name, '\\n"\n  "')
    return (
        f'embedded_components {{\n  id: "{ident}"\n  type: "{type_name}"\n  data: "{payload}"\n}}\n',
        ident,
    )


def append_component_text(text: str, snippet: str) -> str:
    if not text.endswith("\n"):
        text += "\n"
    return text + (snippet if snippet.endswith("\n") else snippet + "\n")


def remove_component_from_go(text: str, component_id: str) -> str:
    marker = f'id: "{component_id}"'
    start = text.find(marker)
    if start < 0:
        raise FileNotFoundError(component_id)
    block = extract_brace_block(text, start)
    if not block:
        raise FileNotFoundError(component_id)
    return text.replace(block, "", 1)


def building_lock_path(project: Path) -> Path:
    return project / ".internal" / "agent" / "building.lock"


class BuildingLock:
    def __init__(self, project: Path):
        self.path = building_lock_path(project)

    def __enter__(self) -> "BuildingLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("building\n", encoding="utf-8")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def compute_readiness(project: Path) -> str:
    control = project / ".internal" / "agent" / "control"
    for kind in ("dump", "screenshot"):
        if (control / f"{kind}.request").is_file() and not (control / f"{kind}.ready").is_file():
            return "observing"
    if building_lock_path(project).is_file():
        return "building"
    try:
        from agent_runtime import live_status

        if live_status(project).get("alive"):
            return "running"
    except Exception:
        pass
    if not (project / "game.project").is_file():
        return "no_collection"
    if read_editor_endpoint(project) is None:
        return "no_editor"
    return "ready"


def reject_write_if_gated(project: Path, command: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not authoring_write(command, params):
        return None
    state = compute_readiness(project)
    if state not in {"building", "observing"}:
        return None
    return error_envelope(
        "EDITOR_NOT_READY",
        f"Authoring writes are blocked while {state}.",
        "Wait for the build or observe handshake to finish.",
        sub_code=state,
    )


def overlay_readiness(project: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    computed = compute_readiness(project)
    if computed in {"building", "observing"}:
        result["readiness"] = computed
        return result
    current = result.get("readiness")
    if computed == "running":
        result["readiness"] = "running"
        return result
    if current in {"no_runtime", "no_editor", "no_collection"}:
        return result
    result["readiness"] = computed
    return result


def game_status_payload(project: Path) -> Dict[str, Any]:
    from agent_runtime import live_status

    engine = live_status(project)
    alive = bool(engine.get("alive"))
    return {
        "status": "live" if alive else "stopped",
        "helper_live": False,
        "session_active": alive,
    }


def parse_collection_hierarchy(text: str, path: str) -> Dict[str, Any]:
    children: List[Dict[str, Any]] = []
    for _start, _end, block in iter_instance_spans(text):
        ident = first_quoted_id(block)
        if not ident:
            continue
        proto = instance_prototype(block)
        header = block.lstrip().split("{", 1)[0].strip()
        nested = re.search(r'(?:^|\n)\s*collection:\s*"([^"]+)"', block)
        if header == "collection_instances":
            item = {
                "id": ident,
                "type": "collection_instance",
                "kind": "collection_instance",
            }
            if nested:
                item["collection"] = nested.group(1)
        else:
            item = {"id": ident, "type": "gameobject", "kind": "referenced" if proto else "embedded"}
            if proto:
                item["prototype"] = proto
        children.append(item)
    return {
        "path": path,
        "type": "collection",
        "source": "disk",
        "total": len(children),
        "children": children,
    }


def parse_game_project(text: str) -> Dict[str, str]:
    section = ""
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[f"{section}.{key.strip()}"] = value.strip()
    return values


def write_game_project_setting(text: str, dotted_key: str, value: Any) -> str:
    if "." not in dotted_key:
        raise ValueError("settings key must look like section.name")
    section, key = dotted_key.split(".", 1)
    lines = text.splitlines()
    current = ""
    replaced = False
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            out.append(line)
            continue
        if current == section and stripped.startswith(f"{key}") and "=" in stripped:
            out.append(f"{key} = {value}")
            replaced = True
            continue
        out.append(line)
    if not replaced:
        if current != section:
            out.append(f"[{section}]")
        out.append(f"{key} = {value}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _read_text(project: Path, path: str) -> str:
    file_path = project_file(project, path)
    if not file_path.is_file():
        raise FileNotFoundError(path)
    return file_path.read_text(encoding="utf-8")


def _write_text(project: Path, path: str, text: str, overwrite: bool) -> Path:
    file_path = project_file(project, path)
    if file_path.exists() and not overwrite:
        raise FileExistsError(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")
    return file_path


def disk_command(project: Path, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    params = params or {}
    try:
        if command == "editor_state":
            title = None
            main = None
            game_project = project / "game.project"
            if game_project.is_file():
                settings = parse_game_project(game_project.read_text(encoding="utf-8"))
                title = settings.get("project.title")
                main = settings.get("bootstrap.main_collection")
            return ok_envelope(
                {
                    "defold_version": "1.13.1",
                    "project_title": title,
                    "project_root": str(project),
                    "main_collection": main,
                    "active_resource": None,
                    "selection": [],
                    "source": "disk",
                    "commands": DISK_COMMANDS,
                    "game_status": game_status_payload(project),
                },
                readiness="no_editor",
            )
        if command == "collection_get_hierarchy":
            path = params.get("path") or params.get("collection")
            if not path:
                return error_envelope("MISSING_PARAM", "Missing collection path")
            data = parse_collection_hierarchy(_read_text(project, path), sanitize_proj_path(path))
            offset = max(int(params.get("offset") or 0), 0)
            limit = max(int(params.get("limit") or 200), 1)
            children = data.get("children") or []
            data["offset"] = offset
            data["limit"] = limit
            data["truncated"] = offset + limit < len(children)
            data["children"] = children[offset : offset + limit]
            return ok_envelope(data)
        if command == "collection_open":
            path = params.get("path") or params.get("collection")
            if not path:
                return error_envelope("MISSING_PARAM", "collection_open needs path")
            _read_text(project, path)
            return ok_envelope({"path": sanitize_proj_path(path), "opened": False, "source": "disk"}, readiness="no_editor")
        if command == "collection_save":
            path = params.get("path") or params.get("collection")
            if not path:
                return error_envelope("MISSING_PARAM", "collection_save needs path")
            _read_text(project, path)
            return ok_envelope({"path": sanitize_proj_path(path), "saved": True, "source": "disk", "undoable": False})
        if command == "gameobject_get_properties":
            path = params.get("collection") or params.get("path")
            go_id = params.get("id")
            if not path or not go_id:
                return error_envelope("MISSING_PARAM", "gameobject_get_properties needs collection and id")
            parsed = parse_gameobject_properties(
                _read_text(project, path),
                sanitize_proj_path(path),
                str(go_id),
                project,
            )
            if parsed is None:
                return error_envelope("NOT_FOUND", f"Game object '{go_id}' was not found")
            return ok_envelope(parsed)
        if command == "script_create":
            path = params.get("path")
            if not path:
                return error_envelope("MISSING_PARAM", "Missing path")
            content = params.get("content") or SCRIPT_TEMPLATE
            _write_text(project, path, content, overwrite=False)
            return ok_envelope({"path": sanitize_proj_path(path), "source": "disk"})
        if command == "script_patch":
            path = params.get("path")
            if not path:
                return error_envelope("MISSING_PARAM", "Missing path")
            text = patch_text(_read_text(project, path), params.get("old_text", ""), params.get("new_text", ""))
            _write_text(project, path, text, overwrite=True)
            return ok_envelope({"path": sanitize_proj_path(path), "patched": True, "source": "disk"})
        if command == "script_manage" and params.get("op") == "read":
            path = params.get("path")
            if not path and params.get("collection") and params.get("id"):
                parsed = parse_gameobject_properties(
                    _read_text(project, params.get("collection")),
                    sanitize_proj_path(str(params.get("collection"))),
                    str(params.get("id")),
                    project,
                )
                if parsed is None:
                    return error_envelope("NOT_FOUND", f"Game object '{params.get('id')}' was not found")
                component = params.get("component")
                matches = parsed.get("components") or []
                if component:
                    matches = [item for item in matches if item.get("id") == component]
                script_paths = [item.get("path") for item in matches if str(item.get("path") or "").endswith(".script")]
                if not script_paths:
                    return error_envelope("NOT_FOUND", "No script component path was found")
                path = script_paths[0]
            if not path:
                return error_envelope("MISSING_PARAM", "script_manage read needs path or collection+id")
            return ok_envelope({"path": sanitize_proj_path(path), "text": _read_text(project, path), "source": "disk"})
        if command == "filesystem_manage":
            op = params.get("op")
            if op == "read_text":
                path = params.get("path")
                file_path = project_file(project, path)
                from agent_runtime import is_snapshot_path

                if is_snapshot_path(project, file_path):
                    return error_envelope(
                        "NOT_ALLOWED",
                        "Do not read snapshot files as text.",
                        "Use runtime_snapshot_query to take a slice.",
                    )
                return ok_envelope({"path": sanitize_proj_path(path), "text": _read_text(project, path), "source": "disk"})
            if op == "write_text":
                path = params.get("path")
                _write_text(project, path, params.get("text", ""), overwrite=True)
                return ok_envelope({"path": sanitize_proj_path(path), "written": True, "undoable": False, "source": "disk"})
            if op == "search":
                query = params.get("query")
                if not query:
                    return error_envelope("MISSING_PARAM", "search needs query")
                ext = params.get("ext")
                offset = max(int(params.get("offset") or 0), 0)
                limit = max(int(params.get("limit") or 100), 1)
                matches: List[str] = []
                for root, dirs, files in os.walk(project):
                    dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
                    for name in files:
                        if ext and not name.endswith(f".{ext}"):
                            continue
                        file_path = Path(root) / name
                        if file_path.stat().st_size > 1_000_000:
                            continue
                        text = file_path.read_text(encoding="utf-8", errors="replace")
                        if query in text:
                            matches.append("/" + file_path.relative_to(project).as_posix())
                sliced = matches[offset : offset + limit]
                return ok_envelope(
                    {
                        "matches": sliced,
                        "total": len(matches),
                        "offset": offset,
                        "limit": limit,
                        "truncated": offset + len(sliced) < len(matches),
                        "source": "disk",
                    }
                )
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=["read_text", "write_text", "search"])
        if command == "collection_manage":
            op = params.get("op")
            if op == "create":
                path = params.get("path")
                if not path:
                    return error_envelope("MISSING_PARAM", "Missing path")
                name = params.get("name") or Path(path).stem
                _write_text(project, path, COLLECTION_TEMPLATE.format(name=name), overwrite=False)
                return ok_envelope({"path": sanitize_proj_path(path), "source": "disk", "undoable": False})
            if op == "add_instance":
                return disk_command(project, "gameobject_create", params)
            if op == "remove_instance":
                path = params.get("collection") or params.get("path")
                go_id = params.get("id")
                if not path or not go_id:
                    return error_envelope("MISSING_PARAM", "remove_instance needs collection and id")
                text = remove_instance_block(_read_text(project, path), str(go_id))
                _write_text(project, path, text, overwrite=True)
                return ok_envelope({"deleted": True, "id": go_id, "undoable": False, "source": "disk"})
            if op == "get_roots":
                return disk_command(
                    project,
                    "collection_get_hierarchy",
                    {**params, "limit": params.get("limit") or 50},
                )
            return error_envelope(
                "UNKNOWN_OP",
                f"Unknown op: {op}",
                suggestions=["create", "add_instance", "remove_instance", "get_roots"],
            )
        if command == "gameobject_manage":
            op = params.get("op")
            path = params.get("collection") or params.get("path")
            go_id = params.get("id")
            if op == "find":
                if not path or not go_id:
                    return error_envelope("MISSING_PARAM", "find needs collection and id")
                tree = parse_collection_hierarchy(_read_text(project, path), sanitize_proj_path(path))
                needle = str(go_id)
                matches = [child for child in tree.get("children") or [] if needle in str(child.get("id"))]
                return ok_envelope({"matches": matches, "source": "disk"})
            if not path or not go_id:
                return error_envelope("MISSING_PARAM", "gameobject_manage needs collection and id")
            if op == "delete":
                return disk_command(project, "collection_manage", {**params, "op": "remove_instance"})
            if op == "rename":
                name = params.get("name")
                if not name:
                    return error_envelope("MISSING_PARAM", "rename needs name")
                text = replace_instance_id(_read_text(project, path), str(go_id), str(name))
                _write_text(project, path, text, overwrite=True)
                return ok_envelope({"id": name, "renamed": True, "undoable": False, "source": "disk"})
            if op == "set_property":
                key = params.get("property") or params.get("key")
                if not key:
                    return error_envelope("MISSING_PARAM", "set_property needs property")
                text = _read_text(project, path)
                start, end, block = find_instance_span(text, str(go_id))
                if str(key) == "position":
                    block = set_position_in_block(block, params.get("value"))
                elif str(key) == "id":
                    return disk_command(project, "gameobject_manage", {**params, "op": "rename", "name": params.get("value")})
                else:
                    raise ValueError(f"Unsupported disk property: {key}")
                _write_text(project, path, text[:start] + block + text[end:], overwrite=True)
                return ok_envelope(
                    {"id": go_id, "property": key, "value": params.get("value"), "undoable": False, "source": "disk"}
                )
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=["delete", "rename", "set_property", "find"])
        if command in {"component_add", "script_attach"}:
            if command == "script_attach":
                params = {**params, "type": "script"}
            collection = params.get("collection")
            go_id = params.get("id")
            go_path = params.get("path") if str(params.get("path") or "").endswith(".go") else None
            snippet, ident = component_snippet(params)
            if go_path:
                text = append_component_text(_read_text(project, go_path), snippet)
                _write_text(project, go_path, text, overwrite=True)
                return ok_envelope(
                    {"id": go_id or Path(go_path).stem, "component": ident, "path": sanitize_proj_path(go_path), "undoable": False, "source": "disk"}
                )
            if not collection or not go_id:
                return error_envelope("MISSING_PARAM", f"{command} needs collection and id, or a .go path")
            text = _read_text(project, collection)
            _start, _end, block = find_instance_span(text, str(go_id))
            proto = instance_prototype(block)
            if proto:
                proto_text = append_component_text(_read_text(project, proto), snippet)
                _write_text(project, proto, proto_text, overwrite=True)
                return ok_envelope(
                    {"id": go_id, "component": ident, "path": sanitize_proj_path(proto), "undoable": False, "source": "disk"}
                )
            go_text = decode_data_field(block) or ""
            new_block = replace_data_field(block, append_component_text(go_text, snippet))
            _write_text(project, collection, replace_instance_block(text, str(go_id), new_block), overwrite=True)
            return ok_envelope({"id": go_id, "component": ident, "undoable": False, "source": "disk"})
        if command == "component_manage":
            op = params.get("op")
            collection = params.get("collection") or params.get("path")
            go_id = params.get("id")
            component = params.get("component")
            if not collection or not go_id or not component:
                return error_envelope("MISSING_PARAM", "component_manage needs collection, id, and component")
            if op == "remove":
                text = _read_text(project, collection)
                if str(collection).endswith(".go"):
                    _write_text(project, collection, remove_component_from_go(text, str(component)), overwrite=True)
                    return ok_envelope({"deleted": True, "id": go_id, "component": component, "undoable": False, "source": "disk"})
                _start, _end, block = find_instance_span(text, str(go_id))
                proto = instance_prototype(block)
                if proto:
                    _write_text(project, proto, remove_component_from_go(_read_text(project, proto), str(component)), overwrite=True)
                    return ok_envelope({"deleted": True, "id": go_id, "component": component, "undoable": False, "source": "disk"})
                go_text = decode_data_field(block) or ""
                new_block = replace_data_field(block, remove_component_from_go(go_text, str(component)))
                _write_text(project, collection, replace_instance_block(text, str(go_id), new_block), overwrite=True)
                return ok_envelope({"deleted": True, "id": go_id, "component": component, "undoable": False, "source": "disk"})
            if op == "set_property":
                key = params.get("property") or params.get("key")
                if not key:
                    return error_envelope("MISSING_PARAM", "set_property needs property")
                text = _read_text(project, collection)
                if str(collection).endswith(".go"):
                    go_text = text
                    write_path = collection
                    wrapper = None
                else:
                    _start, _end, block = find_instance_span(text, str(go_id))
                    proto = instance_prototype(block)
                    if proto:
                        go_text = _read_text(project, proto)
                        write_path = proto
                        wrapper = None
                    else:
                        go_text = decode_data_field(block) or ""
                        write_path = collection
                        wrapper = (text, str(go_id), block)
                marker = f'id: "{component}"'
                start = go_text.find(marker)
                if start < 0:
                    return error_envelope("NOT_FOUND", f"Component '{component}' was not found")
                comp_block = extract_brace_block(go_text, start)
                if not comp_block:
                    return error_envelope("NOT_FOUND", f"Component '{component}' was not found")
                inner = decode_data_field(comp_block)
                if inner is None:
                    return error_envelope("NOT_ALLOWED", "Referenced components have no embedded data to set")
                from agent_domain import set_scalar

                new_inner = set_scalar(inner, str(key), str(params.get("value", "")))
                new_comp = replace_data_field(comp_block, new_inner)
                go_text = go_text.replace(comp_block, new_comp, 1)
                if wrapper:
                    collection_text, instance_id, instance_block = wrapper
                    _write_text(
                        project,
                        write_path,
                        replace_instance_block(collection_text, instance_id, replace_data_field(instance_block, go_text)),
                        overwrite=True,
                    )
                else:
                    _write_text(project, write_path, go_text, overwrite=True)
                return ok_envelope(
                    {
                        "id": go_id,
                        "component": component,
                        "property": key,
                        "value": params.get("value"),
                        "undoable": False,
                        "source": "disk",
                    }
                )
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=["remove", "set_property"])
        if command == "script_manage" and params.get("op") == "detach":
            return disk_command(project, "component_manage", {**params, "op": "remove"})
        if command == "gameobject_create":
            collection = params.get("collection")
            proto = params.get("path")
            nested_collection = None
            if proto and str(proto).endswith(".collection"):
                if collection:
                    nested_collection = sanitize_proj_path(str(proto))
                    proto = None
                else:
                    collection = proto
                    proto = None
            elif proto and not str(proto).endswith(".go"):
                proto = None
            go_id = params.get("id") or "go"
            if not collection:
                return error_envelope("MISSING_PARAM", "Missing collection")
            position = params.get("position") or [0, 0, 0]
            text = _read_text(project, collection)
            try:
                find_instance_span(text, str(go_id))
                return error_envelope("INVALID_PARAM", f"Game object '{go_id}' already exists")
            except FileNotFoundError:
                pass
            x, y, z = (list(position) + [0, 0, 0])[:3]
            if nested_collection:
                block = (
                    f'\ncollection_instances {{\n'
                    f'  id: "{go_id}"\n'
                    f'  collection: "{nested_collection}"\n'
                    f"  position {{\n"
                    f"    x: {float(x)}\n"
                    f"    y: {float(y)}\n"
                    f"    z: {float(z)}\n"
                    f"  }}\n"
                    f"}}\n"
                )
            elif proto:
                proto = sanitize_proj_path(str(proto))
                block = (
                    f'\ninstances {{\n'
                    f'  id: "{go_id}"\n'
                    f'  prototype: "{proto}"\n'
                    f"  position {{\n"
                    f"    x: {float(x)}\n"
                    f"    y: {float(y)}\n"
                    f"    z: {float(z)}\n"
                    f"  }}\n"
                    f"}}\n"
                )
            else:
                block = (
                    f'\nembedded_instances {{\n'
                    f'  id: "{go_id}"\n'
                    f'  data: ""\n'
                    f"  position {{\n"
                    f"    x: {float(x)}\n"
                    f"    y: {float(y)}\n"
                    f"    z: {float(z)}\n"
                    f"  }}\n"
                    f"}}\n"
                )
            _write_text(project, collection, text.rstrip() + block, overwrite=True)
            data = {"id": go_id, "undoable": False, "source": "disk"}
            if nested_collection:
                data["kind"] = "collection_instance"
                data["collection"] = nested_collection
            elif proto:
                data["prototype"] = proto
                data["kind"] = "referenced"
            else:
                data["kind"] = "embedded"
            return ok_envelope(data)
        if command == "editor_manage":
            op = params.get("op")
            if op == "state":
                return disk_command(project, "editor_state", params)
            if op == "selection_get":
                return ok_envelope({"selection": [], "source": "disk"}, readiness="no_editor")
            if op == "quit":
                return error_envelope(
                    "EDITOR_UNREACHABLE",
                    "quit needs the open editor",
                    "Agents should leave the editor running.",
                )
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=["state", "selection_get", "quit"])
        if command == "project_manage":
            op = params.get("op")
            if op == "stop":
                from agent_runtime import stop_live_engine

                return stop_live_engine(project)
            game_project = project / "game.project"
            if not game_project.is_file():
                return error_envelope("NOT_FOUND", "/game.project was not found")
            text = game_project.read_text(encoding="utf-8")
            key = params.get("key") or (
                ".".join(params["path"]) if isinstance(params.get("path"), list) else params.get("path")
            )
            if op == "settings_get":
                if not key:
                    return error_envelope("MISSING_PARAM", "settings_get needs key")
                return ok_envelope({"path": key, "value": parse_game_project(text).get(key), "source": "disk"})
            if op == "settings_set":
                if not key:
                    return error_envelope("MISSING_PARAM", "settings_set needs key")
                game_project.write_text(
                    write_game_project_setting(text, str(key), params.get("value")),
                    encoding="utf-8",
                )
                return ok_envelope({"path": key, "value": params.get("value"), "undoable": False, "source": "disk"})
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}")
        if command == "session_activate":
            return ok_envelope({"activated": True, "sessions": 0, "source": "disk"}, readiness="no_editor")
        if command == "session_manage" and params.get("op") == "list":
            return ok_envelope({"sessions": []}, readiness="no_editor")
        return error_envelope(
            "EDITOR_UNREACHABLE",
            f"{command} needs the open editor graph (first-party /agent/command).",
            "Open the project in the Defold editor, or write the text files directly.",
        )
    except FileNotFoundError as error:
        return error_envelope("NOT_FOUND", f"File not found: {error}")
    except FileExistsError as error:
        return error_envelope("INVALID_PARAM", f"File already exists: {error}")
    except KeyError as error:
        if str(error) == "'OLD_TEXT_NOT_FOUND'":
            return error_envelope("OLD_TEXT_NOT_FOUND", "old_text was not found")
        raise
    except ValueError as error:
        if str(error) == "MULTIPLE_MATCHES":
            return error_envelope("MULTIPLE_MATCHES", "old_text matched more than once")
        return error_envelope("INVALID_PARAM", str(error))


DISK_COMMANDS = [
    "appmanifest_manage",
    "atlas_manage",
    "batch_execute",
    "camera_manage",
    "collection_get_hierarchy",
    "collection_manage",
    "collection_open",
    "collection_save",
    "collectionfactory_manage",
    "collectionproxy_manage",
    "collisionobject_manage",
    "component_add",
    "component_manage",
    "compute_manage",
    "cubemap_manage",
    "editor_manage",
    "editor_state",
    "factory_manage",
    "filesystem_manage",
    "display_profiles_manage",
    "font_manage",
    "gamepads_manage",
    "gameobject_create",
    "gameobject_get_properties",
    "gameobject_manage",
    "gui_manage",
    "input_binding_manage",
    "material_manage",
    "mesh_manage",
    "model_manage",
    "particlefx_manage",
    "project_build",
    "project_check",
    "project_doctor",
    "project_manage",
    "render_manage",
    "script_attach",
    "script_create",
    "script_manage",
    "script_patch",
    "session_activate",
    "session_manage",
    "sound_manage",
    "texture_profiles_manage",
    "tilemap_manage",
    "tilesource_manage",
]


def batch_execute_commands(project: Path, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    commands = params.get("commands")
    if not isinstance(commands, list):
        return error_envelope("MISSING_PARAM", "batch_execute needs commands[]")
    results: List[Any] = []
    for index, item in enumerate(commands):
        if not isinstance(item, dict):
            return error_envelope(
                "INVALID_PARAM",
                "Each commands[] item must be an object with command and params",
                atomic=False,
                undoable_separately=True,
                completed=results,
                failed_index=index,
            )
        name = item.get("command")
        if not name:
            return error_envelope(
                "MISSING_PARAM",
                "commands[] item needs command",
                atomic=False,
                undoable_separately=True,
                completed=results,
                failed_index=index,
            )
        if name == "batch_execute":
            return error_envelope("NOT_ALLOWED", "Nested batch_execute is not allowed")
        step = dispatch_command(project, str(name), item.get("params") or {}, timeout)
        if step.get("status") != "ok":
            error = dict(step.get("error") or {"code": "HANDLER_ERROR", "message": "batch step failed"})
            extra = dict(error.get("data") or {})
            extra.update(
                {
                    "completed": results,
                    "failed_index": index,
                    "atomic": False,
                    "undoable_separately": True,
                }
            )
            error["data"] = extra
            return envelope("error", error=error, readiness=step.get("readiness") or "ready")
        results.append(step.get("data"))
    return ok_envelope(
        {
            "results": results,
            "atomic": False,
            "undoable_separately": True,
            "source": "local",
        }
    )


def intercept_existing_http(
    project: Path,
    command: str,
    params: Dict[str, Any],
    timeout: float,
) -> Optional[Dict[str, Any]]:
    params = params or {}
    if command in {"project_build", "project_check"}:
        from defold_agent import check_project, find_bob

        bob = find_bob(params.get("bob"), project)
        with BuildingLock(project):
            payload = check_project(project, bob, prefer_editor=True, timeout=timeout)
        data = {
            "success": bool(payload.get("success")),
            "launched": False,
            "check_only": True,
            "source": payload.get("source") or "bob",
            "issues": payload.get("issues") or [],
        }
        if data["success"]:
            return ok_envelope(data)
        return error_envelope(
            "HANDLER_ERROR",
            "check failed; the game was not launched",
            issues=data["issues"],
            launched=False,
            check_only=True,
        )
    if command == "logs_read":
        from agent_runtime import read_engine_log_lines

        offset = max(int(params.get("offset") or 0), 0)
        limit = max(int(params.get("limit") or 200), 1)
        wanted = str(params.get("source") or "all")
        got = None if wanted == "engine" else editor_get(project, "/console", timeout)
        lines: List[str] = []
        source = "engine-log"
        if wanted == "editor" and got is None:
            return error_envelope("EDITOR_UNREACHABLE", "logs_read source=editor needs the open editor")
        if got is not None:
            status, body = got
            if status == 200 and isinstance(body, dict):
                lines = [str(line) for line in (body.get("lines") or []) if line]
                source = "console"
            elif status != 200 and wanted == "editor":
                return error_envelope("HANDLER_ERROR", f"GET /console failed ({status})")
            elif status != 200 and not (project / ".internal" / "agent" / "engine.log").is_file():
                return error_envelope("HANDLER_ERROR", f"GET /console failed ({status})")
        if source != "console":
            lines = read_engine_log_lines(project, None)
            if not lines and got is None and wanted != "engine":
                return None
        total = len(lines)
        end = total - offset
        start = max(end - limit, 0)
        sliced = lines[start:end] if end > 0 else []
        from defold_agent import parse_log

        return ok_envelope(
            {
                "lines": sliced,
                "total": total,
                "offset": offset,
                "limit": limit,
                "truncated": start > 0,
                "source": source,
                "issues": parse_log("\n".join(sliced)),
            }
        )
    if command == "editor_preview":
        path = params.get("path") or params.get("resource")
        if not path:
            return error_envelope("MISSING_PARAM", "editor_preview needs path")
        dest = Path(params["dest"]) if params.get("dest") else project / ".internal" / "agent" / "preview.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        endpoint = read_editor_endpoint(project)
        if not endpoint:
            return None
        url, token = endpoint
        width = int(params.get("width") or 1280)
        height = int(params.get("height") or 720)
        request = urllib.request.Request(
            f"{url}/preview{sanitize_proj_path(path)}?width={width}&height={height}"
        )
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                dest.write_bytes(response.read())
            return ok_envelope({"path": sanitize_proj_path(path), "screenshot": str(dest), "source": "editor-preview"})
        except urllib.error.HTTPError as error:
            return error_envelope("HANDLER_ERROR", f"GET /preview failed ({error.code})")
    if command == "api_manage" and params.get("op", "get") == "get":
        q = params.get("q") or params.get("query") or ""
        environment = params.get("environment") or "runtime"
        language = params.get("language") or "Lua"
        query = urllib.parse.urlencode(
            {"environment": environment, "language": language, "q": q}
        )
        got = editor_get(project, f"/ref?{query}", timeout)
        if got is None:
            return None
        status, body = got
        if status == 200 and isinstance(body, list):
            return ok_envelope({"query": query, "results": body[:40], "source": "ref"})
        return error_envelope("HANDLER_ERROR", f"GET /ref failed ({status})")
    return None


RUNTIME_COMMANDS = {
    "runtime_observe",
    "runtime_snapshot_query",
    "runtime_get_hierarchy",
    "runtime_get_properties",
    "runtime_state",
    "runtime_diff",
    "runtime_screenshot",
    "project_run",
    "project_stop",
    "project_doctor",
    "doctor",
}


def handle_runtime_command(
    project: Path,
    command: str,
    params: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    from agent_runtime import (
        query_snapshot,
        runtime_get_hierarchy,
        runtime_get_properties,
        runtime_diff,
        runtime_screenshot,
        runtime_state_payload,
    )

    if command == "runtime_state":
        return runtime_state_payload(project)
    if command == "runtime_diff":
        return runtime_diff(project, params)
    if command == "runtime_screenshot":
        return runtime_screenshot(project, params)
    if command in {"project_doctor", "doctor"}:
        from defold_agent import project_doctor

        return project_doctor(project, params)
    if command == "runtime_snapshot_query":
        return query_snapshot(project, params)
    if command == "runtime_get_hierarchy":
        return runtime_get_hierarchy(project, params)
    if command == "runtime_get_properties":
        return runtime_get_properties(project, params)
    if command == "runtime_observe":
        from defold_agent import observe_runtime

        return observe_runtime(project, params, timeout)
    if command == "project_run":
        from defold_agent import project_run

        return project_run(project, params, timeout)
    if command == "project_stop":
        from agent_runtime import stop_live_engine

        return stop_live_engine(project)
    return error_envelope("UNKNOWN_COMMAND", f"Unknown runtime command: {command}")


def dispatch_command(
    project: Path,
    command: str,
    params: Optional[Dict[str, Any]],
    timeout: float,
) -> Dict[str, Any]:
    command, params = apply_alias(command, params or {})
    blocked = reject_write_if_gated(project, command, params)
    if blocked is not None:
        return blocked
    if command == "editor_manage" and params.get("op") == "mcp_config":
        from agent_mcp import mcp_client_config

        kind = str(params.get("format") or "cursor")
        agent_py = Path(__file__).resolve().parent / "defold_agent.py"
        text = mcp_client_config(agent_py, project, kind)
        return overlay_readiness(
            project,
            ok_envelope({"format": kind, "text": text, "http": False, "source": "local"}),
        )
    if command == "project_manage" and params.get("op") == "stop":
        from agent_runtime import live_status, stop_live_engine

        if live_status(project).get("alive") or read_editor_endpoint(project) is None:
            return overlay_readiness(project, stop_live_engine(project))
    if command == "batch_execute":
        return batch_execute_commands(project, params, timeout)
    if command == "session_activate" and params.get("url"):
        from agent_runtime import activate_target

        return activate_target(project, params)
    if command in RUNTIME_COMMANDS:
        return handle_runtime_command(project, command, params, timeout)
    from agent_domain import DOMAIN_COMMANDS, handle_domain_command

    if command in DOMAIN_COMMANDS:
        result = handle_domain_command(project, command, params)
        path = (result.get("data") or {}).get("path")
        if result.get("status") == "ok" and path and result.get("data", {}).get("undoable") is False:
            endpoint = read_editor_endpoint(project)
            if endpoint:
                try:
                    text = project_file(project, path).read_text(encoding="utf-8")
                    editor_command(
                        project,
                        "filesystem_manage",
                        {"op": "write_text", "path": path, "text": text},
                        timeout,
                    )
                    result["data"]["source"] = "editor"
                except OSError:
                    pass
        return result
    intercepted = intercept_existing_http(project, command, params, timeout)
    if intercepted is not None:
        result = intercepted
    else:
        editor = editor_command(project, command, params, timeout)
        result = editor if editor is not None else disk_command(project, command, params)
    if command in {"editor_state", "editor_manage"} and result.get("status") == "ok" and isinstance(result.get("data"), dict):
        if command == "editor_state" or params.get("op") == "state":
            from agent_runtime import live_status
            from defold_agent import doctor_payload

            result["data"]["engine"] = live_status(project)
            result["data"]["game_status"] = game_status_payload(project)
            result["data"]["ready"] = doctor_payload(project).get("ready") or {}
    return overlay_readiness(project, result)
