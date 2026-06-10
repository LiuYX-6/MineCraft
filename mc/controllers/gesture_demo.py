"""Gesture detection demo — real-time camera + OpenCV visualisation.

Run::

    python -m mc.controllers.gesture_demo

Press **Q** to quit.

On-screen HUD displays:

* **Gesture name** — debounced gesture class (fist, open_palm, point, …)
* **Finger code** — 5-digit binary string (0 = flexed, 1 = extended)
* **Mapped action** — the in-game command triggered by this gesture
  (only shown when the gesture maps to a known action)
* **Per-finger state** — EXTENDED / FLEXED for each digit
"""

import sys
from typing import List, Optional, Tuple

import numpy as np

from mc.controllers.gesture_detector import HandGestureDetector
from mc.controllers.gesture import (
    _ACTION_MAP, _ACTION_DISPLAY,
    _ROTATION_GESTURE_CODE, _MOVEMENT_GESTURE_CODE,
    _ANCHOR_DEAD_ZONE, _ANCHOR_MAX_DELTA,
    _ROTATION_SPEED, _INDEX_TIP,
    GestureController,
)

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
    finger_code: str = '',
    action_label: str = '',
    strafe: Optional[Tuple[float, float]] = None,
    rotation: Optional[Tuple[float, float]] = None,
    control_mode: Optional[str] = None,
) -> None:
    """Draw gesture name, finger code, mapped action, per-finger state,
    control mode, and movement / rotation values.

    Overlay layout (top-left of *image*)::

        Gesture: pinch
        Code: 11000  -> --
        Mode: ROTATION
        Thumb:  EXTENDED
        ...
        ---- Movement / Rotation ----
        Move  . F:+0.00   . L:+0.00
        Look  > Y:+1.20   ^ P:-0.45
    """
    font = cv2.FONT_HERSHEY_SIMPLEX
    white = (255, 255, 255)
    green = (0, 255, 0)
    gray = (128, 128, 128)
    cyan = (255, 255, 0)
    yellow = (0, 255, 255)

    # --- Row 0: gesture name -------------------------------------------
    cv2.putText(
        image, f'Gesture: {gesture}', (10, 30),
        font, 0.8, white, 2, cv2.LINE_AA,
    )

    # --- Row 1: finger code + mapped action ---------------------------
    if finger_code:
        code_text = f'Code: {finger_code}'
        cv2.putText(
            image, code_text, (10, 58),
            font, 0.6, cyan, 2, cv2.LINE_AA,
        )
        # Action label to the right of the code.
        action_text = action_label or '--'
        action_color = yellow if action_label else gray
        cv2.putText(
            image, f' -> {action_text}', (200, 58),
            font, 0.6, action_color, 2, cv2.LINE_AA,
        )

    # --- Row 2: control mode --------------------------------------------
    mode_text = _mode_display(control_mode)
    mode_color = _mode_color(control_mode)
    cv2.putText(
        image, mode_text, (10, 82),
        font, 0.55, mode_color, 2, cv2.LINE_AA,
    )

    if finger_states is None:
        return

    # --- Row 3+: per-finger labels ------------------------------------
    for i, (name, extended) in enumerate(zip(_FINGER_NAMES, finger_states)):
        label = f'{name}: {"EXTENDED" if extended else "FLEXED"}'
        color = green if extended else gray
        y = 112 + i * 24
        cv2.putText(
            image, label, (10, y),
            font, 0.55, color, 2, cv2.LINE_AA,
        )

    # --- Movement / Rotation readout -----------------------------------
    if strafe is None and rotation is None:
        return

    sep_y = 112 + 5 * 24 + 8
    cv2.line(image, (10, sep_y), (330, sep_y), (80, 80, 80), 1, cv2.LINE_AA)

    y0 = sep_y + 18
    if strafe is not None:
        fb, lr = strafe
        # Direction arrows
        arrow_fb = _strafe_arrow(fb, vertical=True)
        arrow_lr = _strafe_arrow(lr, vertical=False)
        move_text = f'Move  {arrow_fb} F:{fb:+.2f}   {arrow_lr} L:{lr:+.2f}'
        cv2.putText(
            image, move_text, (10, y0),
            font, 0.5, white, 1, cv2.LINE_AA,
        )

    if rotation is not None:
        yaw, pitch = rotation
        arrow_yaw = _strafe_arrow(yaw / max(_ROTATION_SPEED, 0.01), vertical=False)
        arrow_pit = _strafe_arrow(pitch / max(_ROTATION_SPEED, 0.01), vertical=True)
        rot_text = f'Look  {arrow_yaw} Y:{yaw:+.2f}   {arrow_pit} P:{pitch:+.2f}'
        cv2.putText(
            image, rot_text, (10, y0 + 20),
            font, 0.5, white, 1, cv2.LINE_AA,
        )


def _mode_display(mode: Optional[str]) -> str:
    """Return a human-readable label for the control mode."""
    if mode == 'rotation':
        return 'Mode: ROTATION  (11000)'
    elif mode == 'movement':
        return 'Mode: MOVEMENT  (11001)'
    else:
        return 'Mode: --'


def _mode_color(mode: Optional[str]) -> Tuple[int, int, int]:
    """Return a BGR colour for the control mode label."""
    if mode == 'rotation':
        return (220, 200, 0)     # cyan-ish
    elif mode == 'movement':
        return (0, 160, 255)     # orange-ish
    else:
        return (128, 128, 128)   # grey


def _strafe_arrow(value: float, vertical: bool) -> str:
    """Return a single-character ASCII directional indicator for *value*."""
    threshold = 0.05
    if abs(value) < threshold:
        return '.'
    if vertical:
        return '^' if value > 0 else 'v'   # up / down
    else:
        return '>' if value > 0 else '<'   # right / left


def _draw_movement_overlay(
    image: np.ndarray,
    landmarks: Optional[List[Tuple[float, float, float]]],
    h: int,
    w: int,
    anchor_tip: Optional[Tuple[float, float]] = None,
    control_mode: Optional[str] = None,
) -> None:
    """Draw anchor, dead-zone, and index-fingertip displacement indicators.

    * Green circle — dead zone around the anchor (anti-jitter).
    * **Yellow** cross — anchor position (set when control gesture activates).
    * **Red** circle + arrow — current index-fingertip and displacement from anchor.
    * Arrow colour changes by mode: cyan for rotation, orange for movement.
    """
    if landmarks is None:
        return

    # --- Index fingertip (current position) -----------------------------
    ix = int(landmarks[_INDEX_TIP][0] * w)
    iy = int(landmarks[_INDEX_TIP][1] * h)

    # --- Anchor + dead zone ---------------------------------------------
    if anchor_tip is not None:
        ax = int(anchor_tip[0] * w)
        ay = int(anchor_tip[1] * h)

        # Dead-zone circle around anchor (green, semi-transparent).
        dz_r = int(w * _ANCHOR_DEAD_ZONE)
        overlay = image.copy()
        cv2.circle(overlay, (ax, ay), dz_r, (0, 200, 0), -1)
        cv2.addWeighted(overlay, 0.18, image, 0.82, 0, image)
        cv2.circle(image, (ax, ay), dz_r, (0, 200, 0), 1, cv2.LINE_AA)

        # Max-range circle around anchor (grey).
        mr_r = int(w * _ANCHOR_MAX_DELTA)
        cv2.circle(image, (ax, ay), mr_r, (100, 100, 100), 1, cv2.LINE_AA)

        # Arrow from anchor to current fingertip.
        arrow_color = (255, 200, 0)  # cyan for rotation
        if control_mode == 'movement':
            arrow_color = (0, 140, 255)  # orange for movement
        cv2.arrowedLine(
            image, (ax, ay), (ix, iy), arrow_color,
            2, cv2.LINE_AA, tipLength=0.15,
        )

        # Anchor cross (yellow).
        ch = 10
        cv2.line(image, (ax - ch, ay), (ax + ch, ay), (0, 220, 220), 2, cv2.LINE_AA)
        cv2.line(image, (ax, ay - ch), (ax, ay + ch), (0, 220, 220), 2, cv2.LINE_AA)
        cv2.circle(image, (ax, ay), 5, (0, 220, 220), -1)

    # --- Current fingertip dot (red) ------------------------------------
    cv2.circle(image, (ix, iy), 7, (0, 60, 220), -1)
    cv2.circle(image, (ix, iy), 7, (255, 255, 255), 2, cv2.LINE_AA)

    # --- Frame centre crosshair (white, faint) --------------------------
    cx, cy = int(w * 0.5), int(h * 0.5)
    ch = 12
    cv2.line(image, (cx - ch, cy), (cx + ch, cy), (180, 180, 180), 1, cv2.LINE_AA)
    cv2.line(image, (cx, cy - ch), (cx, cy + ch), (180, 180, 180), 1, cv2.LINE_AA)


def _draw_dashed_line(
    image: np.ndarray,
    pt1: Tuple[int, int],
    pt2: Tuple[int, int],
    color: Tuple[int, int, int],
    thickness: int = 1,
    gap: int = 6,
) -> None:
    """Draw a dashed line from *pt1* to *pt2*."""
    import math
    dx, dy = pt2[0] - pt1[0], pt2[1] - pt1[1]
    length = math.hypot(dx, dy)
    if length < 2:
        return
    ux, uy = dx / length, dy / length
    step = 0.0
    draw = True
    dash_len = gap
    while step < length:
        seg_end = min(step + dash_len, length)
        if draw:
            cv2.line(
                image,
                (int(pt1[0] + ux * step), int(pt1[1] + uy * step)),
                (int(pt1[0] + ux * seg_end), int(pt1[1] + uy * seg_end)),
                color, thickness, cv2.LINE_AA,
            )
        step = seg_end
        draw = not draw


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

    # Gesture-gated control state (mirrors GestureController logic).
    control_mode: Optional[str] = None       # 'rotation' | 'movement' | None
    anchor_tip: Optional[Tuple[float, float]] = None  # (x, y) in [0, 1]

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

            # Compute finger-code string and look up the mapped action.
            code_str = ''
            action_label = ''
            strafe = None
            rotation = None
            first_landmarks = None

            if result.finger_states is not None and result.hand_landmarks:
                first_landmarks = result.hand_landmarks[0]

                code_tuple = tuple(
                    1 if ext else 0 for ext in result.finger_states
                )
                code_str = ''.join(str(d) for d in code_tuple)
                raw_action = _ACTION_MAP.get(code_tuple)
                if raw_action is not None:
                    action_label = _ACTION_DISPLAY.get(raw_action, raw_action)

                # --- Gesture-gated movement / rotation -------------------
                dz = GestureController._apply_dead_zone
                if code_tuple == _ROTATION_GESTURE_CODE:
                    if control_mode != 'rotation':
                        control_mode = 'rotation'
                        anchor_tip = (
                            first_landmarks[_INDEX_TIP][0],
                            first_landmarks[_INDEX_TIP][1],
                        )
                    # Rotation from anchor-delta (dominant axis only).
                    if anchor_tip is not None:
                        ix, iy, _ = first_landmarks[_INDEX_TIP]
                        raw_yaw = dz(anchor_tip[0] - ix) * _ROTATION_SPEED
                        raw_pitch = dz(anchor_tip[1] - iy) * _ROTATION_SPEED
                        if abs(raw_yaw) >= abs(raw_pitch):
                            rotation = (raw_yaw, 0.0)
                        else:
                            rotation = (0.0, raw_pitch)
                elif code_tuple == _MOVEMENT_GESTURE_CODE:
                    if control_mode != 'movement':
                        control_mode = 'movement'
                        anchor_tip = (
                            first_landmarks[_INDEX_TIP][0],
                            first_landmarks[_INDEX_TIP][1],
                        )
                    # Strafing from anchor-delta (dominant axis only).
                    if anchor_tip is not None:
                        ix, iy, _ = first_landmarks[_INDEX_TIP]
                        raw_lr = dz(anchor_tip[0] - ix)
                        raw_fb = dz(anchor_tip[1] - iy)
                        if abs(raw_fb) >= abs(raw_lr):
                            strafe = (raw_fb, 0.0)
                        else:
                            strafe = (0.0, raw_lr)
                else:
                    control_mode = None
                    anchor_tip = None
            else:
                control_mode = None
                anchor_tip = None

            # Draw movement overlay (anchor + fingertip displacement).
            _draw_movement_overlay(frame, first_landmarks, h, w,
                                   anchor_tip=anchor_tip,
                                   control_mode=control_mode)

            # HUD overlay.
            _draw_hud(frame, result.gesture, result.finger_states,
                      finger_code=code_str, action_label=action_label,
                      strafe=strafe, rotation=rotation,
                      control_mode=control_mode)

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
