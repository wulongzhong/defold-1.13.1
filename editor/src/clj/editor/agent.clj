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

(ns editor.agent
  "First-party agent command surface. Same capabilities as a Godot-style
  editor MCP, implemented in editor source rather than a game plugin."
  (:require [cljfx.api :as fx]
            [clojure.data.json :as json]
            [clojure.java.io :as io]
            [clojure.string :as string]
            [dynamo.graph :as g]
            [editor.agent-config :as agent-config]
            [editor.app-view :as app-view]
            [editor.code.data :as code.data]
            [editor.collection :as collection]
            [editor.defold-project :as project]
            [editor.doc :as doc]
            [editor.fs :as fs]
            [editor.game-object :as game-object]
            [editor.game-project :as game-project]
            [editor.localization :as localization]
            [editor.resource :as resource]
            [editor.resource-node :as resource-node]
            [editor.system :as system]
            [editor.ui :as ui]
            [editor.web-server :as web-server]
            [editor.workspace :as workspace]
            [util.coll :as coll]
            [util.http-server :as http-server])
  (:import [java.io File]
           [java.lang ProcessHandle]))

(set! *warn-on-reflection* true)

(def ^:dynamic *batch-file-journal* nil)

(def command-names
  ["api_manage"
   "appmanifest_manage"
   "atlas_manage"
   "batch_execute"
   "camera_manage"
   "collection_get_hierarchy"
   "collection_manage"
   "collection_open"
   "collection_save"
   "collectionfactory_manage"
   "collectionproxy_manage"
   "collisionobject_manage"
   "component_add"
   "component_manage"
   "compute_manage"
   "cubemap_manage"
   "diagnostics_read"
   "display_profiles_manage"
   "editor_manage"
   "editor_preview"
   "editor_state"
   "factory_manage"
   "filesystem_manage"
   "font_manage"
   "gamepads_manage"
   "gameobject_create"
   "gameobject_get_properties"
   "gameobject_manage"
   "gui_manage"
   "input_binding_manage"
   "logs_read"
   "material_manage"
   "mesh_manage"
   "model_manage"
   "particlefx_manage"
   "project_build"
   "project_manage"
   "render_manage"
   "script_attach"
   "script_create"
   "script_manage"
   "script_patch"
   "session_activate"
   "session_manage"
   "sound_manage"
   "texture_profiles_manage"
   "tilemap_manage"
   "tilesource_manage"])

(def ^:private command-aliases
  {"add_component" "component_add"
   "add_instance" "gameobject_create"
   "create_gameobject" "gameobject_create"
   "create_node" "gameobject_create"
   "create_script" "script_create"
   "node_create" "gameobject_create"
   "node_get_properties" "gameobject_get_properties"
   "node_set_property" "gameobject_manage"
   "patch_script" "script_patch"
   "scene_get_hierarchy" "collection_get_hierarchy"
   "scene_open" "collection_open"
   "scene_save" "collection_save"})

(def ^:private read-ops
  #{"exists" "find" "get" "get_roots" "list" "mcp_config" "read" "read_text" "search" "selection_get" "settings_get" "state" "stop"})

(def ^:private always-read-commands
  #{"api_manage"
    "collection_get_hierarchy"
    "collection_open"
    "diagnostics_read"
    "editor_preview"
    "editor_state"
    "gameobject_get_properties"
    "logs_read"
    "project_build"
    "project_check"
    "session_activate"
    "session_manage"})

(defn- fail!
  ([code message]
   (fail! code message nil nil))
  ([code message hint]
   (fail! code message hint nil))
  ([code message hint data]
   (throw (ex-info (or message code)
                   {:agent/error (cond-> {:code code
                                          :message (or message code)}
                                   hint (assoc :hint hint)
                                   data (assoc :data data))}))))

(defn- require-string [params k]
  (let [v (get params k)]
    (if-not (string? v)
      (fail! "MISSING_PARAM" (str "Missing string param: " (name k)) (str "Pass `" (name k) "` as a string."))
      v)))

(defn- optional-string [params k]
  (let [v (get params k)]
    (cond
      (nil? v) nil
      (string? v) v
      :else (fail! "INVALID_PARAM" (str (name k) " must be a string") nil))))

(defn- command-name [command]
  (let [raw (if (keyword? command)
              (name command)
              (str command))]
    (get command-aliases raw raw)))

(defn- alias-params [raw params]
  (if (and (= "node_set_property" raw)
           (not (or (get params :op) (get params "op"))))
    (assoc params :op "set_property")
    params))

(defn- authoring-write? [command params]
  (let [op (or (get params :op) (get params "op"))]
    (cond
      (contains? always-read-commands command)
      false

      (string? op)
      (not (contains? read-ops op))

      :else
      (not= "batch_execute" command))))

(defn- json-value [v]
  (cond
    (nil? v)
    nil

    (or (string? v) (boolean? v) (int? v))
    v

    (number? v)
    (double v)

    (keyword? v)
    (name v)

    (resource/resource? v)
    (resource/proj-path v)

    (vector? v)
    (mapv json-value v)

    (map? v)
    (into {}
          (keep (fn [[k val]]
                  (when-some [jv (json-value val)]
                    [(if (keyword? k) (name k) (str k)) jv])))
          v)

    :else
    (str v)))

(defn- sanitize-proj-path [path]
  (let [normalized (-> (or path "")
                       (string/replace "\\" "/"))]
    (if-not (string/starts-with? normalized "/")
      (fail! "INVALID_PATH" "Project paths must start with /" "Example: /main/player.script")
      (let [parts (string/split normalized #"/")]
        (if (coll/any? #(or (= % "..") (= % ".")) parts)
          (fail! "INVALID_PATH" "Path must stay inside the project" nil)
          normalized)))))

(defn- project-file ^File [workspace proj-path]
  (io/file (workspace/project-directory workspace) (subs proj-path 1)))

(defn- resource-stem [proj-path]
  (let [slash (.lastIndexOf proj-path (int \/))
        dot (.lastIndexOf proj-path (int \.))
        start (inc slash)]
    (if (and (pos? dot) (< start dot))
      (subs proj-path start dot)
      (subs proj-path start))))

(defn- outline-label [item localization]
  (let [label (:label item)]
    (cond
      (string? label)
      label

      (and localization (localization/message-pattern? label))
      (str (localization label))

      :else
      (or (:node-outline-key item) ""))))

(defn- node-kind [node-id]
  (cond
    (g/node-instance? collection/CollectionNode node-id) "collection"
    (g/node-instance? collection/CollectionInstanceNode node-id) "collection_instance"
    (g/node-instance? collection/GameObjectInstanceNode node-id) "gameobject"
    (g/node-instance? game-object/ComponentNode node-id) "component"
    (g/node-instance? game-object/GameObjectNode node-id) "gameobject_resource"
    :else "node"))

(defn- instance-kind [node-id]
  (cond
    (g/node-instance? collection/ReferencedGOInstanceNode node-id) "referenced"
    (g/node-instance? collection/EmbeddedGOInstanceNode node-id) "embedded"
    :else nil))

(defn- source-proj-path [node-id]
  (let [res (g/maybe-node-value node-id :source-resource)]
    (when (resource/resource? res)
      (resource/proj-path res))))

(defn- outline-node [item localization]
  (let [node-id (:node-id item)
        children (:children item)
        kind (instance-kind node-id)]
    (cond-> {:id (outline-label item localization)
             :type (node-kind node-id)
             :node_id node-id}
      kind
      (assoc :kind kind)

      (source-proj-path node-id)
      (assoc :resource (source-proj-path node-id))

      (and children (not (coll/empty? children)))
      (assoc :children (mapv #(outline-node % localization) children)))))

(defn- resolve-resource-node [ctx path]
  (let [proj-path (sanitize-proj-path path)
        node (project/get-resource-node (:project ctx) proj-path)]
    (if-not node
      (fail! "NOT_FOUND" (str "Resource not found: " proj-path) "Pass an existing project path.")
      node)))

(defn- resolve-collection-node [ctx params]
  (let [path (or (optional-string params :collection)
                 (optional-string params :collection_path)
                 (let [raw (optional-string params :path)]
                   (when (and raw (string/ends-with? raw ".collection"))
                     raw)))]
    (if-not path
      (fail! "MISSING_PARAM" "Missing collection path" "Pass `path` or `collection`.")
      (let [node (resolve-resource-node ctx path)]
        (if-not (g/node-instance? collection/CollectionNode node)
          (fail! "INVALID_PARAM" (str path " is not a collection") nil)
          node)))))

(defn- resolve-go-instance [collection-node go-id]
  (let [instance (get (g/node-value collection-node :go-inst-ids) go-id)]
    (if-not instance
      (fail! "NOT_FOUND" (str "Game object '" go-id "' was not found") "Call collection_get_hierarchy.")
      instance)))

(defn- resolve-go-node [instance]
  (or (g/node-feeding-into instance :source-resource)
      (fail! "NOT_FOUND" "Could not resolve the game object resource node" nil)))

(defn- resolve-component [go-node component-id]
  (let [node (get (g/node-value go-node :component-ids) component-id)]
    (if-not node
      (fail! "NOT_FOUND" (str "Component '" component-id "' was not found") nil)
      node)))

(defn- game-project-node [ctx]
  (project/get-resource-node (:project ctx) "/game.project"))

(defn- readiness [_ctx]
  (if (app-view/building?)
    "building"
    "ready"))

(defn- capture-created-node [f]
  (let [created (atom nil)]
    (f (fn [node-ids]
         (let [node-id (if (sequential? node-ids)
                         (first node-ids)
                         node-ids)]
           (reset! created node-id)
           [])))
    (or @created
        (fail! "HANDLER_ERROR" "The editor did not return the created node" nil))))

(defn- property-snapshot [node-id]
  (let [props (:properties (g/node-value node-id :_properties))]
    (into {}
          (map (fn [[k info]]
                 [(name k)
                  (cond-> {:value (json-value (:value info))}
                    (:read-only info) (assoc :read_only true))]))
          (or props {}))))

(defn- coerce-property-value [info raw workspace]
  (let [current (:value info)]
    (cond
      (and (vector? current) (sequential? raw) (not (map? raw)))
      (mapv #(if (number? %) (double %) %) raw)

      (and (vector? current) (map? raw))
      (case (count current)
        3 [(double (get raw :x (get raw "x" 0)))
           (double (get raw :y (get raw "y" 0)))
           (double (get raw :z (get raw "z" 0)))]
        4 [(double (get raw :x (get raw "x" 0)))
           (double (get raw :y (get raw "y" 0)))
           (double (get raw :z (get raw "z" 0)))
           (double (get raw :w (get raw "w" 1)))]
        raw)

      (and (string? raw) (resource/resource? current) workspace)
      (or (workspace/find-resource workspace raw) raw)

      :else
      raw)))

(defn- set-node-property! [ctx node-id prop-name raw]
  (g/with-auto-evaluation-context evaluation-context
    (let [prop-kw (if (keyword? prop-name) prop-name (keyword prop-name))
          info (get (:properties (g/node-value node-id :_properties evaluation-context)) prop-kw)]
      (if-not info
        (fail! "NOT_FOUND" (str "Property '" (name prop-kw) "' was not found") "Call gameobject_get_properties.")
        (let [value (coerce-property-value info raw (:workspace ctx))]
          (g/transact
            (concat
              (g/operation-label (str "Agent: set " (name prop-kw)))
              (g/set-property node-id prop-kw value)))
          {:id (g/node-value node-id :id)
           :property (name prop-kw)
           :value (json-value (g/node-value node-id prop-kw))})))))

(defn- note-batch-file! [^File file]
  (when-let [journal *batch-file-journal*]
    (let [path (.getAbsolutePath file)]
      (when-not (contains? (:seen @journal) path)
        (if (.isFile file)
          (swap! journal (fn [state]
                           (-> state
                               (update :seen conj path)
                               (update :restore conj [file (slurp file)]))))
          (swap! journal (fn [state]
                           (-> state
                               (update :seen conj path)
                               (update :created conj file)))))))))

(defn- write-new-resource! [ctx proj-path content]
  (let [workspace (:workspace ctx)
        file (project-file workspace proj-path)]
    (if (.exists file)
      (fail! "INVALID_PARAM" (str "File already exists: " proj-path) "Use filesystem_manage write_text to overwrite.")
      (do
        (note-batch-file! file)
        (fs/create-file! file content)
        (workspace/resource-sync! workspace)
        {:path proj-path}))))

(defn- save-resource-node! [node-id]
  (let [save-data (g/node-value node-id :save-data)]
    (if (g/error? save-data)
      (fail! "HANDLER_ERROR" "Resource has save errors" nil)
      (let [resource (:resource save-data)
            content (resource-node/save-data-content save-data)]
        (if-not (and (resource/resource? resource) (string? content))
          (fail! "HANDLER_ERROR" "Resource cannot be written as text" nil)
          (do
            (let [file (io/file (resource/abs-path resource))]
              (note-batch-file! file)
              (fs/create-file! file content))
            {:path (resource/proj-path resource)
             :saved true}))))))

(defn- template-content [workspace ext name]
  (let [resource-type (workspace/get-resource-type workspace ext)
        template (when resource-type
                   (workspace/template workspace resource-type))]
    (if-not (string? template)
      (fail! "INVALID_PARAM" (str "No editor template for type '" ext "'") nil)
      (workspace/replace-template-name template (or name ext)))))

(defn- code-node-text [node-id]
  (let [lines (or (g/node-value node-id :modified-lines)
                  (g/node-value node-id :lines))]
    (if (g/error? lines)
      (fail! "HANDLER_ERROR" "Could not read script lines" nil)
      (code.data/lines->string (or lines [""])))))

(defn- set-code-node-text! [node-id text]
  (g/transact
    (concat
      (g/operation-label "Agent: patch script")
      (g/set-property node-id :modified-lines (code.data/string->lines text))))
  {:path (resource/proj-path (g/node-value node-id :resource))
   :patched true})

(defn- patch-text [text old-text new-text]
  (if-not (and (string? old-text) (string? new-text))
    (fail! "MISSING_PARAM" "script_patch needs old_text and new_text" nil)
    (let [matches (loop [from 0
                         n 0]
                    (let [idx (.indexOf ^String text ^String old-text from)]
                      (if (neg? idx)
                        n
                        (recur (inc idx) (inc n)))))]
      (case matches
        0 (fail! "OLD_TEXT_NOT_FOUND" "old_text was not found" "Pass a unique substring.")
        1 (string/replace-first text old-text new-text)
        (fail! "MULTIPLE_MATCHES" (str "old_text matched " matches " times") "Pass a unique substring.")))))

(defn- vec3-param [params k]
  (let [v (get params k)]
    (cond
      (nil? v) nil
      (and (sequential? v) (= 3 (count v))) (mapv double v)
      (map? v) [(double (get v :x (get v "x" 0)))
                (double (get v :y (get v "y" 0)))
                (double (get v :z (get v "z" 0)))]
      :else (fail! "INVALID_PARAM" (str (name k) " must be [x y z]") nil))))

(defn- rotation-param [params]
  (let [raw (get params :rotation)]
    (cond
      (nil? raw)
      nil

      (number? raw)
      (let [half (* 0.5 (Math/toRadians (double raw)))]
        [0.0 0.0 (Math/sin half) (Math/cos half)])

      (and (sequential? raw) (= 1 (count raw)) (number? (first raw)))
      (let [half (* 0.5 (Math/toRadians (double (first raw))))]
        [0.0 0.0 (Math/sin half) (Math/cos half)])

      (and (sequential? raw) (= 4 (count raw)))
      (mapv double raw)

      (and (sequential? raw) (= 3 (count raw)))
      (let [half (* 0.5 (Math/toRadians (double (nth raw 2))))]
        [0.0 0.0 (Math/sin half) (Math/cos half)])

      :else
      (fail! "INVALID_PARAM" "rotation must be a quaternion [x, y, z, w] or z degrees" nil))))

(defn- scale-param [params]
  (let [raw (get params :scale)]
    (cond
      (nil? raw)
      nil

      (number? raw)
      [(double raw) (double raw) (double raw)]

      (and (sequential? raw) (pos? (count raw)))
      (let [nums (mapv double raw)]
        (subvec (into nums [1.0 1.0 1.0]) 0 3))

      :else
      (fail! "INVALID_PARAM" "scale must be a number or [x, y, z]" nil))))

(defn- cmd-editor-state [ctx _params]
  (g/with-auto-evaluation-context evaluation-context
    (let [project (:project ctx)
          app-view (:app-view ctx)
          workspace (:workspace ctx)
          game-project (game-project-node ctx)
          title (when game-project
                  (game-project/get-setting game-project ["project" "title"] evaluation-context))
          main (when game-project
                 (game-project/get-setting game-project ["bootstrap" "main_collection"] evaluation-context))
          active (when app-view
                   (g/node-value app-view :active-resource evaluation-context))
          selected (when app-view
                     (g/node-value app-view :selected-node-ids evaluation-context))]
      {:defold_version (or (system/defold-version) "1.13.1")
       :project_title title
       :project_root (when workspace
                       (.getAbsolutePath ^File (workspace/project-directory workspace)))
       :main_collection main
       :active_resource (when (resource/resource? active)
                          (resource/proj-path active))
       :selection (into [] (map long) (or selected []))
       :commands command-names
       :source "editor-agent"})))

(defn- cmd-collection-get-hierarchy [ctx params]
  (let [collection-node (resolve-collection-node ctx params)
        outline (g/node-value collection-node :node-outline)
        offset (or (get params :offset) 0)
        limit (or (get params :limit) 200)]
    (if (g/error? outline)
      (fail! "HANDLER_ERROR"
             "Collection outline has errors"
             "Fix the broken resources shown in the editor outline."
             {:issues (into []
                            (comp (keep :message)
                                  (distinct)
                                  (take 8))
                            (g/flatten-errors outline))})
      (let [children (or (:children outline) [])
            page (into [] (comp (drop offset) (take limit)) children)]
        {:path (resource/proj-path (g/node-value collection-node :resource))
         :id (outline-label outline (:localization ctx))
         :type "collection"
         :offset offset
         :limit limit
         :total (count children)
         :source "editor"
         :children (mapv #(outline-node % (:localization ctx)) page)}))))

(defn- cmd-gameobject-get-properties [ctx params]
  (let [collection-node (resolve-collection-node ctx params)
        go-id (require-string params :id)
        instance (resolve-go-instance collection-node go-id)
        go-node (resolve-go-node instance)
        component-id (optional-string params :component)
        target (if component-id
                 (resolve-component go-node component-id)
                 instance)
        ids (g/node-value go-node :component-ids)]
    {:id (g/node-value target :id)
     :type (node-kind target)
     :kind (instance-kind instance)
     :node_id target
     :resource (source-proj-path target)
     :source "editor"
     :components (into []
                       (map (fn [[id node]]
                              {:id id
                               :node_id node
                               :type (node-kind node)
                               :resource (source-proj-path node)}))
                       ids)
     :properties (property-snapshot target)}))

(defn- session-id [^File project-dir token]
  (str (.getName project-dir) "@" (subs (str token) 0 (min 8 (count (str token))))))

(defn- agent-session-file ^File [^File project-dir]
  (io/file project-dir ".internal" "agent" "session.json"))

(defn- user-session-dir ^File []
  (if-let [override (System/getenv "DEFOLD_AGENT_SESSIONS_DIR")]
    (io/file override)
    (if-let [local (System/getenv "LOCALAPPDATA")]
      (io/file local "Defold" "agent" "sessions")
      (let [home (System/getProperty "user.home")
            mac (io/file home "Library" "Application Support" "Defold")]
        (if (.isDirectory mac)
          (io/file mac "agent" "sessions")
          (io/file home ".Defold" "agent" "sessions"))))))

(defn write-session-files!
  "Write the per-project session file and a user-level registry entry."
  [^File project-dir port token]
  (let [session {:schema 1
                 :session_id (session-id project-dir token)
                 :project_path (.getAbsolutePath project-dir)
                 :editor_url (str "http://127.0.0.1:" port)
                 :editor_pid (.pid (ProcessHandle/current))
                 :defold_version (or (system/defold-version) "1.13.1")
                 :readiness "ready"
                 :source "editor"}
        text (json/write-str session)
        project-file (agent-session-file project-dir)
        registry-file (io/file (user-session-dir) (str (:session_id session) ".json"))]
    (io/make-parents project-file)
    (spit project-file text)
    (io/make-parents registry-file)
    (spit registry-file text)
    session))

(defn delete-session-files!
  "Remove session files if they still name this editor port."
  [^File project-dir port]
  (let [url (str "http://127.0.0.1:" port)
        project-file (agent-session-file project-dir)]
    (when (.isFile project-file)
      (try
        (let [data (json/read-str (slurp project-file) :key-fn keyword)]
          (when (= url (:editor_url data))
            (.delete project-file)
            (let [registry (io/file (user-session-dir) (str (:session_id data) ".json"))]
              (when (.isFile registry)
                (.delete registry)))))
        (catch Exception _
          nil)))))

(defn- cmd-session-activate [ctx params]
  (let [workspace (:workspace ctx)
        project-dir (workspace/project-directory workspace)
        session-file (agent-session-file project-dir)
        data (when (.isFile session-file)
               (json/read-str (slurp session-file) :key-fn keyword))
        wanted (or (optional-string params :id) (optional-string params :url))]
    (if (and wanted data (not (or (= wanted (:session_id data))
                                  (= wanted (:editor_url data))
                                  (= wanted "editor"))))
      (fail! "UNKNOWN_TARGET"
             (str "Session is not this editor: " wanted)
             "Call session_manage op=list.")
      {:activated true
       :id (or (:session_id data) "editor")
       :url (:editor_url data)
       :kind "editor"
       :sessions 1
       :source "editor"})))

(defn- cmd-collection-open [ctx params]
  (let [node (resolve-collection-node ctx params)
        resource (g/node-value node :resource)
        path (resource/proj-path resource)
        app-view (:app-view ctx)
        opened (atom false)]
    (when (and app-view (:prefs ctx) (:localization ctx))
      (try
        (ui/run-now
          (reset! opened (boolean (app-view/open-resource! app-view (:prefs ctx) (:localization ctx) (:project ctx) resource))))
        (catch Exception _)))
    {:path path
     :opened @opened}))

(defn- cmd-collection-save [ctx params]
  (save-resource-node! (resolve-collection-node ctx params)))

(defn- cmd-gameobject-create [ctx params]
  (let [collection-node (resolve-collection-node ctx params)
        workspace (:workspace ctx)
        project (:project ctx)
        desired-id (optional-string params :id)
        parent-id (optional-string params :parent)
        parent (if parent-id
                 (resolve-go-instance collection-node parent-id)
                 collection-node)
        prototype-path (optional-string params :path)
        collection-file (and prototype-path (string/ends-with? prototype-path ".collection"))
        position (vec3-param params :position)
        rotation (rotation-param params)
        scale (scale-param params)
        instance (capture-created-node
                   (fn [select-fn]
                     (cond
                       collection-file
                       (let [resource (workspace/find-resource workspace prototype-path)]
                         (if-not resource
                           (fail! "NOT_FOUND" (str "Collection file not found: " prototype-path) nil)
                           (collection/add-referenced-collection!
                             collection-node
                             resource
                             (or desired-id (resource-stem prototype-path))
                             {}
                             []
                             select-fn)))

                       prototype-path
                       (let [resource (workspace/find-resource workspace prototype-path)]
                         (if-not resource
                           (fail! "NOT_FOUND" (str "Game object file not found: " prototype-path) nil)
                           (collection/add-referenced-game-object! collection-node parent resource select-fn)))

                       :else
                       (collection/add-embedded-game-object! workspace project collection-node parent select-fn))))]
    (when (or desired-id position rotation scale)
      (g/transact
        (concat
          (g/operation-label "Agent: configure game object")
          (when desired-id
            (g/set-property instance :id desired-id))
          (when position
            (g/set-property instance :position position))
          (when rotation
            (g/set-property instance :rotation rotation))
          (when scale
            (g/set-property instance :scale scale)))))
    {:id (g/node-value instance :id)
     :node_id instance
     :type (if collection-file "collection_instance" "gameobject")
     :kind (cond
             collection-file "collection_instance"
             prototype-path "referenced"
             :else "embedded")
     :prototype prototype-path
     :undoable true}))

(defn- cmd-component-add [ctx params]
  (let [collection-node (resolve-collection-node ctx params)
        go-id (require-string params :id)
        instance (resolve-go-instance collection-node go-id)
        go-node (resolve-go-node instance)
        workspace (:workspace ctx)
        path (optional-string params :path)
        type-name (or (optional-string params :type) (optional-string params :component_type))
        component (capture-created-node
                    (fn [select-fn]
                      (if path
                        (let [resource (workspace/find-resource workspace path)]
                          (if-not resource
                            (fail! "NOT_FOUND" (str "Component resource not found: " path) nil)
                            (game-object/add-referenced-component! go-node resource select-fn)))
                        (let [resource-type (workspace/get-resource-type workspace (or type-name "script"))]
                          (if-not resource-type
                            (fail! "INVALID_PARAM" (str "Unknown component type: " type-name) "Use script, sprite, model, camera, collisionobject, label, sound, particlefx.")
                            (game-object/add-embedded-component! go-node resource-type select-fn))))))]
    {:id (g/node-value instance :id)
     :component (g/node-value component :id)
     :node_id component
     :type "component"
     :undoable true}))

(defn- cmd-script-create [ctx params]
  (let [path (sanitize-proj-path (require-string params :path))
        ext (resource/filename->type-ext path)
        name (or (optional-string params :name) (resource-stem path))
        content (or (optional-string params :content)
                    (template-content (:workspace ctx) ext name))]
    (write-new-resource! ctx path content)))

(defn- cmd-script-attach [ctx params]
  (cmd-component-add ctx (assoc params :type "script")))

(defn- cmd-script-patch [ctx params]
  (let [node (resolve-resource-node ctx (require-string params :path))
        text (code-node-text node)
        patched (patch-text text (get params :old_text) (get params :new_text))]
    (set-code-node-text! node patched)))

(defn- cmd-project-build [_ctx _params]
  (fail! "NOT_ALLOWED"
         "Build and check stay on POST /command/check and /command/build"
         "Use defold_agent.py check / loop. /command/build also launches the game."))

(defn- cmd-logs-read [ctx params]
  (let [console-view (:console-view ctx)
        limit (long (or (get params :limit) 200))
        offset (long (or (get params :offset) 0))]
    (if-not console-view
      {:lines []
       :total 0
       :offset offset
       :limit limit
       :source "console"
       :hint "GET /console"}
      (let [console-node (g/node-value console-view :resource-node)
            lines (or (g/node-value console-node :lines) [])
            total (count lines)
            end (max 0 (- total offset))
            start (max 0 (- end limit))
            sliced (cond
                     (>= start end)
                     []

                     (vector? lines)
                     (subvec lines start end)

                     :else
                     (into [] (comp (drop start) (take (- end start))) lines))]
        {:lines sliced
         :total total
         :offset offset
         :limit limit
         :truncated (pos? start)
         :source "console"}))))

(defn- cmd-diagnostics-read [ctx params]
  (let [logs (cmd-logs-read ctx (assoc params :limit (or (get params :limit) 80)))]
    {:source "editor"
     :logs logs
     :issues []
     :prints []
     :hint "CLI diagnostics_read merges last check + engine.log + snapshot issues."}))

(defn- cmd-editor-preview [ctx params]
  (let [path (sanitize-proj-path (or (optional-string params :path)
                                     (optional-string params :resource)))]
    (resolve-resource-node ctx path)
    {:path path
     :hint (str "GET /preview" path)}))

(defn- unknown-op [op known]
  (fail! "UNKNOWN_OP"
         (str "Unknown op: " op)
         "Pass one of the documented ops."
         {:suggestions known}))

(defn- settings-path [params]
  (let [raw (or (get params :path) (get params :key))]
    (cond
      (vector? raw) (mapv str raw)
      (string? raw) (string/split raw #"\.")
      :else (fail! "MISSING_PARAM" "settings need path or key" "Example: project.title"))))

(defn- cmd-collection-manage [ctx params]
  (let [op (require-string params :op)]
    (case op
      "create" (let [path (sanitize-proj-path (require-string params :path))
                     name (or (optional-string params :name) (resource-stem path))]
                 (write-new-resource! ctx path (template-content (:workspace ctx) "collection" name)))
      "add_instance" (cmd-gameobject-create ctx params)
      "remove_instance" (let [collection-node (resolve-collection-node ctx params)
                              instance (resolve-go-instance collection-node (require-string params :id))]
                          (g/transact
                            (concat
                              (g/operation-label "Agent: delete game object")
                              (g/delete-node instance)))
                          {:deleted true
                           :id (get params :id)
                           :undoable true})
      "get_roots" (cmd-collection-get-hierarchy ctx (assoc params :limit (or (get params :limit) 50)))
      (unknown-op op ["create" "add_instance" "remove_instance" "get_roots"]))))

(defn- cmd-gameobject-manage [ctx params]
  (let [op (require-string params :op)
        collection-node (resolve-collection-node ctx params)]
    (case op
      "delete" (cmd-collection-manage ctx (assoc params :op "remove_instance"))
      "rename" (set-node-property! ctx
                                   (resolve-go-instance collection-node (require-string params :id))
                                   :id
                                   (require-string params :name))
      "set_property" (set-node-property! ctx
                                         (resolve-go-instance collection-node (require-string params :id))
                                         (require-string params :property)
                                         (get params :value))
      "find" (let [needle (require-string params :id)
                   ids (g/node-value collection-node :go-inst-ids)]
               {:matches (into []
                               (keep (fn [[id node-id]]
                                       (when (string/includes? (str id) needle)
                                         {:id id
                                          :node_id node-id})))
                               ids)})
      (unknown-op op ["delete" "rename" "set_property" "find"]))))

(defn- cmd-component-manage [ctx params]
  (let [op (require-string params :op)
        collection-node (resolve-collection-node ctx params)
        instance (resolve-go-instance collection-node (require-string params :id))
        component (resolve-component (resolve-go-node instance) (require-string params :component))]
    (case op
      "remove" (do
                 (g/transact
                   (concat
                     (g/operation-label "Agent: delete component")
                     (g/delete-node component)))
                 {:deleted true
                  :id (get params :id)
                  :component (get params :component)
                  :undoable true})
      "set_property" (set-node-property! ctx component (require-string params :property) (get params :value))
      (unknown-op op ["remove" "set_property"]))))

(defn- cmd-script-manage [ctx params]
  (let [op (require-string params :op)]
    (case op
      "read" (let [path (or (optional-string params :path)
                            (when-let [component (optional-string params :component)]
                              (source-proj-path
                                (resolve-component
                                  (resolve-go-node
                                    (resolve-go-instance
                                      (resolve-collection-node ctx params)
                                      (require-string params :id)))
                                  component))))]
               (if-not path
                 (fail! "MISSING_PARAM" "script_manage read needs path or id+component" nil)
                 {:path path
                  :text (code-node-text (resolve-resource-node ctx path))}))
      "detach" (cmd-component-manage ctx (assoc params :op "remove"))
      (unknown-op op ["read" "detach"]))))

(defn- collect-project-files [^File dir skip-names acc]
  (let [children (.listFiles dir)]
    (if (nil? children)
      acc
      (reduce (fn [acc ^File f]
                (cond
                  (.isDirectory f)
                  (if (contains? skip-names (.getName f))
                    acc
                    (collect-project-files f skip-names acc))

                  (.isFile f)
                  (conj acc f)

                  :else
                  acc))
              acc
              children))))

(defn- filesystem-copy-or-move! [workspace params move]
  (let [src (sanitize-proj-path (or (optional-string params :path)
                                   (optional-string params :from)
                                   (fail! "MISSING_PARAM" "copy/move needs path" nil)))
        dest (sanitize-proj-path (or (optional-string params :dest)
                                    (optional-string params :to)
                                    (fail! "MISSING_PARAM" "copy/move needs dest" nil)))
        src-file (project-file workspace src)
        dest-file (project-file workspace dest)]
    (when (or (= dest "/game.project")
              (string/starts-with? dest "/.internal/")
              (string/starts-with? src "/.internal/")
              (and move (= src "/game.project")))
      (fail! "NOT_ALLOWED" (str "Refusing to " (if move "move" "copy") " " src " -> " dest) nil))
    (when-not (.isFile src-file)
      (fail! "NOT_FOUND" (str "File not found: " src) nil))
    (note-batch-file! dest-file)
    (when move
      (note-batch-file! src-file))
    (if move
      (fs/move-file! src-file dest-file)
      (fs/copy-file! src-file dest-file))
    (workspace/resource-sync! workspace)
    {:path dest
     :from src
     :copied (not move)
     :moved move
     :undoable false
     :source "editor"}))

(defn- cmd-filesystem-manage [ctx params]
  (let [op (require-string params :op)
        workspace (:workspace ctx)
        root (workspace/project-directory workspace)]
    (case op
      "read_text" (let [path (sanitize-proj-path (require-string params :path))
                        file (project-file workspace path)]
                    (if-not (.isFile file)
                      (fail! "NOT_FOUND" (str "File not found: " path) nil)
                      {:path path
                       :text (slurp file)}))
      "write_text" (let [path (sanitize-proj-path (require-string params :path))
                         text (get params :text)]
                     (if-not (string? text)
                       (fail! "MISSING_PARAM" "write_text needs text" nil)
                       (do
                         (let [file (project-file workspace path)]
                           (note-batch-file! file)
                           (fs/create-file! file text))
                         (workspace/resource-sync! workspace)
                         {:path path
                          :written true
                          :undoable false})))
      "search" (let [query (require-string params :query)
                     ext (optional-string params :ext)
                     offset (or (get params :offset) 0)
                     limit (or (get params :limit) 100)
                     files (collect-project-files root #{".internal" "build" ".git"} [])
                     matches (into []
                                   (comp
                                     (filter (fn [^File file]
                                               (or (nil? ext)
                                                   (string/ends-with? (.getName file) (str "." ext)))))
                                     (keep (fn [^File file]
                                             (when (< (.length file) 1000000)
                                               (let [text (slurp file)]
                                                 (when (string/includes? text query)
                                                   (resource/file->proj-path root file)))))))
                                   files)
                     page (into [] (comp (drop offset) (take limit)) matches)]
                 {:matches page
                  :total (count matches)
                  :offset offset
                  :limit limit
                  :truncated (< (+ offset (count page)) (count matches))})
      "list" (let [rel (or (optional-string params :path) "/")
                   ^File file (if (or (= rel "/") (string/blank? rel))
                                root
                                (project-file workspace (sanitize-proj-path rel)))
                   offset (max (long (or (get params :offset) 0)) 0)
                   limit (max (long (or (get params :limit) 100)) 1)]
               (when-not (.isDirectory file)
                 (fail! "NOT_FOUND" (str "Directory not found: " rel) nil))
               (let [listed (or (.listFiles file) (into-array File []))
                     names (vec
                             (sort
                               (into []
                                     (comp
                                       (map (fn [^File child] (.getName child)))
                                       (remove #(contains? #{".internal" "build" ".git" ".editor"} %)))
                                     listed)))
                     page (into [] (comp (drop offset) (take limit)) names)]
                 {:path (if (or (= rel "/") (string/blank? rel)) "/" (sanitize-proj-path rel))
                  :entries (mapv
                             (fn [name]
                               (let [child (io/file file name)]
                                 {:name name
                                  :path (resource/file->proj-path root child)
                                  :type (if (.isDirectory child) "directory" "file")}))
                             page)
                  :total (count names)
                  :offset offset
                  :limit limit
                  :truncated (< (+ offset (count page)) (count names))}))
      "exists" (let [path (sanitize-proj-path (require-string params :path))
                    file (project-file workspace path)]
                {:path path
                 :exists (.exists file)
                 :type (cond
                         (.isDirectory file) "directory"
                         (.isFile file) "file")
                 :source "editor"})
      "mkdir" (let [path (sanitize-proj-path (require-string params :path))
                    file (project-file workspace path)]
                (.mkdirs file)
                (workspace/resource-sync! workspace)
                {:path path
                 :created true
                 :undoable false
                 :source "editor"})
      "copy" (filesystem-copy-or-move! workspace params false)
      "move" (filesystem-copy-or-move! workspace params true)
      "delete" (let [path (sanitize-proj-path (require-string params :path))
                     file (project-file workspace path)]
                 (when (or (= path "/game.project")
                           (string/starts-with? path "/.internal/"))
                   (fail! "NOT_ALLOWED" (str "Refusing to delete " path) nil))
                 (when-not (.isFile file)
                   (fail! "NOT_FOUND" (str "File not found: " path) nil))
                 (note-batch-file! file)
                 (fs/delete-file! file)
                 (workspace/resource-sync! workspace)
                 {:path path
                  :deleted true
                  :undoable false
                  :source "editor"})
      (unknown-op op ["read_text" "write_text" "list" "exists" "mkdir" "copy" "move" "delete" "search"]))))

(defn- cmd-project-manage [ctx params]
  (let [op (require-string params :op)
        game-project (game-project-node ctx)]
    (if-not game-project
      (fail! "NOT_FOUND" "/game.project was not found" nil)
      (case op
        "settings_get" {:path (settings-path params)
                        :value (json-value (game-project/get-setting game-project (settings-path params)))}
        "settings_set" (do
                         (game-project/set-setting! game-project (settings-path params) (get params :value))
                         {:path (settings-path params)
                          :value (json-value (get params :value))
                          :undoable true})
        "stop" {:stopped false
                :hint "POST /command/debugger-stop"}
        "hot_reload" {:reloaded false
                      :hint "POST /command/hot-reload"}
        (unknown-op op ["settings_get" "settings_set" "stop" "hot_reload"])))))

(defn- cmd-editor-manage [ctx params]
  (let [op (require-string params :op)]
    (case op
      "state" (cmd-editor-state ctx params)
      "selection_get" (let [app-view (:app-view ctx)
                            ids (when app-view
                                  (g/node-value app-view :selected-node-ids))]
                        {:selection (into [] (map long) (or ids []))})
      "quit" {:quit false
              :hint "Quit from the editor UI. Agents should leave the editor running."}
      "mcp_config" (let [workspace (:workspace ctx)
                         format (or (optional-string params :format) "cursor")]
                     (when-not (contains? #{"cursor" "codex"} format)
                       (fail! "INVALID_PARAM" "format must be cursor or codex" nil))
                     {:format format
                      :text (agent-config/mcp-config-text (workspace/project-directory workspace) format)
                      :http false
                      :source "editor"})
      (unknown-op op ["state" "selection_get" "quit" "mcp_config"]))))

(defn- cmd-session-manage [ctx params]
  (let [op (require-string params :op)]
    (case op
      "list" (let [workspace (:workspace ctx)
                   project-dir (workspace/project-directory workspace)
                   session-file (agent-session-file project-dir)
                   data (when (.isFile session-file)
                          (json/read-str (slurp session-file) :key-fn keyword))]
               {:sessions [{:id (or (:session_id data) "editor")
                            :kind "editor"
                            :url (:editor_url data)
                            :project (:project_path data)
                            :alive true
                            :current true
                            :source "editor"}]
                :source "editor"})
      (unknown-op op ["list"]))))

(defn- cmd-api-manage [_ctx params]
  (let [op (require-string params :op)]
    (case op
      "get" (let [q (or (optional-string params :q) (optional-string params :query) "")
                  query (or (optional-string params :raw_query)
                            (str "environment=" (or (optional-string params :environment) "runtime")
                                 "&language=" (or (optional-string params :language) "Lua")
                                 "&q=" q))]
              {:query query
               :results (into [] (take 40) (map json-value (doc/search-ref query)))})
      (unknown-op op ["get"]))))

(defn- slurp-proj-file [ctx path]
  (let [file (project-file (:workspace ctx) path)]
    (if-not (.isFile file)
      (fail! "NOT_FOUND" (str "File not found: " path) nil)
      (slurp file))))

(defn- rewrite-proj-file! [ctx path text]
  (let [workspace (:workspace ctx)
        file (project-file workspace path)]
    (note-batch-file! file)
    (fs/create-file! file text)
    (workspace/resource-sync! workspace)
    {:path path
     :undoable false
     :source "editor"}))

(defn- list-ext-paths [ctx ext]
  (let [workspace (:workspace ctx)
        root (workspace/project-directory workspace)
        files (collect-project-files root #{".internal" "build" ".git"} [])]
    (into []
          (comp
            (filter (fn [^File file]
                      (string/ends-with? (.getName file) (str "." ext))))
            (map (fn [^File file]
                   (resource/file->proj-path root file))))
          files)))

(defn- cmd-file-domain-manage [ctx params ext]
  (let [op (require-string params :op)]
    (case op
      "create" (let [path (sanitize-proj-path (require-string params :path))
                     name (or (optional-string params :name) (resource-stem path))
                     content (or (optional-string params :content)
                                 (if (= "input_binding" ext)
                                   ""
                                   (template-content (:workspace ctx) ext name)))]
                 (assoc (write-new-resource! ctx path content) :source "editor" :undoable false))
      "list" {:paths (list-ext-paths ctx ext)
              :source "editor"}
      "get" (let [path (sanitize-proj-path (require-string params :path))
                  text (slurp-proj-file ctx path)]
              {:path path
               :ids (into [] (map second) (re-seq #"id:\s*\"([^\"]+)\"" text))
               :source "editor"})
      "set_property" (let [path (sanitize-proj-path (require-string params :path))
                           key (require-string params :property)
                           value (str (get params :value))
                           text (slurp-proj-file ctx path)]
                       (rewrite-proj-file! ctx path (str text "\n" key ": \"" value "\"\n")))
      "remove" (let [path (sanitize-proj-path (require-string params :path))
                     needle (or (optional-string params :id)
                                (optional-string params :action)
                                (optional-string params :image)
                                (optional-string params :name))]
                 (if-not needle
                   (fail! "MISSING_PARAM" "remove needs id, action, image, or name" nil)
                   (let [text (slurp-proj-file ctx path)]
                     (if-not (string/includes? text needle)
                       (fail! "NOT_FOUND" (str "Not found: " needle) nil)
                       (rewrite-proj-file! ctx path (string/replace-first text needle ""))))))
      (let [path (sanitize-proj-path (require-string params :path))
            extra (or (optional-string params :block)
                      (str "\n# agent " op "\n"))]
        (rewrite-proj-file! ctx path (str (slurp-proj-file ctx path) extra))))))

(defn- cmd-atlas-manage [ctx params]
  (cmd-file-domain-manage ctx params "atlas"))

(defn- cmd-tilemap-manage [ctx params]
  (cmd-file-domain-manage ctx params "tilemap"))

(defn- cmd-gui-manage [ctx params]
  (cmd-file-domain-manage ctx params "gui"))

(defn- cmd-input-binding-manage [ctx params]
  (cmd-file-domain-manage ctx params "input_binding"))

(defn- cmd-particlefx-manage [ctx params]
  (cmd-file-domain-manage ctx params "particlefx"))

(defn- cmd-material-manage [ctx params]
  (cmd-file-domain-manage ctx params "material"))

(defn- cmd-render-manage [ctx params]
  (cmd-file-domain-manage ctx params "render"))

(defn- cmd-tilesource-manage [ctx params]
  (cmd-file-domain-manage ctx params "tilesource"))

(defn- cmd-font-manage [ctx params]
  (cmd-file-domain-manage ctx params "font"))

(defn- cmd-sound-manage [ctx params]
  (cmd-file-domain-manage ctx params "sound"))

(defn- cmd-gamepads-manage [ctx params]
  (cmd-file-domain-manage ctx params "gamepads"))

(defn- cmd-display-profiles-manage [ctx params]
  (cmd-file-domain-manage ctx params "display_profiles"))

(defn- cmd-model-manage [ctx params]
  (cmd-file-domain-manage ctx params "model"))

(defn- cmd-factory-manage [ctx params]
  (cmd-file-domain-manage ctx params "factory"))

(defn- cmd-collectionfactory-manage [ctx params]
  (cmd-file-domain-manage ctx params "collectionfactory"))

(defn- cmd-collectionproxy-manage [ctx params]
  (cmd-file-domain-manage ctx params "collectionproxy"))

(defn- cmd-collisionobject-manage [ctx params]
  (cmd-file-domain-manage ctx params "collisionobject"))

(defn- cmd-cubemap-manage [ctx params]
  (cmd-file-domain-manage ctx params "cubemap"))

(defn- cmd-mesh-manage [ctx params]
  (cmd-file-domain-manage ctx params "mesh"))

(defn- cmd-texture-profiles-manage [ctx params]
  (cmd-file-domain-manage ctx params "texture_profiles"))

(defn- cmd-compute-manage [ctx params]
  (cmd-file-domain-manage ctx params "compute"))

(defn- cmd-appmanifest-manage [ctx params]
  (cmd-file-domain-manage ctx params "appmanifest"))

(defn- cmd-camera-manage [ctx params]
  (let [op (require-string params :op)
        component (or (optional-string params :component)
                      (optional-string params :id)
                      "camera")]
    (case op
      "add" (cmd-component-add ctx (assoc params :type "camera"))
      "get" (cmd-gameobject-get-properties ctx (assoc params :component component))
      "remove" (cmd-component-manage ctx (assoc params :op "remove" :component component))
      "set_property" (cmd-component-manage ctx (assoc params :op "set_property" :component component))
      (unknown-op op ["add" "get" "remove" "set_property"]))))

(declare handle)

(defn- rollback-batch-files! [workspace journal]
  (doseq [[^File file text] (rseq (vec (:restore journal)))]
    (fs/create-file! file text))
  (doseq [^File file (:created journal)]
    (when (.isFile file)
      (fs/delete-file! file)))
  (when (and workspace
             (or (pos? (count (:created journal)))
                 (pos? (count (:restore journal)))))
    (workspace/resource-sync! workspace)))

(defn- cmd-batch-execute [ctx params]
  (when g/*current-operation-sequence*
    (fail! "NOT_ALLOWED" "Nested batch_execute is not allowed" nil))
  (let [commands (get params :commands)]
    (if-not (sequential? commands)
      (fail! "MISSING_PARAM" "batch_execute needs commands[]" nil)
      (let [project-graph (g/node-id->graph-id (:project ctx))
            op-seq (gensym "agent-batch")
            journal (atom {:seen #{}
                           :created []
                           :restore []})]
        (binding [g/*current-operation-sequence* op-seq
                  *batch-file-journal* journal]
          (let [results (reduce
                          (fn [acc item]
                            (if (not= :ok (:status acc))
                              acc
                              (let [command (or (get item :command) (get item "command"))
                                    item-params (or (get item :params) (get item "params") {})
                                    result (handle ctx command item-params)]
                                (if (= "ok" (:status result))
                                  (update acc :data update :results conj (:data result))
                                  (assoc acc
                                    :status :error
                                    :error (assoc (:error result)
                                             :completed (get-in acc [:data :results])))))))
                          {:status :ok
                           :data {:results []
                                  :atomic true
                                  :undoable_separately false
                                  :undoable true
                                  :source "editor"}}
                          commands)
                snapshot @journal
                can-undo (and (g/has-undo? project-graph)
                              (= op-seq (g/prev-sequence-label project-graph)))
                rolled-back (and (not= :ok (:status results))
                                 (or can-undo
                                     (pos? (count (:created snapshot)))
                                     (pos? (count (:restore snapshot)))))]
            (when (not= :ok (:status results))
              (when can-undo
                (g/undo! project-graph))
              (rollback-batch-files! (:workspace ctx) snapshot))
            (if (= :ok (:status results))
              (:data results)
              (fail! (get-in results [:error :code] "HANDLER_ERROR")
                     (get-in results [:error :message] "batch_execute failed")
                     (get-in results [:error :hint])
                     (assoc (dissoc (:error results) :code :message :hint)
                       :atomic true
                       :rolled_back rolled-back
                       :undoable_separately false)))))))))

(def ^:private command-fns
  {"api_manage" cmd-api-manage
   "appmanifest_manage" cmd-appmanifest-manage
   "atlas_manage" cmd-atlas-manage
   "batch_execute" cmd-batch-execute
   "camera_manage" cmd-camera-manage
   "collection_get_hierarchy" cmd-collection-get-hierarchy
   "collection_manage" cmd-collection-manage
   "collection_open" cmd-collection-open
   "collection_save" cmd-collection-save
   "collectionfactory_manage" cmd-collectionfactory-manage
   "collectionproxy_manage" cmd-collectionproxy-manage
   "collisionobject_manage" cmd-collisionobject-manage
   "component_add" cmd-component-add
   "component_manage" cmd-component-manage
   "compute_manage" cmd-compute-manage
   "cubemap_manage" cmd-cubemap-manage
   "diagnostics_read" cmd-diagnostics-read
   "display_profiles_manage" cmd-display-profiles-manage
   "editor_manage" cmd-editor-manage
   "editor_preview" cmd-editor-preview
   "editor_state" cmd-editor-state
   "factory_manage" cmd-factory-manage
   "filesystem_manage" cmd-filesystem-manage
   "font_manage" cmd-font-manage
   "gamepads_manage" cmd-gamepads-manage
   "gameobject_create" cmd-gameobject-create
   "gameobject_get_properties" cmd-gameobject-get-properties
   "gameobject_manage" cmd-gameobject-manage
   "gui_manage" cmd-gui-manage
   "input_binding_manage" cmd-input-binding-manage
   "logs_read" cmd-logs-read
   "material_manage" cmd-material-manage
   "mesh_manage" cmd-mesh-manage
   "model_manage" cmd-model-manage
   "particlefx_manage" cmd-particlefx-manage
   "project_build" cmd-project-build
   "project_manage" cmd-project-manage
   "render_manage" cmd-render-manage
   "script_attach" cmd-script-attach
   "script_create" cmd-script-create
   "script_manage" cmd-script-manage
   "script_patch" cmd-script-patch
   "session_activate" cmd-session-activate
   "session_manage" cmd-session-manage
   "sound_manage" cmd-sound-manage
   "texture_profiles_manage" cmd-texture-profiles-manage
   "tilemap_manage" cmd-tilemap-manage
   "tilesource_manage" cmd-tilesource-manage})

(defn- envelope [ctx request-id status-kw payload]
  (cond-> {:status (name status-kw)
           :readiness (readiness ctx)}
    request-id (assoc :request_id request-id)
    (= :ok status-kw) (assoc :data payload)
    (= :error status-kw) (assoc :error payload)))

(defn handle
  "Dispatch one agent command. Public boundary.

  Returns {:status \"ok\"|\"error\" :readiness string :data map :error map}."
  [ctx command params]
  (let [request-id (or (get params :request_id) (get params :request-id))
        raw (if (keyword? command)
              (name command)
              (str command))
        params (alias-params raw (dissoc (or params {}) :request_id :request-id))
        command-n (command-name command)]
    (try
      (when (and (authoring-write? command-n params)
                 (= "building" (readiness ctx)))
        (fail! "EDITOR_NOT_READY"
               "Authoring writes are blocked while building."
               "Wait for the editor build to finish."
               {:sub_code "building"}))
      (if-let [f (get command-fns command-n)]
        (envelope ctx request-id :ok (f ctx params))
        (envelope ctx request-id :error {:code "UNKNOWN_COMMAND"
                                         :message (str "Unknown command: " command-n)
                                         :data {:suggestions command-names}}))
      (catch Exception e
        (if-let [err (:agent/error (ex-data e))]
          (envelope ctx request-id :error err)
          (envelope ctx request-id :error {:code "HANDLER_ERROR"
                                           :message (or (ex-message e) (.getName (class e)))}))))))

(defn- parse-command-body [request]
  (try
    (with-open [r (io/reader (:body request))]
      (json/read r :key-fn keyword))
    (catch Exception e
      (throw (http-server/error (http-server/response 400 (str "Invalid JSON: " (.getMessage e) \newline)))))))

(defn- handle-http [ctx request]
  (web-server/require-authorized! request (:token ctx))
  (let [body (parse-command-body request)
        command (or (:command body) (:op body))
        params (or (:params body) (dissoc body :command :op))]
    (if-not command
      (http-server/json-response
        (envelope ctx (:request_id body) :error {:code "MISSING_PARAM"
                                                 :message "Missing command"
                                                 :hint "POST {\"command\":\"editor_state\",\"params\":{}}"})
        200)
      (http-server/json-response
        @(fx/on-fx-thread
           (handle ctx command (or params {})))))))

(defn- handle-state [ctx request]
  (web-server/require-authorized! request (:token ctx))
  (http-server/json-response
    @(fx/on-fx-thread
       (handle ctx "editor_state" {}))))

(defn routes
  "HTTP routes for the first-party agent command surface.

  ctx keys: :project :workspace :app-view :prefs :localization :console-view :token"
  [ctx]
  {"/agent/state"
   {"GET" (with-meta
            (bound-fn [request]
              (handle-state ctx request))
            {:openapi {:summary "Agent editor_state (Bearer)"
                       :security [{"token" []}]
                       :responses {"200" {:description "Envelope with editor_state data"}
                                   "401" {:description "Missing or invalid bearer token"}}}})}
   "/agent/command"
   {"POST" (with-meta
             (bound-fn [request]
               (handle-http ctx request))
             {:openapi {:summary "First-party agent command dispatcher (Bearer)"
                        :description (str "Path B capabilities implemented in editor source, not a game plugin.\n"
                                          "Business errors return HTTP 200 with status=error.\n"
                                          "Commands:\n"
                                          (coll/join-to-string
                                            "\n"
                                            (mapv #(str "- `" % "`") command-names)))
                        :security [{"token" []}]
                        :requestBody {:required true
                                      :content {"application/json"
                                                {:schema {:type "object"
                                                          :required ["command"]
                                                          :properties {:command {:type "string"
                                                                                 :enum command-names}
                                                                       :params {:type "object"}
                                                                       :request_id {:type "string"}}}}}}
                        :responses {"200" {:description "Envelope {status, readiness, data|error}"}
                                    "400" {:description "Invalid JSON"}
                                    "401" {:description "Missing or invalid bearer token"}}}})}})
