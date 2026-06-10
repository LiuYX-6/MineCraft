"""Gesture-based game controller.

Maps hand gestures detected by :class:`HandGestureDetector` to in-game
actions.  Camera capture + MediaPipe inference run in a **background
daemon thread**; the main game thread reads the latest finger-state
snapshot once per tick, debounces it, and pushes edge-triggered actions
into the queue consumed by :meth:`poll_actions`.

Gesture encoding
----------------
From thumb to pinky, each digit is:

* ``0`` — finger **flexed** (curled / bent)
* ``1`` — finger **extended** (straight)

==========  ==============
Code        Action
==========  ==============
``11111``   无动作 (no action)
``10111``   ``place_block``
``11011``   ``break_block``
``10000``   ``pick_block``
==========  ==============
"""

import threading
from typing import Dict, Optional, Set, Tuple

from mc.controllers import PlayerController
from mc.controllers.gesture_detector import HandGestureDetector

# ---------------------------------------------------------------------------
# Action mapping
#   key: (thumb, index, middle, ring, pinky) — 0 = flexed, 1 = extended
#   value: action string consumed by GameWindow._handle_actions
# ---------------------------------------------------------------------------
_ACTION_MAP: Dict[Tuple[int, int, int, int, int], str] = {
    (1, 0, 1, 1, 1): 'place_block',
    (1, 1, 0, 1, 1): 'break_block',
    (1, 0, 0, 0, 0): 'pick_block',
}

# Human-readable Chinese labels for on-screen HUD display.
_ACTION_DISPLAY: Dict[str, str] = {
    'place_block': '方块放置',
    'break_block': '方块破坏',
    'pick_block': '方块类型选择',
}

# Number of consecutive identical frames before an action is considered
# stable (independent of the detector's own debounce).
_DEBOUNCE_FRAMES = 3


class GestureController(PlayerController):
    """Camera-based gesture recognition input controller.

    Uses :class:`HandGestureDetector` (MediaPipe) to detect hand gestures
    from a webcam and maps them to in-game actions.

    Parameters
    ----------
    camera_id : int
        OpenCV camera index (default ``0``).
    """

    def __init__(self, camera_id: int = 0):
        self._camera_id = camera_id

        # --- Movement / rotation (reserved for future use) ---
        self._strafe = [0.0, 0.0]
        self._dyaw = 0.0
        self._dpitch = 0.0

        # --- Discrete action queue (consumed each frame) ---
        self._actions: Set[str] = set()

        # --- Background capture state ---
        self._detector: Optional[HandGestureDetector] = None
        self._cap = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Shared state — written by the background thread, read by update().
        self._latest_finger_states: Optional[
            Tuple[bool, bool, bool, bool, bool]
        ] = None

        # --- Action debounce + edge detection ---
        self._action_history: list = []          # recent raw action names
        self._stable_action: Optional[str] = None  # debounced action
        self._require_reset: bool = False  # lock after fire; release on no-action

        # --- Display state (read by HUD rendering on the main thread) ---
        self._finger_code_str: str = '11111'
        self._action_display: str = ''

    # ------------------------------------------------------------------
    # Background capture thread
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Entry point for the background detection thread.

        Creates the detector and camera in *this* thread (MediaPipe may
        have thread-affinity requirements).  Pushes the latest raw
        ``finger_states`` into ``_latest_finger_states`` under a lock so
        the main thread can read it without blocking.
        """
        try:
            import cv2
        except ImportError:
            self._running = False
            return

        self._detector = HandGestureDetector(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        self._cap = cv2.VideoCapture(self._camera_id)
        if not self._cap.isOpened():
            self._running = False
            return

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self._detector.detect(rgb)

            with self._lock:
                self._latest_finger_states = result.finger_states

        self._cap.release()
        self._detector.close()

    # ------------------------------------------------------------------
    # PlayerController interface
    # ------------------------------------------------------------------

    def get_strafe(self) -> Tuple[float, float]:
        """Return (forward_back, left_right); always ``(0, 0)``.

        Movement via gestures is reserved for future work.
        """
        return (0.0, 0.0)

    def get_rotation_delta(self) -> Tuple[float, float]:
        """Return (yaw_delta, pitch_delta); always ``(0, 0)``.

        Rotation via gestures is reserved for future work.
        """
        return (0.0, 0.0)

    def poll_actions(self) -> Set[str]:
        """Return and clear the set of actions triggered this frame."""
        actions = self._actions
        self._actions = set()
        return actions

    def activate(self) -> None:
        """Start the camera and background detection thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True,
        )
        self._thread.start()

    def deactivate(self) -> None:
        """Stop the background thread and release camera resources."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def update(self, dt: float) -> None:
        """Per-frame update: debounce gesture → edge-triggered action.

        Called once per game tick from the main thread.  Reads the latest
        finger-state snapshot, converts it to the user encoding
        (0 = flexed, 1 = extended), looks up the corresponding action,
        debounces, and pushes a single action when the gesture
        **transitions** into a mapped action.

        Also updates the HUD display state (finger code + action label)
        so that :meth:`finger_code` and :meth:`action_display` return
        up-to-date values for rendering.
        """
        # --- Snapshot shared state (minimal lock hold) ---
        with self._lock:
            finger_states = self._latest_finger_states

        if finger_states is None:
            # No hand detected — show all-extended and reset everything.
            self._finger_code_str = '11111'
            self._action_display = ''
            self._action_history.clear()
            self._stable_action = None
            self._require_reset = False
            return

        # --- Convert finger_states → user code (0 = flexed, 1 = extended) ---
        # finger_states uses True = extended, False = flexed.
        code = tuple(1 if ext else 0 for ext in finger_states)
        self._finger_code_str = ''.join(str(d) for d in code)

        # --- Look up the corresponding action ---
        raw_action = _ACTION_MAP.get(code)  # None if not a mapped gesture

        # --- Debounce ----------------------------------------------------
        self._action_history.append(raw_action)
        if len(self._action_history) > _DEBOUNCE_FRAMES:
            self._action_history.pop(0)

        if (
            len(self._action_history) == _DEBOUNCE_FRAMES
            and len(set(self._action_history)) == 1
        ):
            self._stable_action = self._action_history[0]

        # --- Update display label ---
        self._action_display = _ACTION_DISPLAY.get(
            self._stable_action or '', '',
        )

        # --- Edge-triggered push with reset lock -------------------------
        # Fire **once**, then require the gesture to return to no-action
        # (stable_action → None / 00000) before the next action can fire.
        # This prevents rapid-fire and direct gesture-switching without
        # an explicit release in between.
        if self._stable_action is not None:
            if not self._require_reset:
                self._actions.add(self._stable_action)
                self._require_reset = True
        else:
            self._require_reset = False

    # ------------------------------------------------------------------
    # HUD display properties (read by GameWindow.on_draw)
    # ------------------------------------------------------------------

    @property
    def finger_code(self) -> str:
        """Current finger-state code as a 5-digit string.

        ``'11111'`` means all fingers extended (or no hand detected).
        """
        return self._finger_code_str

    @property
    def action_display(self) -> str:
        """Human-readable label for the current stable action.

        Returns an empty string when no action is active.
        """
        return self._action_display

    # ------------------------------------------------------------------
    # Compatibility with KeyboardMouseController
    # ------------------------------------------------------------------

    @property
    def exclusive(self) -> bool:
        """Gesture mode does not use mouse capture; always ``True``.

        Exists for compatibility with ``GameWindow._handle_actions``
        so that the ``escape`` action path doesn't raise
        :exc:`AttributeError`.
        """
        return True
