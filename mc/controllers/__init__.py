from abc import ABC, abstractmethod
from typing import Set, Tuple


class PlayerController(ABC):
    """Abstract interface for player input control.

    All input sources (keyboard/mouse, gesture, gamepad, etc.) implement
    this interface.  ``GameWindow`` depends only on this interface and is
    completely decoupled from the concrete input source.
    """

    @abstractmethod
    def get_strafe(self) -> Tuple[float, float]:
        """Return (forward_back, left_right), each in range [-1, 1].

        forward_back: positive = forward, negative = backward.
        left_right:  positive = right,   negative = left.
        """
        ...

    @abstractmethod
    def get_rotation_delta(self) -> Tuple[float, float]:
        """Return this frame's (yaw_delta, pitch_delta) in degrees.

        The values are consumed on read — subsequent calls return (0, 0)
        until new input arrives.
        """
        ...

    @abstractmethod
    def poll_actions(self) -> Set[str]:
        """Return the set of actions triggered this frame, then clear them.

        Supported actions:

        * ``'jump'`` — jump
        * ``'fly_toggle'`` — toggle flying mode
        * ``'place_block'`` — place a block
        * ``'break_block'`` — break a block
        * ``'escape'`` — release mouse / exit
        * ``'slot_0'`` … ``'slot_9'`` — switch hotbar slot
        """
        ...

    @abstractmethod
    def activate(self) -> None:
        """Activate / initialise the controller (e.g. capture mouse,
        start camera)."""
        ...

    @abstractmethod
    def deactivate(self) -> None:
        """Deactivate the controller (e.g. release mouse, stop camera)."""
        ...

    @abstractmethod
    def update(self, dt: float) -> None:
        """Per-frame state update (called from the game loop)."""
        ...


# Re-export concrete controller implementations.
# Placed at the bottom to avoid circular-import issues (the sub-modules
# import PlayerController from this __init__).
from mc.controllers.keyboard_mouse import KeyboardMouseController  # noqa: E402
from mc.controllers.gesture import GestureController  # noqa: E402
