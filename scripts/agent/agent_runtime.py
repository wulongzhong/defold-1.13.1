# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Runtime snapshot files and precise queries. Full trees stay on disk."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent_ops import error_envelope, ok_envelope


SNAPSHOT_SCHEMA = 1
SNAPSHOTS_DIR = Path(".internal") / "agent" / "snapshots"
ENGINE_JSON = Path(".internal") / "agent" / "engine.json"
TARGET_JSON = Path(".internal") / "agent" / "target.json"
SESSION_JSON = Path(".internal") / "agent" / "session.json"
SESSION_PIN = Path(".internal") / "agent" / "session_pin.json"
CONTROL_DIR = Path(".internal") / "agent" / "control"
ENGINE_LOG = Path(".internal") / "agent" / "engine.log"
DUMP_WAIT_SEC = 8.0
RETAIN = 8
INLINE_BUDGET = 48 * 1024
NODE_HARD_CAP = 256
PREVIEW_LIMIT = 80
LOG_LINE_LIMIT = 40
HIERARCHY_DEFAULT_LIMIT = 200
FIND_DEFAULT_LIMIT = 50

RE_SERVICE_PORT = re.compile(r"Engine service started on port (\d+)")

QUERY_OPS = (
    "list",
    "summary",
    "list_ids",
    "get_node",
    "get_subtree",
    "find",
    "get_path",
    "compare_authoring",
)


def snapshots_dir(project: Path) -> Path:
    return project / SNAPSHOTS_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_snapshot_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = datetime.now(timezone.utc).strftime("%f")[-4:]
    return f"{stamp}-{suffix}"


def is_snapshot_path(project: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to((project / SNAPSHOTS_DIR).resolve())
        return True
    except (OSError, ValueError):
        return False


def walk_nodes(node: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(node, dict):
        return
    yield node
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            yield from walk_nodes(child)


def node_id(node: Dict[str, Any]) -> str:
    value = node.get("id")
    return "" if value is None else str(value)


def node_type(node: Dict[str, Any]) -> str:
    value = node.get("type")
    return "" if value is None else str(value)


def normalize_node_id(value: Any) -> str:
    return str(value or "").strip().lstrip("/")


def ids_match(left: Any, right: Any) -> bool:
    a = normalize_node_id(left)
    b = normalize_node_id(right)
    return bool(a) and a == b


GO_NODE_TYPES = {"goc", "gameobject", "go"}
COMPONENT_NODE_TYPES = {
    "scriptc",
    "labelc",
    "camerac",
    "spritec",
    "soundc",
    "modelc",
    "collisionobjectc",
    "factoryc",
    "collectionfactoryc",
    "particlefxc",
    "tilemapc",
    "guic",
    "meshc",
}


def is_go_node(node: Dict[str, Any]) -> bool:
    kind = node_type(node)
    if kind in GO_NODE_TYPES:
        return True
    ident = node_id(node)
    return bool(ident.startswith("/")) and kind not in COMPONENT_NODE_TYPES and kind != "collectionc"


def count_nodes(graph: Any) -> int:
    return sum(1 for _ in walk_nodes(graph))


def type_histogram(graph: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for node in walk_nodes(graph):
        kind = node_type(node) or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def root_ids(graph: Any) -> List[str]:
    if not isinstance(graph, dict):
        return []
    ids = [node_id(graph)] if node_id(graph) else []
    children = graph.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict) and node_id(child):
                ids.append(node_id(child))
    return ids[:32]


def shallow_node(node: Dict[str, Any]) -> Dict[str, Any]:
    children = node.get("children")
    child_ids: List[str] = []
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict) and node_id(child):
                child_ids.append(node_id(child))
    item: Dict[str, Any] = {
        "id": node_id(node),
        "type": node_type(node),
        "resource": node.get("resource"),
        "world_position": node.get("world_position"),
        "children": child_ids,
    }
    return item


def collect_preview(graph: Any, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    nodes: List[Dict[str, Any]] = []
    truncated = False
    for node in walk_nodes(graph):
        if len(nodes) >= limit:
            truncated = True
            break
        nodes.append(shallow_node(node))
    return nodes, truncated


def scene_graph_of(snapshot: Dict[str, Any]) -> Any:
    return snapshot.get("scene_graph")


def summarize_graph(graph: Any) -> Dict[str, Any]:
    return {
        "roots": root_ids(graph),
        "types": type_histogram(graph),
        "node_count": count_nodes(graph),
    }


def encoded_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def prune_snapshots(project: Path) -> None:
    directory = snapshots_dir(project)
    if not directory.is_dir():
        return
    files = sorted(
        (path for path in directory.glob("*.json") if path.name != "latest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in files[RETAIN:]:
        stale.unlink(missing_ok=True)
        png = stale.with_suffix(".png")
        if png.is_file():
            png.unlink(missing_ok=True)


def write_engine_json(project: Path, log: str) -> Optional[Dict[str, Any]]:
    match = RE_SERVICE_PORT.search(log or "")
    if not match:
        return None
    port = int(match.group(1))
    payload = {
        "url": f"http://127.0.0.1:{port}",
        "port": port,
        "captured_at": utc_now(),
    }
    existing = read_engine_json(project) or {}
    if existing.get("mode") == "live" and pid_alive(int(existing.get("pid") or 0)):
        existing.update(payload)
        payload = existing
    return write_engine_record(project, payload)


def read_engine_json(project: Path) -> Optional[Dict[str, Any]]:
    path = project / ENGINE_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def wrap_engine_dump(
    project: Path,
    raw_path: Path,
    *,
    mode: str,
    frame: Optional[int],
    target: Optional[Dict[str, Any]],
    issues: Optional[List[Any]] = None,
    screenshot: Optional[Path] = None,
    snapshot_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not raw_path.is_file():
        raise FileNotFoundError(str(raw_path))
    scene_graph = json.loads(raw_path.read_text(encoding="utf-8"))
    snap_id = snapshot_id or new_snapshot_id()
    directory = snapshots_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{snap_id}.json"
    record: Dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "id": snap_id,
        "source": "runtime",
        "mode": mode,
        "captured_at": utc_now(),
        "frame": frame,
        "target": target,
        "scene_graph": scene_graph,
        "issues": issues or [],
    }
    if screenshot and screenshot.is_file():
        png_dest = directory / f"{snap_id}.png"
        if screenshot.resolve() != png_dest.resolve():
            shutil.copy2(screenshot, png_dest)
        record["screenshot"] = str(png_dest)
        latest_png = directory / "latest.png"
        shutil.copy2(png_dest, latest_png)
    dest.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copy2(dest, directory / "latest.json")
    prune_snapshots(project)
    return record


def list_snapshot_records(project: Path) -> List[Dict[str, Any]]:
    directory = snapshots_dir(project)
    if not directory.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.name in {"latest.json", "_raw.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("schema") != SNAPSHOT_SCHEMA or not data.get("scene_graph"):
            continue
        records.append(
            {
                "id": data.get("id") or path.stem,
                "path": str(path),
                "bytes": path.stat().st_size,
                "node_count": count_nodes(data.get("scene_graph")),
                "captured_at": data.get("captured_at"),
                "mode": data.get("mode"),
            }
        )
        if len(records) >= RETAIN:
            break
    return records


def resolve_snapshot_path(project: Path, snapshot: Optional[str]) -> Path:
    directory = snapshots_dir(project)
    token = (snapshot or "latest").strip()
    if token in {"", "latest"}:
        path = directory / "latest.json"
        if not path.is_file():
            raise FileNotFoundError("latest")
        return path
    candidate = Path(token)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    if token.startswith("/") and not token.startswith("//"):
        rel = project / token.lstrip("/")
        if rel.is_file():
            return rel
    by_id = directory / f"{token}.json"
    if by_id.is_file():
        return by_id
    raise FileNotFoundError(token)


def load_snapshot(project: Path, snapshot: Optional[str]) -> Dict[str, Any]:
    path = resolve_snapshot_path(project, snapshot)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("snapshot is not an object")
    data["_path"] = str(path)
    return data


def find_node(graph: Any, go_id: str, component: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not go_id:
        return None
    matches = [node for node in walk_nodes(graph) if ids_match(node_id(node), go_id)]
    if not matches:
        return None
    node = next((item for item in matches if is_go_node(item)), None)
    if node is None:
        node = next((item for item in matches if item.get("world_position") is not None), matches[0])
    if not component:
        return node
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict) and (
                ids_match(node_id(child), component) or node_type(child) == component
            ):
                return child
    return None


def subtree(node: Dict[str, Any], depth: int, limit: int) -> Tuple[Dict[str, Any], bool, int]:
    used = 1
    truncated = False

    def copy_node(current: Dict[str, Any], remaining: int) -> Dict[str, Any]:
        nonlocal used, truncated
        item = {key: value for key, value in current.items() if key != "children"}
        children = current.get("children")
        if remaining <= 0 or not isinstance(children, list):
            if isinstance(children, list) and children:
                item["children"] = []
                truncated = True
            else:
                item["children"] = []
            return item
        copied: List[Any] = []
        for child in children:
            if used >= limit:
                truncated = True
                break
            if not isinstance(child, dict):
                continue
            used += 1
            copied.append(copy_node(child, remaining - 1))
        item["children"] = copied
        return item

    return copy_node(node, max(depth, 0)), truncated, used


def json_pointer(document: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with /")
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdigit():
                raise KeyError(pointer)
            index = int(token)
            if index >= len(current):
                raise KeyError(pointer)
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                raise KeyError(pointer)
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def match_find(node: Dict[str, Any], params: Dict[str, Any]) -> bool:
    wanted_type = params.get("type")
    if wanted_type and node_type(node) != str(wanted_type):
        return False
    glob = params.get("id_glob")
    if glob and not fnmatch.fnmatch(node_id(node), str(glob)):
        return False
    prop = params.get("has_property")
    if prop and prop not in node:
        return False
    return True


def snapshot_handle(record: Dict[str, Any], inline: str) -> Dict[str, Any]:
    path = record.get("_path") or ""
    graph = scene_graph_of(record)
    handle: Dict[str, Any] = {
        "id": record.get("id"),
        "path": path,
        "bytes": Path(path).stat().st_size if path and Path(path).is_file() else encoded_size(record),
        "node_count": count_nodes(graph),
        "inline": inline,
    }
    if record.get("screenshot"):
        handle["screenshot"] = record["screenshot"]
    return handle


def observe_envelope(
    record: Dict[str, Any],
    *,
    inline: str,
    preview_limit: int = PREVIEW_LIMIT,
    log_lines: Optional[List[str]] = None,
    log_source: str = "engine-stdout",
    alive: bool = False,
) -> Dict[str, Any]:
    graph = scene_graph_of(record)
    summary = summarize_graph(graph)
    handle = snapshot_handle(record, inline)
    data: Dict[str, Any] = {
        "source": "runtime",
        "mode": record.get("mode") or "batch",
        "target": dict(record.get("target") or {}),
        "frame": record.get("frame"),
        "snapshot": handle,
        "summary": {"roots": summary["roots"], "types": summary["types"]},
        "issues": record.get("issues") or [],
        "logs": {
            "source": log_source,
            "total": len(log_lines or []),
            "lines": (log_lines or [])[-LOG_LINE_LIMIT:],
        },
    }
    data["target"]["alive"] = alive
    if record.get("screenshot"):
        data["screenshot"] = record["screenshot"]
    if inline == "preview":
        nodes, truncated = collect_preview(graph, preview_limit)
        data["hierarchy"] = {
            "truncated": truncated,
            "offset": 0,
            "limit": preview_limit,
            "total_estimate": summary["node_count"],
            "nodes": nodes,
        }
    elif inline == "full":
        inline_data = {key: value for key, value in record.items() if not str(key).startswith("_")}
        if encoded_size(inline_data) > INLINE_BUDGET:
            return error_envelope(
                "INLINE_TOO_LARGE",
                f"Snapshot is larger than {INLINE_BUDGET} bytes.",
                "Query the snapshot file with runtime_snapshot_query.",
                path=handle.get("path"),
                id=handle.get("id"),
                bytes=handle.get("bytes"),
            )
        data["snapshot"]["inline_data"] = inline_data
    return ok_envelope(data, readiness="no_runtime" if not alive else "running")


def query_snapshot(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    op = params.get("op") or "summary"
    if op not in QUERY_OPS:
        return error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=list(QUERY_OPS))
    if op == "list":
        return ok_envelope({"snapshots": list_snapshot_records(project), "source": "runtime"})
    try:
        record = load_snapshot(project, params.get("snapshot"))
    except FileNotFoundError as error:
        return error_envelope(
            "SNAPSHOT_NOT_FOUND",
            f"Snapshot not found: {error}",
            "Call runtime_observe first, or pass a snapshot id.",
        )
    except (json.JSONDecodeError, ValueError) as error:
        return error_envelope("HANDLER_ERROR", f"Snapshot is not valid JSON: {error}")

    graph = scene_graph_of(record)
    if op == "summary":
        data = summarize_graph(graph)
        data["snapshot"] = snapshot_handle(record, "summary")
        data["source"] = "runtime"
        return ok_envelope(data)

    if op == "list_ids":
        offset = int(params.get("offset") or 0)
        limit = int(params.get("limit") or HIERARCHY_DEFAULT_LIMIT)
        wanted_type = params.get("type")
        glob = params.get("id_glob")
        ids: List[str] = []
        for node in walk_nodes(graph):
            ident = node_id(node)
            if not ident:
                continue
            if wanted_type and node_type(node) != str(wanted_type):
                continue
            if glob and not fnmatch.fnmatch(ident, str(glob)):
                continue
            ids.append(ident)
        sliced = ids[offset : offset + limit]
        return ok_envelope(
            {
                "ids": sliced,
                "offset": offset,
                "limit": limit,
                "total": len(ids),
                "truncated": offset + len(sliced) < len(ids),
                "source": "runtime",
                "snapshot": record.get("id"),
            }
        )

    if op == "get_node":
        go_id = params.get("id")
        if not go_id:
            return error_envelope("MISSING_PARAM", "get_node needs id")
        node = find_node(graph, str(go_id), params.get("component"))
        if node is None:
            return error_envelope(
                "NOT_FOUND",
                f"Runtime node '{go_id}' was not found",
                "Call runtime_snapshot_query op=list_ids or find.",
            )
        return ok_envelope({"node": node, "source": "runtime", "snapshot": record.get("id")})

    if op == "get_subtree":
        go_id = params.get("id")
        if not go_id:
            return error_envelope("MISSING_PARAM", "get_subtree needs id")
        node = find_node(graph, str(go_id))
        if node is None:
            return error_envelope("NOT_FOUND", f"Runtime node '{go_id}' was not found")
        depth = int(params.get("depth") if params.get("depth") is not None else 8)
        limit = min(int(params.get("limit") or PREVIEW_LIMIT), NODE_HARD_CAP)
        tree, truncated, used = subtree(node, depth, limit)
        return ok_envelope(
            {
                "node": tree,
                "truncated": truncated,
                "count": used,
                "limit": limit,
                "source": "runtime",
                "snapshot": record.get("id"),
            }
        )

    if op == "find":
        limit = min(int(params.get("limit") or FIND_DEFAULT_LIMIT), NODE_HARD_CAP)
        matches: List[Dict[str, Any]] = []
        total = 0
        for node in walk_nodes(graph):
            if not match_find(node, params):
                continue
            total += 1
            if len(matches) < limit:
                matches.append(shallow_node(node))
        return ok_envelope(
            {
                "matches": matches,
                "total": total,
                "limit": limit,
                "truncated": total > len(matches),
                "source": "runtime",
                "snapshot": record.get("id"),
            }
        )

    if op == "get_path":
        pointer = params.get("path") or params.get("pointer")
        if not pointer:
            return error_envelope("MISSING_PARAM", "get_path needs path")
        document = {key: value for key, value in record.items() if not str(key).startswith("_")}
        try:
            value = json_pointer(document, str(pointer))
        except ValueError as error:
            return error_envelope("INVALID_PARAM", str(error))
        except KeyError:
            return error_envelope("NOT_FOUND", f"JSON Pointer not found: {pointer}")
        if encoded_size(value) > INLINE_BUDGET and not params.get("truncate", True):
            return error_envelope(
                "INLINE_TOO_LARGE",
                f"Pointer result is larger than {INLINE_BUDGET} bytes.",
                "Narrow the pointer or raise no truncate only for small slices.",
            )
        if encoded_size(value) > INLINE_BUDGET:
            return error_envelope(
                "INLINE_TOO_LARGE",
                f"Pointer result is larger than {INLINE_BUDGET} bytes.",
                "Use a narrower JSON Pointer.",
            )
        return ok_envelope({"path": pointer, "value": value, "source": "runtime", "snapshot": record.get("id")})

    if op == "compare_authoring":
        return compare_authoring(project, record, params)

    return error_envelope("UNKNOWN_OP", f"Unknown op: {op}")


def find_runtime_parent(graph: Any, target_id: str) -> Optional[str]:
    for node in walk_nodes(graph):
        children = node.get("children")
        if not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, dict) and ids_match(node_id(child), target_id):
                if node_type(node) == "collectionc":
                    return None
                parent = node_id(node)
                return parent or None
    return None


def _as_vec(value: Any) -> Optional[List[float]]:
    if isinstance(value, (int, float)):
        return [float(value), float(value), float(value)]
    if isinstance(value, list) and value:
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None
    return None


def compare_authoring(project: Path, record: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    from agent_ops import parse_collection_hierarchy, parse_gameobject_properties, parse_game_project, sanitize_proj_path

    collection = params.get("collection") or params.get("path")
    if not collection:
        game_project = project / "game.project"
        if game_project.is_file():
            collection = parse_game_project(game_project.read_text(encoding="utf-8")).get("bootstrap.main_collection")
    if not collection:
        return error_envelope("MISSING_PARAM", "compare_authoring needs collection")
    collection = sanitize_proj_path(str(collection))
    try:
        text = (project / collection.lstrip("/")).read_text(encoding="utf-8")
    except OSError:
        return error_envelope("NOT_FOUND", f"Collection not found: {collection}")
    graph = scene_graph_of(record)
    runtime_idx = index_nodes_by_id(graph)
    wanted = params.get("id")
    authoring_ids = [
        str(node.get("id"))
        for node in (parse_collection_hierarchy(text, collection).get("children") or [])
        if node.get("id")
    ]
    if wanted:
        ids = [str(wanted)]
    else:
        ids = authoring_ids or [nid for nid in runtime_idx if node_type(runtime_idx[nid]) in {"", "goc"}]
    limit = min(int(params.get("limit") or PREVIEW_LIMIT), NODE_HARD_CAP)
    items: List[Dict[str, Any]] = []
    for go_id in ids[:limit]:
        authoring = parse_gameobject_properties(text, collection, go_id, project)
        runtime = runtime_idx.get(go_id)
        deltas: List[Dict[str, Any]] = []
        if authoring is None:
            deltas.append({"property": "id", "authoring": None, "runtime": go_id if runtime else None})
        if runtime is None:
            deltas.append({"property": "missing_runtime", "authoring": go_id, "runtime": None})
        props = (authoring or {}).get("properties") or {}
        if authoring is not None and runtime is not None:
            for key in ("position", "rotation", "scale"):
                left = props.get(key)
                right = runtime.get(key)
                left_vec = _as_vec(left)
                right_vec = _as_vec(right)
                changed = vec_changed(left_vec, right_vec) if left_vec is not None and right_vec is not None else left != right and right is not None
                if changed or (left is not None and right is None and key == "position"):
                    item = {"property": key, "authoring": left, "runtime": right}
                    if key == "position":
                        item["world_position"] = runtime.get("world_position")
                    if left is not None and (right is None or changed):
                        deltas.append(item)
            author_parent = authoring.get("parent")
            runtime_parent = find_runtime_parent(graph, go_id)
            if (author_parent or None) != (runtime_parent or None):
                deltas.append({"property": "parent", "authoring": author_parent, "runtime": runtime_parent})
            if left_vec is not None and runtime.get("world_position") is not None:
                if vec_changed(left_vec, _as_vec(runtime.get("world_position"))):
                    if not any(delta.get("property") == "position" for delta in deltas):
                        deltas.append(
                            {
                                "property": "world_position",
                                "authoring": left,
                                "runtime": runtime.get("world_position"),
                            }
                        )
        items.append(
            {
                "id": go_id,
                "authoring": {
                    "source": "disk",
                    "position": props.get("position"),
                    "rotation": props.get("rotation"),
                    "scale": props.get("scale"),
                    "parent": (authoring or {}).get("parent"),
                }
                if authoring
                else None,
                "runtime": {
                    "source": "runtime",
                    "position": runtime.get("position"),
                    "world_position": runtime.get("world_position"),
                    "rotation": runtime.get("rotation"),
                    "scale": runtime.get("scale"),
                    "parent": find_runtime_parent(graph, go_id),
                }
                if runtime
                else None,
                "deltas": deltas,
                "same": not deltas,
            }
        )
    return ok_envelope(
        {
            "source": "compare",
            "collection": collection,
            "snapshot": record.get("id"),
            "items": items,
            "total": len(ids),
            "limit": limit,
            "truncated": len(ids) > limit,
        }
    )


def control_dir(project: Path) -> Path:
    return project / CONTROL_DIR


def engine_log_path(project: Path) -> Path:
    return project / ENGINE_LOG


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_query = 0x1000
        still_active = 259
        handle = kernel32.OpenProcess(process_query, wintypes.BOOL(False), wintypes.DWORD(int(pid)))
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == still_active
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live_status(project: Path) -> Dict[str, Any]:
    engine = read_engine_json(project) or {}
    pid = int(engine["pid"]) if engine.get("pid") else 0
    alive = bool(engine.get("mode") == "live" and pid_alive(pid))
    status = dict(engine)
    status["alive"] = alive
    status["pid"] = pid or None
    return status


def write_engine_record(project: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    dest = project / ENGINE_JSON
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def request_live_file(project: Path, kind: str, dest: Path, timeout: float = DUMP_WAIT_SEC) -> Path:
    directory = control_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    request = directory / f"{kind}.request"
    ready = directory / f"{kind}.ready"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    if ready.exists():
        ready.unlink()
    tmp = directory / f"{kind}.request.tmp"
    tmp.write_text(str(dest), encoding="utf-8")
    tmp.replace(request)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ready.is_file():
            text = ready.read_text(encoding="utf-8", errors="replace")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            status = lines[0] if lines else ""
            ready.unlink(missing_ok=True)
            if status != "OK" or not dest.is_file():
                raise RuntimeError(f"Live {kind} failed")
            return dest
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {kind}.ready")


def request_live_dump(project: Path, dest: Path, timeout: float = DUMP_WAIT_SEC) -> Path:
    return request_live_file(project, "dump", dest, timeout)


def request_live_screenshot(project: Path, dest: Path, timeout: float = DUMP_WAIT_SEC) -> Path:
    return request_live_file(project, "screenshot", dest, timeout)


def observe_from_live(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    status = live_status(project)
    if not status.get("alive"):
        return error_envelope(
            "ENGINE_NOT_RUNNING",
            "No live dmengine is running.",
            "Call project_run with mode=live, or omit live and use batch observe.",
        )
    raw = snapshots_dir(project) / "_raw.json"
    try:
        request_live_dump(project, raw, float(params.get("timeout") or DUMP_WAIT_SEC))
    except TimeoutError:
        return error_envelope(
            "RUNTIME_DUMP_MISSING",
            "Live engine did not write dump.ready. Rebuild dmengine with --agent-control.",
        )
    except RuntimeError as error:
        return error_envelope("RUNTIME_DUMP_MISSING", str(error))
    shot = None
    include = params.get("include") or []
    if isinstance(include, str):
        include = [include]
    if "screenshot" in include:
        dest = params.get("dest")
        shot = Path(dest) if dest else snapshots_dir(project) / "_shot.png"
        if not shot.is_absolute():
            shot = project / shot
        try:
            request_live_screenshot(project, shot, float(params.get("timeout") or DUMP_WAIT_SEC))
        except (TimeoutError, RuntimeError):
            shot = None
    record = wrap_engine_dump(
        project,
        raw,
        mode="live",
        frame=None,
        target={"pid": status.get("pid"), "mode": "live"},
        issues=[],
        screenshot=shot if shot and shot.is_file() else None,
    )
    log_lines = read_engine_log_lines(project)
    return observe_envelope(
        {**record, "_path": str(snapshots_dir(project) / f"{record['id']}.json")},
        inline=params.get("inline") or "summary",
        log_lines=log_lines,
        alive=True,
    )


def stop_live_engine(project: Path) -> Dict[str, Any]:
    status = live_status(project)
    pid = status.get("pid")
    if not pid:
        return error_envelope("ENGINE_NOT_RUNNING", "No live engine pid is recorded.")
    if status.get("alive"):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        else:
            try:
                os.kill(int(pid), 15)
            except OSError:
                pass
        deadline = time.time() + 3
        while time.time() < deadline and pid_alive(int(pid)):
            time.sleep(0.05)
        if pid_alive(int(pid)) and os.name != "nt":
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass
    engine = read_engine_json(project) or {}
    engine["mode"] = "stopped"
    engine["alive"] = False
    write_engine_record(project, engine)
    return ok_envelope({"stopped": True, "pid": pid, "source": "runtime"}, readiness="no_runtime")


def read_engine_log_lines(project: Path, limit: Optional[int] = 200) -> List[str]:
    path = engine_log_path(project)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line]
    if len(lines) > 20000:
        lines = lines[-20000:]
    if limit is None:
        return lines
    return lines[-max(int(limit), 0) :]


def read_pinned_target(project: Path) -> Optional[Dict[str, Any]]:
    path = project / TARGET_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_pinned_target(project: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    dest = project / TARGET_JSON
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def probe_engine_info(url: str, timeout: float = 0.05) -> Optional[Dict[str, Any]]:
    if not url:
        return None
    request = None
    try:
        import urllib.request

        request = urllib.request.Request(url.rstrip("/") + "/info")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        data = json.loads(raw.decode("utf-8")) if raw[:1] == b"{" else {"raw": raw.decode("utf-8", errors="replace")}
        data["url"] = url
        return data
    except Exception:
        return None


def list_targets(project: Path) -> List[Dict[str, Any]]:
    seen: List[Dict[str, Any]] = []
    urls: set[str] = set()

    def add(item: Dict[str, Any]) -> None:
        url = item.get("url")
        if url and url in urls:
            return
        if url:
            urls.add(str(url))
        seen.append(item)

    status = live_status(project)
    if status.get("alive"):
        add(
            {
                "kind": "cli-live",
                "pid": status.get("pid"),
                "url": status.get("url"),
                "mode": "live",
                "alive": True,
            }
        )
    env_url = os.environ.get("DEFOLD_AI_ENGINE_URL")
    if env_url:
        add({"kind": "env", "url": env_url, "alive": probe_engine_info(env_url) is not None})
    recorded = read_engine_json(project) or {}
    if recorded.get("url"):
        pid = int(recorded["pid"]) if recorded.get("pid") else 0
        add(
            {
                "kind": "engine-json",
                "url": recorded.get("url"),
                "pid": pid or None,
                "alive": bool(recorded.get("mode") == "live" and pid_alive(pid)),
            }
        )
    pinned = read_pinned_target(project)
    if pinned and pinned.get("url"):
        add({"kind": "pinned", "url": pinned.get("url"), "alive": probe_engine_info(str(pinned.get("url"))) is not None})
    add({"kind": "loopback-8001", "url": "http://127.0.0.1:8001", "alive": probe_engine_info("http://127.0.0.1:8001") is not None})
    current = None
    for item in seen:
        if item.get("kind") == "cli-live" and item.get("alive"):
            current = item
            break
    if current is None and pinned and pinned.get("url"):
        for item in seen:
            if item.get("url") == pinned.get("url"):
                current = item
                break
    if current is None:
        for item in seen:
            if item.get("alive"):
                current = item
                break
    if current is None and seen:
        current = seen[0]
    for item in seen:
        item["current"] = item is current
    return seen


def activate_target(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    return activate_session(project, params)


def user_session_dir() -> Path:
    override = os.environ.get("DEFOLD_AGENT_SESSIONS_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Defold" / "agent" / "sessions"
    home = Path.home()
    mac = home / "Library" / "Application Support" / "Defold"
    if mac.is_dir():
        return mac / "agent" / "sessions"
    return home / ".Defold" / "agent" / "sessions"


def read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_session_pin(project: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    dest = project / SESSION_PIN
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def list_other_editor_sessions(project: Path) -> List[Dict[str, Any]]:
    directory = user_session_dir()
    if not directory.is_dir():
        return []
    here = str(project.resolve())
    others: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        data = read_json_file(path)
        if not data:
            continue
        other_project = str(Path(str(data.get("project_path") or "")).resolve()) if data.get("project_path") else ""
        if other_project == here:
            continue
        pid = int(data["editor_pid"]) if data.get("editor_pid") else 0
        others.append(
            {
                "id": data.get("session_id") or path.stem,
                "kind": "other-editor",
                "url": data.get("editor_url"),
                "project": data.get("project_path"),
                "pid": pid or None,
                "alive": pid_alive(pid) if pid else True,
                "current": False,
                "source": "registry",
            }
        )
    return others


def session_list_payload(project: Path) -> Dict[str, Any]:
    from agent_ops import read_editor_endpoint

    sessions: List[Dict[str, Any]] = []
    pin = read_json_file(project / SESSION_PIN) or {}
    editor = read_editor_endpoint(project)
    recorded = read_json_file(project / SESSION_JSON) or {}
    if editor or recorded:
        url = (editor[0] if editor else None) or recorded.get("editor_url")
        sessions.append(
            {
                "id": recorded.get("session_id") or "editor",
                "kind": "editor",
                "url": url,
                "project": recorded.get("project_path") or str(project),
                "pid": recorded.get("editor_pid"),
                "alive": editor is not None,
                "source": "editor",
            }
        )
    for target in list_targets(project):
        sessions.append(
            {
                "id": target.get("kind") or target.get("url") or "runtime",
                "kind": target.get("kind") or "runtime",
                "url": target.get("url"),
                "pid": target.get("pid"),
                "alive": bool(target.get("alive")),
                "source": "runtime",
            }
        )
    sessions.extend(list_other_editor_sessions(project))
    current_id = pin.get("id")
    current = None
    if current_id:
        current = next((item for item in sessions if item.get("id") == current_id), None)
    if current is None:
        current = next((item for item in sessions if item.get("kind") == "cli-live" and item.get("alive")), None)
    if current is None:
        current = next((item for item in sessions if item.get("kind") == "editor" and item.get("alive")), None)
    if current is None and sessions:
        current = next((item for item in sessions if item.get("kind") != "other-editor"), sessions[0])
    for item in sessions:
        item["current"] = item is current
    return ok_envelope({"sessions": sessions, "source": "session"})


def activate_session(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    wanted = params.get("id") or params.get("url")
    listed = session_list_payload(project)
    sessions = (listed.get("data") or {}).get("sessions") or []
    if not wanted:
        current = next((item for item in sessions if item.get("current")), None)
        if current is None:
            return ok_envelope({"activated": True, "sessions": sessions, "source": "session"})
        wanted = current.get("id")
    match = next(
        (
            item
            for item in sessions
            if item.get("id") == wanted or item.get("url") == wanted
        ),
        None,
    )
    if match is None:
        return error_envelope(
            "UNKNOWN_TARGET",
            f"Session is not in the discovered list: {wanted}",
            "Call session_manage op=list.",
        )
    if match.get("kind") == "other-editor":
        return error_envelope(
            "NOT_ALLOWED",
            "That editor belongs to another project.",
            "Start a stdio MCP with --project pointing at that project.",
        )
    write_session_pin(
        project,
        {"id": match.get("id"), "url": match.get("url"), "kind": match.get("kind"), "captured_at": utc_now()},
    )
    if match.get("url") and match.get("kind") != "editor":
        write_pinned_target(project, {"url": match.get("url"), "captured_at": utc_now()})
    return ok_envelope({"activated": True, "session": match, "source": "session"})


def runtime_screenshot(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    dest = Path(params["dest"]) if params.get("dest") else snapshots_dir(project) / "latest.png"
    if not dest.is_absolute():
        dest = project / dest
    status = live_status(project)
    if not status.get("alive"):
        return error_envelope(
            "ENGINE_NOT_RUNNING",
            "runtime_screenshot needs a live engine.",
            "Call project_run mode=live, or observe with include=['screenshot'] for a batch PNG.",
        )
    try:
        request_live_screenshot(project, dest, float(params.get("timeout") or DUMP_WAIT_SEC))
    except TimeoutError:
        return error_envelope(
            "RUNTIME_DUMP_MISSING",
            "Live engine did not write screenshot.ready. Rebuild dmengine with --agent-control.",
        )
    except RuntimeError as error:
        return error_envelope("RUNTIME_DUMP_MISSING", str(error))
    return ok_envelope(
        {
            "source": "runtime",
            "screenshot": str(dest),
            "bytes": dest.stat().st_size,
            "target": {"pid": status.get("pid"), "alive": True},
        },
        readiness="running",
    )


def runtime_state_payload(project: Path) -> Dict[str, Any]:
    status = live_status(project)
    targets = list_targets(project)
    current = next((item for item in targets if item.get("current")), None)
    latest = None
    latest_path = snapshots_dir(project) / "latest.json"
    if latest_path.is_file():
        try:
            record = load_snapshot(project, "latest")
            latest = snapshot_handle(record, "summary")
        except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError):
            latest = {"path": str(latest_path)}
    readiness = "running" if status.get("alive") else "no_runtime"
    return ok_envelope(
        {
            "source": "runtime",
            "engine": status,
            "latest_snapshot": latest,
            "targets": targets,
            "target": current
            or {"pid": status.get("pid"), "alive": bool(status.get("alive")), "mode": status.get("mode")},
        },
        readiness=readiness,
    )


def runtime_get_hierarchy(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("snapshot") == "live":
        refreshed = observe_from_live(project, {"inline": "summary"})
        if refreshed.get("status") != "ok":
            return refreshed
        params = dict(params)
        params["snapshot"] = "latest"
    if params.get("id"):
        merged = dict(params)
        merged["op"] = "get_subtree"
        merged.setdefault("depth", 8)
        merged.setdefault("limit", HIERARCHY_DEFAULT_LIMIT)
        return query_snapshot(project, merged)
    record_result = query_snapshot(project, {"op": "summary", "snapshot": params.get("snapshot")})
    if record_result.get("status") != "ok":
        return record_result
    try:
        record = load_snapshot(project, params.get("snapshot"))
    except FileNotFoundError as error:
        return error_envelope("SNAPSHOT_NOT_FOUND", f"Snapshot not found: {error}")
    offset = int(params.get("offset") or 0)
    limit = min(int(params.get("limit") or HIERARCHY_DEFAULT_LIMIT), NODE_HARD_CAP)
    nodes, truncated = collect_preview(scene_graph_of(record), offset + limit)
    sliced = nodes[offset : offset + limit]
    return ok_envelope(
        {
            "nodes": sliced,
            "offset": offset,
            "limit": limit,
            "truncated": truncated or offset + len(sliced) < len(nodes),
            "total_estimate": count_nodes(scene_graph_of(record)),
            "source": "runtime",
            "snapshot": record.get("id"),
        }
    )


def runtime_get_properties(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("snapshot") == "live":
        refreshed = observe_from_live(project, {"inline": "summary"})
        if refreshed.get("status") != "ok":
            return refreshed
        params = dict(params)
        params["snapshot"] = "latest"
    merged = dict(params)
    merged["op"] = "get_node"
    return query_snapshot(project, merged)


DIFF_SKIP = {
    "id",
    "type",
    "resource",
    "children",
    "world_position",
    "world_rotation",
    "world_scale",
}


def index_nodes_by_id(graph: Any) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}

    def put(key: str, node: Dict[str, Any]) -> None:
        if not key:
            return
        existing = found.get(key)
        if existing is None or (is_go_node(node) and not is_go_node(existing)):
            found[key] = node

    for node in walk_nodes(graph):
        if not isinstance(node, dict):
            continue
        ident = node_id(node)
        if not ident:
            continue
        put(ident, node)
        put(normalize_node_id(ident), node)
    return found


def vec_changed(left: Any, right: Any, epsilon: float = 1e-4) -> bool:
    if left is None and right is None:
        return False
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return left != right
    try:
        return any(abs(float(a) - float(b)) > epsilon for a, b in zip(left, right))
    except (TypeError, ValueError):
        return left != right


def node_fields(node: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in node.items() if key not in DIFF_SKIP}


def resolve_diff_pair(project: Path, params: Dict[str, Any]) -> Tuple[str, str]:
    right = params.get("b") or params.get("to") or params.get("snapshot") or "latest"
    left = params.get("a") or params.get("from") or "previous"
    if left != "previous":
        return str(left), str(right)
    records = list_snapshot_records(project)
    if len(records) < 2:
        raise FileNotFoundError("previous")
    if right in {"latest", "", None}:
        return str(records[1]["id"]), str(records[0]["id"])
    right_id = str(load_snapshot(project, str(right)).get("id") or right)
    for record in records:
        if record.get("id") != right_id:
            return str(record["id"]), str(right)
    raise FileNotFoundError("previous")


def runtime_diff(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        left_token, right_token = resolve_diff_pair(project, params)
        left = load_snapshot(project, left_token)
        right = load_snapshot(project, right_token)
    except FileNotFoundError as error:
        return error_envelope(
            "SNAPSHOT_NOT_FOUND",
            f"Need two snapshots to diff: {error}",
            "Call runtime_observe twice, then runtime_diff.",
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return error_envelope("SNAPSHOT_NOT_FOUND", str(error))
    left_idx = index_nodes_by_id(scene_graph_of(left))
    right_idx = index_nodes_by_id(scene_graph_of(right))
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    for nid, node in right_idx.items():
        if nid not in left_idx:
            added.append(shallow_node(node))
            continue
        old = left_idx[nid]
        if vec_changed(old.get("world_position"), node.get("world_position")):
            moved.append(
                {
                    "id": nid,
                    "type": node_type(node),
                    "from": old.get("world_position"),
                    "to": node.get("world_position"),
                }
            )
        if node_type(old) != node_type(node) or old.get("resource") != node.get("resource") or node_fields(old) != node_fields(node):
            changed.append(
                {
                    "id": nid,
                    "type": node_type(node),
                    "from": {"type": node_type(old), "resource": old.get("resource"), "fields": node_fields(old)},
                    "to": {"type": node_type(node), "resource": node.get("resource"), "fields": node_fields(node)},
                }
            )
    for nid, node in left_idx.items():
        if nid not in right_idx:
            removed.append(shallow_node(node))
    limit = min(int(params.get("limit") or PREVIEW_LIMIT), NODE_HARD_CAP)
    counts = {
        "added": len(added),
        "removed": len(removed),
        "moved": len(moved),
        "changed": len(changed),
    }
    return ok_envelope(
        {
            "source": "runtime",
            "from": {"id": left.get("id"), "path": left.get("_path")},
            "to": {"id": right.get("id"), "path": right.get("_path")},
            "added": added[:limit],
            "removed": removed[:limit],
            "moved": moved[:limit],
            "changed": changed[:limit],
            "counts": counts,
            "limit": limit,
            "truncated": any(count > limit for count in counts.values()),
        }
    )
