;; Copyright 2020-2026 The Defold Foundation
;; Copyright 2014-2020 King
;; Copyright 2009-2014 Ragnar Svensson, Christian Murray
;; Licensed under the Defold License version 1.0 (the "License"); you may not use
;; this file except in compliance with the License.
;;
;; You may obtain a copy of the License, together with FAQs at
;; https://www.defold.com/license
;;
;; Unless required by applicable law or agreed to in writing, software distributed
;; under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
;; CONDITIONS OF ANY KIND, either express or implied. See the License for the
;; specific language governing permissions and limitations under the License.

(ns editor.agent-config
  "stdio MCP client snippet. Never an HTTP URL."
  (:require [clojure.data.json :as json]
            [clojure.java.io :as io]
            [clojure.string :as string])
  (:import [java.io File]))

(set! *warn-on-reflection* true)

(defn- find-agent-script
  ^File []
  (loop [dir (.getCanonicalFile (io/file (System/getProperty "user.dir")))]
    (when dir
      (let [candidate (io/file dir "scripts" "agent" "defold_agent.py")]
        (if (.isFile candidate)
          candidate
          (recur (.getParentFile dir)))))))

(defn mcp-config-text
  "Cursor JSON or Codex TOML that launches `defold_agent.py mcp` over stdio."
  ^String [^File project-dir format]
  (let [script (or (find-agent-script)
                   (io/file "scripts" "agent" "defold_agent.py"))
        args [(.getAbsolutePath ^File script)
              "mcp"
              "--project"
              (.getAbsolutePath project-dir)]]
    (if (= "codex" format)
      (str "[mcp_servers.\"defold-agent\"]\n"
           "command = \"python\"\n"
           "args = ["
           (string/join ", " (mapv json/write-str args))
           "]\n"
           "enabled = true\n"
           "startup_timeout_sec = 60\n"
           "tool_timeout_sec = 360\n")
      (str "{\n"
           "  \"mcpServers\": {\n"
           "    \"defold-agent\": {\n"
           "      \"command\": \"python\",\n"
           "      \"args\": [\n"
           (string/join ",\n" (mapv #(str "        " (json/write-str %)) args))
           "\n      ]\n"
           "    }\n"
           "  }\n"
           "}\n"))))
