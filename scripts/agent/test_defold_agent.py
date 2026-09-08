#!/usr/bin/env python3
# Copyright 2020-2026 The Defold Foundation
# Licensed under the Defold License version 1.0 (the "License");
# you may not use this file except in compliance with the License.
#
# You may obtain a copy of the License, together with FAQs at
# https://www.defold.com/license

from __future__ import annotations

import unittest

from agent_ops import disk_command, parse_collection_hierarchy, patch_text
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


if __name__ == "__main__":
    unittest.main()
