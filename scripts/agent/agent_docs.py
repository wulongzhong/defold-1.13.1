# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License"); you may not use
# this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license
"""Search engine /*# script docs when the editor /ref endpoint is closed."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


DOC_PACKAGES = ("gameobject", "gamesys", "script", "gui", "render", "sound", "physics")
SKIP_DIR_NAMES = {"test", "tests", "mbedtls", "build"}
RE_DOC = re.compile(r"/\*#(.*?)\*/", re.DOTALL)
RE_TAG = re.compile(r"^@(\w+)\s+(.*)$")

_INDEX: Optional[List[Dict[str, Any]]] = None


def _engine_roots() -> List[Path]:
    roots: List[Path] = []
    for parent in Path(__file__).resolve().parents:
        engine = parent / "engine"
        if engine.is_dir() and (engine / "gameobject").is_dir():
            for name in DOC_PACKAGES:
                package = engine / name
                if package.is_dir():
                    roots.append(package)
            return roots
    return roots


def _strip_stars(block: str) -> str:
    lines: List[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("*"):
            line = line[1:]
            if line.startswith(" "):
                line = line[1:]
        lines.append(line)
    return "\n".join(lines).strip()


def parse_doc_block(block: str) -> Optional[Dict[str, Any]]:
    text = _strip_stars(block)
    tags: Dict[str, List[str]] = {}
    desc_lines: List[str] = []
    for line in text.splitlines():
        match = RE_TAG.match(line)
        if match:
            tags.setdefault(match.group(1), []).append(match.group(2).strip())
        elif not tags:
            desc_lines.append(line)
    name = (tags.get("name") or [None])[0]
    if not name:
        return None
    return {
        "name": name,
        "brief": desc_lines[0] if desc_lines else name,
        "description": "\n".join(desc_lines).strip(),
        "parameters": tags.get("param") or [],
        "returns": tags.get("return") or tags.get("treturn") or [],
        "language": "Lua",
        "environment": "runtime",
    }


def _iter_source_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".cpp", ".c", ".h", ".inl"}:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def load_script_docs() -> List[Dict[str, Any]]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    items: List[Dict[str, Any]] = []
    seen = set()
    for root in _engine_roots():
        for path in _iter_source_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in RE_DOC.finditer(text):
                item = parse_doc_block(match.group(1))
                if item is None or item["name"] in seen:
                    continue
                seen.add(item["name"])
                items.append(item)
    _INDEX = items
    return items


def query_matches(item: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    alternatives = [alt.strip() for alt in query.split("|") if alt.strip()]
    if not alternatives:
        return True
    hay = f"{item.get('name', '')}\n{item.get('brief', '')}\n{item.get('description', '')}".lower()
    for alternative in alternatives:
        terms = alternative.split()
        if terms and all(term.lower() in hay for term in terms):
            return True
    return False


def search_script_docs(query: str, limit: int = 40) -> Dict[str, Any]:
    items = load_script_docs()
    matches = [item for item in items if query_matches(item, query)]
    return {
        "results": matches[:limit],
        "total": len(matches),
        "truncated": len(matches) > limit,
        "source": "engine-docs",
        "available": bool(items),
    }
