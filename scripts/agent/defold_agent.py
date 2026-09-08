#!/usr/bin/env python3
# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License");
# you may not use this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""
Defold first-party agent CLI.

Path A: write files -> bob/dmengine -> JSON diagnostics / PNG.
Path B features (hierarchy, create-go, script patch, ...) are the same CLI
talking to editor source `/agent/command`, not a game plugin.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent_mcp import serve_stdio
from agent_ops import dispatch_command, read_editor_endpoint as ops_read_editor_endpoint


ISSUE_KEYS = ("severity", "resource", "line", "message")

RE_ERROR_SCRIPT = re.compile(
    r"ERROR:SCRIPT:\s+(?P<resource>.+?):(?P<line>\d+):\s+(?P<message>.+)$"
)
RE_AT_LOCATION = re.compile(
    r"^\s+at:\s+(?P<resource>.+?):(?P<line>\d+)\s*$"
)
RE_ERROR_BUILD = re.compile(
    r"(?P<severity>ERROR|WARNING):BUILD:\s+(?P<resource>.+?)(?::(?P<line>\d+))?:\s+(?P<message>.+)$"
)
RE_BOB_ERROR = re.compile(
    r"^ERROR\s+(?P<resource>\S+?)(?::(?P<line>\d+))?\s+(?P<message>.+)$"
)
RE_BOB_LOG = re.compile(
    r"^(?P<severity>ERROR|WARNING|INFO):\s+(?P<resource>.+?):(?P<line>\d+):\s+'(?P<message>.*)'\s*$"
)
RE_GODOT_SCRIPT = re.compile(
    r"^SCRIPT ERROR:\s+(?P<message>.+)$"
)
RE_GODOT_AT = re.compile(
    r"^\s+at:.*\((?P<resource>res://[^):]+):(?P<line>\d+)\)\s*$"
)
RE_TRACE_FRAME = re.compile(
    r"^\s+(?P<resource>\S+?):(?P<line>\d+):\s+in function"
)


def _issue(
    severity: str,
    message: str,
    resource: Optional[str] = None,
    line: Optional[int] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "severity": severity,
        "message": message.strip(),
    }
    if resource:
        path = resource.strip().replace("\\", "/")
        if path.startswith("res://"):
            path = path[6:]
        if path and not path.startswith("/") and ":" not in path[:3]:
            path = "/" + path
        item["resource"] = path
    if line is not None and int(line) > 0:
        line_i = int(line)
        item["line"] = line_i
        item["range"] = {
            "start": {"line": line_i - 1, "character": 0},
            "end": {"line": line_i - 1, "character": 0},
        }
    return item


def _issue_key(issue: Dict[str, Any]) -> Tuple[Any, ...]:
    return tuple(issue.get(k) for k in ISSUE_KEYS)


def parse_log(text: str) -> List[Dict[str, Any]]:
    """Parse engine / bob / Godot-style logs into editor-shaped issues."""
    issues: List[Dict[str, Any]] = []
    pending_godot: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        match = RE_ERROR_SCRIPT.search(line)
        if match:
            issues.append(
                _issue(
                    "error",
                    match.group("message"),
                    match.group("resource"),
                    int(match.group("line")),
                )
            )
            continue
        match = RE_AT_LOCATION.search(line)
        if match and issues:
            last = issues[-1]
            if "resource" not in last:
                last.update(
                    _issue(
                        last.get("severity", "error"),
                        last.get("message", ""),
                        match.group("resource"),
                        int(match.group("line")),
                    )
                )
            continue
        match = RE_ERROR_BUILD.search(line)
        if match:
            issues.append(
                _issue(
                    "error" if match.group("severity") == "ERROR" else "warning",
                    match.group("message"),
                    match.group("resource"),
                    int(match.group("line")) if match.group("line") else None,
                )
            )
            continue
        match = RE_BOB_LOG.search(line)
        if match:
            severity = {"ERROR": "error", "WARNING": "warning", "INFO": "information"}[
                match.group("severity")
            ]
            issues.append(
                _issue(
                    severity,
                    match.group("message"),
                    match.group("resource"),
                    int(match.group("line")),
                )
            )
            continue
        match = RE_BOB_ERROR.search(line)
        if match:
            issues.append(
                _issue(
                    "error",
                    match.group("message"),
                    match.group("resource"),
                    int(match.group("line")) if match.group("line") else None,
                )
            )
            continue
        match = RE_GODOT_SCRIPT.search(line)
        if match:
            pending_godot = match.group("message")
            continue
        match = RE_GODOT_AT.search(line)
        if match and pending_godot is not None:
            issues.append(
                _issue(
                    "error",
                    pending_godot,
                    match.group("resource"),
                    int(match.group("line")),
                )
            )
            pending_godot = None
            continue
        match = RE_TRACE_FRAME.search(line)
        if match and issues and "resource" not in issues[-1]:
            issues[-1].update(
                _issue(
                    issues[-1].get("severity", "error"),
                    issues[-1].get("message", ""),
                    match.group("resource"),
                    int(match.group("line")),
                )
            )
    unique: List[Dict[str, Any]] = []
    seen = set()
    for issue in issues:
        key = _issue_key(issue)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def find_project(start: Optional[Path] = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "game.project").is_file():
            return candidate
    raise FileNotFoundError("No game.project found. Pass --project or run from a Defold project.")


def read_editor_endpoint(project: Path) -> Optional[Tuple[str, str]]:
    return ops_read_editor_endpoint(project)


def _host_bin_name() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    if system == "Windows":
        return f"{arch}-win32"
    if system == "Darwin":
        return f"{arch}-macos"
    return f"{arch}-linux"


def _walk_parents(start: Path) -> Iterable[Path]:
    cur = start.resolve()
    yield cur
    yield from cur.parents


def find_bob(explicit: Optional[str] = None, project: Optional[Path] = None) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    env = os.environ.get("DEFOLD_BOB")
    if env:
        path = Path(env)
        if path.exists():
            return path
    search_roots = []
    if project:
        search_roots.extend(_walk_parents(project))
    search_roots.extend(_walk_parents(Path(__file__).resolve().parent))
    rels = (
        Path("com.dynamo.cr/com.dynamo.cr.bob/dist/bob.jar"),
        Path("tmp/dynamo_home/share/java/bob.jar"),
        Path("share/java/bob.jar"),
        Path("bob.jar"),
    )
    for root in search_roots:
        for rel in rels:
            candidate = root / rel
            if candidate.is_file():
                return candidate
    which = shutil.which("bob") or shutil.which("bob.jar")
    return Path(which) if which else None


def find_engine(explicit: Optional[str] = None, project: Optional[Path] = None) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    env = os.environ.get("DEFOLD_ENGINE")
    if env:
        path = Path(env)
        if path.exists():
            return path
    exe = "dmengine.exe" if os.name == "nt" else "dmengine"
    search_roots = []
    if project:
        search_roots.extend(_walk_parents(project))
    search_roots.extend(_walk_parents(Path(__file__).resolve().parent))
    host = _host_bin_name()
    rels = (
        Path(f"tmp/dynamo_home/bin/{host}") / exe,
        Path(f"bin/{host}") / exe,
        Path(exe),
    )
    for root in search_roots:
        for rel in rels:
            candidate = root / rel
            if candidate.is_file():
                return candidate
    which = shutil.which("dmengine") or shutil.which("dmengine.exe")
    return Path(which) if which else None


def find_java() -> Optional[str]:
    return shutil.which("java")


def dump_json(payload: Dict[str, Any], out: Optional[Path] = None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)


def _http_json(
    url: str,
    token: str,
    method: str = "GET",
    timeout: float = 120.0,
    body: Any = None,
) -> Tuple[int, Any, Dict[str, str]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("content-type", "")
            parsed: Any
            if "json" in content_type or raw[:1] in (b"{", b"["):
                parsed = json.loads(raw.decode("utf-8"))
            else:
                parsed = raw
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = raw.decode("utf-8", errors="replace")
        return error.code, parsed, dict(error.headers)


def _http_bytes(url: str, token: str, dest: Path, timeout: float = 60.0) -> int:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.read())
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def editor_check(project: Path, timeout: float) -> Optional[Dict[str, Any]]:
    endpoint = read_editor_endpoint(project)
    if not endpoint:
        return None
    url, token = endpoint
    status, body, _ = _http_json(f"{url}/command/check", token, method="POST", timeout=timeout)
    if status == 404:
        return None
    if isinstance(body, dict):
        body.setdefault("source", "editor")
        body.setdefault("success", status == 200)
        return body
    return {
        "success": status == 200,
        "source": "editor",
        "issues": [_issue("error", f"Unexpected /command/check response ({status}): {body}")],
    }


def bob_argv(bob: Path, extra: Sequence[str]) -> List[str]:
    if bob.suffix.lower() == ".jar":
        java = find_java()
        if not java:
            raise FileNotFoundError("java not found. Install a JDK or set PATH.")
        return [java, "-jar", str(bob), *extra]
    return [str(bob), *extra]


def run_bob(
    project: Path,
    bob: Path,
    extra: Sequence[str],
    diagnostics_path: Optional[Path] = None,
) -> Dict[str, Any]:
    args = [
        "--root",
        str(project),
        "--variant",
        "debug",
    ]
    if diagnostics_path:
        args.extend(["--diagnostics-json", str(diagnostics_path)])
    args.extend(extra)
    cmd = bob_argv(bob, args)
    proc = subprocess.run(cmd, cwd=str(project), capture_output=True, text=True)
    log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    issues = parse_log(log)
    payload: Dict[str, Any] = {
        "success": proc.returncode == 0,
        "source": "bob",
        "exit_code": proc.returncode,
        "command": cmd,
        "issues": issues,
        "log": log,
    }
    if diagnostics_path and diagnostics_path.is_file():
        try:
            file_payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            if isinstance(file_payload, dict) and "issues" in file_payload:
                payload["success"] = bool(file_payload.get("success", payload["success"]))
                payload["issues"] = file_payload["issues"] or issues
        except json.JSONDecodeError:
            pass
    return payload


def check_project(
    project: Path,
    bob: Optional[Path],
    prefer_editor: bool,
    timeout: float,
) -> Dict[str, Any]:
    if prefer_editor:
        editor_payload = editor_check(project, timeout=timeout)
        if editor_payload is not None:
            return editor_payload
    if not bob:
        return {
            "success": False,
            "source": "agent",
            "issues": [
                _issue(
                    "error",
                    "bob not found. Set DEFOLD_BOB or pass --bob. "
                    "If the editor is open, retry with --editor after rebuilding the editor with /command/check.",
                )
            ],
        }
    with tempfile.TemporaryDirectory(prefix="defold-agent-") as tmp:
        diagnostics_path = Path(tmp) / "diagnostics.json"
        return run_bob(project, bob, ["build"], diagnostics_path)


def run_engine(
    project: Path,
    engine: Path,
    frames: int,
    screenshot: Optional[Path],
    debug_collisions: bool,
    extra: Sequence[str],
) -> Dict[str, Any]:
    projectc = project / "build" / "default" / "game.projectc"
    args = [str(engine)]
    if projectc.is_file():
        args.append(str(projectc))
    if frames > 0:
        args.append(f"--quit-after-frames={frames}")
    if screenshot:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        args.append(f"--screenshot={screenshot}")
    if debug_collisions:
        args.append("--debug-collisions")
    args.extend(extra)
    proc = subprocess.run(args, cwd=str(project), capture_output=True, text=True)
    log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    payload: Dict[str, Any] = {
        "success": proc.returncode == 0,
        "source": "dmengine",
        "exit_code": proc.returncode,
        "command": args,
        "issues": parse_log(log),
        "log": log,
    }
    if screenshot and screenshot.is_file():
        payload["screenshot"] = str(screenshot)
        payload["success"] = payload["success"] and True
    elif screenshot:
        payload["success"] = False
        payload["issues"].append(_issue("error", f"Screenshot was not written: {screenshot}"))
    return payload


def editor_preview(project: Path, resource: str, dest: Path, width: int, height: int) -> Optional[Dict[str, Any]]:
    endpoint = read_editor_endpoint(project)
    if not endpoint:
        return None
    url, token = endpoint
    path = resource.lstrip("/")
    status = _http_bytes(f"{url}/preview/{path}?width={width}&height={height}", token, dest)
    if status == 200 and dest.is_file():
        return {
            "success": True,
            "source": "editor-preview",
            "screenshot": str(dest),
            "issues": [],
        }
    return {
        "success": False,
        "source": "editor-preview",
        "issues": [_issue("error", f"GET /preview/{path} failed ({status})")],
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    project = None
    try:
        project = find_project(Path(args.project) if args.project else None)
    except FileNotFoundError as error:
        payload = {"success": False, "issues": [_issue("error", str(error))]}
        dump_json(payload, Path(args.out) if args.out else None)
        return 1
    editor = read_editor_endpoint(project)
    bob = find_bob(args.bob, project)
    engine = find_engine(args.engine, project)
    payload = {
        "success": True,
        "project": str(project),
        "java": find_java(),
        "bob": str(bob) if bob else None,
        "engine": str(engine) if engine else None,
        "editor": {"url": editor[0]} if editor else None,
        "hints": {
            "check": "defold_agent.py check --project <dir>",
            "run": "defold_agent.py run --frames 30 --screenshot .internal/agent/shot.png",
            "command": "defold_agent.py command editor_state",
            "mcp": "defold_agent.py mcp   # stdio MCP, no game plugin",
            "engine_flags": [
                "--quit-after-frames=N",
                "--screenshot=path.png",
                "--debug-collisions",
            ],
        },
    }
    dump_json(payload, Path(args.out) if args.out else None)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = find_project(Path(args.project) if args.project else None)
    bob = find_bob(args.bob, project)
    payload = check_project(project, bob, prefer_editor=args.editor, timeout=args.timeout)
    dump_json(payload, Path(args.out) if args.out else None)
    return 0 if payload.get("success") else 1


def cmd_run(args: argparse.Namespace) -> int:
    project = find_project(Path(args.project) if args.project else None)
    bob = find_bob(args.bob, project)
    engine = find_engine(args.engine, project)
    if not args.no_build:
        build_payload = check_project(project, bob, prefer_editor=False, timeout=args.timeout)
        if not build_payload.get("success"):
            dump_json(build_payload, Path(args.out) if args.out else None)
            return 1
    if not engine:
        payload = {
            "success": False,
            "source": "agent",
            "issues": [_issue("error", "dmengine not found. Set DEFOLD_ENGINE or pass --engine.")],
        }
        dump_json(payload, Path(args.out) if args.out else None)
        return 1
    screenshot = Path(args.screenshot) if args.screenshot else None
    if screenshot and not screenshot.is_absolute():
        screenshot = project / screenshot
    payload = run_engine(
        project,
        engine,
        frames=args.frames,
        screenshot=screenshot,
        debug_collisions=args.debug_collisions,
        extra=args.engine_arg or [],
    )
    dump_json(payload, Path(args.out) if args.out else None)
    return 0 if payload.get("success") else 1


def cmd_shot(args: argparse.Namespace) -> int:
    project = find_project(Path(args.project) if args.project else None)
    dest = Path(args.screenshot)
    if not dest.is_absolute():
        dest = project / dest
    if args.resource:
        preview = editor_preview(project, args.resource, dest, args.width, args.height)
        if preview is not None:
            dump_json(preview, Path(args.out) if args.out else None)
            return 0 if preview.get("success") else 1
    args.screenshot = str(dest)
    return cmd_run(args)


def cmd_loop(args: argparse.Namespace) -> int:
    project = find_project(Path(args.project) if args.project else None)
    bob = find_bob(args.bob, project)
    check_payload = check_project(project, bob, prefer_editor=args.editor, timeout=args.timeout)
    if not check_payload.get("success"):
        dump_json(check_payload, Path(args.out) if args.out else None)
        return 1
    projectc = project / "build" / "default" / "game.projectc"
    args.no_build = projectc.is_file()
    return cmd_run(args)


def cmd_parse_log(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    payload = {"success": True, "source": "log", "issues": parse_log(text)}
    dump_json(payload, Path(args.out) if args.out else None)
    return 0


def _parse_json_params(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"--params is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("--params must be a JSON object")
    return parsed


def _dump_command(result: Dict[str, Any], out: Optional[str]) -> int:
    dump_json(result, Path(out) if out else None)
    return 0 if result.get("status") == "ok" else 1


def cmd_command(args: argparse.Namespace) -> int:
    project = find_project(Path(args.project) if args.project else None)
    params = _parse_json_params(args.params)
    result = dispatch_command(project, args.name, params, args.timeout)
    return _dump_command(result, args.out)


def cmd_state(args: argparse.Namespace) -> int:
    args.name = "editor_state"
    args.params = args.params or "{}"
    return cmd_command(args)


def cmd_hierarchy(args: argparse.Namespace) -> int:
    args.name = "collection_get_hierarchy"
    args.params = json.dumps({"path": args.collection, "offset": args.offset, "limit": args.limit})
    return cmd_command(args)


def cmd_props(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {"collection": args.collection, "id": args.id}
    if args.component:
        params["component"] = args.component
    args.name = "gameobject_get_properties"
    args.params = json.dumps(params)
    return cmd_command(args)


def cmd_create_go(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {"collection": args.collection, "id": args.id}
    if args.position:
        params["position"] = [float(part) for part in args.position.split(",")]
    if args.parent:
        params["parent"] = args.parent
    if args.go:
        params["path"] = args.go
    args.name = "gameobject_create"
    args.params = json.dumps(params)
    return cmd_command(args)


def cmd_add_component(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {"collection": args.collection, "id": args.id}
    if args.type:
        params["type"] = args.type
    if args.path:
        params["path"] = args.path
    args.name = "component_add"
    args.params = json.dumps(params)
    return cmd_command(args)


def cmd_create_script(args: argparse.Namespace) -> int:
    args.name = "script_create"
    args.params = json.dumps({"path": args.path})
    return cmd_command(args)


def cmd_patch_script(args: argparse.Namespace) -> int:
    args.name = "script_patch"
    args.params = json.dumps({"path": args.path, "old_text": args.old, "new_text": args.new})
    return cmd_command(args)


def cmd_logs(args: argparse.Namespace) -> int:
    args.name = "logs_read"
    args.params = json.dumps({"limit": args.limit})
    return cmd_command(args)


def cmd_ref(args: argparse.Namespace) -> int:
    args.name = "api_manage"
    args.params = json.dumps({"op": "get", "q": args.q, "environment": args.environment, "language": args.language})
    return cmd_command(args)


def cmd_batch(args: argparse.Namespace) -> int:
    commands = json.loads(Path(args.file).read_text(encoding="utf-8") if args.file else args.params)
    if isinstance(commands, dict):
        commands = commands.get("commands", commands)
    args.name = "batch_execute"
    args.params = json.dumps({"commands": commands})
    return cmd_command(args)


def cmd_mcp(args: argparse.Namespace) -> int:
    project = find_project(Path(args.project) if args.project else None)
    return serve_stdio(project, args.timeout)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="Defold project directory (contains game.project).")
    common.add_argument("--bob", help="Path to bob.jar or bob executable.")
    common.add_argument("--engine", help="Path to dmengine.")
    common.add_argument("--out", help="Write JSON result to this file as well as stdout.")
    common.add_argument("--timeout", type=float, default=180.0, help="Editor HTTP timeout in seconds.")
    parser = argparse.ArgumentParser(
        description="Defold agent CLI: check / run / screenshot / editor graph commands."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", parents=[common], help="Report project / bob / dmengine / editor discovery.")
    doctor.set_defaults(func=cmd_doctor)

    check = sub.add_parser("check", parents=[common], help="Compile only. Editor /command/check if --editor, else bob.")
    check.add_argument("--editor", action="store_true", help="Prefer the open editor HTTP check.")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", parents=[common], help="Build (unless --no-build) and run dmengine.")
    run.add_argument("--no-build", action="store_true")
    run.add_argument("--frames", type=int, default=30, help="--quit-after-frames. 0 keeps the window open.")
    run.add_argument("--screenshot", help="PNG path written by dmengine --screenshot.")
    run.add_argument("--debug-collisions", action="store_true")
    run.add_argument("--engine-arg", action="append", help="Extra dmengine argument. Repeatable.")
    run.set_defaults(func=cmd_run)

    shot = sub.add_parser("shot", parents=[common], help="Capture a PNG via editor /preview or dmengine --screenshot.")
    shot.add_argument("--screenshot", default=".internal/agent/shot.png")
    shot.add_argument("--resource", help="Editor preview resource, e.g. /main/main.collection")
    shot.add_argument("--width", type=int, default=1280)
    shot.add_argument("--height", type=int, default=720)
    shot.add_argument("--no-build", action="store_true")
    shot.add_argument("--frames", type=int, default=30)
    shot.add_argument("--debug-collisions", action="store_true")
    shot.add_argument("--engine-arg", action="append")
    shot.set_defaults(func=cmd_shot)

    loop = sub.add_parser("loop", parents=[common], help="check, then run with a screenshot (the agent iteration).")
    loop.add_argument("--editor", action="store_true")
    loop.add_argument("--no-build", action="store_true")
    loop.add_argument("--frames", type=int, default=30)
    loop.add_argument("--screenshot", default=".internal/agent/shot.png")
    loop.add_argument("--debug-collisions", action="store_true")
    loop.add_argument("--engine-arg", action="append")
    loop.set_defaults(func=cmd_loop)

    parse = sub.add_parser("parse-log", parents=[common], help="Parse a log file or stdin into issues JSON.")
    parse.add_argument("--file", help="Log file. Reads stdin if omitted.")
    parse.set_defaults(func=cmd_parse_log)

    command = sub.add_parser("command", parents=[common], help="Dispatch a first-party editor/disk agent command.")
    command.add_argument("name", help="Command name, e.g. editor_state or gameobject_create.")
    command.add_argument("--params", default="{}", help="JSON object of command params.")
    command.set_defaults(func=cmd_command)

    state = sub.add_parser("state", parents=[common], help="editor_state")
    state.add_argument("--params", default="{}")
    state.set_defaults(func=cmd_state)

    hierarchy = sub.add_parser("hierarchy", parents=[common], help="collection_get_hierarchy")
    hierarchy.add_argument("--collection", required=True)
    hierarchy.add_argument("--offset", type=int, default=0)
    hierarchy.add_argument("--limit", type=int, default=200)
    hierarchy.set_defaults(func=cmd_hierarchy)

    props = sub.add_parser("props", parents=[common], help="gameobject_get_properties")
    props.add_argument("--collection", required=True)
    props.add_argument("--id", required=True)
    props.add_argument("--component")
    props.set_defaults(func=cmd_props)

    create_go = sub.add_parser("create-go", parents=[common], help="gameobject_create (editor graph, or disk fallback)")
    create_go.add_argument("--collection", required=True)
    create_go.add_argument("--id", required=True)
    create_go.add_argument("--position", help="x,y,z")
    create_go.add_argument("--parent")
    create_go.add_argument("--go", help="Referenced .go path")
    create_go.set_defaults(func=cmd_create_go)

    add_component = sub.add_parser("add-component", parents=[common], help="component_add")
    add_component.add_argument("--collection", required=True)
    add_component.add_argument("--id", required=True)
    add_component.add_argument("--type", dest="type")
    add_component.add_argument("--path")
    add_component.set_defaults(func=cmd_add_component)

    create_script = sub.add_parser("create-script", parents=[common], help="script_create")
    create_script.add_argument("--path", required=True)
    create_script.set_defaults(func=cmd_create_script)

    patch_script = sub.add_parser("patch-script", parents=[common], help="script_patch unique substring")
    patch_script.add_argument("--path", required=True)
    patch_script.add_argument("--old", required=True)
    patch_script.add_argument("--new", required=True)
    patch_script.set_defaults(func=cmd_patch_script)

    logs = sub.add_parser("logs", parents=[common], help="logs_read via GET /console")
    logs.add_argument("--limit", type=int, default=200)
    logs.set_defaults(func=cmd_logs)

    ref = sub.add_parser("ref", parents=[common], help="api_manage get → GET /ref")
    ref.add_argument("-q", "--q", required=True)
    ref.add_argument("--environment", default="runtime")
    ref.add_argument("--language", default="Lua")
    ref.set_defaults(func=cmd_ref)

    batch = sub.add_parser("batch", parents=[common], help="batch_execute from --file JSON or --params")
    batch.add_argument("--file")
    batch.add_argument("--params")
    batch.set_defaults(func=cmd_batch)

    mcp = sub.add_parser("mcp", parents=[common], help="stdio MCP (Content-Length JSON-RPC). No game plugin.")
    mcp.set_defaults(func=cmd_mcp)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as error:
        dump_json({"success": False, "issues": [_issue("error", str(error))]}, Path(args.out) if getattr(args, "out", None) else None)
        return 1
    except ValueError as error:
        dump_json({"status": "error", "error": {"code": "INVALID_PARAM", "message": str(error)}}, Path(args.out) if getattr(args, "out", None) else None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
