from typing import Set, Tuple

from pyglet.window import key, mouse

from mc.controllers import PlayerController


class KeyboardMouseController(PlayerController):
    """Keyboard + mouse input controller.

    Registers itself as a pyglet event handler on the given *window*.
    Maintains internal key-state tables and accumulates mouse motion /
    discrete actions each frame.

    ``GameWindow`` queries ``get_strafe()``, ``get_rotation_delta()`` and
    ``poll_actions()`` once per tick — the accumulated values are then
    consumed (zeroed out) so each event is processed exactly once.
    """

    # ------------------------------------------------------------------
    def __init__(self, window):
        self._window = window

        # --- strafe state ---
        # _strafe[0]: +1 forward, -1 backward, 0 idle
        # _strafe[1]: +1 right,   -1 left,     0 idle
        self._strafe = [0, 0]

        # --- per-frame rotation delta (accumulated, consumed on read) ---
        self._dyaw = 0.0
        self._dpitch = 0.0

        # --- discrete actions accumulated this frame ---
        self._actions: Set[str] = set()

        # --- key state for modifiers / held keys ---
        self._keys = set()

        # --- mouse exclusivity ---
        self._exclusive = False

        # Register pyglet event handlers
        window.push_handlers(self)

    # -- pyglet event handlers -------------------------------------------

    def on_key_press(self, symbol, modifiers):
        self._keys.add(symbol)

        if symbol == key.A:
            self._strafe[0] += 1
        elif symbol == key.D:
            self._strafe[0] -= 1
        elif symbol == key.W:
            self._strafe[1] -= 1
        elif symbol == key.S:
            self._strafe[1] += 1
        elif symbol == key.SPACE:
            self._actions.add('jump')
        elif symbol == key.ESCAPE:
            self._actions.add('escape')
        elif symbol == key.TAB:
            self._actions.add('fly_toggle')
        elif symbol in _NUM_KEYS:
            index = (symbol - _NUM_KEYS[0]) % 10
            self._actions.add(f'slot_{index}')

    def on_key_release(self, symbol, modifiers):
        self._keys.discard(symbol)

        if symbol == key.A:
            self._strafe[0] -= 1
        elif symbol == key.D:
            self._strafe[0] += 1
        elif symbol == key.W:
            self._strafe[1] += 1
        elif symbol == key.S:
            self._strafe[1] -= 1

    def on_mouse_press(self, x, y, button, modifiers):
        if self._exclusive:
            if (button == mouse.RIGHT) or \
                    ((button == mouse.LEFT) and (modifiers & key.MOD_CTRL)):
                self._actions.add('place_block')
            elif button == mouse.LEFT:
                self._actions.add('break_block')
        else:
            self._exclusive = True
            self._window.set_exclusive_mouse(True)

    def on_mouse_motion(self, x, y, dx, dy):
        if self._exclusive:
            m = 0.15
            self._dyaw += dx * m
            self._dpitch += dy * m

    # -- PlayerController interface --------------------------------------

    def get_strafe(self) -> Tuple[float, float]:
        return (float(self._strafe[0]), float(self._strafe[1]))

    def get_rotation_delta(self) -> Tuple[float, float]:
        dyaw, dpitch = self._dyaw, self._dpitch
        self._dyaw = 0.0
        self._dpitch = 0.0
        return (dyaw, dpitch)

    def poll_actions(self) -> Set[str]:
        actions = self._actions
        self._actions = set()
        return actions

    def activate(self) -> None:
        self._exclusive = True
        self._window.set_exclusive_mouse(True)

    def deactivate(self) -> None:
        self._exclusive = False
        self._window.set_exclusive_mouse(False)

    def update(self, dt: float) -> None:
        """No-op — this controller is purely event-driven."""
        pass

    @property
    def exclusive(self) -> bool:
        return self._exclusive


# ---- module-level helpers -----------------------------------------------

_NUM_KEYS = [
    key._1, key._2, key._3, key._4, key._5,
    key._6, key._7, key._8, key._9, key._0,
]
