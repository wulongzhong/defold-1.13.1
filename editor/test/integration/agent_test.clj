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

(ns integration.agent-test
  (:require [clojure.string :as string]
            [clojure.test :refer :all]
            [dynamo.graph :as g]
            [editor.agent :as agent]
            [editor.defold-project :as project]
            [integration.test-util :as test-util]
            [util.coll :as coll]))

(set! *warn-on-reflection* true)

(defn- ctx [project workspace app-view]
  {:project project
   :workspace workspace
   :app-view app-view
   :localization test-util/localization})

(defn- data [result]
  (is (= "ok" (:status result)) result)
  (:data result))

(deftest editor-state-and-hierarchy-test
  (test-util/with-scratch-project "test/resources/small_project"
    (let [ctx (ctx project workspace app-view)
          state (data (agent/handle ctx "editor_state" {}))
          hierarchy (data (agent/handle ctx "collection_get_hierarchy" {:path "/main/main.collection"}))]
      (is (string/includes? (:project_root state) "small_project"))
      (is (coll/any? #(= "collection_get_hierarchy" %) (:commands state)))
      (is (= "/main/main.collection" (:path hierarchy)))
      (is (coll/any? #(= "logo" (:id %)) (:children hierarchy))))))

(deftest create-game-object-is-undoable-test
  (test-util/with-scratch-project "test/resources/small_project"
    (let [ctx (ctx project workspace app-view)
          graph-id (g/node-id->graph-id project)
          created (data (agent/handle ctx "gameobject_create" {:collection "/main/main.collection"
                                                              :id "cube"
                                                              :position [10.0 20.0 0.0]}))
          props (data (agent/handle ctx "gameobject_get_properties" {:collection "/main/main.collection"
                                                                     :id "cube"}))
          hierarchy (data (agent/handle ctx "collection_get_hierarchy" {:path "/main/main.collection"}))]
      (is (= "cube" (:id created)))
      (is (:undoable created))
      (is (= "cube" (:id props)))
      (is (= [10.0 20.0 0.0] (get-in props [:properties "position" :value])))
      (is (coll/any? #(= "cube" (:id %)) (:children hierarchy)))
      (is (g/has-undo? graph-id))
      (g/undo! graph-id)
      (g/undo! graph-id)
      (let [after (data (agent/handle ctx "collection_get_hierarchy" {:path "/main/main.collection"}))]
        (is (not (coll/any? #(= "cube" (:id %)) (:children after))))
        (is (coll/any? #(= "logo" (:id %)) (:children after)))))))

(deftest script-create-attach-and-patch-test
  (test-util/with-scratch-project "test/resources/small_project"
    (let [ctx (ctx project workspace app-view)
          created (data (agent/handle ctx "script_create" {:path "/main/cube.script"}))
          attached (data (agent/handle ctx "script_attach" {:collection "/main/main.collection"
                                                           :id "logo"
                                                           :path "/main/cube.script"}))
          patched (data (agent/handle ctx "script_patch" {:path "/main/cube.script"
                                                          :old_text "function init(self)"
                                                          :new_text "function init(self) -- agent"}))
          read (data (agent/handle ctx "script_manage" {:op "read"
                                                        :path "/main/cube.script"}))
          missing (agent/handle ctx "script_patch" {:path "/main/cube.script"
                                                    :old_text "this substring is not in the file"
                                                    :new_text "x"})]
      (is (= "/main/cube.script" (:path created)))
      (is (some? (project/get-resource-node project "/main/cube.script")))
      (is (= "logo" (:id attached)))
      (is (:patched patched))
      (is (string/includes? (:text read) "function init(self) -- agent"))
      (is (= "error" (:status missing)))
      (is (= "OLD_TEXT_NOT_FOUND" (get-in missing [:error :code]))))))

(deftest unknown-command-and-manage-op-test
  (test-util/with-scratch-project "test/resources/small_project"
    (let [ctx (ctx project workspace app-view)
          unknown (agent/handle ctx "not_a_command" {})
          bad-op (agent/handle ctx "collection_manage" {:op "explode"})]
      (is (= "error" (:status unknown)))
      (is (= "UNKNOWN_COMMAND" (get-in unknown [:error :code])))
      (is (= "error" (:status bad-op)))
      (is (= "UNKNOWN_OP" (get-in bad-op [:error :code]))))))

(deftest get-properties-resolves-collection-instance-test
  (test-util/with-scratch-project "test/resources/small_project"
    (let [ctx (ctx project workspace app-view)

          _created-coll (data (agent/handle ctx "collection_manage" {:op "create"
                                                                    :path "/main/room.collection"}))

          created (data (agent/handle ctx "gameobject_create" {:collection "/main/main.collection"
                                                              :id "room"
                                                              :path "/main/room.collection"
                                                              :position [256.0 165.0 1.0]}))

          props (data (agent/handle ctx "gameobject_get_properties" {:collection "/main/main.collection"
                                                                     :id "room"}))]
      (is (= "collection_instance" (:kind created)))
      (is (= "room" (:id props)))
      (is (= "collection_instance" (:kind props)))
      (is (= [256.0 165.0 1.0] (get-in props [:properties "position" :value]))))))
