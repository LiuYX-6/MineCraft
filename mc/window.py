import math

import pyglet
from pyglet.gl import (GL_QUADS, GL_LINES, GL_FOG, GL_FOG_COLOR, GL_FOG_HINT,
                        GL_FOG_MODE, GL_FOG_START, GL_FOG_END, GL_DONT_CARE,
                        GL_LINEAR, GL_CULL_FACE, GL_TEXTURE_2D,
                        GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
                        GL_NEAREST, GL_DEPTH_TEST, GL_FRONT_AND_BACK,
                        GL_LINE, GL_FILL,
                        GL_PROJECTION, GL_MODELVIEW,
                        GLfloat,
                        glClearColor, glEnable, glDisable, glViewport,
                        glMatrixMode, glLoadIdentity, glOrtho, gluPerspective,
                        glRotatef, glTranslatef, glColor3d, glPolygonMode,
                        glTexParameteri, glFogfv, glFogi, glFogf, glHint)
from mc.config import TICKS_PER_SEC
from mc.utils import cube_vertices, sectorize
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
                 **kwargs):
        super(GameWindow, self).__init__(*args, **kwargs)

        # ---- Component composition ----
        # Accept external instances; use defaults when not provided.
        self.controller = controller or KeyboardMouseController(self)
        self.world = world or World(FlatWorldGenerator())
        self.player = player or Player()

        # ---- Per-frame state ----
        # Which sector the player is currently in (for sector-change detection).
        self.sector = None

        # ---- HUD elements ----
        # FPS / position label (top-left).
        self.label = pyglet.text.Label('', font_name='Arial', font_size=18,
            x=10, y=self.height - 10, anchor_x='left', anchor_y='top',
            color=(0, 0, 0, 255))
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

        # 4. Discrete actions (consumed on read by the controller).
        actions = self.controller.poll_actions()
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
                self.controller.deactivate()
            elif action == 'place_block':
                vector = self.player.get_sight_vector()
                block, previous = self.world.hit_test(
                    self.player.position, vector)
                if previous:
                    self.world.add_block(previous,
                                         self.player.selected_block)
            elif action == 'break_block':
                vector = self.player.get_sight_vector()
                block, _ = self.world.hit_test(
                    self.player.position, vector)
                if block:
                    texture = self.world.world[block]
                    if texture != STONE:
                        self.world.remove_block(block)
            elif action.startswith('slot_'):
                index = int(action.split('_')[1])
                self.player.switch_slot(index)

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def on_resize(self, width, height):
        """Re-position the HUD label and recreate the reticle."""
        # Label
        self.label.y = height - 10
        # Reticle
        if self.reticle:
            self.reticle.delete()
        x, y = self.width // 2, self.height // 2
        n = 10
        self.reticle = pyglet.graphics.vertex_list(4,
            ('v2i', (x - n, y, x + n, y, x, y - n, x, y + n))
        )

    # ------------------------------------------------------------------
    # Rendering — 2D / 3D setup
    # ------------------------------------------------------------------

    def set_2d(self):
        """Configure OpenGL for 2D (HUD) drawing."""
        width, height = self.get_size()
        glDisable(GL_DEPTH_TEST)
        viewport = self.get_viewport_size()
        glViewport(0, 0, max(1, viewport[0]), max(1, viewport[1]))
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, max(1, width), 0, max(1, height), -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    def set_3d(self):
        """Configure OpenGL for 3D (world) drawing.

        Reads camera position and rotation from ``self.player``.
        """
        width, height = self.get_size()
        glEnable(GL_DEPTH_TEST)
        viewport = self.get_viewport_size()
        glViewport(0, 0, max(1, viewport[0]), max(1, viewport[1]))
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(65.0, width / float(height), 0.1, 60.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        x, y = self.player.rotation
        glRotatef(x, 0, 1, 0)
        glRotatef(-y, math.cos(math.radians(x)), 0,
                  math.sin(math.radians(x)))
        x, y, z = self.player.position
        glTranslatef(-x, -y, -z)

    # ------------------------------------------------------------------
    # Rendering — draw callbacks
    # ------------------------------------------------------------------

    def on_draw(self):
        """Called by pyglet to render the full frame."""
        self.clear()
        self.set_3d()
        glColor3d(1, 1, 1)
        self.world.batch.draw()
        self.draw_focused_block()
        self.set_2d()
        self.draw_label()
        self.draw_reticle()

    def draw_focused_block(self):
        """Draw a wireframe outline around the block under the crosshairs."""
        vector = self.player.get_sight_vector()
        block = self.world.hit_test(self.player.position, vector)[0]
        if block:
            x, y, z = block
            vertex_data = cube_vertices(x, y, z, 0.51)
            glColor3d(0, 0, 0)
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            pyglet.graphics.draw(24, GL_QUADS, ('v3f/static', vertex_data))
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)

    def draw_label(self):
        """Draw the FPS / position / block-count label (top-left)."""
        x, y, z = self.player.position
        self.label.text = '%02d (%.2f, %.2f, %.2f) %d / %d' % (
            pyglet.clock.get_fps(), x, y, z,
            len(self.world._shown), len(self.world.world))
        self.label.draw()

    def draw_reticle(self):
        """Draw the crosshair reticle at the centre of the screen."""
        glColor3d(0, 0, 0)
        self.reticle.draw(GL_LINES)


# ------------------------------------------------------------------
# Module-level OpenGL helpers
# ------------------------------------------------------------------

def setup_fog():
    """Configure OpenGL fog properties."""
    glEnable(GL_FOG)
    glFogfv(GL_FOG_COLOR, (GLfloat * 4)(0.5, 0.69, 1.0, 1))
    glHint(GL_FOG_HINT, GL_DONT_CARE)
    glFogi(GL_FOG_MODE, GL_LINEAR)
    glFogf(GL_FOG_START, 20.0)
    glFogf(GL_FOG_END, 60.0)


def setup():
    """Basic OpenGL configuration (call after window creation)."""
    glClearColor(0.5, 0.69, 1.0, 1)
    glEnable(GL_CULL_FACE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    setup_fog()


def run():
    """Convenience entry point: create a window and start the game loop."""
    window = GameWindow(width=800, height=600, caption='Minecraft',
                        resizable=True)
    window.controller.activate()
    setup()
    pyglet.app.run()
