#!/usr/bin/env python3
# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License");
# you may not use this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license

from __future__ import annotations

import unittest

from agent_ops import disk_command, dispatch_command, parse_collection_hierarchy, patch_text
from agent_runtime import (
    INLINE_BUDGET,
    observe_envelope,
    query_snapshot,
    wrap_engine_dump,
)
from defold_agent import parse_log


class ParseLogTest(unittest.TestCase):
    def test_lua_runtime_error(self):
        issues = parse_log(
            "ERROR:SCRIPT: /main/player.script:12: attempt to index a nil value\n"
            "          at: /main/player.script:12\n"
            "stack traceback:\n"
            "  /main/player.script:12: in function update\n"
        )
        self.assertEqual(1, len(issues))
        self.assertEqual("error", issues[0]["severity"])
        self.assertEqual("/main/player.script", issues[0]["resource"])
        self.assertEqual(12, issues[0]["line"])
        self.assertEqual(11, issues[0]["range"]["start"]["line"])
        self.assertIn("nil", issues[0]["message"])

    def test_bob_agent_line(self):
        issues = parse_log("ERROR:BUILD: /main/main.script:3: unexpected symbol near 'endd'\n")
        self.assertEqual("/main/main.script", issues[0]["resource"])
        self.assertEqual(3, issues[0]["line"])

    def test_bob_legacy_line(self):
        issues = parse_log("ERROR /main/hero.go:8 missing required field\n")
        self.assertEqual("/main/hero.go", issues[0]["resource"])
        self.assertEqual(8, issues[0]["line"])
        self.assertEqual("missing required field", issues[0]["message"])

    def test_bob_quoted_log_line(self):
        issues = parse_log("ERROR: /gui/menu.gui_script:4: 'unexpected symbol near end'\n")
        self.assertEqual("/gui/menu.gui_script", issues[0]["resource"])
        self.assertEqual("unexpected symbol near end", issues[0]["message"])

    def test_godot_script_error(self):
        issues = parse_log(
            'SCRIPT ERROR: Parse Error: Could not find type "void2" in the current scope.\n'
            "          at: GDScript::reload (res://new_script2.gd:3)\n"
        )
        self.assertEqual("/new_script2.gd", issues[0]["resource"])
        self.assertEqual(3, issues[0]["line"])
        self.assertIn("void2", issues[0]["message"])

    def test_dedupes_repeated_lines(self):
        log = "ERROR:BUILD: /a.script:1: boom\nERROR:BUILD: /a.script:1: boom\n"
        self.assertEqual(1, len(parse_log(log)))


class DiskCommandTest(unittest.TestCase):
    def test_patch_text_unique(self):
        self.assertEqual("abX", patch_text("abY", "Y", "X"))

    def test_patch_text_missing(self):
        with self.assertRaises(KeyError):
            patch_text("abc", "zzz", "X")

    def test_patch_text_multiple(self):
        with self.assertRaises(ValueError):
            patch_text("aaa", "a", "b")

    def test_hierarchy_from_collection_text(self):
        text = (
            'name: "main"\n'
            'embedded_instances {\n'
            '  id: "logo"\n'
            '  data: ""\n'
            "}\n"
        )
        tree = parse_collection_hierarchy(text, "/main/main.collection")
        self.assertEqual("logo", tree["children"][0]["id"])

    def test_script_create_and_patch_on_disk(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            created = disk_command(project, "script_create", {"path": "/main/cube.script"})
            self.assertEqual("ok", created["status"])
            patched = disk_command(
                project,
                "script_patch",
                {
                    "path": "/main/cube.script",
                    "old_text": "function init(self)",
                    "new_text": "function init(self) -- hi",
                },
            )
            self.assertEqual("ok", patched["status"])
            text = (project / "main" / "cube.script").read_text(encoding="utf-8")
            self.assertIn("function init(self) -- hi", text)

    def test_snapshot_read_text_is_blocked(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            snap = project / ".internal" / "agent" / "snapshots" / "latest.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text("{}", encoding="utf-8")
            blocked = disk_command(
                project,
                "filesystem_manage",
                {"op": "read_text", "path": "/.internal/agent/snapshots/latest.json"},
            )
            self.assertEqual("error", blocked["status"])
            self.assertEqual("NOT_ALLOWED", blocked["error"]["code"])


class RuntimeSnapshotTest(unittest.TestCase):
    def _graph(self):
        return {
            "id": "main",
            "type": "collectionc",
            "children": [
                {
                    "id": "cube",
                    "type": "goc",
                    "resource": "/main/cube.go",
                    "world_position": [10.0, 20.0, 0.0],
                    "velocity": 3,
                    "children": [
                        {"id": "sprite", "type": "spritec", "children": []},
                    ],
                },
                {
                    "id": "other",
                    "type": "goc",
                    "world_position": [1.0, 2.0, 3.0],
                    "children": [],
                },
            ],
        }

    def _project_with_snapshot(self, tmp):
        from pathlib import Path

        project = Path(tmp)
        raw = project / "raw.json"
        raw.write_text(__import__("json").dumps(self._graph()), encoding="utf-8")
        record = wrap_engine_dump(project, raw, mode="batch", frame=30, target={"url": "http://127.0.0.1:8001"})
        return project, record

    def test_observe_summary_has_no_tree(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, record = self._project_with_snapshot(tmp)
            result = observe_envelope({**record, "_path": str(project / ".internal" / "agent" / "snapshots" / f"{record['id']}.json")}, inline="summary")
            self.assertEqual("ok", result["status"])
            self.assertNotIn("hierarchy", result["data"])
            self.assertNotIn("inline_data", result["data"]["snapshot"])
            self.assertEqual("cube", result["data"]["summary"]["roots"][1])
            self.assertGreater(result["data"]["snapshot"]["node_count"], 1)

    def test_get_node_reads_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            result = query_snapshot(project, {"op": "get_node", "id": "cube"})
            self.assertEqual("ok", result["status"])
            self.assertEqual([10.0, 20.0, 0.0], result["data"]["node"]["world_position"])

    def test_find_by_type(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            result = query_snapshot(project, {"op": "find", "type": "spritec"})
            self.assertEqual("ok", result["status"])
            self.assertEqual("sprite", result["data"]["matches"][0]["id"])

    def test_get_path(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            result = query_snapshot(project, {"op": "get_path", "path": "/scene_graph/children/0/world_position"})
            self.assertEqual("ok", result["status"])
            self.assertEqual([10.0, 20.0, 0.0], result["data"]["value"])

    def test_inline_full_too_large(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, record = self._project_with_snapshot(tmp)
            record["padding"] = "x" * (INLINE_BUDGET + 100)
            result = observe_envelope(
                {**record, "_path": str(project / ".internal" / "agent" / "snapshots" / f"{record['id']}.json")},
                inline="full",
            )
            self.assertEqual("error", result["status"])
            self.assertEqual("INLINE_TOO_LARGE", result["error"]["code"])
            self.assertTrue((project / ".internal" / "agent" / "snapshots" / f"{record['id']}.json").is_file())

    def test_dispatch_query_and_hierarchy(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            props = dispatch_command(project, "runtime_get_properties", {"id": "cube"}, 5)
            self.assertEqual("ok", props["status"])
            self.assertEqual("runtime", props["data"]["source"])
            tree = dispatch_command(project, "runtime_get_hierarchy", {}, 5)
            self.assertEqual("ok", tree["status"])
            ids = [node["id"] for node in tree["data"]["nodes"]]
            self.assertIn("cube", ids)

    def test_live_dump_handshake(self):
        import json
        import tempfile
        import threading
        import time
        from pathlib import Path

        from agent_runtime import request_live_dump

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            control = project / ".internal" / "agent" / "control"
            dest = project / ".internal" / "agent" / "snapshots" / "_raw.json"
            graph = self._graph()

            def engine_side():
                request = control / "dump.request"
                for _ in range(200):
                    if request.is_file():
                        break
                    time.sleep(0.01)
                path = Path(request.read_text(encoding="utf-8").strip())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(graph), encoding="utf-8")
                (control / "dump.ready").write_text(f"OK\n{path}\n", encoding="utf-8")

            worker = threading.Thread(target=engine_side)
            worker.start()
            written = request_live_dump(project, dest, timeout=2)
            worker.join(timeout=2)
            self.assertTrue(written.is_file())
            self.assertEqual("cube", json.loads(written.read_text(encoding="utf-8"))["children"][0]["id"])

    def test_logs_read_from_engine_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            log = project / ".internal" / "agent" / "engine.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("hello\nERROR:SCRIPT: /main/a.script:1: boom\n", encoding="utf-8")
            result = dispatch_command(project, "logs_read", {"limit": 10}, 1)
            self.assertEqual("ok", result["status"])
            self.assertEqual("engine-log", result["data"]["source"])
            self.assertIn("truncated", result["data"])
            self.assertTrue(any("boom" in line for line in result["data"]["lines"]))

    def test_editor_state_includes_engine(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            result = dispatch_command(project, "editor_state", {}, 1)
            self.assertEqual("ok", result["status"])
            self.assertIn("engine", result["data"])
            self.assertFalse(result["data"]["engine"].get("alive"))

    def test_observe_uses_live_handshake(self):
        import json
        import os
        import tempfile
        import threading
        import time
        from pathlib import Path

        from agent_runtime import write_engine_record
        from defold_agent import observe_runtime, project_run

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_engine_record(project, {"pid": os.getpid(), "mode": "live"})
            graph = self._graph()
            control = project / ".internal" / "agent" / "control"

            def engine_side():
                request = control / "dump.request"
                for _ in range(200):
                    if request.is_file():
                        break
                    time.sleep(0.01)
                path = Path(request.read_text(encoding="utf-8").strip())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(graph), encoding="utf-8")
                (control / "dump.ready").write_text(f"OK\n{path}\n", encoding="utf-8")

            worker = threading.Thread(target=engine_side)
            worker.start()
            result = observe_runtime(project, {"inline": "summary"}, 2)
            worker.join(timeout=2)
            self.assertEqual("ok", result["status"])
            self.assertEqual("live", result["data"]["mode"])
            self.assertTrue(result["data"]["target"]["alive"])
            self.assertNotIn("hierarchy", result["data"])
            blocked = project_run(project, {"mode": "live", "no_build": True}, 1)
            self.assertEqual("error", blocked["status"])
            self.assertEqual("NOT_ALLOWED", blocked["error"]["code"])

    def test_stop_marks_missing_pid_stopped(self):
        import os
        import tempfile
        from pathlib import Path

        from agent_runtime import pid_alive, stop_live_engine, write_engine_record

        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(999999))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_engine_record(project, {"pid": 999999, "mode": "live"})
            result = stop_live_engine(project)
            self.assertEqual("ok", result["status"])
            self.assertTrue(result["data"]["stopped"])


class ToolQualityTest(unittest.TestCase):
    def test_closed_mcp_schemas(self):
        from agent_mcp import TOOLS, TOOL_SCHEMAS, _tool_schema

        for name, description in TOOLS:
            schema = TOOL_SCHEMAS[name]
            self.assertEqual(False, schema.get("additionalProperties"), name)
            listed = _tool_schema(name, description)
            self.assertEqual(name, listed["name"])

    def test_batch_execute_is_not_atomic(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            result = dispatch_command(
                project,
                "batch_execute",
                {
                    "commands": [
                        {"command": "editor_state", "params": {}},
                        {"command": "script_create", "params": {"path": "/main/a.script"}},
                    ]
                },
                2,
            )
            self.assertEqual("ok", result["status"])
            self.assertFalse(result["data"]["atomic"])
            self.assertTrue(result["data"]["undoable_separately"])
            self.assertTrue((project / "main" / "a.script").is_file())
            failed = dispatch_command(
                project,
                "batch_execute",
                {
                    "commands": [
                        {"command": "script_create", "params": {"path": "/main/b.script"}},
                        {"command": "script_patch", "params": {"path": "/missing.script"}},
                    ]
                },
                2,
            )
            self.assertEqual("error", failed["status"])
            self.assertFalse(failed["error"]["data"]["atomic"])
            self.assertEqual(1, failed["error"]["data"]["failed_index"])
            self.assertTrue((project / "main" / "b.script").is_file())

    def test_project_build_does_not_launch(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            result = dispatch_command(project, "project_build", {"bob": "/no/such/bob.jar"}, 2)
            extra = (result.get("data") or result.get("error", {}).get("data") or {})
            self.assertFalse(extra.get("launched", True))
            self.assertTrue(extra.get("check_only"))


if __name__ == "__main__":
    unittest.main()
