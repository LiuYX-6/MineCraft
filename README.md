# Minecraft — Multi-modal Voxel Game

A Minecraft-inspired voxel world built with Python and Pyglet, featuring
**multi-modal input**: play with keyboard & mouse, hand gestures (camera-based),
and Chinese voice commands — all simultaneously.

基于 Pyglet 的体素沙盒游戏，支持**键盘鼠标、手势识别、中文语音**三种输入方式并行控制。

> Forked from [fogleman/Minecraft](https://github.com/fogleman/Minecraft) and
> significantly extended with a pluggable controller architecture, gesture
> recognition (MediaPipe), and speech recognition (Vosk/Google).

---

## Features

- **Voxel world** with block placing, breaking, picking, and terrain generation
- **First-person camera** with collision detection and physics (gravity, jumping, flying)
- **Three input modes** that work together seamlessly:
  - 🖱️ **Keyboard + Mouse** — traditional WASD movement, click to build/destroy
  - ✋ **Hand Gesture** — camera-based finger tracking (MediaPipe), gesture-gated movement & rotation
  - 🎤 **Voice** — offline Chinese speech recognition (Vosk), toggle-based continuous control
- **Pluggable controller architecture** — all input sources implement a common `PlayerController` interface
- **In-game HUD** showing FPS, position, gesture state, voice status, and a crosshair reticle
- **Sector-based rendering** for efficient large-world display
- **Focus-block wireframe highlight** on the block under the crosshairs
- **Gesture overlay widget** — bottom-right visual indicator for anchor-relative fingertip control

---

## Architecture

```
mc/
├── __init__.py          # Package entry, public API exports
├── __main__.py          # python -m mc entry point
├── config.py            # Physics constants, sector size, texture path
├── blocks.py            # Block texture coordinate definitions
├── player.py            # Player state, physics, collision (pure logic)
├── world.py             # World model, block storage, ray-casting, sector culling
├── terrain.py           # Terrain generation (FlatWorldGenerator + ABC)
├── shaders.py           # OpenGL shader programs (block + line)
├── utils.py             # Geometry helpers, sectorization, normalization
├── window.py            # Game window — thin coordination layer + HUD rendering
└── controllers/
    ├── __init__.py          # PlayerController abstract interface
    ├── keyboard_mouse.py    # WASD + mouse look + click actions
    ├── gesture.py           # Hand-gesture controller (MediaPipe)
    ├── gesture_detector.py  # Hand landmark detection core library
    ├── gesture_demo.py      # Standalone gesture detection demo
    └── voice.py             # Voice controller (Vosk / Google)
```

### Design

All input controllers implement the abstract `PlayerController` interface defined in
[mc/controllers/__init__.py](mc/controllers/__init__.py):

- `get_strafe()` → `(forward_back, left_right)` in `[-1, 1]`
- `get_rotation_delta()` → `(yaw_delta, pitch_delta)` in degrees
- `poll_actions()` → set of discrete action strings (consumed on read)
- `activate()` / `deactivate()` — lifecycle management
- `update(dt)` — per-frame update

`GameWindow` is fully decoupled from the concrete input source — it merges
strafe, rotation, and actions from all active controllers each frame.

---

## Installation

### Requirements

- **Python** ≥ 3.10
- **Pyglet** ≥ 2.0 — rendering and windowing

#### Optional: Gesture control

```bash
pip install opencv-python mediapipe
```

#### Optional: Voice control (Vosk — offline, recommended)

```bash
pip install vosk pyaudio
```

The Vosk small Chinese model (~42 MB) is auto-downloaded on first run to
`~/.cache/mc_voice/`.

#### Optional: Voice control (Google — online)

```bash
pip install SpeechRecognition pyaudio
```

> Google backend requires network access and may need a VPN inside China.

### Quick start

```bash
# Clone and enter the project
git clone <repo-url>
cd Minecraft

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/Scripts/activate   # Windows
# or: source .venv/bin/activate  # macOS / Linux

# Install core dependency
pip install pyglet

# Run (keyboard + mouse only)
python -m mc
```

---

## How to Run

```bash
# Keyboard + Mouse only (default)
python -m mc

# Keyboard + Mouse + Gesture
python -m mc --gesture

# Keyboard + Mouse + Voice
python -m mc --voice

# All three input modes
python -m mc --gesture --voice
```

The `--gesture` and `--voice` flags enable those controllers **in addition**
to the default keyboard + mouse controller. All active input sources work
simultaneously — their outputs are merged each frame.

---

## How to Play

### Keyboard + Mouse

| Key / Action          | Effect                           |
|-----------------------|----------------------------------|
| `W` / `A` / `S` / `D` | Move forward / left / back / right |
| Mouse move            | Look around                      |
| `Space`               | Jump                             |
| `Tab`                 | Toggle flying mode               |
| Left-click            | Break block                      |
| Right-click           | Place block                      |
| Middle-click          | Pick focused block type          |
| `Ctrl` + Left-click   | Place block (alternative)        |
| `1` / `2` / `3`      | Select hotbar slot               |
| `Esc`                 | Release mouse / exit             |

### Hand Gesture (requires camera)

When launched with `--gesture`, a camera preview window opens showing hand
landmarks. Finger states are encoded as a 5-bit code (thumb→pinky, `1`=extended, `0`=flexed):

| Gesture Code | Action                     |
|-------------|----------------------------|
| `10111`     | Place block (放置方块)       |
| `11011`     | Break block (破坏方块)       |
| `10000`     | Pick block type (选取方块)   |
| `11000`     | **Rotation mode** — drag index fingertip to look around |
| `11001`     | **Movement mode** — drag index fingertip to walk |

**Anchor-relative control:** When you enter rotation (`11000`) or movement
(`11001`) mode, the current index-fingertip position becomes the *anchor*.
Dragging away from the anchor controls the camera or movement direction.
A dead zone around the anchor prevents jitter. Release the gesture to stop.

A visual widget in the bottom-right corner of the game window shows the
current anchor, fingertip displacement, and dead zone.

### Voice Commands (Chinese)

When launched with `--voice`, the game listens for Chinese speech via
microphone.

**Discrete commands** (fire once per utterance):

| Command              | Action           |
|----------------------|------------------|
| "放置" / "放置方块"    | Place block      |
| "破坏" / "破坏方块"    | Break block      |
| "选取" / "选取方块"    | Pick block type  |
| "跳跃" / "跳"         | Jump             |
| "飞行" / "切换飞行"    | Toggle flying    |
| "切换" / "下一个"      | Next hotbar slot |
| "退出" / "关闭"       | Exit game        |

**Continuous commands** (toggle-based, persists until "停"):

| Command   | Effect           |
|-----------|------------------|
| "前进"    | Move forward     |
| "后退"    | Move backward    |
| "向左走"  | Strafe left      |
| "向右走"  | Strafe right     |
| "向左转"  | Turn left        |
| "向右转"  | Turn right       |
| "停" / "停止" | Stop all movement / rotation |

---

## Multi-modal Fusion

All three input sources are active simultaneously. Their outputs are merged
in `GameWindow.update()`:

- **Strafe** values from keyboard, gesture, and voice are **added** together
- **Rotation** deltas from mouse, gesture, and voice are **added** together
- **Discrete actions** from all sources are **union-ed** into one action set

This enables natural interaction patterns like:

- Gesture movement + Voice actions = walk while speaking commands
- Keyboard look + Gesture block placement = aim with mouse, build with hand
- All three together = full multi-modal control

---

## Configuration

Key parameters in [mc/config.py](mc/config.py):

| Parameter          | Default | Description                  |
|--------------------|---------|------------------------------|
| `TICKS_PER_SEC`    | 60      | Game loop tick rate          |
| `WALKING_SPEED`    | 5       | Walking speed (blocks/sec)   |
| `FLYING_SPEED`     | 15      | Flying speed (blocks/sec)    |
| `GRAVITY`          | 20.0    | Gravity acceleration         |
| `MAX_JUMP_HEIGHT`  | 1.0     | Jump height (blocks)         |
| `SECTOR_SIZE`      | 16      | Sector size for render culling |
| `TEXTURE_PATH`     | `texture.png` | Block texture atlas     |

---

## Credits

- Original project: [fogleman/Minecraft](https://github.com/fogleman/Minecraft)
- Hand gesture recognition: [MediaPipe](https://developers.google.com/mediapipe) by Google
- Voice recognition: [Vosk](https://alphacephei.com/vosk/) (offline) / Google Speech Recognition
- Built with [Pyglet](https://pyglet.org/)
