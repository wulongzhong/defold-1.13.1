#!/usr/bin/env python3
"""Replace the Play/unpack dmengine inside a packaged Defold zip.

Hosted desktop still downloads official 1.13.1 launcher/bob bits, but the
user-facing editor must ship this repository's debug dmengine so
--agent-control / --runtime-dump work without a system JDK.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path


def engine_filename(platform: str) -> str:
    return "dmengine.exe" if "win32" in platform else "dmengine"


def replace_jar_entry(jar_bytes: bytes, inner: str, payload: bytes) -> bytes:
    src = io.BytesIO(jar_bytes)
    dst = io.BytesIO()
    replaced = False
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            name = info.filename.replace("\\", "/")
            data = payload if name == inner else zin.read(info.filename)
            if name == inner:
                replaced = True
            zout.writestr(info, data)
        if not replaced:
            info = zipfile.ZipInfo(inner)
            info.external_attr = 0o755 << 16
            zout.writestr(info, payload)
            replaced = True
    if not replaced:
        raise SystemExit(f"failed to write {inner} into editor jar")
    return dst.getvalue()


def inject(bundle: Path, platform: str, engine: Path) -> None:
    inner = f"libexec/{platform}/{engine_filename(platform)}"
    payload = engine.read_bytes()
    tmp = bundle.with_suffix(bundle.suffix + ".injecting")
    replaced_jars = 0
    with zipfile.ZipFile(bundle, "r") as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            name = info.filename.replace("\\", "/")
            data = zin.read(info.filename)
            if "/packages/defold-" in f"/{name}" and name.endswith(".jar"):
                data = replace_jar_entry(data, inner, payload)
                replaced_jars += 1
            zout.writestr(info, data)
    if replaced_jars < 1:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"no packages/defold-*.jar found in {bundle}")
    tmp.replace(bundle)
    print(f"injected {engine} ({len(payload)} bytes) as {inner} into {replaced_jars} jar(s) in {bundle}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, help="Defold-<platform>.zip")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--engine", required=True, help="Path to dmengine or a directory containing it")
    args = parser.parse_args()
    bundle = Path(args.bundle)
    engine = Path(args.engine)
    if engine.is_dir():
        candidate = engine / engine_filename(args.platform)
        if not candidate.is_file():
            matches = list(engine.rglob(engine_filename(args.platform)))
            if not matches:
                raise SystemExit(f"dmengine not found under {engine}")
            candidate = matches[0]
        engine = candidate
    if not bundle.is_file():
        raise SystemExit(f"bundle not found: {bundle}")
    if not engine.is_file():
        raise SystemExit(f"engine not found: {engine}")
    inject(bundle, args.platform, engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
