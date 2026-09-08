# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Log / diagnostics helpers. Observe is still snapshot files; this is the debug layer."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


LAST_CHECK = Path(".internal") / "agent" / "last_check.json"
ISSUE_KEYS = ("severity", "resource", "line", "message")

RE_PREFIX = re.compile(
    r"^(?P<severity>ERROR|WARNING|WARN|INFO|DEBUG|FATAL):(?P<domain>[A-Z][A-Z0-9_]*):\s*(?P<rest>.*)$"
)
RE_LOC = re.compile(
    r"^(?P<resource>@?/?[^\s:]+):(?P<line>\d+):\s*(?P<message>.+)$"
)
RE_AT_LOCATION = re.compile(r"^\s+at:\s+(?P<resource>.+?):(?P<line>\d+)\s*$")
RE_BOB_ERROR = re.compile(
    r"^ERROR\s+(?P<resource>\S+?)(?::(?P<line>\d+))?\s+(?P<message>.+)$"
)
RE_BOB_LOG = re.compile(
    r"^(?P<severity>ERROR|WARNING|INFO):\s+(?P<resource>.+?):(?P<line>\d+):\s+'(?P<message>.*)'\s*$"
)
RE_GODOT_SCRIPT = re.compile(r"^SCRIPT ERROR:\s+(?P<message>.+)$")
RE_GODOT_AT = re.compile(
    r"^\s+at:.*\((?P<resource>res://[^):]+):(?P<line>\d+)\)\s*$"
)
RE_TRACE_START = re.compile(r"^stack traceback:\s*$")
RE_TRACE_FRAME = re.compile(
    r"^\s+(?P<resource>\S+?):(?P<line>\d+):\s+(?P<where>.*)$"
)
RE_LOGBACK = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\S+\s+\S+\s+\[(?P<thread>[^\]]+)\]\s+"
    r"(?P<severity>ERROR|WARN|WARNING|INFO|DEBUG)\s+(?P<logger>\S+)\s+-\s+(?P<message>.*)$"
)

SEVERITY_MAP = {
    "ERROR": "error",
    "FATAL": "error",
    "WARNING": "warning",
    "WARN": "warning",
    "INFO": "information",
    "DEBUG": "debug",
}

LINE_PREFIXES = {
    "error": ("ERROR:", "FATAL:"),
    "warning": ("WARNING:", "WARN:"),
    "information": ("INFO:",),
    "debug": ("DEBUG:",),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue(
    severity: str,
    message: str,
    resource: Optional[str] = None,
    line: Optional[int] = None,
    domain: Optional[str] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "severity": severity,
        "message": (message or "").strip(),
    }
    if domain:
        item["domain"] = domain
    if resource:
        path = resource.strip().replace("\\", "/")
        if path.startswith("@"):
            path = path[1:]
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


def _unique_issues(issues: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen = set()
    for issue in issues:
        key = _issue_key(issue)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _fill_location(target: Dict[str, Any], resource: str, line: int) -> None:
    if "resource" in target:
        return
    target.update(_issue(target.get("severity", "error"), target.get("message", ""), resource, line, target.get("domain")))


def _parse_loc(rest: str) -> Tuple[Optional[str], Optional[int], str]:
    match = RE_LOC.match(rest)
    if not match:
        return None, None, rest
    resource = match.group("resource")
    if "/" not in resource and not resource.startswith("@"):
        return None, None, rest
    return resource, int(match.group("line")), match.group("message")


def parse_log_report(text: str) -> Dict[str, Any]:
    """Split engine / bob / editor / Godot logs into issues, prints, and stacks."""
    issues: List[Dict[str, Any]] = []
    prints: List[Dict[str, Any]] = []
    pending_godot: Optional[str] = None
    in_traceback = False
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        if in_traceback:
            if RE_TRACE_START.match(line):
                continue
            frame = RE_TRACE_FRAME.match(line)
            if frame and issues:
                last = issues[-1]
                last.setdefault("stack", []).append(
                    {
                        "resource": _issue("error", "", frame.group("resource"), int(frame.group("line"))).get("resource"),
                        "line": int(frame.group("line")),
                        "where": frame.group("where").strip(),
                    }
                )
                _fill_location(last, frame.group("resource"), int(frame.group("line")))
                continue
            if line.startswith(" ") or line.startswith("\t"):
                if issues:
                    issues[-1].setdefault("stack", []).append({"text": line.strip()})
                continue
            in_traceback = False
        if RE_TRACE_START.match(line):
            in_traceback = True
            continue
        at_match = RE_AT_LOCATION.match(line)
        if at_match and issues:
            _fill_location(issues[-1], at_match.group("resource"), int(at_match.group("line")))
            continue
        prefix = RE_PREFIX.match(line)
        if prefix:
            severity = SEVERITY_MAP[prefix.group("severity")]
            domain = prefix.group("domain")
            resource, line_no, message = _parse_loc(prefix.group("rest"))
            item = _issue(severity, message, resource, line_no, domain)
            if severity == "debug":
                prints.append(item)
                continue
            if severity == "information" and domain != "CRASH":
                continue
            issues.append(item)
            continue
        logback = RE_LOGBACK.match(line)
        if logback:
            severity = SEVERITY_MAP[logback.group("severity")]
            item = _issue(severity, logback.group("message"), domain=logback.group("logger"))
            if severity == "debug":
                prints.append(item)
            elif severity != "information":
                issues.append(item)
            continue
        match = RE_BOB_LOG.match(line)
        if match:
            severity = SEVERITY_MAP[match.group("severity")]
            issues.append(
                _issue(severity, match.group("message"), match.group("resource"), int(match.group("line")), "BUILD")
            )
            continue
        match = RE_BOB_ERROR.match(line)
        if match:
            issues.append(
                _issue(
                    "error",
                    match.group("message"),
                    match.group("resource"),
                    int(match.group("line")) if match.group("line") else None,
                    "BOB",
                )
            )
            continue
        match = RE_GODOT_SCRIPT.match(line)
        if match:
            pending_godot = match.group("message")
            continue
        match = RE_GODOT_AT.match(line)
        if match and pending_godot is not None:
            issues.append(_issue("error", pending_godot, match.group("resource"), int(match.group("line")), "GODOT"))
            pending_godot = None
            continue
        match = RE_TRACE_FRAME.match(line)
        if match and issues and "resource" not in issues[-1]:
            _fill_location(issues[-1], match.group("resource"), int(match.group("line")))
    return {
        "issues": _unique_issues(issues),
        "prints": prints,
        "domains": sorted({item.get("domain") for item in issues if item.get("domain")}),
    }


def parse_log(text: str) -> List[Dict[str, Any]]:
    return parse_log_report(text)["issues"]


def line_is_continuation(line: str) -> bool:
    stripped = line.strip()
    return stripped == "stack traceback:" or line[:1] in {" ", "\t"}


def filter_lines(
    lines: Sequence[str],
    q: Optional[str] = None,
    severity: Optional[str] = None,
    domain: Optional[str] = None,
) -> List[str]:
    needle = (q or "").strip().lower()
    wanted = (severity or "all").lower()
    domain_u = (domain or "").strip().upper()
    prefixes = LINE_PREFIXES.get(wanted, ())
    out: List[str] = []
    keep_next = False
    for line in lines:
        if keep_next and line_is_continuation(line):
            out.append(line)
            continue
        keep_next = False
        if needle and needle not in line.lower():
            continue
        if wanted != "all" and prefixes and not any(part in line for part in prefixes):
            continue
        if domain_u and f":{domain_u}:" not in line.upper():
            continue
        out.append(line)
        keep_next = True
    return out


def filter_items(
    items: Sequence[Dict[str, Any]],
    q: Optional[str] = None,
    severity: Optional[str] = None,
    domain: Optional[str] = None,
) -> List[Dict[str, Any]]:
    needle = (q or "").strip().lower()
    wanted = (severity or "all").lower()
    domain_u = (domain or "").strip().upper()
    out: List[Dict[str, Any]] = []
    for item in items:
        if wanted != "all" and item.get("severity") != wanted:
            continue
        if domain_u and str(item.get("domain") or "").upper() != domain_u:
            continue
        hay = " ".join(
            str(item.get(key) or "")
            for key in ("message", "resource", "domain")
        ).lower()
        if needle and needle not in hay:
            continue
        out.append(item)
    return out


def issues_from_console_regions(lines: Sequence[str], regions: Any) -> List[Dict[str, Any]]:
    if not isinstance(regions, list):
        return []
    found: List[Dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        kind = str(region.get("type") or "")
        if kind not in {"extension-error", "eval-error"}:
            continue
        row = ((region.get("from") or {}).get("row"))
        message = ""
        if isinstance(row, int) and 0 <= row < len(lines):
            message = str(lines[row])
        found.append(_issue("error", message or kind, domain="CONSOLE"))
    return found


def editor_log_dirs() -> List[Path]:
    dirs: List[Path] = []
    env = os.environ.get("DEFOLD_LOG_DIR") or os.environ.get("defold.log.dir")
    if env:
        dirs.append(Path(env))
    home = Path.home()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Defold")
    dirs.extend(
        [
            home / "AppData" / "Local" / "Defold",
            home / "Library" / "Application Support" / "Defold",
            home / ".local" / "share" / "Defold",
            home / ".Defold",
        ]
    )
    unique: List[Path] = []
    seen = set()
    for path in dirs:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def find_editor_log_file() -> Optional[Path]:
    candidates: List[Path] = []
    for directory in editor_log_dirs():
        if not directory.is_dir():
            continue
        candidates.extend(directory.glob("editor2*.log"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_text_lines(path: Path) -> List[str]:
    if not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def last_check_path(project: Path) -> Path:
    return project / LAST_CHECK


def write_last_check(project: Path, payload: Dict[str, Any]) -> Path:
    dest = last_check_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "captured_at": utc_now(),
        "success": bool(payload.get("success")),
        "source": payload.get("source"),
        "issues": list(payload.get("issues") or []),
    }
    dest.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def read_last_check(project: Path) -> Optional[Dict[str, Any]]:
    path = last_check_path(project)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def find_crash_files(project: Path) -> List[str]:
    found: List[str] = []
    for path in (
        project / "_crash",
        project / "build" / "default" / "_crash",
        project / ".internal" / "agent" / "_crash",
    ):
        if path.is_file():
            found.append(str(path))
    return found


def count_severities(issues: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"error": 0, "warning": 0, "information": 0, "debug": 0}
    for issue in issues:
        key = str(issue.get("severity") or "error")
        counts[key] = counts.get(key, 0) + 1
    return counts
