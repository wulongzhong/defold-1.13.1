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
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent_ops import error_envelope, ok_envelope


SNAPSHOT_SCHEMA = 1
SNAPSHOTS_DIR = Path(".internal") / "agent" / "snapshots"
ENGINE_JSON = Path(".internal") / "agent" / "engine.json"
RETAIN = 8
INLINE_BUDGET = 48 * 1024
NODE_HARD_CAP = 256
PREVIEW_LIMIT = 80
LOG_LINE_LIMIT = 40
HIERARCHY_DEFAULT_LIMIT = 200
FIND_DEFAULT_LIMIT = 50

RE_SERVICE_PORT = re.compile(r"Engine service started on port (\d+)")

QUERY_OPS = ("list", "summary", "list_ids", "get_node", "get_subtree", "find", "get_path")


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
    dest = project / ENGINE_JSON
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


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
        if path.name == "latest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
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
    for node in walk_nodes(graph):
        if node_id(node) != go_id:
            continue
        if not component:
            return node
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict) and (
                    node_id(child) == component or node_type(child) == component
                ):
                    return child
        return None
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

    return error_envelope("UNKNOWN_OP", f"Unknown op: {op}")


def runtime_state_payload(project: Path) -> Dict[str, Any]:
    engine = read_engine_json(project)
    latest = None
    latest_path = snapshots_dir(project) / "latest.json"
    if latest_path.is_file():
        try:
            record = load_snapshot(project, "latest")
            latest = snapshot_handle(record, "summary")
        except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError):
            latest = {"path": str(latest_path)}
    alive = False
    readiness = "no_runtime"
    return ok_envelope(
        {
            "source": "runtime",
            "engine": engine,
            "latest_snapshot": latest,
            "target": {"url": (engine or {}).get("url"), "alive": alive},
        },
        readiness=readiness,
    )


def runtime_get_hierarchy(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("snapshot") == "live":
        return error_envelope(
            "ENGINE_NOT_RUNNING",
            "Live hierarchy is not available in R0.",
            "Omit snapshot or pass latest to read the last dump file.",
        )
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
        return error_envelope(
            "ENGINE_NOT_RUNNING",
            "Live properties are not available in R0.",
            "Omit snapshot or pass latest to read the last dump file.",
        )
    merged = dict(params)
    merged["op"] = "get_node"
    return query_snapshot(project, merged)
