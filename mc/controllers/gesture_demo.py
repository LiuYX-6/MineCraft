"""Gesture detection demo — real-time camera + OpenCV visualisation.

Run::

    python -m mc.controllers.gesture_demo

Press **Q** to quit.
"""

import sys
from typing import List, Optional, Tuple

import numpy as np

from mc.controllers.gesture_detector import HandGestureDetector

# ---------------------------------------------------------------------------
# OpenCV helpers (tolerant to missing opencv — the import guard below is
# the real gate, these helpers just keep the drawing code tidy).
# ---------------------------------------------------------------------------

# Finger names for on-screen labels.
_FINGER_NAMES = ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky']

# MediaPipe hand-connection pairs (21-point topology).
_HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # index
    (0, 9), (9, 10), (10, 11), (11, 12),   # middle
    (0, 13), (13, 14), (14, 15), (15, 16), # ring
    (0, 17), (17, 18), (18, 19), (19, 20), # pinky
    (5, 9), (9, 13), (13, 17),              # palm arches
]

# Landmark colours in BGR (OpenCV format).
_LM_COLORS = [
    (240, 240, 240),  #  0  wrist       – white
    (255, 180, 180),  #  1  thumb_cmc   – light blue
    (255, 140, 140),  #  2  thumb_mcp
    (255, 100, 100),  #  3  thumb_ip
    (255,  60,  60),  #  4  thumb_tip    – red
    (180, 255, 180),  #  5  index_mcp   – light green
    (140, 255, 140),  #  6  index_pip
    (100, 255, 100),  #  7  index_dip
    ( 60, 255,  60),  #  8  index_tip    – green
    (180, 180, 255),  #  9  middle_mcp  – light orange
    (140, 140, 255),  # 10  middle_pip
    (100, 100, 255),  # 11  middle_dip
    ( 60,  60, 255),  # 12  middle_tip   – orange
    (255, 180, 255),  # 13  ring_mcp    – light pink
    (255, 140, 255),  # 14  ring_pip
    (255, 100, 255),  # 15  ring_dip
    (255,  60, 255),  # 16  ring_tip     – magenta
    (255, 255, 180),  # 17  pinky_mcp   – light cyan
    (255, 255, 140),  # 18  pinky_pip
    (255, 255, 100),  # 19  pinky_dip
    (255, 255,  60),  # 20  pinky_tip    – cyan
]

# Radius for landmark dots (pixels).
_LM_RADIUS = 5
# Thickness for connection lines (pixels).
_LINE_THICKNESS = 2


def _draw_landmarks(
    image: np.ndarray,
    landmarks: List[Tuple[float, float, float]],
    h: int,
    w: int,
) -> None:
    """Draw coloured landmark dots + white connection lines on *image*."""
    # Connections first (behind dots).
    for a_idx, b_idx in _HAND_CONNECTIONS:
        ax, ay = int(landmarks[a_idx][0] * w), int(landmarks[a_idx][1] * h)
        bx, by = int(landmarks[b_idx][0] * w), int(landmarks[b_idx][1] * h)
        cv2.line(image, (ax, ay), (bx, by), (255, 255, 255), _LINE_THICKNESS)

    # Landmark dots.
    for i, (lx, ly, _) in enumerate(landmarks):
        px, py = int(lx * w), int(ly * h)
        cv2.circle(image, (px, py), _LM_RADIUS, _LM_COLORS[i], -1)


def _draw_hud(
    image: np.ndarray,
    gesture: str,
    finger_states: Optional[Tuple[bool, bool, bool, bool, bool]],
) -> None:
    """Draw gesture name + per-finger state on the top-left of *image*."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    white = (255, 255, 255)
    green = (0, 255, 0)
    gray = (128, 128, 128)

    # Gesture name
    cv2.putText(
        image, f'Gesture: {gesture}', (10, 30),
        font, 0.8, white, 2, cv2.LINE_AA,
    )

    if finger_states is None:
        return

    # Per-finger labels
    for i, (name, extended) in enumerate(zip(_FINGER_NAMES, finger_states)):
        label = f'{name}: {"EXTENDED" if extended else "FLEXED"}'
        color = green if extended else gray
        y = 60 + i * 24
        cv2.putText(
            image, label, (10, y),
            font, 0.55, color, 2, cv2.LINE_AA,
        )


# ===========================================================================
# main
# ===========================================================================

def main(rotate_camera: bool = False) -> None:
    """Run the gesture detection demo loop.

    Parameters
    ----------
    rotate_camera : bool
        If ``True``, rotate the camera image 90° counter-clockwise before
        detection.  The displayed window also shows the rotated image, but
        the HUD overlay (gesture name, finger states) is drawn in the
        normal upright orientation on top of it.
    """

    # -- delayed OpenCV import so the module is importable without it -----
    global cv2
    try:
        import cv2 as _cv2
        cv2 = _cv2
    except ImportError:
        print(
            'OpenCV is required for the demo. '
            'Install it with: pip install opencv-python',
            file=sys.stderr,
        )
        sys.exit(1)

    # -- camera -----------------------------------------------------------
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print('ERROR: Could not open camera (index 0).', file=sys.stderr)
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # -- detector ---------------------------------------------------------
    detector = HandGestureDetector(
        static_image_mode=False,
        max_num_hands=2,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    print('Gesture demo started. Press Q to quit.')
    if rotate_camera:
        print('Camera rotation: 90° CCW')

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print('WARNING: Failed to read frame from camera.')
                break

            # Optionally rotate the display frame 90° CCW.
            if rotate_camera:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

            # MediaPipe requires RGB; OpenCV gives BGR.
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = detector.detect(rgb)

            h, w = frame.shape[:2]

            # Draw landmarks for all detected hands.
            for landmarks in result.hand_landmarks:
                _draw_landmarks(frame, landmarks, h, w)

            # HUD overlay (drawn upright — the text is never rotated).
            _draw_hud(frame, result.gesture, result.finger_states)

            # Show.
            cv2.imshow('Gesture Demo — press Q to quit', frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print('Demo exited.')


if __name__ == '__main__':
    rotate = '--rotate' in sys.argv or '-r' in sys.argv
    main(rotate_camera=rotate)
