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
    readiness = "no_editor" if code == "EDITOR_UNREACHABLE" else "ready"
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


def parse_collection_hierarchy(text: str, path: str) -> Dict[str, Any]:
    children = [{"id": match, "type": "gameobject"} for match in RE_COLLECTION_ID.findall(text)]
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
            if not path:
                return error_envelope("MISSING_PARAM", "Missing path")
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
                            rel = "/" + file_path.relative_to(project).as_posix()
                            matches.append(rel)
                            if len(matches) >= 100:
                                return ok_envelope(
                                    {"matches": matches, "truncated": True, "limit": 100, "source": "disk"}
                                )
                return ok_envelope({"matches": matches, "truncated": False, "limit": 100, "source": "disk"})
            return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=["read_text", "write_text", "search"])
        if command == "collection_manage" and params.get("op") == "create":
            path = params.get("path")
            if not path:
                return error_envelope("MISSING_PARAM", "Missing path")
            name = params.get("name") or Path(path).stem
            _write_text(project, path, COLLECTION_TEMPLATE.format(name=name), overwrite=False)
            return ok_envelope({"path": sanitize_proj_path(path), "source": "disk"})
        if command == "gameobject_create":
            path = params.get("collection") or params.get("path")
            go_id = params.get("id") or "go"
            if not path:
                return error_envelope("MISSING_PARAM", "Missing collection")
            position = params.get("position") or [0, 0, 0]
            text = _read_text(project, path)
            if f'id: "{go_id}"' in text:
                return error_envelope("INVALID_PARAM", f"Game object '{go_id}' already exists")
            x, y, z = (list(position) + [0, 0, 0])[:3]
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
            _write_text(project, path, text.rstrip() + block, overwrite=True)
            return ok_envelope({"id": go_id, "undoable": False, "source": "disk"})
        if command == "project_manage":
            op = params.get("op")
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
    "batch_execute",
    "collection_get_hierarchy",
    "collection_manage",
    "editor_state",
    "filesystem_manage",
    "gameobject_create",
    "project_build",
    "project_check",
    "project_manage",
    "script_create",
    "script_manage",
    "script_patch",
    "session_activate",
    "session_manage",
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
        got = editor_get(project, "/console", timeout)
        lines: List[str] = []
        source = "engine-log"
        if got is not None:
            status, body = got
            if status == 200 and isinstance(body, dict):
                lines = [str(line) for line in (body.get("lines") or []) if line]
                source = "console"
            elif status != 200 and not (project / ".internal" / "agent" / "engine.log").is_file():
                return error_envelope("HANDLER_ERROR", f"GET /console failed ({status})")
        if source != "console":
            lines = read_engine_log_lines(project, None)
            if not lines and got is None:
                return None
        total = len(lines)
        end = total - offset
        start = max(end - limit, 0)
        sliced = lines[start:end] if end > 0 else []
        return ok_envelope(
            {
                "lines": sliced,
                "total": total,
                "offset": offset,
                "limit": limit,
                "truncated": start > 0,
                "source": source,
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
    "project_run",
    "project_stop",
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
        runtime_state_payload,
    )

    if command == "runtime_state":
        return runtime_state_payload(project)
    if command == "runtime_diff":
        return runtime_diff(project, params)
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
    params = params or {}
    if command == "batch_execute":
        return batch_execute_commands(project, params, timeout)
    if command in RUNTIME_COMMANDS:
        return handle_runtime_command(project, command, params, timeout)
    intercepted = intercept_existing_http(project, command, params, timeout)
    if intercepted is not None:
        result = intercepted
    else:
        editor = editor_command(project, command, params, timeout)
        result = editor if editor is not None else disk_command(project, command, params)
    if command == "editor_state" and result.get("status") == "ok" and isinstance(result.get("data"), dict):
        from agent_runtime import live_status

        result["data"]["engine"] = live_status(project)
    return result
