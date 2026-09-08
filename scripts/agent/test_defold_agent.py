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
        self.assertEqual("embedded", tree["children"][0]["kind"])

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
            self.assertEqual("/main/a.script", result["data"]["issues"][0]["resource"])

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
            self.assertEqual("stopped", result["data"]["game_status"]["status"])
            self.assertFalse(result["data"]["game_status"]["helper_live"])
            self.assertIn("game_project", result["data"]["ready"])
            self.assertTrue(result["data"]["ready"]["game_project"])

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

    def test_runtime_diff_moved_and_added(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project, _first = self._project_with_snapshot(tmp)
            moved = self._graph()
            moved["children"][0]["world_position"] = [40.0, 20.0, 0.0]
            moved["children"].append({"id": "newbie", "type": "goc", "world_position": [0.0, 0.0, 0.0], "children": []})
            raw = Path(tmp) / "raw2.json"
            raw.write_text(json.dumps(moved), encoding="utf-8")
            wrap_engine_dump(project, raw, mode="batch", frame=31, target={})
            result = dispatch_command(project, "runtime_diff", {}, 2)
            self.assertEqual("ok", result["status"])
            self.assertIn("newbie", [node["id"] for node in result["data"]["added"]])
            self.assertIn("cube", [node["id"] for node in result["data"]["moved"]])
            from agent_mcp import handle_rpc

            listed = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, project, 2)
            uris = [item["uri"] for item in listed["result"]["resources"]]
            self.assertTrue(any(uri.startswith("defold://runtime/snapshot/") for uri in uris))
            read = handle_rpc(
                {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uris[0]}},
                project,
                2,
            )
            body = json.loads(read["result"]["contents"][0]["text"])
            self.assertEqual("ok", body["status"])
            self.assertNotIn("scene_graph", body.get("data") or {})

    def test_stdio_mcp_resources_and_prompts(self):
        import json
        import tempfile
        from pathlib import Path

        from agent_mcp import handle_rpc, mcp_client_config

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            (project / "main").mkdir()
            (project / "main" / "main.collection").write_text('name: "main"\n', encoding="utf-8")
            init = handle_rpc(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                project,
                2,
            )
            self.assertIn("prompts", init["result"]["capabilities"])
            self.assertIn("resources", init["result"]["capabilities"])
            self.assertIn("stdio", init["result"]["instructions"])
            listed = handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, project, 2)
            uris = [item["uri"] for item in listed["result"]["resources"]]
            self.assertIn("defold://editor/state", uris)
            self.assertIn("defold://project/info", uris)
            self.assertIn("defold://project/logs", uris)
            self.assertIn("defold://project/mcp-config", uris)
            self.assertTrue(any(item.startswith("defold://collection/hierarchy") for item in uris))
            templates = handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list"}, project, 2)
            names = [item["name"] for item in templates["result"]["resourceTemplates"]]
            self.assertIn("editor-state", names)
            state = handle_rpc(
                {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "defold://editor/state"}},
                project,
                2,
            )
            body = json.loads(state["result"]["contents"][0]["text"])
            self.assertEqual("ok", body["status"])
            prompts = handle_rpc({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"}, project, 2)
            self.assertTrue(any(item["name"] == "defold-observe" for item in prompts["result"]["prompts"]))
            self.assertTrue(any(item["name"] == "defold-check" for item in prompts["result"]["prompts"]))
            prompt = handle_rpc(
                {"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "defold-observe"}},
                project,
                2,
            )
            self.assertIn("runtime_observe", prompt["result"]["messages"][0]["content"]["text"])
            config = mcp_client_config(Path("scripts/agent/defold_agent.py"), project, "cursor")
            self.assertIn("defold-agent", config)
            self.assertNotIn("http://", config)


class ToolQualityTest(unittest.TestCase):
    def test_closed_mcp_schemas(self):
        from agent_mcp import TOOLS, TOOL_SCHEMAS, _tool_schema

        for name, description in TOOLS:
            schema = TOOL_SCHEMAS[name]
            self.assertEqual(False, schema.get("additionalProperties"), name)
            listed = _tool_schema(name, description)
            self.assertEqual(name, listed["name"])
        self.assertLess(len(TOOLS), 100)

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

    def test_project_doctor_lists_ready_flags(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            result = dispatch_command(project, "project_doctor", {}, 2)
            self.assertEqual("ok", result["status"])
            self.assertTrue(result["data"]["ready"]["game_project"])
            self.assertIn("bob", result["data"]["ready"])
            self.assertEqual("stdio", result["data"]["mcp"]["transport"])
            self.assertIsNone(result["data"]["mcp"]["url"])
            self.assertGreater(result["data"]["mcp"]["tools"], 20)
            self.assertLess(result["data"]["mcp"]["tools"], 100)

    def test_domain_atlas_and_input(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            created = dispatch_command(
                project, "atlas_manage", {"op": "create", "path": "/main/sprites.atlas"}, 2
            )
            self.assertEqual("ok", created["status"])
            self.assertFalse(created["data"]["undoable"])
            added = dispatch_command(
                project,
                "atlas_manage",
                {"op": "add_image", "path": "/main/sprites.atlas", "image": "/main/box.png", "id": "box"},
                2,
            )
            self.assertEqual("ok", added["status"])
            got = dispatch_command(project, "atlas_manage", {"op": "get", "path": "/main/sprites.atlas"}, 2)
            self.assertIn("/main/box.png", got["data"]["images"])
            dispatch_command(project, "input_binding_manage", {"op": "create", "path": "/input/game.input_binding"}, 2)
            dispatch_command(
                project,
                "input_binding_manage",
                {"op": "add_key", "path": "/input/game.input_binding", "input": "KEY_LEFT", "action": "left"},
                2,
            )
            binds = dispatch_command(
                project, "input_binding_manage", {"op": "get", "path": "/input/game.input_binding"}, 2
            )
            self.assertEqual("left", binds["data"]["bindings"][0]["action"])
            cam = dispatch_command(
                project,
                "camera_manage",
                {"op": "add", "path": "/main/hero.go", "id": "camera"},
                2,
            )
            # .go missing
            self.assertEqual("error", cam["status"])
            (project / "main").mkdir(exist_ok=True)
            (project / "main" / "hero.go").write_text("", encoding="utf-8")
            cam = dispatch_command(
                project, "camera_manage", {"op": "add", "path": "/main/hero.go", "id": "camera"}, 2
            )
            self.assertEqual("ok", cam["status"])
            self.assertIn('type: "camera"', (project / "main" / "hero.go").read_text(encoding="utf-8"))

    def test_authoring_properties_have_disk_source(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "main" / "main.collection").write_text(
                'name: "main"\nembedded_instances {\n  id: "cube"\n  position {\n    x: 1.0\n    y: 2.0\n    z: 3.0\n  }\n}\n',
                encoding="utf-8",
            )
            result = dispatch_command(
                project,
                "gameobject_get_properties",
                {"collection": "/main/main.collection", "id": "cube"},
                2,
            )
            self.assertEqual("ok", result["status"])
            self.assertEqual("disk", result["data"]["source"])
            self.assertEqual([1.0, 2.0, 3.0], result["data"]["properties"]["position"])
            self.assertEqual("embedded", result["data"]["kind"])
            self.assertEqual([], result["data"]["components"])

    def test_runtime_state_lists_targets(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = dispatch_command(project, "runtime_state", {}, 2)
            self.assertEqual("ok", result["status"])
            self.assertIn("targets", result["data"])
            kinds = [item["kind"] for item in result["data"]["targets"]]
            self.assertIn("loopback-8001", kinds)

    def test_screenshot_handshake(self):
        import tempfile
        import threading
        import time
        from pathlib import Path

        from agent_runtime import request_live_screenshot

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            control = project / ".internal" / "agent" / "control"
            dest = project / ".internal" / "agent" / "snapshots" / "shot.png"

            def engine_side():
                request = control / "screenshot.request"
                for _ in range(200):
                    if request.is_file():
                        break
                    time.sleep(0.01)
                path = Path(request.read_text(encoding="utf-8").strip())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"png")
                (control / "screenshot.ready").write_text(f"OK\n{path}\n", encoding="utf-8")

            worker = threading.Thread(target=engine_side)
            worker.start()
            written = request_live_screenshot(project, dest, timeout=2)
            worker.join(timeout=2)
            self.assertTrue(written.is_file())
            self.assertEqual(b"png", written.read_bytes())

    def test_aliases_and_disk_collection_ops(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "main" / "main.collection").write_text('name: "main"\n', encoding="utf-8")
            created = dispatch_command(
                project,
                "create_gameobject",
                {"collection": "/main/main.collection", "id": "cube", "position": [1, 2, 3]},
                2,
            )
            self.assertEqual("ok", created["status"])
            found = dispatch_command(
                project,
                "gameobject_manage",
                {"op": "find", "collection": "/main/main.collection", "id": "cu"},
                2,
            )
            self.assertEqual("cube", found["data"]["matches"][0]["id"])
            moved = dispatch_command(
                project,
                "node_set_property",
                {"collection": "/main/main.collection", "id": "cube", "property": "position", "value": [4, 5, 6]},
                2,
            )
            self.assertEqual("ok", moved["status"])
            props = dispatch_command(
                project,
                "gameobject_get_properties",
                {"collection": "/main/main.collection", "id": "cube"},
                2,
            )
            self.assertEqual([4.0, 5.0, 6.0], props["data"]["properties"]["position"])
            (project / "main" / "cube.go").write_text("", encoding="utf-8")
            (project / "main" / "cube.script").write_text("function init(self)\nend\n", encoding="utf-8")
            attached = dispatch_command(
                project,
                "component_add",
                {"path": "/main/cube.go", "type": "sprite", "id": "cube"},
                2,
            )
            self.assertEqual("ok", attached["status"])
            self.assertIn('type: "sprite"', (project / "main" / "cube.go").read_text(encoding="utf-8"))
            painted = dispatch_command(
                project,
                "component_manage",
                {
                    "op": "set_property",
                    "path": "/main/cube.go",
                    "id": "cube",
                    "component": "sprite",
                    "property": "default_animation",
                    "value": "idle",
                },
                2,
            )
            self.assertEqual("ok", painted["status"])
            self.assertIn("idle", (project / "main" / "cube.go").read_text(encoding="utf-8"))
            scripted = dispatch_command(
                project,
                "script_attach",
                {"collection": "/main/main.collection", "id": "cube", "path": "/main/cube.script"},
                2,
            )
            self.assertEqual("ok", scripted["status"])
            collection = (project / "main" / "main.collection").read_text(encoding="utf-8")
            self.assertIn("cube.script", collection)
            read = dispatch_command(
                project,
                "script_manage",
                {"op": "read", "collection": "/main/main.collection", "id": "cube"},
                2,
            )
            self.assertEqual("ok", read["status"])
            self.assertIn("function init(self)", read["data"]["text"])
            props = dispatch_command(
                project,
                "gameobject_get_properties",
                {"collection": "/main/main.collection", "id": "cube"},
                2,
            )
            self.assertTrue(any(item.get("path") == "/main/cube.script" for item in props["data"]["components"]))
            renamed = dispatch_command(
                project,
                "gameobject_manage",
                {"op": "rename", "collection": "/main/main.collection", "id": "cube", "name": "box"},
                2,
            )
            self.assertEqual("ok", renamed["status"])
            roots = dispatch_command(
                project,
                "collection_manage",
                {"op": "get_roots", "path": "/main/main.collection"},
                2,
            )
            self.assertEqual("box", roots["data"]["children"][0]["id"])
            removed = dispatch_command(
                project,
                "collection_manage",
                {"op": "remove_instance", "collection": "/main/main.collection", "id": "box"},
                2,
            )
            self.assertEqual("ok", removed["status"])
            self.assertNotIn('id: "box"', (project / "main" / "main.collection").read_text(encoding="utf-8"))

    def test_search_offset_and_exclude_domains(self):
        import tempfile
        from pathlib import Path

        from agent_mcp import configure_mcp, handle_rpc

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "main" / "a.script").write_text("needle here\n", encoding="utf-8")
            (project / "main" / "b.script").write_text("needle here\n", encoding="utf-8")
            result = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "search", "query": "needle", "offset": 0, "limit": 1},
                2,
            )
            self.assertEqual("ok", result["status"])
            self.assertEqual(2, result["data"]["total"])
            self.assertEqual(1, len(result["data"]["matches"]))
            self.assertTrue(result["data"]["truncated"])
            page = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "search", "query": "needle", "offset": 1, "limit": 1},
                2,
            )
            self.assertEqual(1, len(page["data"]["matches"]))
            self.assertNotEqual(result["data"]["matches"], page["data"]["matches"])
            configure_mcp(["atlas"])
            try:
                listed = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, project, 2)
                names = [item["name"] for item in listed["result"]["tools"]]
                self.assertNotIn("atlas_manage", names)
                self.assertIn("runtime_observe", names)
                called = handle_rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "atlas_manage", "arguments": {"op": "list"}},
                    },
                    project,
                    2,
                )
                body = __import__("json").loads(called["result"]["content"][0]["text"])
                self.assertEqual("NOT_ALLOWED", body["error"]["code"])
            finally:
                configure_mcp([])

    def test_building_lock_blocks_writes(self):
        import tempfile
        from pathlib import Path

        from agent_ops import BuildingLock

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with BuildingLock(project):
                result = dispatch_command(project, "script_create", {"path": "/main/a.script"}, 2)
            self.assertEqual("error", result["status"])
            self.assertEqual("EDITOR_NOT_READY", result["error"]["code"])
            self.assertEqual("building", result["error"]["data"]["sub_code"])
            self.assertFalse((project / "main" / "a.script").exists())
            created = dispatch_command(project, "script_create", {"path": "/main/a.script"}, 2)
            self.assertEqual("ok", created["status"])

    def test_tilesource_font_sound_disk(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            tiles = dispatch_command(
                project,
                "tilesource_manage",
                {"op": "create", "path": "/main/tiles.tilesource", "image": "/main/tiles.png"},
                2,
            )
            self.assertEqual("ok", tiles["status"])
            dispatch_command(
                project,
                "tilesource_manage",
                {"op": "add_animation", "path": "/main/tiles.tilesource", "id": "walk", "start_tile": 1, "end_tile": 4},
                2,
            )
            got = dispatch_command(project, "tilesource_manage", {"op": "get", "path": "/main/tiles.tilesource"}, 2)
            self.assertIn("walk", got["data"]["animations"])
            font = dispatch_command(project, "font_manage", {"op": "create", "path": "/main/ui.font"}, 2)
            self.assertEqual("ok", font["status"])
            sound = dispatch_command(
                project,
                "sound_manage",
                {"op": "create", "path": "/main/beep.sound", "sound": "/main/beep.ogg"},
                2,
            )
            self.assertEqual("ok", sound["status"])
            beep = dispatch_command(project, "sound_manage", {"op": "get", "path": "/main/beep.sound"}, 2)
            self.assertEqual("/main/beep.ogg", beep["data"]["sound"])
            pads = dispatch_command(project, "gamepads_manage", {"op": "create", "path": "/input/default.gamepads"}, 2)
            self.assertEqual("ok", pads["status"])
            listed = dispatch_command(project, "gamepads_manage", {"op": "get", "path": "/input/default.gamepads"}, 2)
            self.assertTrue(listed["data"]["devices"])
            profiles = dispatch_command(
                project, "display_profiles_manage", {"op": "create", "path": "/builtins/display.display_profiles"}, 2
            )
            self.assertEqual("ok", profiles["status"])
            factory = dispatch_command(
                project,
                "factory_manage",
                {"op": "create", "path": "/main/box.factory", "prototype": "/main/cube.go"},
                2,
            )
            self.assertEqual("ok", factory["status"])
            got_factory = dispatch_command(project, "factory_manage", {"op": "get", "path": "/main/box.factory"}, 2)
            self.assertEqual("/main/cube.go", got_factory["data"]["prototype"])
            proxy = dispatch_command(
                project,
                "collectionproxy_manage",
                {"op": "create", "path": "/main/level.collectionproxy", "collection": "/main/main.collection"},
                2,
            )
            self.assertEqual("ok", proxy["status"])
            cube_map = dispatch_command(project, "cubemap_manage", {"op": "create", "path": "/main/sky.cubemap"}, 2)
            self.assertEqual("ok", cube_map["status"])
            textures = dispatch_command(
                project, "texture_profiles_manage", {"op": "create", "path": "/main/os.texture_profiles"}, 2
            )
            self.assertEqual("ok", textures["status"])
            compute = dispatch_command(project, "compute_manage", {"op": "create", "path": "/main/blur.compute"}, 2)
            self.assertEqual("ok", compute["status"])
            manifest = dispatch_command(
                project, "appmanifest_manage", {"op": "create", "path": "/main/game.appmanifest"}, 2
            )
            self.assertEqual("ok", manifest["status"])

    def test_logs_read_source_engine(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            log = project / ".internal" / "agent" / "engine.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("only-engine\n", encoding="utf-8")
            result = dispatch_command(project, "logs_read", {"source": "engine", "limit": 10}, 1)
            self.assertEqual("ok", result["status"])
            self.assertEqual("engine-log", result["data"]["source"])
            self.assertIn("only-engine", result["data"]["lines"][0])
            blocked = dispatch_command(project, "logs_read", {"source": "editor"}, 1)
            self.assertEqual("error", blocked["status"])
            self.assertEqual("EDITOR_UNREACHABLE", blocked["error"]["code"])

    def test_referenced_go_and_project_stop(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "main" / "hero.go").write_text("", encoding="utf-8")
            (project / "main" / "main.collection").write_text('name: "main"\n', encoding="utf-8")
            created = dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "hero", "path": "/main/hero.go"},
                2,
            )
            self.assertEqual("ok", created["status"])
            self.assertEqual("referenced", created["data"]["kind"])
            tree = dispatch_command(
                project,
                "collection_get_hierarchy",
                {"path": "/main/main.collection"},
                2,
            )
            self.assertEqual("referenced", tree["data"]["children"][0]["kind"])
            self.assertEqual("/main/hero.go", tree["data"]["children"][0]["prototype"])
            (project / "main" / "level.collection").write_text('name: "level"\n', encoding="utf-8")
            nested = dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "level", "path": "/main/level.collection"},
                2,
            )
            self.assertEqual("ok", nested["status"])
            self.assertEqual("collection_instance", nested["data"]["kind"])
            tree = dispatch_command(
                project,
                "collection_get_hierarchy",
                {"path": "/main/main.collection"},
                2,
            )
            kinds = {child["id"]: child["kind"] for child in tree["data"]["children"]}
            self.assertEqual("collection_instance", kinds["level"])
            state = dispatch_command(project, "editor_manage", {"op": "state"}, 1)
            self.assertEqual("ok", state["status"])
            self.assertIn("ready", state["data"])
            stopped = dispatch_command(project, "project_manage", {"op": "stop"}, 1)
            self.assertEqual("error", stopped["status"])
            self.assertEqual("ENGINE_NOT_RUNNING", stopped["error"]["code"])

    def test_mcp_config_write(self):
        import tempfile
        from pathlib import Path

        from defold_agent import build_parser

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            parser = build_parser()
            args = parser.parse_args(["mcp-config", "--project", str(project), "--write"])
            self.assertEqual(0, args.func(args))
            dest = project / ".cursor" / "mcp.json"
            self.assertTrue(dest.is_file())
            text = dest.read_text(encoding="utf-8")
            self.assertIn("defold-agent", text)
            self.assertNotIn("http://", text)
            snippet = dispatch_command(project, "editor_manage", {"op": "mcp_config", "format": "cursor"}, 1)
            self.assertEqual("ok", snippet["status"])
            self.assertIn("defold-agent", snippet["data"]["text"])
            self.assertFalse(snippet["data"]["http"])

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
