import math

from mc.config import (
    xrange,
    PLAYER_HEIGHT,
    WALKING_SPEED,
    FLYING_SPEED,
    GRAVITY,
    TERMINAL_VELOCITY,
    JUMP_SPEED,
)
from mc.utils import normalize, FACES
from mc.blocks import BRICK, GRASS, SAND


class Player(object):
    """Pure-logic player class — decoupled from input source and rendering.

    All external input (strafe, flying, actions) is passed in as method
    parameters rather than read from a controller or window directly.
    """

    def __init__(self, position=(0, 0, 0), rotation=(0, 0)):
        # Current (x, y, z) position in the world (floats).
        self.position = position

        # (yaw, pitch) in degrees.  yaw = rotation in the XZ (ground)
        # plane measured from the z-axis down; pitch = angle from the
        # ground plane up, clamped to [-90, 90].
        self.rotation = rotation

        # Vertical velocity.
        self.dy = 0.0

        # Whether or not the player is currently flying.
        self.flying = False

        # Player height for collision detection.
        self.height = PLAYER_HEIGHT

        # Inventory: hotbar block textures.
        self.inventory = [BRICK, GRASS, SAND]

        # Index of the currently selected hotbar slot.
        self.selected_block_index = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def selected_block(self):
        """Return the texture of the currently selected block."""
        return self.inventory[self.selected_block_index]

    # ------------------------------------------------------------------
    # Sight / motion vectors
    # ------------------------------------------------------------------

    def get_sight_vector(self):
        """Return the current line-of-sight unit vector for the player's
        rotation.

        """
        x, y = self.rotation
        # y ranges from -90 to 90, or -pi/2 to pi/2, so m ranges from 0
        # to 1 and is 1 when looking parallel to the ground.
        m = math.cos(math.radians(y))
        # dy ranges from -1 to 1 (-1 = straight down, 1 = straight up).
        dy = math.sin(math.radians(y))
        dx = math.cos(math.radians(x - 90)) * m
        dz = math.sin(math.radians(x - 90)) * m
        return (dx, dy, dz)

    def get_motion_vector(self, strafe, flying):
        """Return the motion velocity vector given *strafe* and *flying*.

        Parameters
        ----------
        strafe : tuple of (float, float)
            (forward_back, left_right) where +1 = forward / right.
        flying : bool
            Whether the player is in flying mode.

        Returns
        -------
        vector : tuple of len 3
            (dx, dy, dz) velocity components (not yet scaled by speed).
        """
        if any(strafe):
            x, y = self.rotation
            # Convert strafe to the internal angle convention.
            # strafe[0] = +1 forward;  game uses -1 forward internally,
            # so negate the first component for atan2.
            strafe_angle = math.degrees(math.atan2(strafe[1], -strafe[0]))
            y_angle = math.radians(y)
            x_angle = math.radians(x + strafe_angle)
            if flying:
                m = math.cos(y_angle)
                dy = math.sin(y_angle)
                if strafe[1]:
                    # Moving left or right — no vertical component.
                    dy = 0.0
                    m = 1
                if strafe[0] < 0:
                    # Moving backward — invert vertical component.
                    dy *= -1
                dx = math.cos(x_angle) * m
                dz = math.sin(x_angle) * m
            else:
                dy = 0.0
                dx = math.cos(x_angle)
                dz = math.sin(x_angle)
        else:
            dx = dy = dz = 0.0
        return (dx, dy, dz)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def jump(self):
        """Attempt to jump.  Only succeeds when the player is on the
        ground (``dy == 0``)."""
        if self.dy == 0:
            self.dy = JUMP_SPEED

    def toggle_flying(self):
        """Toggle flying mode on / off."""
        self.flying = not self.flying

    def switch_slot(self, index):
        """Switch the selected hotbar slot to *index* (wraps around)."""
        self.selected_block_index = index % len(self.inventory)

    # ------------------------------------------------------------------
    # Physics
    # ------------------------------------------------------------------

    def update_physics(self, dt, world_blocks, strafe, flying):
        """Update player position, gravity and collision for one frame.

        *dt* is clamped and sub-stepped for collision accuracy.

        Parameters
        ----------
        dt : float
            Delta-time for this frame (seconds).
        world_blocks : dict
            Mapping ``(x, y, z) → texture`` for all blocks in the world.
        strafe : tuple of (float, float)
            Current strafe input from the controller.
        flying : bool
            Whether the player is currently flying.
        """
        dt = min(dt, 0.2)
        for _ in xrange(8):  # sub-step for collision resolution
            self._physics_step(dt / 8.0, world_blocks, strafe, flying)

    def _physics_step(self, dt, world_blocks, strafe, flying):
        """Single sub-step of the physics simulation."""
        # walking
        speed = FLYING_SPEED if flying else WALKING_SPEED
        d = dt * speed  # distance covered this sub-step
        dx, dy, dz = self.get_motion_vector(strafe, flying)
        dx, dy, dz = dx * d, dy * d, dz * d

        # gravity
        if not flying:
            self.dy -= dt * GRAVITY
            self.dy = max(self.dy, -TERMINAL_VELOCITY)
            dy += self.dy * dt

        # collisions
        x, y, z = self.position
        x, y, z = self.collide((x + dx, y + dy, z + dz), self.height,
                               world_blocks)
        self.position = (x, y, z)

    # ------------------------------------------------------------------
    # Collision detection
    # ------------------------------------------------------------------

    def collide(self, position, height, world_blocks):
        """Check whether the player at *position* with *height* is
        colliding with any blocks in *world_blocks*.

        Parameters
        ----------
        position : tuple of len 3
            The proposed (x, y, z) position.
        height : int or float
            The height of the player.
        world_blocks : dict
            Mapping ``(x, y, z) → texture``.

        Returns
        -------
        position : tuple of len 3
            Corrected position after resolving collisions.
        """
        # Overlap threshold: how much you can overlap a block before
        # it counts as a collision.  A value >= 0.5 lets you fall
        # through the ground.
        pad = 0.25
        p = list(position)
        np = normalize(position)
        for face in FACES:  # check all six surrounding directions
            for i in xrange(3):  # check each dimension independently
                if not face[i]:
                    continue
                d = (p[i] - np[i]) * face[i]
                if d < pad:
                    continue
                for dy in xrange(height):  # check each vertical level
                    op = list(np)
                    op[1] -= dy
                    op[i] += face[i]
                    if tuple(op) not in world_blocks:
                        continue
                    p[i] -= (d - pad) * face[i]
                    if face == (0, -1, 0) or face == (0, 1, 0):
                        # Colliding with ground or ceiling — stop
                        # falling / rising.
                        self.dy = 0
                    break
        return tuple(p)
