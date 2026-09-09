# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Live engine intervention via --agent-control files. Not HTTP, not debug>."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_ops import error_envelope, ok_envelope
from agent_runtime import DUMP_WAIT_SEC, control_dir, live_status, retry_file_op


MAX_INPUT_EVENTS = 16
MAX_HOLD_FRAMES = 30
MAX_EVAL_BYTES = 4096
KNOWN_KEYS = {
    "space",
    "left",
    "right",
    "up",
    "down",
    "enter",
    "return",
    "esc",
    "escape",
    "tab",
    "backspace",
    "lshift",
    "rshift",
    "shift",
    "lctrl",
    "rctrl",
    "ctrl",
    "lalt",
    "ralt",
    "alt",
}
KNOWN_MOUSE = {"left", "right", "middle", "mouse_left", "mouse_right", "mouse_middle"}
EVAL_FORBIDDEN = (
    "os.execute",
    "io.popen",
    "io.open",
    "loadfile",
    "dofile",
    "package.loadlib",
    "debug.debug",
    "socket.bind",
    "socket.connect",
)
DEBUG_OPS = (
    "status",
    "stack",
    "locals",
    "pause",
    "continue",
    "step",
    "set_breakpoint",
    "clear_breakpoint",
)


def game_eval_allowed(params: Dict[str, Any]) -> bool:
    if params.get("confirm") is True:
        return True
    return os.environ.get("DEFOLD_AGENT_GAME_EVAL", "").strip().lower() in {"1", "true", "yes"}


def normalize_key_name(name: str) -> str:
    token = (name or "").strip().lower()
    if token.startswith("key_"):
        token = token[4:]
    return token


def is_known_key(name: str) -> bool:
    token = normalize_key_name(name)
    return token in KNOWN_KEYS or (len(token) == 1 and token.isalnum())


def format_input_request(params: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    hold = int(params.get("hold") or params.get("frames") or 1)
    if hold < 1:
        hold = 1
    if hold > MAX_HOLD_FRAMES:
        return "", error_envelope(
            "INVALID_PARAM",
            f"hold/frames must be 1..{MAX_HOLD_FRAMES}",
        )
    lines: List[str] = [f"hold={hold}"]
    events = 0

    def add_event() -> Optional[Dict[str, Any]]:
        nonlocal events
        events += 1
        if events > MAX_INPUT_EVENTS:
            return error_envelope("INVALID_PARAM", f"At most {MAX_INPUT_EVENTS} input events per call.")
        return None

    keys = params.get("keys") or params.get("key")
    if isinstance(keys, str):
        keys = [keys]
    if isinstance(keys, list):
        for item in keys:
            if isinstance(item, dict):
                name = str(item.get("key") or item.get("name") or "")
                mode = str(item.get("mode") or "down")
            else:
                name = str(item)
                mode = "down"
            if not is_known_key(name):
                return "", error_envelope("INVALID_PARAM", f"Unknown key: {name}")
            err = add_event()
            if err:
                return "", err
            suffix = "" if mode in {"", "down"} else f":{mode}"
            lines.append(f"key={normalize_key_name(name)}{suffix}")

    buttons = params.get("mouse_buttons") or params.get("mouse_button")
    if isinstance(buttons, str):
        buttons = [buttons]
    if isinstance(buttons, list):
        for item in buttons:
            if isinstance(item, dict):
                name = str(item.get("button") or item.get("name") or "")
                mode = str(item.get("mode") or "down")
            else:
                name = str(item)
                mode = "down"
            token = name.strip().lower()
            if token not in KNOWN_MOUSE:
                return "", error_envelope("INVALID_PARAM", f"Unknown mouse button: {name}")
            err = add_event()
            if err:
                return "", err
            suffix = "" if mode in {"", "down"} else f":{mode}"
            lines.append(f"mouse_button={token}{suffix}")

    if params.get("mouse") is not None or (params.get("x") is not None and params.get("y") is not None):
        mouse = params.get("mouse")
        if isinstance(mouse, (list, tuple)) and len(mouse) >= 2:
            x, y = mouse[0], mouse[1]
        else:
            x, y = params.get("x"), params.get("y")
        try:
            xi = int(x)
            yi = int(y)
        except (TypeError, ValueError):
            return "", error_envelope("INVALID_PARAM", "mouse needs numeric x,y")
        err = add_event()
        if err:
            return "", err
        lines.append(f"mouse={xi},{yi}")

    if params.get("wheel") is not None:
        err = add_event()
        if err:
            return "", err
        lines.append(f"wheel={int(params['wheel'])}")

    text = params.get("text")
    if text:
        if "\n" in str(text):
            return "", error_envelope("INVALID_PARAM", "text must be a single line")
        err = add_event()
        if err:
            return "", err
        lines.append(f"text={text}")

    if events == 0:
        return "", error_envelope(
            "MISSING_PARAM",
            "runtime_input needs keys, mouse_button, mouse, wheel, or text",
        )
    return "\n".join(lines) + "\n", None


def held_input_release_body(params: Dict[str, Any]) -> Optional[str]:
    lines: List[str] = []
    keys = params.get("keys") or params.get("key")
    if isinstance(keys, str):
        keys = [keys]
    if isinstance(keys, list):
        for item in keys:
            if isinstance(item, dict):
                name = str(item.get("key") or item.get("name") or "")
                mode = str(item.get("mode") or "down")
            else:
                name = str(item)
                mode = "down"
            if mode in {"", "down"} and is_known_key(name):
                lines.append(f"key={normalize_key_name(name)}:up")
    buttons = params.get("mouse_buttons") or params.get("mouse_button")
    if isinstance(buttons, str):
        buttons = [buttons]
    if isinstance(buttons, list):
        for item in buttons:
            if isinstance(item, dict):
                name = str(item.get("button") or item.get("name") or "")
                mode = str(item.get("mode") or "down")
            else:
                name = str(item)
                mode = "down"
            token = name.strip().lower()
            if mode in {"", "down"} and token in KNOWN_MOUSE:
                lines.append(f"mouse_button={token}:up")
    if not lines:
        return None
    return "hold=1\n" + "\n".join(lines) + "\n"


def format_debug_request(params: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    op = str(params.get("op") or "status")
    if op not in DEBUG_OPS:
        return "", error_envelope("UNKNOWN_OP", f"Unknown op: {op}", suggestions=list(DEBUG_OPS))
    lines = [f"op={op}"]
    if params.get("file"):
        path = str(params["file"]).replace("\\", "/")
        if path.startswith("/"):
            path = path[1:]
        lines.append(f"file={path}")
    if params.get("line") is not None:
        lines.append(f"line={int(params['line'])}")
    if op == "set_breakpoint" and (not params.get("file") or not params.get("line")):
        return "", error_envelope("MISSING_PARAM", "set_breakpoint needs file and line")
    return "\n".join(lines) + "\n", None


def parse_ready_text(text: str) -> Tuple[bool, str, Dict[str, str]]:
    lines = [line.rstrip("\r") for line in (text or "").splitlines()]
    status = lines[0] if lines else ""
    ok = status == "OK"
    fields: Dict[str, str] = {}
    body_lines: List[str] = []
    for line in lines[1:]:
        if "=" in line and not line.startswith(" "):
            key, value = line.split("=", 1)
            fields[key] = value
        body_lines.append(line)
    return ok, "\n".join(body_lines), fields


def parse_debug_ready(text: str) -> Tuple[bool, str, Dict[str, str], Dict[str, Any]]:
    raw = text or ""
    marker = "--json--"
    if marker in raw:
        head, json_part = raw.split(marker, 1)
    else:
        head, json_part = raw, ""
    ok, extra, fields = parse_ready_text(head)
    stack: Dict[str, Any] = {}
    payload = json_part.strip()
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            stack = parsed
    return ok, extra, fields, stack


def request_live_control(project: Path, kind: str, body: str, timeout: float = DUMP_WAIT_SEC) -> str:
    directory = control_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    request = directory / f"{kind}.request"
    ready = directory / f"{kind}.ready"
    if ready.exists():
        retry_file_op(lambda: ready.unlink(missing_ok=True))
    tmp = directory / f"{kind}.request.tmp"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(request)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ready.is_file():
            text = retry_file_op(lambda: ready.read_text(encoding="utf-8", errors="replace"))
            retry_file_op(lambda: ready.unlink(missing_ok=True))
            return text
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {kind}.ready")


def _need_live(project: Path) -> Optional[Dict[str, Any]]:
    if live_status(project).get("alive"):
        return None
    return error_envelope(
        "ENGINE_NOT_RUNNING",
        "This command needs a live engine with --agent-control.",
        "Call project_run mode=live first. Rebuild dmengine if the control files are ignored.",
    )


def hold_wait_sec(hold: int) -> float:
    """Wall time for a hold so vsync-off games still see the key for N frames at 60 Hz."""
    return min(max(int(hold), 1) / 60.0 + 0.05, 2.0)


def keep_input_held(project: Path, body: str, hold: int, timeout: float) -> None:
    deadline = time.time() + hold_wait_sec(hold)
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.08, remaining))
        if time.time() >= deadline:
            return
        try:
            request_live_control(project, "input", body, timeout)
        except (TimeoutError, PermissionError):
            return


def runtime_input(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    body, error = format_input_request(params)
    if error:
        return error
    blocked = _need_live(project)
    if blocked:
        return blocked
    timeout_sec = float(params.get("timeout") or DUMP_WAIT_SEC)
    try:
        text = request_live_control(project, "input", body, timeout_sec)
    except PermissionError:
        try:
            text = request_live_control(project, "input", body, timeout_sec)
        except PermissionError:
            return error_envelope(
                "HANDLER_ERROR",
                "Live engine input.ready is locked. Retry runtime_input.",
            )
        except TimeoutError:
            return error_envelope(
                "AGENT_CONTROL_TIMEOUT",
                "Live engine did not write input.ready. Rebuild dmengine with --agent-control.",
            )
    except TimeoutError:
        return error_envelope(
            "AGENT_CONTROL_TIMEOUT",
            "Live engine did not write input.ready. Rebuild dmengine with --agent-control.",
        )
    ok, extra, fields = parse_ready_text(text)
    if not ok:
        return error_envelope("INVALID_PARAM", extra or "input was rejected")
    hold = int(fields.get("hold") or params.get("hold") or 1)
    release = held_input_release_body(params)
    if release:
        keep_input_held(project, body, hold, float(params.get("timeout") or DUMP_WAIT_SEC))
        try:
            request_live_control(project, "input", release, timeout_sec)
        except PermissionError:
            try:
                request_live_control(project, "input", release, timeout_sec)
            except (TimeoutError, PermissionError):
                return error_envelope(
                    "AGENT_CONTROL_TIMEOUT",
                    "Live engine did not release held input. Rebuild dmengine with --agent-control.",
                )
        except TimeoutError:
            return error_envelope(
                "AGENT_CONTROL_TIMEOUT",
                "Live engine did not release held input. Rebuild dmengine with --agent-control.",
            )
    return ok_envelope(
        {
            "source": "runtime",
            "applied": int(fields.get("applied") or 0),
            "hold": hold,
            "kind": "input",
        },
        readiness="running",
    )


def game_eval(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    if not game_eval_allowed(params):
        return error_envelope(
            "NOT_ALLOWED",
            "game_eval is off until you pass confirm=true (or set DEFOLD_AGENT_GAME_EVAL=1).",
            "This is the same class of capability as /eval. Do not curl /eval.",
        )
    source = params.get("code") or params.get("source") or params.get("lua")
    if not isinstance(source, str) or not source.strip():
        return error_envelope("MISSING_PARAM", "game_eval needs code")
    if len(source.encode("utf-8")) > MAX_EVAL_BYTES:
        return error_envelope("INVALID_PARAM", f"code exceeds {MAX_EVAL_BYTES} bytes")
    for token in EVAL_FORBIDDEN:
        if token in source:
            return error_envelope("NOT_ALLOWED", f"code uses a forbidden API: {token}")
    blocked = _need_live(project)
    if blocked:
        return blocked
    try:
        text = request_live_control(project, "eval", source, float(params.get("timeout") or DUMP_WAIT_SEC))
    except PermissionError:
        try:
            text = request_live_control(project, "eval", source, float(params.get("timeout") or DUMP_WAIT_SEC))
        except PermissionError:
            return error_envelope(
                "HANDLER_ERROR",
                "Live engine eval.ready is locked. Retry game_eval.",
            )
        except TimeoutError:
            return error_envelope(
                "AGENT_CONTROL_TIMEOUT",
                "Live engine did not write eval.ready. Rebuild dmengine with --agent-control.",
            )
    except TimeoutError:
        return error_envelope(
            "AGENT_CONTROL_TIMEOUT",
            "Live engine did not write eval.ready. Rebuild dmengine with --agent-control.",
        )
    ok, extra, _fields = parse_ready_text(text)
    if not ok:
        return error_envelope("HANDLER_ERROR", extra or "game_eval failed")
    return ok_envelope(
        {"source": "runtime", "result": extra, "kind": "eval"},
        readiness="running",
    )


def runtime_debug(project: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    body, error = format_debug_request(params)
    if error:
        return error
    blocked = _need_live(project)
    if blocked:
        return blocked
    try:
        text = request_live_control(project, "debug", body, float(params.get("timeout") or DUMP_WAIT_SEC))
    except TimeoutError:
        return error_envelope(
            "AGENT_CONTROL_TIMEOUT",
            "Live engine did not write debug.ready. Rebuild dmengine with --agent-control.",
        )
    ok, extra, fields, stack = parse_debug_ready(text)
    if not ok:
        return error_envelope("INVALID_PARAM", extra or "runtime_debug failed")
    frames = stack.get("frames") if isinstance(stack.get("frames"), list) else []
    locals_list: List[Any] = []
    if frames and isinstance(frames[0], dict) and isinstance(frames[0].get("locals"), list):
        locals_list = frames[0]["locals"]
    return ok_envelope(
        {
            "source": "runtime",
            "op": fields.get("op") or params.get("op") or "status",
            "paused": fields.get("paused") == "true",
            "break_file": fields.get("break_file") or None,
            "break_line": int(fields["break_line"]) if fields.get("break_line") else None,
            "breakpoints": int(fields.get("breakpoints") or 0),
            "frames": frames,
            "locals": locals_list,
            "stack": frames,
            "frame_count": int(fields.get("frames") or len(frames) or 0),
            "local_count": int(fields.get("locals") or 0),
            "stack_reason": fields.get("stack_reason") or ("breakpoint" if frames else "none"),
            "prompt": False,
            "kind": "debug",
        },
        readiness="running",
    )
