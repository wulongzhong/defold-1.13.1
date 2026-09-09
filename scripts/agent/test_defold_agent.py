#!/usr/bin/env python3
# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License");
# you may not use this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license

from __future__ import annotations

import unittest

from agent_ops import (
    decode_data_field,
    disk_command,
    dispatch_command,
    encode_data_lines,
    parse_collection_hierarchy,
    patch_text,
    reject_snapshot_read,
    unescape_proto,
)
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

    def test_warning_domain_and_print_and_stack(self):
        from agent_debug import parse_log_report

        report = parse_log_report(
            "WARNING:SCRIPT: Http cache disabled\n"
            "DEBUG:SCRIPT: hello player\n"
            "ERROR:GAMESYS: Factory failed to spawn\n"
            "ERROR:SCRIPT: /main/player.script:12: attempt to index a nil value\n"
            "          at: /main/player.script:12\n"
            "stack traceback:\n"
            "  /main/player.script:12: in function update\n"
            "INFO:ENGINE: Wrote runtime dump\n"
        )
        messages = [item["message"] for item in report["issues"]]
        self.assertIn("Http cache disabled", messages)
        self.assertIn("Factory failed to spawn", messages)
        self.assertTrue(all(item["severity"] != "debug" for item in report["issues"]))
        self.assertEqual(1, len(report["prints"]))
        self.assertIn("hello player", report["prints"][0]["message"])
        script = next(item for item in report["issues"] if item.get("resource") == "/main/player.script")
        self.assertEqual("SCRIPT", script["domain"])
        self.assertIn("update", script["stack"][0]["where"])
        self.assertFalse(any("Wrote runtime dump" in item["message"] for item in report["issues"]))


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
            gated = reject_snapshot_read(
                project,
                "filesystem_manage",
                {"op": "read_text", "path": "/.internal/agent/snapshots/latest.json"},
            )
            self.assertEqual("NOT_ALLOWED", gated["error"]["code"])

    def test_unescape_proto_handles_backslash_then_newline(self):
        self.assertEqual("size {\n", unescape_proto("size {\\n"))
        inner = 'size {\n  x: 128.0\n}\ntext: "Label"\n'
        block = 'embedded_components {\n  id: "label"\n  type: "label"\n' + encode_data_lines(inner) + "}\n"
        self.assertEqual(inner, decode_data_field(block))
        go = 'components {\n  id: "cube"\n  component: "/main/cube.script"\n}\n' + block
        instance = 'embedded_instances {\n  id: "cube"\n' + encode_data_lines(go) + "}\n"
        self.assertEqual(go, decode_data_field(instance))

    def test_embedded_sound_set_property_keeps_collection_valid(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            inner = 'sound: ""\nlooping: 0\ngroup: "master"\n'
            component = (
                'embedded_components {\n  id: "sound"\n  type: "sound"\n' + encode_data_lines(inner) + "}\n"
            )
            go = 'components {\n  id: "cube"\n  component: "/main/cube.script"\n}\n' + component
            collection = 'name: "main"\nembedded_instances {\n  id: "cube"\n' + encode_data_lines(go) + "}\n"
            (project / "main" / "main.collection").write_text(collection, encoding="utf-8")
            result = disk_command(
                project,
                "component_manage",
                {
                    "op": "set_property",
                    "collection": "/main/main.collection",
                    "id": "cube",
                    "component": "sound",
                    "property": "looping",
                    "value": True,
                },
            )
            self.assertEqual("ok", result["status"], result)
            text = (project / "main" / "main.collection").read_text(encoding="utf-8")
            decoded = decode_data_field(text[text.find("embedded_instances") :])
            self.assertIsNotNone(decoded)
            self.assertIn("sound", decoded)
            self.assertIn("looping: 1", decoded)
            self.assertNotIn('looping: "True"', decoded)
            self.assertIn("embedded_components", decoded)


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
            missing = query_snapshot(project, {"op": "get_node", "id": "no-such-go-xyz"})
            self.assertEqual("NOT_FOUND", missing["error"]["code"])
            self.assertIn("runtime_get_hierarchy", missing["error"].get("hint") or "")

    def test_get_node_prefers_game_object_over_script(self):
        import json
        import tempfile
        from pathlib import Path

        from agent_runtime import wrap_engine_dump

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            raw = project / "raw.json"
            raw.write_text(
                json.dumps(
                    {
                        "id": "main",
                        "type": "collectionc",
                        "children": [
                            {
                                "id": "/player",
                                "type": "goc",
                                "world_position": [8.0, 0.0, 0.0],
                                "children": [
                                    {
                                        "id": "player",
                                        "type": "scriptc",
                                        "resource": "/main/player.scriptc",
                                        "speed": 120.0,
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            wrap_engine_dump(project, raw, mode="live", frame=1, target={})
            result = query_snapshot(project, {"op": "get_node", "id": "player"})
            self.assertEqual("ok", result["status"])
            self.assertEqual("goc", result["data"]["node"]["type"])
            self.assertEqual([8.0, 0.0, 0.0], result["data"]["node"]["world_position"])

    def test_find_by_type(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            result = query_snapshot(project, {"op": "find", "type": "spritec"})
            self.assertEqual("ok", result["status"])
            self.assertEqual("sprite", result["data"]["matches"][0]["id"])
            paged = query_snapshot(project, {"op": "find", "type": "goc", "offset": 0, "limit": 1})
            self.assertEqual(0, paged["data"]["offset"])
            self.assertEqual(1, paged["data"]["limit"])
            self.assertTrue(paged["data"]["truncated"])
            subtree = query_snapshot(project, {"op": "get_subtree", "id": "cube", "offset": 0, "limit": 1})
            self.assertEqual(0, subtree["data"]["offset"])
            self.assertEqual(1, subtree["data"]["limit"])
            self.assertIsInstance(subtree["data"]["truncated"], bool)

    def test_dest_survives_prune_and_truncate_false(self):
        import json
        import tempfile
        from pathlib import Path

        from agent_runtime import prune_snapshots, snapshots_dir

        with tempfile.TemporaryDirectory() as tmp:
            project, record = self._project_with_snapshot(tmp)
            dest = project / ".internal" / "agent" / "kept-observe.json"
            raw = project / "raw.json"
            wrap_engine_dump(project, raw, mode="batch", frame=1, target={}, dest=dest)
            self.assertTrue(dest.is_file())
            self.assertIn("scene_graph", dest.read_text(encoding="utf-8"))
            snap_dir = snapshots_dir(project)
            for i in range(10):
                dummy = snap_dir / f"19990101T00000{i}Z-pad.json"
                dummy.write_text(json.dumps({"schema": 1, "id": dummy.stem, "scene_graph": {"id": "pad"}}), encoding="utf-8")
            wrap_engine_dump(project, raw, mode="batch", frame=2, target={})
            prune_snapshots(project)
            self.assertTrue(dest.is_file())
            wide = {
                "schema": 1,
                "id": "wide-nodes",
                "source": "runtime",
                "scene_graph": {
                    "id": "wide",
                    "type": "goc",
                    "children": [{"id": f"n{i}", "type": "goc", "children": []} for i in range(257)],
                },
            }
            (snap_dir / "wide-nodes.json").write_text(json.dumps(wide), encoding="utf-8")
            blocked = query_snapshot(project, {"op": "get_subtree", "id": "wide", "snapshot": "wide-nodes", "truncate": False})
            self.assertEqual("error", blocked["status"])
            self.assertEqual("INLINE_TOO_LARGE", blocked["error"]["code"])
            allowed = query_snapshot(project, {"op": "get_subtree", "id": "wide", "snapshot": "wide-nodes", "limit": 1})
            self.assertEqual("ok", allowed["status"])
            self.assertTrue(allowed["data"]["truncated"])
            self.assertTrue(allowed["data"].get("hint"))
            capped = query_snapshot(project, {"op": "get_subtree", "id": "wide", "snapshot": "wide-nodes", "limit": 256})
            self.assertEqual("ok", capped["status"])
            self.assertTrue(capped["data"]["truncated"])
            self.assertTrue(capped["data"].get("hint"))
            default_sub = query_snapshot(project, {"op": "get_subtree", "id": "cube"})
            self.assertEqual(80, default_sub["data"]["limit"])
            self.assertEqual(8, default_sub["data"]["depth"])
            default_find = query_snapshot(project, {"op": "find", "type": "goc"})
            self.assertEqual(50, default_find["data"]["limit"])
            unknown = query_snapshot(project, {"op": "explode"})
            self.assertEqual("UNKNOWN_OP", unknown["error"]["code"])
            missing = query_snapshot(project, {"op": "get_subtree"})
            self.assertEqual("MISSING_PARAM", missing["error"]["code"])
            missing_tree = query_snapshot(project, {"op": "get_subtree", "id": "no-such-go-xyz"})
            self.assertEqual("NOT_FOUND", missing_tree["error"]["code"])
            self.assertIn("runtime_get_hierarchy", missing_tree["error"].get("hint") or "")
            missing_node = query_snapshot(project, {"op": "get_node"})
            self.assertEqual("MISSING_PARAM", missing_node["error"]["code"])
            missing_cmp = query_snapshot(project, {"op": "compare_authoring", "id": "cube"})
            self.assertEqual("MISSING_PARAM", missing_cmp["error"]["code"])
            by_prop = query_snapshot(project, {"op": "find", "has_property": "world_position"})
            self.assertTrue(by_prop["data"]["matches"])
            typed = query_snapshot(project, {"op": "list_ids", "type": "goc"})
            self.assertTrue(any("cube" in str(item) for item in typed["data"]["ids"]))
            listed = query_snapshot(project, {"op": "list"})
            self.assertLessEqual(len(listed["data"]["snapshots"]), 8)
            globbed = query_snapshot(project, {"op": "list_ids", "id_glob": "*cube*"})
            self.assertTrue(any("cube" in str(item) for item in globbed["data"]["ids"]))
            bad_ptr = query_snapshot(project, {"op": "get_path", "path": "scene_graph"})
            self.assertEqual("INVALID_PARAM", bad_ptr["error"]["code"])
            missing_ptr = query_snapshot(project, {"op": "get_path"})
            self.assertEqual("MISSING_PARAM", missing_ptr["error"]["code"])
            hierarchy = __import__("agent_runtime", fromlist=["runtime_get_hierarchy"]).runtime_get_hierarchy(project, {})
            self.assertEqual(200, hierarchy["data"]["limit"])

    def test_get_path(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            result = query_snapshot(project, {"op": "get_path", "path": "/scene_graph/children/0/world_position"})
            self.assertEqual("ok", result["status"])
            self.assertEqual([10.0, 20.0, 0.0], result["data"]["value"])

    def test_snapshot_live_requires_engine(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = query_snapshot(project, {"op": "get_node", "id": "player", "snapshot": "live"})
            self.assertEqual("error", result["status"])
            self.assertEqual("ENGINE_NOT_RUNNING", result["error"]["code"])

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
            self.assertEqual(_record["id"], props["data"]["snapshot"])
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
            filtered = dispatch_command(project, "logs_read", {"source": "engine", "severity": "error", "q": "boom"}, 1)
            self.assertEqual(1, len(filtered["data"]["issues"]))
            prints = dispatch_command(project, "logs_read", {"source": "engine", "q": "hello"}, 1)
            self.assertIn("hello", prints["data"]["lines"][0])

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

    def test_project_run_reuses_projectc_when_editor_closed(self):
        import tempfile
        from pathlib import Path

        from defold_agent import project_run

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            missing_engine = str(project / "no-such-dmengine")
            missing = project_run(project, {"mode": "live", "engine": missing_engine}, 1)
            self.assertEqual("error", missing["status"])
            self.assertEqual("check failed before project_run", missing["error"]["message"])
            (project / "build" / "default").mkdir(parents=True)
            (project / "build" / "default" / "game.projectc").write_bytes(b"cached")
            reused = project_run(project, {"mode": "live", "engine": missing_engine}, 1)
            self.assertEqual("error", reused["status"])
            self.assertEqual("ENGINE_UNREACHABLE", reused["error"]["code"])
            self.assertNotEqual("check failed before project_run", reused["error"]["message"])

    def test_batch_observe_ignores_stale_raw_dump(self):
        import shutil
        import tempfile
        from pathlib import Path

        from defold_agent import observe_runtime

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            raw = project / ".internal" / "agent" / "snapshots" / "_raw.json"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text('{"id":"stale","type":"collectionc","children":[]}', encoding="utf-8")
            decoy = shutil.which("where") or shutil.which("where.exe")
            self.assertTrue(decoy)
            result = observe_runtime(
                project,
                {"mode": "batch", "frames": 1, "no_build": True, "engine": decoy},
                2,
            )
            self.assertEqual("error", result["status"])
            self.assertEqual("RUNTIME_DUMP_MISSING", result["error"]["code"])
            self.assertFalse(raw.is_file())

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
            raw_dump = project / ".internal" / "agent" / "snapshots" / "_raw.json"
            raw_dump.write_text(json.dumps({"id": "main", "type": "collectionc", "children": []}), encoding="utf-8")
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

    def test_compare_authoring_and_diagnostics(self):
        import tempfile
        from pathlib import Path

        from agent_debug import last_check_path, write_last_check

        with tempfile.TemporaryDirectory() as tmp:
            project, _record = self._project_with_snapshot(tmp)
            main = Path(tmp) / "main"
            main.mkdir()
            (main / "main.collection").write_text(
                'name: "main"\nembedded_instances {\n  id: "cube"\n  data: ""\n'
                "  position {\n    x: 0.0\n    y: 0.0\n    z: 0.0\n  }\n}\n",
                encoding="utf-8",
            )
            (project / "game.project").write_text(
                "[bootstrap]\nmain_collection = /main/main.collection\n",
                encoding="utf-8",
            )
            compared = dispatch_command(
                project,
                "runtime_snapshot_query",
                {"op": "compare_authoring", "id": "cube", "collection": "/main/main.collection"},
                2,
            )
            self.assertEqual("ok", compared["status"])
            item = compared["data"]["items"][0]
            self.assertEqual("cube", item["id"])
            self.assertFalse(item["same"])
            self.assertTrue(any(delta["property"] in {"position", "world_position"} for delta in item["deltas"]))
            write_last_check(
                project,
                {"success": False, "source": "bob", "issues": [{"severity": "error", "message": "boom", "resource": "/main/a.script"}]},
            )
            (project / ".internal" / "agent").mkdir(parents=True, exist_ok=True)
            (project / ".internal" / "agent" / "engine.log").write_text(
                "ERROR:SCRIPT: /main/a.script:4: nil\n",
                encoding="utf-8",
            )
            check_file = last_check_path(project)
            before_mtime = check_file.stat().st_mtime_ns
            diag = dispatch_command(project, "diagnostics_read", {}, 2)
            self.assertEqual("ok", diag["status"])
            self.assertEqual("diagnostics", diag["data"]["source"])
            self.assertEqual(before_mtime, check_file.stat().st_mtime_ns)
            self.assertGreaterEqual(diag["data"]["counts"]["error"], 1)
            self.assertTrue(any(issue.get("resource") == "/main/a.script" for issue in diag["data"]["issues"]))
            blocked = dispatch_command(project, "project_manage", {"op": "hot_reload"}, 1)
            self.assertEqual("error", blocked["status"])
            self.assertEqual("EDITOR_UNREACHABLE", blocked["error"]["code"])

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
            self.assertIn("defold://project/diagnostics", uris)
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
            self.assertTrue(any(item["name"] == "defold-author" for item in prompts["result"]["prompts"]))
            self.assertTrue(any(item["name"] == "defold-diagnose" for item in prompts["result"]["prompts"]))
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

    def test_batch_execute_rolls_back_on_disk(self):
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
            self.assertTrue(result["data"]["atomic"])
            self.assertFalse(result["data"]["undoable_separately"])
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
            self.assertTrue(failed["error"]["data"]["atomic"])
            mixed = dispatch_command(
                project,
                "batch_execute",
                {
                    "commands": [
                        {"command": "filesystem_manage", "params": {"op": "write_text", "path": "/main/mixed.txt", "text": "m"}},
                        {"command": "project_check", "params": {}},
                    ]
                },
                2,
            )
            payload = mixed.get("data") or (mixed.get("error") or {}).get("data") or {}
            self.assertFalse(payload.get("atomic"))
            self.assertTrue(failed["error"]["data"]["rolled_back"])
            self.assertEqual(1, failed["error"]["data"]["failed_index"])
            self.assertFalse((project / "main" / "b.script").is_file())
            from agent_ops import batch_uses_editor

            self.assertFalse(batch_uses_editor(project, [{"command": "script_create", "params": {"path": "/main/c.script"}}]))
            internal = project / ".internal"
            internal.mkdir(parents=True, exist_ok=True)
            (internal / "editor.port").write_text("59999", encoding="utf-8")
            (internal / "editor.token").write_text("tok", encoding="utf-8")
            self.assertTrue(
                batch_uses_editor(project, [{"command": "script_create", "params": {"path": "/main/c.script"}}])
            )
            self.assertFalse(
                batch_uses_editor(
                    project,
                    [
                        {"command": "script_create", "params": {"path": "/main/c.script"}},
                        {"command": "runtime_observe", "params": {}},
                    ],
                )
            )

    def test_session_list_and_activate(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        from agent_runtime import write_engine_record

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            session_dir = project / ".internal" / "agent"
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "session.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "session_id": "demo@abcd1234",
                        "project_path": str(project),
                        "editor_url": "http://127.0.0.1:59999",
                    }
                ),
                encoding="utf-8",
            )
            write_engine_record(project, {"pid": os.getpid(), "mode": "live", "url": "http://127.0.0.1:8001"})
            listed = dispatch_command(project, "session_manage", {"op": "list"}, 1)
            self.assertEqual("ok", listed["status"])
            kinds = {item["kind"] for item in listed["data"]["sessions"]}
            self.assertIn("editor", kinds)
            self.assertIn("cli-live", kinds)
            pinned = dispatch_command(project, "session_activate", {"id": "demo@abcd1234"}, 1)
            self.assertEqual("ok", pinned["status"])
            self.assertEqual("editor", pinned["data"]["session"]["kind"])
            by_url = dispatch_command(project, "session_activate", {"url": "http://127.0.0.1:59999"}, 1)
            self.assertEqual("ok", by_url["status"])
            self.assertEqual("editor", by_url["data"]["session"]["kind"])
            missing = dispatch_command(project, "session_activate", {"id": "no-such-session"}, 1)
            self.assertEqual("error", missing["status"])
            self.assertEqual("UNKNOWN_TARGET", missing["error"]["code"])
            unknown_op = dispatch_command(project, "session_manage", {"op": "switch"}, 1)
            self.assertEqual("error", unknown_op["status"])
            self.assertEqual("UNKNOWN_OP", unknown_op["error"]["code"])

    def test_session_other_editor_cannot_activate(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        previous = os.environ.get("DEFOLD_AGENT_SESSIONS_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "here"
            other = root / "other"
            project.mkdir()
            other.mkdir()
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            registry = root / "sessions"
            registry.mkdir()
            os.environ["DEFOLD_AGENT_SESSIONS_DIR"] = str(registry)
            try:
                (registry / "other@deadbeef.json").write_text(
                    json.dumps(
                        {
                            "schema": 1,
                            "session_id": "other@deadbeef",
                            "project_path": str(other),
                            "editor_url": "http://127.0.0.1:58888",
                            "editor_pid": os.getpid(),
                        }
                    ),
                    encoding="utf-8",
                )
                listed = dispatch_command(project, "session_manage", {"op": "list"}, 1)
                self.assertEqual("ok", listed["status"])
                kinds = {item["kind"] for item in listed["data"]["sessions"]}
                self.assertIn("other-editor", kinds)
                blocked = dispatch_command(project, "session_activate", {"id": "other@deadbeef"}, 1)
                self.assertEqual("error", blocked["status"])
                self.assertEqual("NOT_ALLOWED", blocked["error"]["code"])
            finally:
                if previous is None:
                    os.environ.pop("DEFOLD_AGENT_SESSIONS_DIR", None)
                else:
                    os.environ["DEFOLD_AGENT_SESSIONS_DIR"] = previous

    def test_runtime_intervention_gates(self):
        import tempfile
        from pathlib import Path

        from agent_intervene import format_debug_request, format_input_request

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            missing = dispatch_command(project, "runtime_input", {"keys": ["left"]}, 1)
            self.assertEqual("error", missing["status"])
            self.assertEqual("ENGINE_NOT_RUNNING", missing["error"]["code"])
            eval_off = dispatch_command(project, "game_eval", {"code": "return 1"}, 1)
            self.assertEqual("error", eval_off["status"])
            self.assertEqual("NOT_ALLOWED", eval_off["error"]["code"])
            eval_live = dispatch_command(project, "game_eval", {"code": "return 1", "confirm": True}, 1)
            self.assertEqual("ENGINE_NOT_RUNNING", eval_live["error"]["code"])
            forbidden = dispatch_command(
                project, "game_eval", {"code": "os.execute('dir')", "confirm": True}, 1
            )
            self.assertEqual("NOT_ALLOWED", forbidden["error"]["code"])
            debug_missing = dispatch_command(project, "runtime_debug", {"op": "status"}, 1)
            self.assertEqual("ENGINE_NOT_RUNNING", debug_missing["error"]["code"])
            unknown = dispatch_command(project, "runtime_debug", {"op": "repl"}, 1)
            self.assertEqual("UNKNOWN_OP", unknown["error"]["code"])
            empty_input = dispatch_command(project, "runtime_input", {}, 1)
            self.assertEqual("MISSING_PARAM", empty_input["error"]["code"])
            missing_eval = dispatch_command(project, "game_eval", {"confirm": True}, 1)
            self.assertEqual("MISSING_PARAM", missing_eval["error"]["code"])
            body, error = format_input_request({"keys": ["left", "space"], "hold": 4})
            self.assertIsNone(error)
            self.assertIn("key=left", body)
            self.assertIn("hold=4", body)
            from agent_intervene import held_input_release_body

            release = held_input_release_body({"key": "left", "hold": 24})
            self.assertIn("key=left:up", release)
            self.assertIsNone(held_input_release_body({"keys": [{"key": "left", "mode": "up"}]}))
            _, bad_key = format_input_request({"keys": ["not-a-key"]})
            self.assertEqual("INVALID_PARAM", bad_key["error"]["code"])
            _, long_hold = format_input_request({"keys": ["left"], "hold": 31})
            self.assertEqual("INVALID_PARAM", long_hold["error"]["code"])
            _, too_many = format_input_request({"keys": ["left"] * 17})
            self.assertEqual("INVALID_PARAM", too_many["error"]["code"])
            too_big = dispatch_command(project, "game_eval", {"code": "x" * 4097, "confirm": True}, 1)
            self.assertEqual("INVALID_PARAM", too_big["error"]["code"])
            _, bp = format_debug_request({"op": "set_breakpoint"})
            self.assertEqual("MISSING_PARAM", bp["error"]["code"])
            bp_body, bp_ok = format_debug_request({"op": "set_breakpoint", "file": "/main/player.script", "line": 9})
            self.assertIsNone(bp_ok)
            self.assertIn("file=main/player.script", bp_body)
            self.assertIn("line=9", bp_body)
            stack_body, stack_err = format_debug_request({"op": "stack"})
            self.assertIsNone(stack_err)
            self.assertIn("op=stack", stack_body)
            locals_body, locals_err = format_debug_request({"op": "locals"})
            self.assertIsNone(locals_err)
            self.assertIn("op=locals", locals_body)

    def test_parse_debug_ready_stack(self):
        from agent_intervene import parse_debug_ready

        text = (
            "OK\n"
            "op=stack\n"
            "paused=true\n"
            "break_file=/main/player.script\n"
            "break_line=12\n"
            "breakpoints=1\n"
            "frames=2\n"
            "locals=3\n"
            "stack_reason=breakpoint\n"
            "--json--\n"
            '{"frames":[{"depth":0,"file":"/main/player.script","line":12,'
            '"func":"update","what":"Lua",'
            '"locals":[{"name":"dt","value":0.016},{"name":"note","value":"a=b"}]},'
            '{"depth":1,"file":"/main/player.script","line":40,"func":"?",'
            '"what":"Lua","locals":[{"name":"msg","value":"hash"}]}]}\n'
        )
        ok, extra, fields, stack = parse_debug_ready(text)
        self.assertTrue(ok)
        self.assertEqual("stack", fields["op"])
        self.assertEqual("breakpoint", fields["stack_reason"])
        self.assertNotIn("note", extra)
        self.assertEqual(2, len(stack["frames"]))
        self.assertEqual("update", stack["frames"][0]["func"])
        self.assertEqual(0.016, stack["frames"][0]["locals"][0]["value"])
        self.assertEqual("a=b", stack["frames"][0]["locals"][1]["value"])

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
            self.assertIn("user_path", result["data"]["ready"])
            self.assertFalse(result["data"]["java_required"])
            self.assertEqual("stdio", result["data"]["mcp"]["transport"])
            self.assertIsNone(result["data"]["mcp"]["url"])
            self.assertGreater(result["data"]["mcp"]["tools"], 20)
            self.assertLess(result["data"]["mcp"]["tools"], 100)

    def test_find_engine_uses_editor_unpack(self):
        import os
        import tempfile
        from pathlib import Path

        from defold_agent import _host_bin_name, find_engine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editor_dir = root / "Defold"
            editor_dir.mkdir()
            editor_exe = editor_dir / ("Defold.exe" if os.name == "nt" else "Defold")
            editor_exe.write_bytes(b"editor")
            (editor_dir / "config").write_text("editor_sha1=abc\n", encoding="utf-8")
            host = _host_bin_name()
            machine = "arm64" if host.startswith("arm64") else "x86_64"
            unpack = root / "Defold" / "unpack" / f"abc-{machine}" / host / "bin"
            unpack.mkdir(parents=True)
            engine = unpack / ("dmengine.exe" if os.name == "nt" else "dmengine")
            engine.write_bytes(b"x")
            old_local = os.environ.get("LOCALAPPDATA")
            old_engine = os.environ.get("DEFOLD_ENGINE")
            old_editor = os.environ.get("DEFOLD_EDITOR")
            os.environ["LOCALAPPDATA"] = str(root)
            os.environ["DEFOLD_EDITOR"] = str(editor_exe)
            os.environ.pop("DEFOLD_ENGINE", None)
            try:
                found = find_engine(project=root / "missing")
                self.assertIsNotNone(found)
                self.assertEqual(engine.resolve(), found.resolve())
            finally:
                if old_local is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_local
                if old_engine is None:
                    os.environ.pop("DEFOLD_ENGINE", None)
                else:
                    os.environ["DEFOLD_ENGINE"] = old_engine
                if old_editor is None:
                    os.environ.pop("DEFOLD_EDITOR", None)
                else:
                    os.environ["DEFOLD_EDITOR"] = old_editor

    def test_find_engine_extracts_from_editor_jar(self):
        import os
        import tempfile
        import zipfile
        from pathlib import Path

        from defold_agent import _host_bin_name, find_engine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editor_dir = root / "boxed" / "Defold"
            packages = editor_dir / "packages"
            packages.mkdir(parents=True)
            editor_exe = editor_dir / ("Defold.exe" if os.name == "nt" else "Defold")
            editor_exe.write_bytes(b"editor")
            (editor_dir / "config").write_text("editor_sha1=deadbeef\n", encoding="utf-8")
            host = _host_bin_name()
            exe_name = "dmengine.exe" if os.name == "nt" else "dmengine"
            jar = packages / "defold-deadbeef.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr(f"libexec/{host}/{exe_name}", b"custom-engine")
            old_local = os.environ.get("LOCALAPPDATA")
            old_engine = os.environ.get("DEFOLD_ENGINE")
            old_editor = os.environ.get("DEFOLD_EDITOR")
            os.environ["LOCALAPPDATA"] = str(root)
            os.environ["DEFOLD_EDITOR"] = str(editor_exe)
            os.environ.pop("DEFOLD_ENGINE", None)
            try:
                found = find_engine(project=root / "missing")
                self.assertIsNotNone(found)
                self.assertEqual(b"custom-engine", found.read_bytes())
            finally:
                if old_local is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_local
                if old_engine is None:
                    os.environ.pop("DEFOLD_ENGINE", None)
                else:
                    os.environ["DEFOLD_ENGINE"] = old_engine
                if old_editor is None:
                    os.environ.pop("DEFOLD_EDITOR", None)
                else:
                    os.environ["DEFOLD_EDITOR"] = old_editor

    def test_camera_manage_accepts_collection_and_id(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "main" / "main.collection").write_text('name: "main"\n', encoding="utf-8")
            dispatch_command(project, "gameobject_create", {"collection": "/main/main.collection", "id": "cube"}, 2)
            result = dispatch_command(
                project,
                "camera_manage",
                {"op": "add", "collection": "/main/main.collection", "id": "cube"},
                2,
            )
            self.assertEqual("ok", result["status"], result)
            text = (project / "main" / "main.collection").read_text(encoding="utf-8")
            self.assertIn("camera", text)
            removed = dispatch_command(
                project,
                "camera_manage",
                {"op": "remove", "collection": "/main/main.collection", "id": "cube"},
                2,
            )
            self.assertEqual("ok", removed["status"], removed)
            text = (project / "main" / "main.collection").read_text(encoding="utf-8")
            self.assertNotIn("camera", text)

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

    def test_authoring_position_omitted_z_and_missing_block(self):
        from agent_ops import parse_gameobject_properties

        text = (
            'name: "main"\n'
            "embedded_instances {\n"
            '  id: "cube"\n'
            "  position {\n"
            "    x: 120.0\n"
            "    y: 40.0\n"
            "  }\n"
            "}\n"
            "embedded_instances {\n"
            '  id: "player"\n'
            '  data: ""\n'
            "}\n"
        )
        cube = parse_gameobject_properties(text, "/main/main.collection", "cube")
        player = parse_gameobject_properties(text, "/main/main.collection", "player")
        self.assertEqual([120.0, 40.0, 0.0], cube["properties"]["position"])
        self.assertEqual([0.0, 0.0, 0.0], player["properties"]["position"])

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
                {
                    "path": "/main/cube.go",
                    "type": "sprite",
                    "id": "cube",
                    "tile_set": "/main/hero.atlas",
                    "animation": "idle",
                },
                2,
            )
            self.assertEqual("ok", attached["status"])
            go_text = (project / "main" / "cube.go").read_text(encoding="utf-8")
            self.assertIn('type: "sprite"', go_text)
            self.assertIn("hero.atlas", go_text)
            self.assertIn("idle", go_text)
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

    def test_observing_handshake_blocks_writes(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            control = project / ".internal" / "agent" / "control"
            control.mkdir(parents=True, exist_ok=True)
            (control / "dump.request").write_text("observe\n", encoding="utf-8")
            result = dispatch_command(project, "script_create", {"path": "/main/a.script"}, 2)
            self.assertEqual("error", result["status"])
            self.assertEqual("EDITOR_NOT_READY", result["error"]["code"])
            self.assertEqual("observing", result["error"]["data"]["sub_code"])
            self.assertFalse((project / "main" / "a.script").exists())
            (control / "dump.request").unlink()
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
            invalid = dispatch_command(project, "logs_read", {"source": "explode"}, 1)
            self.assertEqual("error", invalid["status"])
            self.assertEqual("INVALID_PARAM", invalid["error"]["code"])

    def test_gameobject_parent_on_disk(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "main" / "main.collection").write_text('name: "main"\n', encoding="utf-8")
            dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "cube"},
                2,
            )
            missing = dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "hat", "parent": "nope"},
                2,
            )
            self.assertEqual("NOT_FOUND", missing["error"]["code"])
            child = dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "hat", "parent": "cube"},
                2,
            )
            self.assertEqual("ok", child["status"])
            self.assertEqual("cube", child["data"]["parent"])
            text = (project / "main" / "main.collection").read_text(encoding="utf-8")
            self.assertIn('children: "hat"', text)
            tree = dispatch_command(
                project,
                "collection_get_hierarchy",
                {"path": "/main/main.collection"},
                2,
            )
            by_id = {item["id"]: item for item in tree["data"]["children"]}
            self.assertEqual(["hat"], by_id["cube"]["children"])
            self.assertEqual("cube", by_id["hat"]["parent"])
            nested = dispatch_command(
                project,
                "collection_get_hierarchy",
                {"path": "/main/main.collection", "nested": True},
                2,
            )
            self.assertTrue(nested["data"]["nested"])
            self.assertEqual(["cube"], [item["id"] for item in nested["data"]["children"]])
            self.assertEqual("hat", nested["data"]["children"][0]["children"][0]["id"])
            roots = dispatch_command(
                project,
                "collection_manage",
                {"op": "get_roots", "path": "/main/main.collection"},
                2,
            )
            self.assertEqual(["cube"], [item["id"] for item in roots["data"]["children"]])
            props = dispatch_command(
                project,
                "gameobject_get_properties",
                {"collection": "/main/main.collection", "id": "hat"},
                2,
            )
            self.assertEqual("cube", props["data"]["parent"])
            renamed = dispatch_command(
                project,
                "gameobject_manage",
                {"op": "rename", "collection": "/main/main.collection", "id": "hat", "name": "cap"},
                2,
            )
            self.assertEqual("ok", renamed["status"])
            text = (project / "main" / "main.collection").read_text(encoding="utf-8")
            self.assertIn('children: "cap"', text)
            self.assertNotIn('children: "hat"', text)
            dispatch_command(
                project,
                "gameobject_manage",
                {"op": "delete", "collection": "/main/main.collection", "id": "cap"},
                2,
            )
            text = (project / "main" / "main.collection").read_text(encoding="utf-8")
            self.assertNotIn('children: "cap"', text)
            self.assertIn('id: "cube"', text)

    def test_disk_transform_and_filesystem_list_delete(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            (project / "main" / "main.collection").write_text('name: "main"\n', encoding="utf-8")
            (project / "main" / "scratch.script").write_text("function init(self)\nend\n", encoding="utf-8")
            dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "cube"},
                2,
            )
            dispatch_command(
                project,
                "gameobject_create",
                {"collection": "/main/main.collection", "id": "hat"},
                2,
            )
            turned = dispatch_command(
                project,
                "gameobject_manage",
                {
                    "op": "set_property",
                    "collection": "/main/main.collection",
                    "id": "cube",
                    "property": "rotation",
                    "value": 90,
                },
                2,
            )
            self.assertEqual("ok", turned["status"])
            sized = dispatch_command(
                project,
                "gameobject_manage",
                {
                    "op": "set_property",
                    "collection": "/main/main.collection",
                    "id": "cube",
                    "property": "scale",
                    "value": 2,
                },
                2,
            )
            self.assertEqual("ok", sized["status"])
            parented = dispatch_command(
                project,
                "gameobject_manage",
                {
                    "op": "set_property",
                    "collection": "/main/main.collection",
                    "id": "hat",
                    "property": "parent",
                    "value": "cube",
                },
                2,
            )
            self.assertEqual("ok", parented["status"])
            props = dispatch_command(
                project,
                "gameobject_get_properties",
                {"collection": "/main/main.collection", "id": "cube"},
                2,
            )
            rotation = props["data"]["properties"]["rotation"]
            self.assertAlmostEqual(0.7071, rotation[2], places=3)
            self.assertAlmostEqual(0.7071, rotation[3], places=3)
            self.assertEqual([2.0, 2.0, 2.0], props["data"]["properties"]["scale"])
            self.assertEqual(["hat"], props["data"]["children"])
            listed = dispatch_command(project, "filesystem_manage", {"op": "list", "path": "/"}, 2)
            names = {item["name"] for item in listed["data"]["entries"]}
            self.assertIn("main", names)
            self.assertIn("game.project", names)
            self.assertNotIn(".internal", names)
            deleted = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "delete", "path": "/main/scratch.script"},
                2,
            )
            self.assertEqual("ok", deleted["status"])
            self.assertFalse((project / "main" / "scratch.script").is_file())
            blocked = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "delete", "path": "/game.project"},
                2,
            )
            self.assertEqual("NOT_ALLOWED", blocked["error"]["code"])
            escaped = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "exists", "path": "/main/../game.project"},
                2,
            )
            self.assertEqual("error", escaped["status"])
            self.assertEqual("INVALID_PARAM", escaped["error"]["code"])
            created = dispatch_command(
                project,
                "gameobject_create",
                {
                    "collection": "/main/main.collection",
                    "id": "spinner",
                    "rotation": 90,
                    "scale": 2,
                },
                2,
            )
            self.assertEqual("ok", created["status"])
            spun = dispatch_command(
                project,
                "gameobject_get_properties",
                {"collection": "/main/main.collection", "id": "spinner"},
                2,
            )
            self.assertAlmostEqual(0.7071, spun["data"]["properties"]["rotation"][2], places=3)
            self.assertEqual([2.0, 2.0, 2.0], spun["data"]["properties"]["scale"])
            (project / "main" / "note.txt").write_text("hi\n", encoding="utf-8")
            copied = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "copy", "path": "/main/note.txt", "dest": "/main/note2.txt"},
                2,
            )
            self.assertEqual("ok", copied["status"])
            self.assertTrue((project / "main" / "note2.txt").is_file())
            moved = dispatch_command(
                project,
                "filesystem_manage",
                {"op": "move", "path": "/main/note2.txt", "dest": "/main/note3.txt"},
                2,
            )
            self.assertEqual("ok", moved["status"])
            self.assertFalse((project / "main" / "note2.txt").is_file())
            self.assertTrue((project / "main" / "note3.txt").is_file())
            logs = dispatch_command(project, "logs_read", {"source": "all"}, 1)
            self.assertEqual("ok", logs["status"])
            self.assertEqual([], logs["data"]["lines"])
            made = dispatch_command(project, "filesystem_manage", {"op": "mkdir", "path": "/fx"}, 2)
            self.assertEqual("ok", made["status"])
            self.assertTrue((project / "fx").is_dir())
            existed = dispatch_command(project, "filesystem_manage", {"op": "exists", "path": "/fx"}, 2)
            self.assertTrue(existed["data"]["exists"])
            self.assertEqual("directory", existed["data"]["type"])

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

    def test_gui_set_and_get_node(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            created = dispatch_command(project, "gui_manage", {"op": "create", "path": "/main/hud.gui"}, 2)
            self.assertEqual("ok", created["status"])
            dispatch_command(
                project,
                "gui_manage",
                {"op": "add_text", "path": "/main/hud.gui", "id": "score", "text": "0"},
                2,
            )
            moved = dispatch_command(
                project,
                "gui_manage",
                {
                    "op": "set_node",
                    "path": "/main/hud.gui",
                    "id": "score",
                    "property": "position",
                    "value": [10, 20, 0],
                },
                2,
            )
            self.assertEqual("ok", moved["status"])
            labeled = dispatch_command(
                project,
                "gui_manage",
                {"op": "set_node", "path": "/main/hud.gui", "id": "score", "property": "text", "value": "99"},
                2,
            )
            self.assertEqual("ok", labeled["status"])
            node = dispatch_command(
                project,
                "gui_manage",
                {"op": "get_node", "path": "/main/hud.gui", "id": "score"},
                2,
            )
            self.assertEqual("99", node["data"]["text"])
            self.assertEqual([10.0, 20.0, 0.0], node["data"]["position"])
            preview = dispatch_command(project, "editor_preview", {"path": "/main/hud.gui"}, 1)
            self.assertEqual("EDITOR_UNREACHABLE", preview["error"]["code"])
            self.assertIn("runtime_screenshot", preview["error"].get("hint") or "")

    def test_tilemap_set_and_get_tile(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main").mkdir()
            created = dispatch_command(
                project,
                "tilemap_manage",
                {"op": "create", "path": "/main/level.tilemap", "tile_set": "/main/tiles.tilesource"},
                2,
            )
            self.assertEqual("ok", created["status"])
            dispatch_command(
                project,
                "tilemap_manage",
                {"op": "add_layer", "path": "/main/level.tilemap", "id": "ground"},
                2,
            )
            painted = dispatch_command(
                project,
                "tilemap_manage",
                {"op": "set_tile", "path": "/main/level.tilemap", "layer": "ground", "x": 2, "y": 3, "tile": 7},
                2,
            )
            self.assertEqual("ok", painted["status"])
            cell = dispatch_command(
                project,
                "tilemap_manage",
                {"op": "get_tile", "path": "/main/level.tilemap", "layer": "ground", "x": 2, "y": 3},
                2,
            )
            self.assertEqual(7, cell["data"]["tile"])
            dispatch_command(
                project,
                "tilemap_manage",
                {"op": "set_tile", "path": "/main/level.tilemap", "layer": "ground", "x": 2, "y": 3, "tile": 9},
                2,
            )
            text = (project / "main" / "level.tilemap").read_text(encoding="utf-8")
            self.assertEqual(1, text.count("x: 2"))
            self.assertIn("tile: 9", text)

    def test_api_manage_reads_engine_docs(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "game.project").write_text("[project]\ntitle = T\n", encoding="utf-8")
            result = dispatch_command(project, "api_manage", {"op": "get", "q": "go.set_position"}, 2)
            self.assertEqual("ok", result["status"])
            self.assertEqual("engine-docs", result["data"]["source"])
            names = [item["name"] for item in result["data"]["results"]]
            self.assertTrue(any("set_position" in name for name in names), names)

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
