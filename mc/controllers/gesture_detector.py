"""MediaPipe-based hand gesture detection core.

Pure library — no window/visualisation/game dependencies.
Provides :class:`HandGestureDetector` which takes an RGB image and returns
a :class:`DetectionResult` with landmarks, handedness, finger states, and
the classified gesture name.

Uses the **modern** MediaPipe Tasks API (``mediapipe>=0.10``) which
requires a ``.task`` model file.  The model is auto-downloaded on first
use and cached under ``~/.cache/mc_gesture/``.
"""

import logging
import math
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python.vision import (
        HandLandmarker,
        HandLandmarkerOptions,
        HandLandmarkerResult,
        RunningMode,
    )

    _MP_AVAILABLE = True
except ImportError:
    _MP_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model download
# ---------------------------------------------------------------------------

_MODEL_URL = (
    'https://storage.googleapis.com/mediapipe-models/'
    'hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task'
)
_CACHE_DIR = Path.home() / '.cache' / 'mc_gesture'
_MODEL_PATH = _CACHE_DIR / 'hand_landmarker.task'


def _ensure_model() -> Path:
    """Download the MediaPipe hand-landmarker model if not cached."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if _MODEL_PATH.exists() and _MODEL_PATH.stat().st_size > 0:
        return _MODEL_PATH

    logger.info('Downloading MediaPipe hand-landmarker model …')
    try:
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    except Exception:
        # Clean up partial download on failure.
        if _MODEL_PATH.exists():
            _MODEL_PATH.unlink()
        raise RuntimeError(
            f'Failed to download hand-landmarker model from {_MODEL_URL}. '
            f'Please download it manually and place it at {_MODEL_PATH}.'
        )
    logger.info('Model cached at %s', _MODEL_PATH)
    return _MODEL_PATH


# ---------------------------------------------------------------------------
# MediaPipe landmark indices
# ---------------------------------------------------------------------------
_WRIST = 0

# Thumb:  tip=4,  ip=3
_THUMB_TIP = 4
_THUMB_IP = 3

# Index:  tip=8,  pip=6
_INDEX_TIP = 8
_INDEX_PIP = 6

# Middle: tip=12, pip=10
_MIDDLE_TIP = 12
_MIDDLE_PIP = 10

# Ring:   tip=16, pip=14
_RING_TIP = 16
_RING_PIP = 14

# Pinky:  tip=20, pip=18
_PINKY_TIP = 20
_PINKY_PIP = 18

_FINGER_DEFS = [
    (_THUMB_TIP, _THUMB_IP),   # 0 – thumb
    (_INDEX_TIP, _INDEX_PIP),  # 1 – index
    (_MIDDLE_TIP, _MIDDLE_PIP),  # 2 – middle
    (_RING_TIP, _RING_PIP),    # 3 – ring
    (_PINKY_TIP, _PINKY_PIP),  # 4 – pinky
]

# ---------------------------------------------------------------------------
# Gesture classification table
# (thumb, index, middle, ring, pinky) → gesture_name
# ---------------------------------------------------------------------------
_GESTURE_TABLE: dict[Tuple[int, ...], str] = {
    (0, 0, 0, 0, 0): 'fist',
    (1, 1, 1, 1, 1): 'open_palm',
    (0, 1, 0, 0, 0): 'point',
    (0, 1, 1, 0, 0): 'two_fingers',
    (0, 1, 1, 1, 0): 'three_fingers',
    (1, 1, 0, 0, 0): 'pinch',
    (1, 0, 0, 0, 0): 'thumbs_up',
}

# Debounce window size — gesture must be stable for this many frames.
_DEBOUNCE_FRAMES = 3


# ===========================================================================
# DetectionResult
# ===========================================================================


@dataclass
class DetectionResult:
    """Result of a single :meth:`HandGestureDetector.detect` call.

    Attributes
    ----------
    has_hand : bool
        Whether at least one hand was detected.
    hand_landmarks : list of list of (x, y, z)
        Normalised landmark positions ``[0, 1]`` for each detected hand.
        Each inner list has 21 ``(x, y, z)`` tuples.
    handedness : list of str
        ``'Left'`` or ``'Right'`` for each detected hand, in the same order
        as *hand_landmarks*.
    gesture : str or None
        Debounced gesture name for the **first** detected hand, or
        ``'no_hand'`` / ``None`` if no hand is present.
    finger_states : tuple of 5 bool or None
        Extended (True) / flexed (False) for each of the 5 fingers of the
        first detected hand.
    """

    has_hand: bool = False
    hand_landmarks: List[List[Tuple[float, float, float]]] = field(
        default_factory=list
    )
    handedness: List[str] = field(default_factory=list)
    gesture: Optional[str] = None
    finger_states: Optional[Tuple[bool, bool, bool, bool, bool]] = None


# ===========================================================================
# HandGestureDetector
# ===========================================================================


class HandGestureDetector:
    """MediaPipe hand-keypoint detection + gesture classification.

    Uses the modern MediaPipe Tasks ``HandLandmarker`` API
    (``mediapipe >= 0.10``).  The model file is auto-downloaded on first
    instantiation.

    Parameters
    ----------
    static_image_mode : bool
        Set to ``True`` for still images, ``False`` for video stream.
    max_num_hands : int
        Maximum number of hands to detect (1 or 2).
    min_detection_confidence : float
        Minimum confidence for hand detection to be considered successful.
    min_tracking_confidence : float
        Minimum confidence for hand tracking to be considered successful.
    """

    def __init__(
        self,
        static_image_mode: bool = False,
        max_num_hands: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ):
        if not _MP_AVAILABLE:
            raise ImportError(
                'mediapipe is required for HandGestureDetector. '
                'Install it with: pip install mediapipe'
            )

        # Resolve model file (auto-download on first use).
        model_path = _ensure_model()

        base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
        running_mode = (
            RunningMode.IMAGE if static_image_mode else RunningMode.VIDEO
        )

        options = HandLandmarkerOptions(
            base_options=base_options,
            running_mode=running_mode,
            num_hands=max_num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

        self._landmarker = HandLandmarker.create_from_options(options)
        self._static_mode = static_image_mode

        # Debounce state: ring buffer of recent raw gesture strings.
        self._gesture_history: deque = deque(maxlen=_DEBOUNCE_FRAMES)
        self._current_gesture: Optional[str] = None

        # Monotonically increasing timestamp for VIDEO mode (ms).
        self._frame_timestamp_ms = 0

    # ------------------------------------------------------------------
    def detect(self, rgb_image: np.ndarray) -> DetectionResult:
        """Run detection on a single RGB frame.

        Parameters
        ----------
        rgb_image : np.ndarray
            RGB image of shape ``(H, W, 3)`` with ``uint8`` pixels.

        Returns
        -------
        DetectionResult
        """
        # Wrap image for MediaPipe Tasks API.
        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb_image,
        )

        # Run inference.
        if self._static_mode:
            result = self._landmarker.detect(mp_image)
        else:
            result = self._landmarker.detect_for_video(
                mp_image, self._frame_timestamp_ms
            )
            self._frame_timestamp_ms += 33  # ~30 fps

        return self._build_result(result)

    # ------------------------------------------------------------------
    def __del__(self):
        """Release MediaPipe resources."""
        try:
            self._landmarker.close()
        except Exception:
            pass

    def close(self) -> None:
        """Explicitly release MediaPipe resources."""
        try:
            self._landmarker.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_result(
        self, mp_result: HandLandmarkerResult
    ) -> DetectionResult:
        """Convert raw MediaPipe result → :class:`DetectionResult`."""
        result = DetectionResult()

        if not mp_result.hand_landmarks:
            self._gesture_history.clear()
            self._current_gesture = None
            result.gesture = 'no_hand'
            return result

        result.has_hand = True

        # --- Extract landmarks -----------------------------------------
        for hand_lms in mp_result.hand_landmarks:
            lm_list = [
                (lm.x or 0.0, lm.y or 0.0, lm.z or 0.0)
                for lm in hand_lms
            ]
            result.hand_landmarks.append(lm_list)

        # --- Extract handedness ----------------------------------------
        for hand_categories in mp_result.handedness:
            # hand_categories is a list of Category objects; best one first.
            if hand_categories:
                label = hand_categories[0].category_name or 'Unknown'
                # Map to 'Left' / 'Right' (MediaPipe uses these strings).
            else:
                label = 'Unknown'
            result.handedness.append(label)

        # --- Process first hand for gesture ----------------------------
        first_lm = result.hand_landmarks[0]
        finger_states = self._get_finger_states(first_lm)
        result.finger_states = finger_states

        raw_gesture = self._classify_gesture(finger_states)
        debounced = self._debounce(raw_gesture)
        result.gesture = debounced

        return result

    # ------------------------------------------------------------------
    # Finger state helpers
    # ------------------------------------------------------------------

    def _get_finger_states(
        self, landmarks: List[Tuple[float, float, float]]
    ) -> Tuple[bool, bool, bool, bool, bool]:
        """Return a 5-tuple of bools indicating each finger is extended.

        A finger is considered **extended** when the Euclidean distance
        from its tip to the wrist is **greater** than the distance from
        its PIP joint (or IP for the thumb) to the wrist.
        """
        wrist = landmarks[_WRIST]
        states: List[bool] = []
        for tip_idx, pip_idx in _FINGER_DEFS:
            tip = landmarks[tip_idx]
            pip = landmarks[pip_idx]
            dist_tip_wrist = _euclidean_2d(tip, wrist)
            dist_pip_wrist = _euclidean_2d(pip, wrist)
            states.append(dist_tip_wrist > dist_pip_wrist)
        return tuple(states)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Gesture classification
    # ------------------------------------------------------------------

    def _classify_gesture(
        self,
        finger_states: Tuple[bool, bool, bool, bool, bool],
    ) -> str:
        """Map a 5-finger-state tuple to a gesture name string.

        Returns ``'unknown'`` if the combination is not in the table.
        """
        int_key = tuple(1 if f else 0 for f in finger_states)
        return _GESTURE_TABLE.get(int_key, 'unknown')

    # ------------------------------------------------------------------
    # Debounce
    # ------------------------------------------------------------------

    def _debounce(self, raw_gesture: str) -> str:
        """Debounce gesture: requires N consecutive identical frames.

        Returns the confirmed gesture, or the previous confirmed gesture
        if the debounce window hasn't settled yet.
        """
        self._gesture_history.append(raw_gesture)

        if (
            len(self._gesture_history) == _DEBOUNCE_FRAMES
            and len(set(self._gesture_history)) == 1
        ):
            self._current_gesture = raw_gesture

        return (
            self._current_gesture
            if self._current_gesture is not None
            else raw_gesture
        )


# ===========================================================================
# Helpers
# ===========================================================================


def _euclidean_2d(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
) -> float:
    """Euclidean distance in the XY plane (ignoring Z)."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return float(math.sqrt(dx * dx + dy * dy))
