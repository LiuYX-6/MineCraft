from typing import Set, Tuple

from mc.controllers import PlayerController


class GestureController(PlayerController):
    """Camera-based gesture recognition input controller (skeleton).

    Intended to be driven by a camera + hand-tracking library (e.g.
    MediaPipe).  Implements the same ``PlayerController`` interface as
    ``KeyboardMouseController`` so that ``GameWindow`` requires zero
    changes when swapping input sources.

    .. note::
        This is a **skeleton** implementation.  All methods raise
        ``NotImplementedError``.  It will be developed in a follow-up.
    """

    def __init__(self, camera_id=0):
        self._camera_id = camera_id
        self._strafe = [0.0, 0.0]
        self._dyaw = 0.0
        self._dpitch = 0.0
        self._actions: Set[str] = set()

    # -- PlayerController interface --------------------------------------

    def get_strafe(self) -> Tuple[float, float]:
        raise NotImplementedError('GestureController.get_strafe')

    def get_rotation_delta(self) -> Tuple[float, float]:
        raise NotImplementedError('GestureController.get_rotation_delta')

    def poll_actions(self) -> Set[str]:
        raise NotImplementedError('GestureController.poll_actions')

    def activate(self) -> None:
        raise NotImplementedError('GestureController.activate')

    def deactivate(self) -> None:
        raise NotImplementedError('GestureController.deactivate')

    def update(self, dt: float) -> None:
        raise NotImplementedError('GestureController.update')
