// Copyright 2020-2026 The Defold Foundation
// Copyright 2014-2020 King
// Copyright 2009-2014 Ragnar Svensson, Christian Murray
// Licensed under the Defold License version 1.0 (the "License"); you may not use
// this file except in compliance with the License.
//
// You may obtain a copy of the License, together with FAQs at
// https://www.defold.com/license
//
// Unless required by applicable law or agreed to in writing, software distributed
// under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

package com.dynamo.bob.util;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import org.junit.Test;

import com.dynamo.bob.CompileExceptionError;
import com.dynamo.bob.test.util.MockFileSystem;
import com.dynamo.bob.test.util.MockResource;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public class BuildDiagnosticsTest {
    @Test
    public void testWriteJsonMatchesEditorShape() throws Exception {
        MockFileSystem fs = new MockFileSystem();
        MockResource resource = new MockResource(fs, "/main/player.script", new byte[0], 0);
        List<BuildDiagnostics.Issue> issues = new ArrayList<>();
        BuildDiagnostics.addFromCompileException(
                issues,
                new CompileExceptionError(resource, 12, "unexpected symbol near 'endd'"));

        File out = File.createTempFile("diagnostics", ".json");
        out.deleteOnExit();
        BuildDiagnostics.writeJson(out, false, issues);

        JsonNode root = new ObjectMapper().readTree(Files.readAllBytes(out.toPath()));
        assertEquals(false, root.get("success").asBoolean());
        assertEquals("bob", root.get("source").asText());
        assertEquals(1, root.get("issues").size());
        JsonNode issue = root.get("issues").get(0);
        assertEquals("error", issue.get("severity").asText());
        assertEquals("/main/player.script", issue.get("resource").asText());
        assertEquals(12, issue.get("line").asInt());
        assertEquals(11, issue.get("range").get("start").get("line").asInt());
        assertTrue(new String(Files.readAllBytes(out.toPath()), StandardCharsets.UTF_8)
                .contains("unexpected symbol near 'endd'"));
    }
}
