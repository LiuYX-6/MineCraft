"""Voice-based game controller.

Captures microphone audio and maps recognised Chinese speech to in-game
actions.  Microphone capture + speech recognition run in a **background
daemon thread**; the main game thread consumes the recognised commands
once per tick via the standard :class:`PlayerController` interface.

Backends
--------
+----------+--------+----------+-----------------------------------------+
| Backend  | Offline| Chinese  | Notes                                   |
+==========+========+==========+=========================================+
| ``vosk`` | ✓      | ✓        | **Default**.  Model auto-downloaded.    |
+----------+--------+----------+-----------------------------------------+
| ``google``| ✗     | ✓        | Needs VPN in China.                     |
+----------+--------+----------+-----------------------------------------+

Command vocabulary
------------------
Discrete actions (edge-triggered, consumed once per utterance):

==============  ===================================
Voice command    Action
==============  ===================================
"放置" / "放置方块"  ``place_block``
"破坏" / "破坏方块"  ``break_block``
"选取" / "选取方块"  ``pick_block``
"跳跃" / "跳"      ``jump``
"飞行" / "切换飞行"  ``fly_toggle``
"切换" / "下一个"   ``slot_next`` (cycle hotbar)
"退出" / "关闭"     ``escape``
==============  ===================================

Continuous control (toggle-based, persists until "停"):

==============  ===================
Voice command    Effect
==============  ===================
"前进"           Move forward
"后退"           Move backward
"向左走"         Strafe left
"向左转"         Turn left (yaw)
"向右转"         Turn right (yaw)
"停" / "停止"     Stop all movement / rotation
==============  ===================

Multi-modal fusion
------------------
Voice commands coexist with other controllers (keyboard/mouse, gesture).
In ``GameWindow.update()`` all three channels are merged, enabling natural
interaction patterns::

    Gesture (movement) + Voice (actions) = walk while speaking
    Gesture (selection) + Voice (confirm) = look at block + "place"

Dependencies
------------
- ``vosk`` (``pip install vosk``) — offline Chinese recognition (default)
- ``pyaudio`` (``pip install pyaudio``) — microphone access
- ``SpeechRecognition`` (``pip install SpeechRecognition``) — for Google backend
"""

import json
import logging
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from mc.controllers import PlayerController

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------
try:
    import vosk as _vosk
    _VOSK_AVAILABLE = True
except ImportError:
    _VOSK_AVAILABLE = False
    _vosk = None  # type: ignore[assignment]

try:
    import speech_recognition as _sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False
    _sr = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Vosk model auto-download
# ---------------------------------------------------------------------------
_VOSK_MODEL_URL = (
    'https://alphacephei.com/vosk/models/'
    'vosk-model-small-cn-0.22.zip'
)
_VOICE_CACHE_DIR = Path.home() / '.cache' / 'mc_voice'
_VOSK_MODEL_DIR = _VOICE_CACHE_DIR / 'vosk-model-small-cn-0.22'


def _ensure_vosk_model() -> Path:
    """Download the Vosk small Chinese model if not cached."""
    _VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if _VOSK_MODEL_DIR.exists() and any(_VOSK_MODEL_DIR.iterdir()):
        return _VOSK_MODEL_DIR

    logger.info('Downloading Vosk Chinese model (≈42 MB) …')
    zip_path = _VOICE_CACHE_DIR / 'vosk-model-small-cn-0.22.zip'
    try:
        urllib.request.urlretrieve(_VOSK_MODEL_URL, zip_path)
    except Exception as exc:
        if zip_path.exists():
            zip_path.unlink()
        raise RuntimeError(
            f'Failed to download Vosk model from {_VOSK_MODEL_URL}: {exc}. '
            f'Please download it manually and extract to {_VOSK_MODEL_DIR}.'
        )

    logger.info('Extracting Vosk model …')
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(_VOICE_CACHE_DIR)
    zip_path.unlink()
    logger.info('Vosk model ready at %s', _VOSK_MODEL_DIR)
    return _VOSK_MODEL_DIR


# ---------------------------------------------------------------------------
# Voice-command → action mapping (Chinese → internal action name)
# ---------------------------------------------------------------------------

# Discrete (edge-triggered) commands — fired once per utterance.
_DISCRETE_COMMANDS: Dict[str, str] = {
    # Block manipulation
    '放置': 'place_block',
    '放置方块': 'place_block',
    '放': 'place_block',
    '破坏': 'break_block',
    '破坏方块': 'break_block',
    '拆': 'break_block',
    '选取': 'pick_block',
    '选取方块': 'pick_block',
    '选择': 'pick_block',
    '选择方块': 'pick_block',
    # Movement / mode
    '跳跃': 'jump',
    '跳': 'jump',
    '飞行': 'fly_toggle',
    '切换飞行': 'fly_toggle',
    '飞行模式': 'fly_toggle',
    # Inventory
    '切换': 'slot_next',
    '下一个': 'slot_next',
    '下一个方块': 'slot_next',
    # System
    '退出': 'escape',
    '关闭': 'escape',
    # Slot selection (explicit)
    '方块一': 'slot_0',
    '方块二': 'slot_1',
    '方块三': 'slot_2',
}

# Continuous (toggle-based) movement commands.
# Mapping: command → (forward_back, left_right)
_MOVEMENT_COMMANDS: Dict[str, Tuple[float, float]] = {
    '前进': (0.0, -1.0),
    '后退': (0.0, 1.0),
    '向左跑': (1.0, 0.0),
    '向右跑': (-1.0, 0.0),
}
_MOVEMENT_ALIASES: Dict[str, str] = {
    '向前': '前进',
    '往后': '后退',
    '向左': '向左跑',
    '向右': '向右跑',
}

# Continuous rotation commands (toggle-based).
# Mapping: command → yaw_delta in degrees per tick.
_ROTATION_COMMANDS: Dict[str, float] = {
    '向左转': -1.0,
    '向右转': 1.0,
}
_ROTATION_ALIASES: Dict[str, str] = {
    '右转': '向右转',
    '左转': '向左转',
}

_STOP_COMMANDS = {'停', '停止', '停下', '停住'}

# ---------------------------------------------------------------------------
# Recognition tuning
# ---------------------------------------------------------------------------

_PHRASE_TIMEOUT = 5.0          # seconds to wait for a phrase before restarting
_DEFAULT_LANGUAGE = 'zh-CN'
_HUD_HISTORY_SIZE = 3

# ---------------------------------------------------------------------------
# Backend enum
# ---------------------------------------------------------------------------
_BACKEND_VOSK = 'vosk'
_BACKEND_GOOGLE = 'google'


class VoiceController(PlayerController):
    """Speech-recognition input controller.

    Captures microphone audio in a background thread, recognises Chinese
    speech, and maps recognised phrases to in-game actions.  Continuous
    movement is toggle-based: saying "前进" starts moving forward; saying
    "停" stops.

    Parameters
    ----------
    backend : str
        Recognition backend: ``'vosk'`` (default, offline) or ``'google'``.
    model_path : str or None
        Path to Vosk model directory.  Auto-downloaded if ``None``.
    language : str
        Language code for the Google backend (default ``'zh-CN'``).
    """

    def __init__(
        self,
        backend: str = _BACKEND_VOSK,
        model_path: Optional[str] = None,
        language: str = _DEFAULT_LANGUAGE,
    ):
        self._backend = backend
        self._model_path = model_path
        self._language = language

        # --- Movement / rotation state (toggle-based) ---
        self._strafe = [0.0, 0.0]
        self._dyaw = 0.0
        self._dpitch = 0.0
        self._movement_active = False

        # --- Discrete action queue (thread-safe) ---
        self._actions: Set[str] = set()
        self._action_lock = threading.Lock()

        # --- Background thread state ---
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # --- HUD display state (written by bg thread, read by main thread) ---
        self._hud_lock = threading.Lock()
        self._last_command: str = ''
        self._command_history: list = []
        self._listening: bool = False
        self._status_text: str = '语音: 未启动'

        # --- Slot tracking for slot_next ---
        self._current_slot = 0

    # ------------------------------------------------------------------
    # Background listening — dispatcher
    # ------------------------------------------------------------------

    def _listen_loop(self) -> None:
        """Entry point: choose backend and enter the listen loop."""
        if self._backend == _BACKEND_VOSK:
            self._listen_vosk()
        elif self._backend == _BACKEND_GOOGLE:
            self._listen_google()
        else:
            self._set_status(f'语音: 未知后端 "{self._backend}"')
            self._running = False

    # ------------------------------------------------------------------
    # Vosk backend (offline)
    # ------------------------------------------------------------------

    def _listen_vosk(self) -> None:
        """Vosk offline recognition loop."""
        if not _VOSK_AVAILABLE:
            self._set_status('语音: vosk 未安装 (pip install vosk)')
            self._running = False
            return

        try:
            import pyaudio
        except ImportError:
            self._set_status('语音: pyaudio 未安装 (pip install pyaudio)')
            self._running = False
            return

        # Resolve / download model.
        try:
            if self._model_path:
                model_dir = Path(self._model_path)
            else:
                model_dir = _ensure_vosk_model()
        except Exception as exc:
            self._set_status(f'语音: 模型加载失败 ({exc})')
            logger.error('Failed to load Vosk model: %s', exc)
            self._running = False
            return

        # Initialise Vosk model and recognizer.
        try:
            model = _vosk.Model(str(model_dir))
            recognizer = _vosk.KaldiRecognizer(model, 16000)
            # Enable partial results for faster feedback.
            recognizer.SetWords(True)
        except Exception as exc:
            self._set_status(f'语音: 识别器初始化失败 ({exc})')
            logger.error('Failed to init Vosk recognizer: %s', exc)
            self._running = False
            return

        # Open microphone.
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=4000,
            )
        except Exception as exc:
            self._set_status(f'语音: 麦克风不可用 ({exc})')
            logger.error('Failed to open microphone: %s', exc)
            self._running = False
            return

        self._set_status('语音: 已就绪 [Vosk 离线] (说出指令)')
        stream.start_stream()

        while self._running:
            try:
                self._set_listening(True)
                data = stream.read(4000, exception_on_overflow=False)
                self._set_listening(False)
            except Exception as exc:
                logger.error('Microphone read error: %s', exc)
                self._set_listening(False)
                continue

            if recognizer.AcceptWaveform(data):
                result_json = recognizer.Result()
                try:
                    result = json.loads(result_json)
                except json.JSONDecodeError:
                    continue
                text = result.get('text', '').strip()
                if text:
                    # Vosk may return space-separated characters or words.
                    # Remove spaces for Chinese text.
                    text_compact = text.replace(' ', '')
                    logger.info('Vosk recognised: "%s"', text_compact)
                    self._record_command(text_compact)
                    self._dispatch(text_compact)
            else:
                # Partial result — update status but don't dispatch.
                partial_json = recognizer.PartialResult()
                try:
                    partial = json.loads(partial_json)
                    partial_text = partial.get('partial', '').strip()
                    if partial_text:
                        partial_compact = partial_text.replace(' ', '')
                        self._set_status(f'语音: 听到 … "{partial_compact}"')
                except json.JSONDecodeError:
                    pass

        stream.stop_stream()
        stream.close()
        pa.terminate()

    # ------------------------------------------------------------------
    # Google backend (online, needs VPN in China)
    # ------------------------------------------------------------------

    def _listen_google(self) -> None:
        """Google Speech Recognition loop (via speech_recognition)."""
        if not _SR_AVAILABLE:
            self._set_status('语音: SpeechRecognition 未安装')
            self._running = False
            return

        try:
            import pyaudio  # noqa: F811
        except ImportError:
            self._set_status('语音: pyaudio 未安装')
            self._running = False
            return

        try:
            rec = _sr.Recognizer()
            rec.energy_threshold = 300
            rec.dynamic_energy_threshold = True
            rec.dynamic_energy_adjustment_damping = 0.15
            rec.dynamic_energy_adjustment_ratio = 1.5
        except Exception as exc:
            self._set_status(f'语音: 识别器初始化失败 ({exc})')
            self._running = False
            return

        try:
            microphone = _sr.Microphone()
            with microphone as source:
                self._set_status('语音: 校准中 …')
                rec.adjust_for_ambient_noise(source, duration=1.0)
        except Exception as exc:
            self._set_status(f'语音: 麦克风不可用 ({exc})')
            self._running = False
            return

        self._set_status('语音: 已就绪 [Google] (说出指令)')

        while self._running:
            try:
                self._set_listening(True)
                with microphone as source:
                    audio = rec.listen(
                        source,
                        timeout=_PHRASE_TIMEOUT,
                        phrase_time_limit=_PHRASE_TIMEOUT,
                    )
                self._set_listening(False)

                try:
                    text = rec.recognize_google(audio, language=self._language)
                except _sr.UnknownValueError:
                    continue
                except _sr.RequestError as exc:
                    self._set_status(f'语音: 识别服务不可用 ({exc})')
                    continue

                if not text:
                    continue

                text = text.strip()
                logger.info('Google recognised: "%s"', text)
                self._record_command(text)
                self._dispatch(text)

            except _sr.WaitTimeoutError:
                self._set_listening(False)
                continue
            except Exception as exc:
                logger.error('Unexpected error in listen loop: %s', exc)
                self._set_listening(False)
                continue

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, text: str) -> None:
        """Map recognised *text* to an action and push to the queue."""
        # 1. Check discrete commands (exact match).
        action = _DISCRETE_COMMANDS.get(text)
        if action is not None:
            if action == 'slot_next':
                self._current_slot = (self._current_slot + 1) % 3
                action = f'slot_{self._current_slot}'
            self._push_action(action)
            self._set_status(f'语音: "{text}" → {action}')
            return

        # 2. Check stop commands.
        if text in _STOP_COMMANDS:
            self._stop_movement()
            self._stop_rotation()
            self._set_status(f'语音: "{text}" → 停止')
            return

        # 3. Check movement commands.
        mv_cmd = _MOVEMENT_ALIASES.get(text, text)
        strafe = _MOVEMENT_COMMANDS.get(mv_cmd)
        if strafe is not None:
            self._stop_rotation()
            self._strafe = [strafe[0], strafe[1]]
            self._movement_active = True
            self._set_status(
                f'语音: "{text}" → 移动 ({strafe[0]:+.0f}, {strafe[1]:+.0f})'
            )
            return

        # 4. Check rotation commands.
        rot_cmd = _ROTATION_ALIASES.get(text, text)
        yaw = _ROTATION_COMMANDS.get(rot_cmd)
        if yaw is not None:
            self._stop_movement()
            self._dyaw = yaw
            self._set_status(f'语音: "{text}" → 旋转 ({yaw:+.0f}°/tick)')
            return

        # 5. Unrecognised.
        self._set_status(f'语音: 未知指令 "{text}"')

    def _stop_movement(self) -> None:
        """Stop all continuous movement."""
        self._strafe = [0.0, 0.0]
        self._movement_active = False

    def _stop_rotation(self) -> None:
        """Stop continuous rotation."""
        self._dyaw = 0.0
        self._dpitch = 0.0

    def _push_action(self, action: str) -> None:
        """Thread-safe push of a discrete action."""
        with self._action_lock:
            self._actions.add(action)

    # ------------------------------------------------------------------
    # HUD helpers (thread-safe)
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        with self._hud_lock:
            self._status_text = text

    def _set_listening(self, value: bool) -> None:
        with self._hud_lock:
            self._listening = value

    def _record_command(self, text: str) -> None:
        with self._hud_lock:
            self._last_command = text
            self._command_history.append(text)
            if len(self._command_history) > _HUD_HISTORY_SIZE:
                self._command_history.pop(0)

    # ------------------------------------------------------------------
    # PlayerController interface
    # ------------------------------------------------------------------

    def get_strafe(self) -> Tuple[float, float]:
        return (self._strafe[0], self._strafe[1])

    def get_rotation_delta(self) -> Tuple[float, float]:
        return (self._dyaw, self._dpitch)

    def poll_actions(self) -> Set[str]:
        with self._action_lock:
            actions = self._actions
            self._actions = set()
            return actions

    def activate(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True,
        )
        self._thread.start()

    def deactivate(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def update(self, dt: float) -> None:
        pass

    # ------------------------------------------------------------------
    # HUD display properties
    # ------------------------------------------------------------------

    @property
    def status_text(self) -> str:
        with self._hud_lock:
            return self._status_text

    @property
    def last_command(self) -> str:
        with self._hud_lock:
            return self._last_command

    @property
    def command_history(self) -> list:
        with self._hud_lock:
            return list(self._command_history)

    @property
    def is_listening(self) -> bool:
        with self._hud_lock:
            return self._listening

    # ------------------------------------------------------------------
    # Compatibility
    # ------------------------------------------------------------------

    @property
    def exclusive(self) -> bool:
        return True
