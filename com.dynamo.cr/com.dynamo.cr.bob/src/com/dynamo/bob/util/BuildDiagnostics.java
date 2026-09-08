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

import com.dynamo.bob.CompileExceptionError;
import com.dynamo.bob.MultipleCompileException;
import com.dynamo.bob.Task;
import com.dynamo.bob.TaskResult;
import com.dynamo.bob.fs.IResource;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Structured build issues for tools and agents.
 * Matches the editor {@code POST /command/build} JSON shape, with extra 1-based {@code line}.
 */
public final class BuildDiagnostics {
    public static final class Issue {
        public String severity = "error";
        public String message = "";
        public String resource;
        public Integer line;

        public Issue() {
        }

        public Issue(String severity, String resource, Integer line, String message) {
            this.severity = severity;
            this.resource = resource;
            this.line = line;
            this.message = message != null ? message : "";
        }
    }

    private BuildDiagnostics() {
    }

    public static String resourcePath(IResource resource) {
        if (resource == null) {
            return null;
        }
        String path = resource.getPath();
        if (path == null || path.isEmpty()) {
            return null;
        }
        path = path.replace('\\', '/');
        if (!path.startsWith("/")) {
            path = "/" + path;
        }
        return path;
    }

    public static Issue fromResource(String severity, IResource resource, int lineNumber, String message) {
        Integer line = lineNumber > 0 ? lineNumber : null;
        return new Issue(severity, resourcePath(resource), line, message);
    }

    public static void addFromTaskResult(List<Issue> issues, TaskResult taskResult) {
        if (taskResult == null || taskResult.isOk()) {
            return;
        }
        String message = taskResult.getMessage();
        if (message == null || message.isEmpty()) {
            if (taskResult.getException() != null && taskResult.getException().getMessage() != null) {
                message = taskResult.getException().getMessage();
            } else {
                message = "undefined";
            }
        }
        Task task = taskResult.getTask();
        IResource resource = task != null ? task.input(0) : null;
        issues.add(fromResource("error", resource, taskResult.getLineNumber(), message));
    }

    public static void addFromCompileException(List<Issue> issues, CompileExceptionError error) {
        if (error == null) {
            return;
        }
        issues.add(fromResource("error", error.getResource(), error.getLineNumber(), error.getMessage()));
    }

    public static void addFromMultiple(List<Issue> issues, MultipleCompileException error) {
        if (error == null) {
            return;
        }
        for (MultipleCompileException.Info info : error.issues) {
            String severity = "error";
            if (info.getSeverity() == MultipleCompileException.Info.SEVERITY_WARNING) {
                severity = "warning";
            } else if (info.getSeverity() == MultipleCompileException.Info.SEVERITY_INFO) {
                severity = "information";
            }
            issues.add(fromResource(severity, info.getResource(), info.getLineNumber(), info.getMessage()));
        }
    }

    public static void printAgentLines(List<Issue> issues) {
        for (Issue issue : issues) {
            String prefix = "warning".equals(issue.severity) ? "WARNING" : "ERROR";
            String resource = issue.resource != null ? issue.resource : "<unknown>";
            String line = issue.line != null ? (":" + issue.line) : "";
            System.out.printf("%s:BUILD: %s%s: %s%n", prefix, resource, line, issue.message);
        }
    }

    public static Map<String, Object> toMap(boolean success, List<Issue> issues) {
        List<Map<String, Object>> encoded = new ArrayList<>();
        for (Issue issue : issues) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("severity", issue.severity);
            item.put("message", issue.message);
            if (issue.resource != null) {
                item.put("resource", issue.resource);
            }
            if (issue.line != null) {
                item.put("line", issue.line);
                Map<String, Object> start = new LinkedHashMap<>();
                start.put("line", issue.line - 1);
                start.put("character", 0);
                Map<String, Object> end = new LinkedHashMap<>();
                end.put("line", issue.line - 1);
                end.put("character", 0);
                Map<String, Object> range = new LinkedHashMap<>();
                range.put("start", start);
                range.put("end", end);
                item.put("range", range);
            }
            encoded.add(item);
        }
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("success", success);
        root.put("source", "bob");
        root.put("issues", encoded);
        return root;
    }

    public static void writeJson(File file, boolean success, List<Issue> issues) throws IOException {
        File parent = file.getParentFile();
        if (parent != null) {
            parent.mkdirs();
        }
        ObjectMapper mapper = new ObjectMapper();
        ObjectNode root = mapper.createObjectNode();
        root.put("success", success);
        root.put("source", "bob");
        ArrayNode array = root.putArray("issues");
        for (Issue issue : issues) {
            ObjectNode item = array.addObject();
            item.put("severity", issue.severity);
            item.put("message", issue.message);
            if (issue.resource != null) {
                item.put("resource", issue.resource);
            }
            if (issue.line != null) {
                item.put("line", issue.line);
                ObjectNode range = item.putObject("range");
                ObjectNode start = range.putObject("start");
                start.put("line", issue.line - 1);
                start.put("character", 0);
                ObjectNode end = range.putObject("end");
                end.put("line", issue.line - 1);
                end.put("character", 0);
            }
        }
        mapper.writerWithDefaultPrettyPrinter().writeValue(file, root);
    }
}
