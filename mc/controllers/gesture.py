"""Gesture-based game controller.

Maps hand gestures detected by :class:`HandGestureDetector` to in-game
actions.  Camera capture + MediaPipe inference run in a **background
daemon thread**; the main game thread reads the latest finger-state
snapshot once per tick, debounces it, and pushes edge-triggered actions
into the queue consumed by :meth:`poll_actions`.

Gesture-gated movement and rotation (Section 1.2 revised)
----------------------------------------------------------
Control is **gesture-gated** and **anchor-relative**:

* **Rotation** — activated by gesture ``11000`` (pinch: thumb + index
  extended).  The index-fingertip position at activation becomes the
  *anchor*; subsequent fingertip displacement from the anchor is mapped
  to yaw / pitch delta.
* **Movement** — activated by gesture ``11001`` (thumb + index + pinky
  extended).  Same anchor mechanism; displacement from the anchor is
  mapped to WASD-style strafe values.
* A **dead zone** around the anchor prevents jitter.
* When the gesture is released, control stops immediately.

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
``11000``   视角旋转 (rotation)
``11001``   移动控制 (movement)
==========  ==============
"""

import threading
from typing import Dict, List, Optional, Set, Tuple

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

# ---------------------------------------------------------------------------
# Gesture-gated movement / rotation (Section 1.2 revised)
# ---------------------------------------------------------------------------

# Finger codes that activate continuous control modes.
_ROTATION_GESTURE_CODE: Tuple[int, int, int, int, int] = (1, 1, 0, 0, 0)
_MOVEMENT_GESTURE_CODE: Tuple[int, int, int, int, int] = (1, 1, 0, 0, 1)

# Anchor-delta tuning parameters.
_ANCHOR_DEAD_ZONE = 0.015   # minimal dead zone around anchor (anti-jitter)
_ANCHOR_MAX_DELTA = 0.25    # 25 % of frame width → full deflection

# Maximum rotation speed (degrees per tick) at full deflection.
_ROTATION_SPEED = 3.0

# Landmark index used for position tracking.
_INDEX_TIP = 8


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

        # --- Movement / rotation (anchor-delta, gesture-gated) ---
        self._strafe = [0.0, 0.0]
        self._dyaw = 0.0
        self._dpitch = 0.0
        self._control_mode: Optional[str] = None      # 'rotation' | 'movement' | None
        self._anchor_tip: Optional[Tuple[float, float]] = None  # (x, y) in [0,1]

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
        self._latest_landmarks: Optional[
            List[Tuple[float, float, float]]
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
                if result.has_hand and result.hand_landmarks:
                    self._latest_landmarks = result.hand_landmarks[0]
                else:
                    self._latest_landmarks = None

        self._cap.release()
        self._detector.close()

    # ------------------------------------------------------------------
    # PlayerController interface
    # ------------------------------------------------------------------

    def get_strafe(self) -> Tuple[float, float]:
        """Return (forward_back, left_right) when gesture ``11001`` is held.

        The index-fingertip displacement from the anchor (set when the
        gesture activated) is mapped to movement direction and speed.
        Returns ``(0.0, 0.0)`` when no movement gesture is active.
        """
        return (self._strafe[0], self._strafe[1])

    def get_rotation_delta(self) -> Tuple[float, float]:
        """Return (yaw_delta, pitch_delta) when gesture ``11000`` is held.

        The index-fingertip displacement from the anchor (set when the
        gesture activated) is mapped to rotation speed.
        Returns ``(0.0, 0.0)`` when no rotation gesture is active.
        """
        return (self._dyaw, self._dpitch)

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
        """Per-frame update: debounce gesture → edge-triggered action,
        and drive gesture-gated movement / rotation.

        Called once per game tick from the main thread.  Reads the latest
        finger-state + landmarks snapshot, determines the control mode
        (rotation / movement / none), and computes continuous movement and
        rotation values from the anchor-relative index-fingertip displacement.
        """
        # --- Snapshot shared state (minimal lock hold) ---
        with self._lock:
            finger_states = self._latest_finger_states
            landmarks = self._latest_landmarks

        # --- No-hand path: reset everything --------------------------------
        if finger_states is None or landmarks is None:
            self._finger_code_str = '11111'
            self._action_display = ''
            self._action_history.clear()
            self._stable_action = None
            self._require_reset = False
            self._control_mode = None
            self._anchor_tip = None
            self._strafe = [0.0, 0.0]
            self._dyaw = 0.0
            self._dpitch = 0.0
            return

        # --- Convert finger_states → user code (0 = flexed, 1 = extended) ---
        code = tuple(1 if ext else 0 for ext in finger_states)
        self._finger_code_str = ''.join(str(d) for d in code)

        # --- Gesture-gated movement / rotation -----------------------------
        if code == _ROTATION_GESTURE_CODE:
            # Enter rotation mode on first frame; save anchor.
            if self._control_mode != 'rotation':
                self._control_mode = 'rotation'
                self._anchor_tip = (
                    landmarks[_INDEX_TIP][0], landmarks[_INDEX_TIP][1],
                )
            self._dyaw, self._dpitch = self._compute_rotation(landmarks)
            self._strafe = [0.0, 0.0]
        elif code == _MOVEMENT_GESTURE_CODE:
            # Enter movement mode on first frame; save anchor.
            if self._control_mode != 'movement':
                self._control_mode = 'movement'
                self._anchor_tip = (
                    landmarks[_INDEX_TIP][0], landmarks[_INDEX_TIP][1],
                )
            self._strafe = self._compute_strafe(landmarks)
            self._dyaw = 0.0
            self._dpitch = 0.0
        else:
            # Any other gesture → no continuous control.
            self._control_mode = None
            self._anchor_tip = None
            self._strafe = [0.0, 0.0]
            self._dyaw = 0.0
            self._dpitch = 0.0

        # --- Look up discrete action ---------------------------------------
        raw_action = _ACTION_MAP.get(code)  # None if not a mapped gesture

        # --- Debounce -------------------------------------------------------
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
        if self._stable_action is not None:
            if not self._require_reset:
                self._actions.add(self._stable_action)
                self._require_reset = True
        else:
            self._require_reset = False

    # ------------------------------------------------------------------
    # Hand-position → movement / rotation mapping (Section 1.2)
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_dead_zone(
        value: float,
        dead_zone: float = _ANCHOR_DEAD_ZONE,
        max_range: float = _ANCHOR_MAX_DELTA,
    ) -> float:
        """Apply dead-zone + normalise an offset value to [-1, 1].

        Values within *dead_zone* of zero are clamped to 0.  Beyond that,
        the value is linearly mapped to [0, 1] (sign preserved) so that
        *max_range* equals full deflection.

        Parameters
        ----------
        value : float
            Signed offset (e.g. fingertip displacement from anchor).
        dead_zone : float
            Radius of the dead zone.  Default: ``_ANCHOR_DEAD_ZONE``.
        max_range : float
            Offset that maps to 1.0 (full deflection).
            Default: ``_ANCHOR_MAX_DELTA``.

        Returns
        -------
        float
            Mapped value in [-1, 1].
        """
        if abs(value) < dead_zone:
            return 0.0
        sign = 1.0 if value > 0 else -1.0
        normalised = (abs(value) - dead_zone) / (max_range - dead_zone)
        return sign * min(normalised, 1.0)

    def _compute_strafe(
        self, landmarks: List[Tuple[float, float, float]]
    ) -> list:
        """Compute ``[forward_back, left_right]`` (four-directional).

        Only the **dominant axis** produces output — the other is zeroed.
        This gives strict forward / back / left / right movement with no
        diagonal blending.
        """
        if self._anchor_tip is None:
            return [0.0, 0.0]

        ix, iy, _ = landmarks[_INDEX_TIP]
        ax, ay = self._anchor_tip

        dx = ax - ix   # finger right → positive dx
        dy = ay - iy   # finger up → positive dy

        # Match keyboard convention (A/D → strafe[0], W/S → strafe[1]):
        #   finger left/right (dx) → strafe[0] (forward_back, negated)
        #   finger up/down   (dy) → strafe[1] (left_right, negated)
        # Both negated because on non-mirrored camera, user's left = camera's
        # right, so natural-drag direction is opposite to MediaPipe movement.
        raw_fb = -self._apply_dead_zone(dx)
        raw_lr = -self._apply_dead_zone(dy)

        # Pick dominant axis.
        if abs(raw_fb) >= abs(raw_lr):
            return [raw_fb, 0.0]
        else:
            return [0.0, raw_lr]

    def _compute_rotation(
        self, landmarks: List[Tuple[float, float, float]]
    ) -> Tuple[float, float]:
        """Compute ``(yaw_delta, pitch_delta)`` (four-directional).

        Only the **dominant axis** produces output — the other is zeroed.
        This gives strict left / right / up / down rotation (yaw or pitch)
        with no diagonal blending.
        """
        if self._anchor_tip is None:
            return (0.0, 0.0)

        ix, iy, _ = landmarks[_INDEX_TIP]
        ax, ay = self._anchor_tip

        dx = ax - ix   # finger right → yaw left (natural drag)
        dy = ay - iy   # finger up → pitch up

        raw_yaw = self._apply_dead_zone(dx) * _ROTATION_SPEED
        raw_pitch = self._apply_dead_zone(dy) * _ROTATION_SPEED  # dy already: finger up → positive

        # Pick dominant axis.
        if abs(raw_yaw) >= abs(raw_pitch):
            return (raw_yaw, 0.0)
        else:
            return (0.0, raw_pitch)

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

    @property
    def control_mode(self) -> Optional[str]:
        """Current control mode: ``'rotation'``, ``'movement'``, or ``None``."""
        return self._control_mode

    @property
    def anchor_tip(self) -> Optional[Tuple[float, float]]:
        """Anchor fingertip position ``(x, y)`` in normalised [0,1] coords,
        or ``None`` when no control mode is active."""
        return self._anchor_tip

    @property
    def current_tip(self) -> Optional[Tuple[float, float]]:
        """Current index-fingertip position ``(x, y)`` in normalised [0,1]
        coords, or ``None`` when no landmarks are available."""
        # Read under lock for thread safety.
        with self._lock:
            lm = self._latest_landmarks
        if lm is None:
            return None
        return (lm[_INDEX_TIP][0], lm[_INDEX_TIP][1])

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
