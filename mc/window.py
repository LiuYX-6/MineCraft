import math

import pyglet
from pyglet.gl import (GL_LINES, GL_CULL_FACE, GL_TEXTURE_2D,
                        GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
                        GL_NEAREST, GL_DEPTH_TEST,
                        glClearColor, glEnable, glDisable, glViewport,
                        glLineWidth, glTexParameteri)
from pyglet.math import Mat4, Vec3

from mc.config import TICKS_PER_SEC
from mc.shaders import create_line_shader
from mc.utils import sectorize, visible_block_edges
from mc.blocks import STONE
from mc.world import World
from mc.player import Player
from mc.controllers.keyboard_mouse import KeyboardMouseController
from mc.terrain import FlatWorldGenerator


class GameWindow(pyglet.window.Window):
    """Main game window — thin coordination layer.

    Composes ``Player`` + ``World`` + ``PlayerController``.  Responsible
    only for OpenGL rendering, the game-loop schedule, and coordinating
    the components.  All player state and input handling have been moved
    to the respective modules.
    """

    def __init__(self, *args,
                 controller=None,
                 world=None,
                 player=None,
                 gesture_controller=None,
                 **kwargs):
        super(GameWindow, self).__init__(*args, **kwargs)

        # ---- Component composition ----
        # Primary controller: keyboard + mouse (always active).
        self.controller = controller or KeyboardMouseController(self)
        # Optional gesture controller: camera-based hand-gesture input.
        self.gesture_controller = gesture_controller
        self.world = world or World(FlatWorldGenerator())
        self.player = player or Player()

        # ---- Per-frame state ----
        # Which sector the player is currently in (for sector-change detection).
        self.sector = None
        # Manual FPS counter (pyglet 2.x removed clock.get_fps).
        self._fps = 0
        self._frames = 0
        self._fps_time = 0.0

        # ---- Line shader (for reticle and wireframe highlight) ----
        self.line_shader = create_line_shader()

        # ---- HUD elements ----
        # FPS / position label (top-left).
        self.label = pyglet.text.Label('', font_name='Arial', font_size=18,
            x=10, y=self.height - 10, anchor_x='left', anchor_y='top',
            color=(0, 0, 0, 255))
        # Gesture finger-code label (top-right).
        self.gesture_label = pyglet.text.Label(
            '', font_name='Arial', font_size=18,
            x=self.width - 10, y=self.height - 10,
            anchor_x='right', anchor_y='top',
            color=(0, 0, 0, 255))
        # Gesture action-name label (below finger code).
        self.gesture_action_label = pyglet.text.Label(
            '', font_name='Arial', font_size=16,
            x=self.width - 10, y=self.height - 34,
            anchor_x='right', anchor_y='top',
            color=(0, 160, 0, 255))
        # Crosshair reticle (created on first draw or resize).
        self.reticle = None

        # Schedule the main game loop.
        pyglet.clock.schedule_interval(self.update, 1.0 / TICKS_PER_SEC)

    # ------------------------------------------------------------------
    # Game loop
    # ------------------------------------------------------------------

    def update(self, dt):
        """Main game loop — dispatches to all components once per tick.

        1. Process deferred world-render queue.
        2. Detect sector changes and show/hide sectors.
        3. Let the controller run its per-frame update.
        4. Poll and handle discrete actions (jump, place block, …).
        5. Advance player physics (movement, gravity, collision).
        6. Apply camera-rotation delta from the controller.
        """
        # 0. Manual FPS counting.
        self._frames += 1
        self._fps_time += dt
        if self._fps_time >= 0.5:
            self._fps = self._frames / self._fps_time
            self._frames = 0
            self._fps_time = 0.0

        # 1. Process deferred render queue.
        self.world.process_queue()

        # 2. Sector change detection.
        sector = sectorize(self.player.position)
        if sector != self.sector:
            self.world.change_sectors(self.sector, sector)
            if self.sector is None:
                self.world.process_entire_queue()
            self.sector = sector

        # 3. Per-frame controller update.
        self.controller.update(dt)
        if self.gesture_controller is not None:
            self.gesture_controller.update(dt)

        # 4. Discrete actions (consume on read, merge from both sources).
        actions = self.controller.poll_actions()
        if self.gesture_controller is not None:
            actions |= self.gesture_controller.poll_actions()
        self._handle_actions(actions)

        # 5. Player physics.
        self.player.update_physics(dt, self.world.world,
                                    self.controller.get_strafe(),
                                    flying=self.player.flying)

        # 6. Camera rotation.
        dy, dp = self.controller.get_rotation_delta()
        if dy or dp:
            x, y = self.player.rotation
            x, y = x + dy, y + dp
            y = max(-90, min(90, y))  # clamp pitch
            self.player.rotation = (x, y)

    def _handle_actions(self, actions):
        """Process the set of discrete actions returned by the controller."""
        for action in actions:
            if action == 'jump':
                self.player.jump()
            elif action == 'fly_toggle':
                self.player.toggle_flying()
            elif action == 'escape':
                if self.controller.exclusive:
                    self.controller.deactivate()   # 1st press: release mouse
                else:
                    self.close()                    # 2nd press: quit game
            elif action == 'place_block':
                vector = self.player.get_sight_vector()
                block, previous = self.world.hit_test(
                    self.player.position, vector, max_distance=4)
                if previous:
                    self.world.add_block(previous,
                                         self.player.selected_block)
            elif action == 'break_block':
                vector = self.player.get_sight_vector()
                block, _ = self.world.hit_test(
                    self.player.position, vector, max_distance=4)
                if block:
                    texture = self.world.world[block]
                    if texture != STONE:
                        self.world.remove_block(block)
            elif action == 'pick_block':
                vector = self.player.get_sight_vector()
                block, _ = self.world.hit_test(
                    self.player.position, vector, max_distance=4)
                if block and block in self.world.world:
                    texture = self.world.world[block]
                    self.player.inventory[
                        self.player.selected_block_index] = texture
            elif action.startswith('slot_'):
                index = int(action.split('_')[1])
                self.player.switch_slot(index)

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def on_resize(self, width, height):
        """Re-position the HUD labels and recreate the reticle."""
        self.label.y = height - 10
        self.gesture_label.x = width - 10
        self.gesture_label.y = height - 10
        self.gesture_action_label.x = width - 10
        self.gesture_action_label.y = height - 34
        self._create_reticle()

    # ------------------------------------------------------------------
    # Rendering — 2D / 3D setup
    # ------------------------------------------------------------------

    def set_2d(self):
        """Configure OpenGL for 2D (HUD) drawing."""
        width, height = self.get_size()
        glDisable(GL_DEPTH_TEST)
        viewport = self.get_framebuffer_size()
        glViewport(0, 0, max(1, viewport[0]), max(1, viewport[1]))
        # Orthographic projection matching window pixel coordinates
        self.projection = Mat4.orthogonal_projection(
            0, max(1, width), 0, max(1, height), -1, 1)
        self.view = Mat4()

    def set_3d(self):
        """Configure OpenGL for 3D (world) drawing.

        Reads camera position and rotation from ``self.player``.
        """
        width, height = self.get_size()
        glEnable(GL_DEPTH_TEST)
        viewport = self.get_framebuffer_size()
        glViewport(0, 0, max(1, viewport[0]), max(1, viewport[1]))
        # Perspective projection
        self.projection = Mat4.perspective_projection(
            max(1, width) / max(1, height), 0.1, 60.0, fov=65.0)
        # View matrix: rotate (yaw around Y, pitch around camera right) then
        # translate.  angles are in degrees; Mat4.from_rotation expects radians.
        x, y = self.player.rotation
        x_rad, y_rad = math.radians(x), math.radians(y)
        # Camera right axis in world space (depends on yaw)
        right = (math.cos(x_rad), 0.0, math.sin(x_rad))
        self.view = (Mat4.from_rotation(x_rad, (0, 1, 0)) @
                     Mat4.from_rotation(-y_rad, right))
        px, py, pz = self.player.position
        self.view = self.view @ Mat4.from_translation(Vec3(-px, -py, -pz))

    # ------------------------------------------------------------------
    # Rendering — draw callbacks
    # ------------------------------------------------------------------

    def on_draw(self):
        """Called by pyglet to render the full frame."""
        self.clear()
        self.set_3d()

        # Update block shader uniforms
        mvp = self.projection @ self.view
        self.world.shader['mvp'] = mvp
        self.world.shader['view'] = self.view

        self.world.batch.draw()
        self.draw_focused_block()
        self.set_2d()
        self.draw_label()
        self.draw_reticle()

    def draw_focused_block(self):
        """Draw visible-face edges around the block under the crosshairs.

        Only faces that are front-facing AND not covered by a neighbouring
        block are drawn.  Outline is suppressed when the block is too far
        from the player.
        """
        vector = self.player.get_sight_vector()
        block = self.world.hit_test(self.player.position, vector)[0]
        if block:
            x, y, z = block
            wireframe_data = visible_block_edges(
                x, y, z, 0.51, self.world.world,
                self.player.position, max_distance=4)
            if not wireframe_data:
                return
            mvp = self.projection @ self.view
            self.line_shader.use()
            self.line_shader['mvp'] = mvp
            self.line_shader['color'] = (0.0, 0.0, 0.0, 1.0)
            count = len(wireframe_data) // 3
            vl = self.line_shader.vertex_list(
                count, GL_LINES,
                position=('f', wireframe_data)
            )
            vl.draw(GL_LINES)
            vl.delete()

    def draw_label(self):
        """Draw the HUD: FPS / position (top-left) + gesture info (top-right)."""
        # --- Top-left: FPS + position + block count ---
        x, y, z = self.player.position
        self.label.text = '%02d (%.2f, %.2f, %.2f) %d / %d' % (
            self._fps, x, y, z,
            len(self.world._shown), len(self.world.world))
        self.label.draw()

        # --- Top-right: gesture finger code + action (when active) ---
        if self.gesture_controller is not None:
            code = self.gesture_controller.finger_code
            action = self.gesture_controller.action_display
            self.gesture_label.text = f'手指: {code}'
            self.gesture_label.draw()
            if action:
                self.gesture_action_label.text = f'操作: {action}'
                self.gesture_action_label.draw()

    def _create_reticle(self):
        """Create or recreate the crosshair vertex list."""
        if self.reticle:
            self.reticle.delete()
        x, y = self.width // 2, self.height // 2
        n = 10
        self.reticle = self.line_shader.vertex_list(
            4, GL_LINES,
            position=('f', (float(x - n), float(y), 0.0,
                            float(x + n), float(y), 0.0,
                            float(x), float(y - n), 0.0,
                            float(x), float(y + n), 0.0))
        )

    def draw_reticle(self):
        """Draw the crosshair reticle at the centre of the screen."""
        if self.reticle is None:
            self._create_reticle()
        self.line_shader.use()
        self.line_shader['mvp'] = self.projection @ self.view
        self.line_shader['color'] = (1.0, 1.0, 1.0, 1.0)
        glLineWidth(2.0)
        self.reticle.draw(GL_LINES)
        glLineWidth(1.0)


# ------------------------------------------------------------------
# Module-level OpenGL helpers
# ------------------------------------------------------------------

def setup_fog(shader):
    """Set fog parameters on the block shader (replaces fixed-pipeline fog)."""
    shader['fog_color'] = (0.5, 0.69, 1.0, 1.0)
    shader['fog_start'] = 20.0
    shader['fog_end'] = 60.0


def setup(shader):
    """Basic OpenGL configuration (call after window creation)."""
    glClearColor(0.5, 0.69, 1.0, 1)
    glEnable(GL_CULL_FACE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    setup_fog(shader)


def run():
    """Convenience entry point: create a window and start the game loop.

    Command-line flags
    ------------------
    ``--gesture``
        Enable camera-based hand-gesture input **in addition** to the
        default keyboard + mouse controller.  Both input sources are
        active simultaneously.
    """
    import sys

    gesture_controller = None
    if '--gesture' in sys.argv:
        from mc.controllers.gesture import GestureController
        gesture_controller = GestureController()

    window = GameWindow(width=800, height=600, caption='Minecraft',
                        resizable=True,
                        gesture_controller=gesture_controller)
    window.controller.activate()
    if gesture_controller is not None:
        gesture_controller.activate()
    setup(window.world.shader)
    pyglet.app.run()
