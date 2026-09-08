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

/*# Engine runtime documentation
 *
 * @document
 * @name Engine runtime
 * @namespace engine
 * @language C++
 */

/*# override the save directory
 * Overrides the save directory when using `sys.get_save_file()`
 *
 * @macro
 * @name DM_SAVE_HOME
 */

/*# set the engine service port
 * Overrides the engine service port, when creating the internal http server (e.g web profiler)
 *
 * Valid values are integers between [0, 65535] and the string "dynamic" (which translates to 0).
 * Default value is 8001.
 *
 * @macro
 * @name DM_SERVICE_PORT
 */

/*# enables quit on escape key
 * Enables quitting the app directly by pressing the `ESCAPE` key.
 * Set to "1" to enable this feature.
 *
 * @macro
 * @name DM_QUIT_ON_ESC
 */

/*# sets the logging port
 * Enables receiving the log on a designated port (e.g. using telnet)
 * Valid values are integers between [0, 65535]
 *
 * @macro
 * @name DM_LOG_PORT
 */

/*# disables OpenGL error checking
 * Disables OpenGL error checking.
 * This is especially beneficial for running debug builds in the Chrome browser,
 * in which the calls to `glGetError()` is notoriously slow.
 *
 * Default value is "true" for debug builds, and "false" for release builds.
 *
 * @macro
 * @name --verify-graphics-calls=
 * @examples
 * ```bash
 * $ ./dmengine --verify-graphics-calls=false
 * ```
 */

/*# launch with a specific project
 * Launch the engine with a specific project file
 *
 * If no project is specified, it will default to "./game.projectc", "build/default/game.projectc"
 *
 * @note It has to be the first argument
 *
 * @macro
 * @name launch_project
 * @examples
 * ```bash
 * $ ./dmengine some/folder/game.projectc
 * ```
 */

/*# override game property
 * Override game properties with the format `--config=section.key=value`
 *
 * @macro
 * @name --config=
 * @examples
 * ```bash
 * $ ./dmengine --config=project.mode=TEST --config=project.server=http://testserver.com
 * ```
 */

/*# quit after N frames
 * Exit after the given number of presented frames. Intended for tools and agents.
 * Combine with `--screenshot=` to capture the last frame (Godot `--write-movie` + `--quit`).
 *
 * @macro
 * @name --quit-after-frames=
 * @examples
 * ```bash
 * $ ./dmengine --quit-after-frames=30 --screenshot=shot.png
 * ```
 */

/*# write a PNG screenshot
 * Capture the framebuffer to a PNG and quit. If `--quit-after-frames` is omitted, the engine
 * captures the first frame and exits.
 *
 * @macro
 * @name --screenshot=
 * @examples
 * ```bash
 * $ ./dmengine --screenshot=shot.png
 * $ ./dmengine --quit-after-frames=60 --screenshot=.internal/agent/frame.png
 * ```
 */

/*# write a runtime scene-graph dump
 * Write the live scene graph as JSON when the engine quits (same payload as
 * the in-process scene graph walk). Independent of `--screenshot=`. Combine with
 * `--quit-after-frames=` so an agent can query the tree after a batch run.
 *
 * @macro
 * @name --runtime-dump=
 * @examples
 * ```bash
 * $ ./dmengine --quit-after-frames=30 --runtime-dump=.internal/agent/snapshots/raw.json
 * ```
 */

/*# file handshake for live agent dumps
 * Watch `DIR/dump.request`, `DIR/screenshot.request`, `DIR/input.request`,
 * `DIR/eval.request`, and `DIR/debug.request` each frame. Dump/screenshot
 * write a scene graph or PNG. Input injects HID. Eval runs a short Lua
 * chunk. Debug pause/step/breakpoints never sit at an interactive prompt.
 * A breakpoint hit captures the Lua stack and locals for later `stack` / `locals`.
 * Replies are `*.ready`. No HTTP. Used by `defold_agent.py` live observe
 * and R3 intervention tools.
 *
 * @macro
 * @name --agent-control=
 * @examples
 * ```bash
 * $ ./dmengine --agent-control=.internal/agent/control
 * ```
 */

/*# draw physics collision overlays
 * Enable physics debug drawing (same as `physics.debug=1`). Pair with `--screenshot=` so an
 * agent can see colliders.
 *
 * @macro
 * @name --debug-collisions
 */
