# Minecraft 项目重构方案

## Context

当前项目所有代码（~900 行）集中在单个 `main.py` 中，包含：世界模型、地形生成、玩家物理、碰撞检测、OpenGL 渲染、输入处理、HUD 等多重职责。

**关键需求**：后续需要将玩家操作从键鼠切换为基于摄像头的手势操作。因此必须在重构时将「输入控制」从窗口/玩家逻辑中彻底解耦，抽象为统一的 `PlayerController` 接口，让键鼠和手势分别实现同一接口。

## 目标包结构

```
Minecraft/
├── main.py                  # 兼容入口：from mc import run; run()
├── mc/                      # 核心包
│   ├── __init__.py          # 公开 API
│   ├── __main__.py          # python -m mc 入口
│   ├── config.py            # 所有可配置常量
│   ├── utils.py             # 通用工具函数
│   ├── blocks.py            # 方块类型定义
│   ├── world.py             # World 类（世界数据管理）
│   ├── terrain.py           # 地形生成策略
│   ├── player.py            # 玩家状态、物理、碰撞
│   ├── controllers/         # 输入控制抽象层
│   │   ├── __init__.py      # PlayerController 抽象基类
│   │   ├── keyboard_mouse.py  # 键鼠实现
│   │   └── gesture.py       # 摄像头手势实现（骨架，后续开发）
│   └── window.py            # 窗口/渲染/游戏循环（薄协调层）
├── texture.png
└── README.md
```

## 各模块详细内容

### 1. `mc/config.py` — 配置常量
**来源**: main.py 第 14–37 行

```python
TICKS_PER_SEC = 60
SECTOR_SIZE = 16
WALKING_SPEED = 5
FLYING_SPEED = 15
GRAVITY = 20.0
MAX_JUMP_HEIGHT = 1.0
JUMP_SPEED = math.sqrt(2 * GRAVITY * MAX_JUMP_HEIGHT)
TERMINAL_VELOCITY = 50
PLAYER_HEIGHT = 2
TEXTURE_PATH = 'texture.png'
```

可用一个 dataclass 封装为 `GameConfig`，允许用户实例化时覆盖默认值。

### 2. `mc/utils.py` — 工具函数
**来源**: main.py 第 39–51, 84–127 行

- `cube_vertices(x, y, z, n)` — 生成立方体顶点数据
- `tex_coord(x, y, n=4)` — 单面纹理坐标
- `tex_coords(top, bottom, side)` — 方块六面纹理坐标
- `normalize(position)` — 浮点坐标→方块整数坐标
- `sectorize(position)` — 坐标→扇区坐标
- `FACES` — 六个方向的单位向量列表

### 3. `mc/blocks.py` — 方块定义
**来源**: main.py 第 78–82 行

- `GRASS`, `SAND`, `BRICK`, `STONE` 预计算纹理坐标
- 后续可扩展为方块类型枚举

### 4. `mc/world.py` — 世界模型（`Model` → `World`）
**来源**: main.py 第 129–431 行

公有接口：
- `__init__(terrain_generator)` — 接受地形生成器，不再硬编码地形
- `add_block(position, texture, immediate)` / `remove_block(position, immediate)`
- `hit_test(position, vector, max_distance)` — 射线检测
- `exposed(position)` — 方块是否暴露
- `process_queue()` / `process_entire_queue()` — 渲染队列处理
- `show_sector(sector)` / `hide_sector(sector)` / `change_sectors(before, after)`
- 属性：`batch`, `world`, `shown`

地形生成逻辑（原 `_initialize`）全部移出，由外部 `TerrainGenerator` 注入。

### 5. `mc/terrain.py` — 地形生成（新增模块）
**来源**: main.py 第 158–193 行

```python
class TerrainGenerator(ABC):
    """地形生成器基类"""
    @abstractmethod
    def generate(self, world: 'World') -> None:
        """向 world 填充方块"""
        ...

class FlatWorldGenerator(TerrainGenerator):
    """默认实现：平坦地面 + 随机丘陵 + 围墙"""
    def generate(self, world):
        # 原 Model._initialize 的逻辑
        ...
```

### 6. `mc/controllers/__init__.py` — 输入控制器抽象层
**核心新增模块**

```python
class PlayerController(ABC):
    """玩家输入控制的抽象接口。
    
    所有输入源（键鼠、手势、手柄等）都实现此接口。
    GameWindow 仅依赖此接口，不感知具体输入来源。
    """

    @abstractmethod
    def get_strafe(self) -> Tuple[float, float]:
        """返回 (前后, 左右)，各分量范围 [-1, 1]。
        前后: 正=前进, 负=后退
        左右: 正=右移, 负=左移
        """
        ...

    @abstractmethod
    def get_rotation_delta(self) -> Tuple[float, float]:
        """返回本帧的 (水平旋转增量, 垂直旋转增量)，单位：度"""
        ...

    @abstractmethod
    def poll_actions(self) -> Set[str]:
        """返回本帧触发的动作集合，消费后即清空。
        
        支持的动作:
        - 'jump'          — 跳跃
        - 'fly_toggle'    — 切换飞行模式
        - 'place_block'   — 放置方块
        - 'break_block'   — 破坏方块
        - 'escape'        — 释放鼠标/退出
        - 'slot_0'~'slot_9' — 切换物品栏
        """
        ...

    @abstractmethod
    def activate(self) -> None:
        """激活/初始化控制器（如捕获鼠标、启动摄像头等）"""
        ...

    @abstractmethod
    def deactivate(self) -> None:
        """停用控制器"""
        ...

    @abstractmethod
    def update(self, dt: float) -> None:
        """每帧更新内部状态"""
        ...
```

### 7. `mc/controllers/keyboard_mouse.py` — 键鼠实现
**来源**: main.py 中分散在 Window 类的输入处理代码（第 658–759 行）

```python
class KeyboardMouseController(PlayerController):
    """基于键盘+鼠标的输入控制器。
    
    通过 pyglet 窗口事件驱动，内部维护按键状态表。
    GameWindow 将此控制器的回调注册到 pyglet 事件。
    """
    
    def __init__(self, window: pyglet.window.Window):
        self._strafe = [0, 0]           # 前后 / 左右
        self._rotation_delta = [0, 0]   # 本帧鼠标移动累计
        self._actions: Set[str] = set() # 本帧触发的动作
        self._key_state = {}             # 按键状态
        self._exclusive = False          # 鼠标捕获状态
        
        # 注册 pyglet 事件回调
        window.push_handlers(self)
    
    # --- pyglet 事件处理器 ---
    def on_key_press(self, symbol, modifiers): ...
    def on_key_release(self, symbol, modifiers): ...
    def on_mouse_press(self, x, y, button, modifiers): ...
    def on_mouse_motion(self, x, y, dx, dy): ...
    
    # --- PlayerController 接口实现 ---
    def get_strafe(self): ...
    def get_rotation_delta(self): ...
    def poll_actions(self): ...
```

### 8. `mc/controllers/gesture.py` — 手势实现（骨架）
**后续开发**

```python
class GestureController(PlayerController):
    """基于摄像头手势识别的输入控制器。
    
    依赖摄像头输入和手势识别库（如 MediaPipe）。
    实现与 KeyboardMouseController 完全相同的接口。
    """
    
    def __init__(self, camera_id=0):
        self._camera = None
        self._hand_tracker = None
        # ... 初始化摄像头和手势识别
    
    def get_strafe(self): ...       # 手部位置映射为移动方向
    def get_rotation_delta(self): ...  # 头部/手部朝向映射为视角
    def poll_actions(self): ...     # 特定手势映射为动作
    def activate(self): ...         # 启动摄像头
    def deactivate(self): ...       # 释放摄像头
    def update(self, dt): ...       # 读取摄像头帧、识别手势
```

### 9. `mc/player.py` — 玩家状态与物理
**来源**: main.py Window 类中与物理相关的部分（第 453–475, 505–656 行）

```python
class Player:
    """玩家纯逻辑类。不依赖任何输入源或渲染。
    
    所有外部输入通过方法参数传入，不直接读取键盘/鼠标。
    """
    
    def __init__(self, config=None):
        self.position = (0, 0, 0)
        self.rotation = (0, 0)       # (yaw, pitch) 度
        self.dy = 0.0                # 垂直速度
        self.flying = False
        self.height = PLAYER_HEIGHT
        self.inventory = [BRICK, GRASS, SAND]
        self.selected_block_index = 0
    
    @property
    def selected_block(self):
        return self.inventory[self.selected_block_index]
    
    def get_sight_vector(self) -> Tuple[float, float, float]:
        """从当前 rotation 计算视线方向"""
        ...
    
    def get_motion_vector(self, strafe, flying) -> Tuple[float, float, float]:
        """从 strafe 向量和 flying 状态计算移动方向"""
        ...
    
    def update_physics(self, dt, world, strafe, flying):
        """物理更新：位移、重力、碰撞。
        接受 strafe 和 flying 作为参数（由外部 controller 提供）。
        """
        ...
    
    def collide(self, position, height, world_blocks):
        """AABB 碰撞检测"""
        ...
```

关键变更：`Player` 的所有输入敏感方法（`get_motion_vector`、`update_physics`）将 `strafe`、`flying` 等作为**参数传入**，而非从 `self` 读取。这使得同一 `Player` 实例可以被任何 `PlayerController` 驱动。

### 10. `mc/window.py` — 窗口、渲染、游戏循环
**来源**: main.py Window 类渲染部分（第 436–449, 487–496, 761–852 行）+ OpenGL 设置（第 855–891 行）

```python
class GameWindow(pyglet.window.Window):
    """游戏主窗口 — 薄协调层。
    
    职责仅限于：OpenGL 渲染、游戏循环调度、协调各组件。
    """
    
    def __init__(self, *args, 
                 controller: PlayerController = None,
                 world: World = None,
                 player: Player = None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        
        # 组件组合
        self.controller = controller or KeyboardMouseController(self)
        self.world = world or World(FlatWorldGenerator())
        self.player = player or Player()
        
        # HUD 元素
        self.label = ...
        self.reticle = None
        
        pyglet.clock.schedule_interval(self.update, 1.0 / TICKS_PER_SEC)
    
    def update(self, dt):
        """游戏主循环"""
        # 1. 处理渲染队列
        self.world.process_queue()
        # 2. 扇区切换
        sector = sectorize(self.player.position)
        if sector != self.player.sector:
            self.world.change_sectors(...)
        # 3. 控制器更新
        self.controller.update(dt)
        # 4. 动作处理
        actions = self.controller.poll_actions()
        self._handle_actions(actions)
        # 5. 玩家物理
        self.player.update_physics(dt, self.world, 
                                   self.controller.get_strafe(),
                                   flying=self.player.flying)
        # 6. 相机旋转
        dy, dp = self.controller.get_rotation_delta()
        self.player.rotation = ...
    
    # --- 渲染 ---
    def set_2d(self): ...
    def set_3d(self): ...
    def on_draw(self): ...
    def draw_focused_block(self): ...
    def draw_label(self): ...
    def draw_reticle(self): ...

def setup_fog(): ...
def setup(): ...

def run():
    """便捷入口：创建窗口并运行"""
    window = GameWindow(width=800, height=600, caption='Minecraft', resizable=True)
    window.controller.activate()
    setup()
    pyglet.app.run()
```

### 11. `mc/__init__.py` — 公开 API
```python
from mc.config import GameConfig
from mc.world import World
from mc.terrain import TerrainGenerator, FlatWorldGenerator
from mc.player import Player
from mc.controllers import PlayerController, KeyboardMouseController, GestureController
from mc.window import GameWindow, run
from mc.blocks import GRASS, SAND, BRICK, STONE

__all__ = [
    'GameConfig', 'World', 'Player', 'GameWindow',
    'TerrainGenerator', 'FlatWorldGenerator',
    'PlayerController', 'KeyboardMouseController', 'GestureController',
    'GRASS', 'SAND', 'BRICK', 'STONE', 'run',
]
```

### 12. `mc/__main__.py`
```python
from mc.window import run
run()
```

### 13. `main.py`（根目录，向后兼容）
```python
"""向后兼容入口，推荐使用 python -m mc"""
from mc import run
if __name__ == '__main__':
    run()
```

## 模块依赖关系图

```
config          ← 无依赖
utils           ← 无依赖
blocks          ← utils
terrain         ← world (类型引用), blocks, utils    [生成器→世界]
world           ← blocks, utils, config
controllers/__init__  ← 无依赖
controllers/kb_mouse  ← controllers 基类
controllers/gesture   ← controllers 基类
player          ← world (碰撞查询), config
window          ← world, player, controllers, utils, config
```

**无循环依赖**。`terrain.py` 仅通过类型注解（`TYPE_CHECKING`）引用 `World`。

## 数据流设计（关键）

```
┌──────────────┐    每帧 poll_actions() + get_strafe() + get_rotation_delta()
│ Controller   │ ───────────────────────────────────────────────────────┐
│ (键鼠/手势)   │                                                        │
└──────────────┘                                                        ▼
                                                              ┌─────────────────┐
┌──────────────┐  position/rotation  ┌──────────┐  actions   │  GameWindow     │
│   Player     │ ◄────────────────── │ 协调层    │ ◄────────── │  (update 循环)  │
│ (纯逻辑)      │ ──────────────────► │          │ ──────────►│                 │
└──────────────┘  collision query    └──────────┘  渲染命令   └─────────────────┘
       │                                    │                         │
       │ update_physics(world)              │                         │
       ▼                                    ▼                         ▼
┌──────────────┐                   ┌──────────────┐          ┌──────────────┐
│    World     │                   │   OpenGL     │          │    HUD       │
│ (方块管理)    │                   │  (3D/2D)     │          │  (FPS/坐标)   │
└──────────────┘                   └──────────────┘          └──────────────┘
```

## 实现步骤（增量迁移）

### 第一步：创建包结构
- 创建 `mc/` 目录及 `mc/controllers/` 子目录，建立所有空模块文件

### 第二步：迁移底层模块（无依赖）
1. `config.py` — 移动所有常量；可选封装 `GameConfig` dataclass
2. `utils.py` — 移动 `cube_vertices`, `tex_coord`, `tex_coords`, `normalize`, `sectorize`, `FACES`
3. `blocks.py` — 移动 `GRASS`, `SAND`, `BRICK`, `STONE`, `TEXTURE_PATH`

### 第三步：迁移 World + Terrain
1. 将 `Model` 类移至 `world.py`，重命名为 `World`
2. 将 `_initialize()` 逻辑提取到 `terrain.py` 的 `FlatWorldGenerator`
3. `World.__init__` 接受 `terrain_generator` 参数调用 `generate(self)`

### 第四步：抽象 Controller 层
1. `controllers/__init__.py` — 定义 `PlayerController` 抽象基类
2. `controllers/keyboard_mouse.py` — 提取 Window 中的输入处理代码，实现接口
3. `controllers/gesture.py` — 创建骨架实现（方法体 `raise NotImplementedError`）

### 第五步：提取 Player
1. 从 Window 类提取玩家状态和物理 → `player.py` 的 `Player` 类
2. 关键：`get_motion_vector()` 和 `update_physics()` 将输入作为参数接受

### 第六步：重构 Window 为薄协调层
1. 移除 Window 中的玩家状态和输入处理代码
2. 组合 `Player` + `World` + `PlayerController` 实例
3. `update()` 方法改为调度各组件

### 第七步：组装 API 并验证
1. 编写 `__init__.py`、`__main__.py`、根 `main.py`
2. 运行功能测试
3. 验证可 import 为库使用

### 第八步：升级 pyglet（重构完成后再执行）

**说明**：重构完成后，所有 pyglet 调用集中在以下 2 个文件：

| 文件 | pyglet 相关调用 |
|---|---|
| `mc/window.py` | `Window`、`pyglet.graphics`、`pyglet.text.Label`、`pyglet.clock`、`pyglet.app`、OpenGL（gl/glu 函数）、`setup_fog` / `setup`、`image.load` |
| `mc/controllers/keyboard_mouse.py` | `pyglet.window.key`、`pyglet.window.mouse`、窗口事件处理器 |

其余模块（`config`、`utils`、`blocks`、`world`、`terrain`、`player`、`controllers/__init__`、`controllers/gesture`）**完全不依赖 pyglet**。

**升级改动范围**：
- 新增 pyglet 版本：`~=2.0` 或当前最新
- 适配 API 变化（如 `push_handlers` → `@window.event`、`pyglet.gl` 到 `pyglet.gl` 的变化等，具体视版本差异）
- 更新 `main.py` 顶部 `requirements.txt` 或依赖声明

**升级后验证**：
- 全部功能回归测试（同第七步的验证）
- 确认性能未退化（FPS 对比）

## pyglet 依赖边界总结

```
         ┌──────────────┐
         │   main.py    │  (无 pyglet)
         └──────┬───────┘
    ┌──────────┼──────────┐
    │  mc/ 包              │
    │                      │
    │  config.py    ✗      │
    │  utils.py     ✗      │
    │  blocks.py    ✗      │
    │  world.py     ✗      │
    │  terrain.py   ✗      │
    │  player.py    ✗      │
    │  controllers/        │
    │   ├─ __init__  ✗     │
    │   ├─ kb_mouse  ✓     │  ← 仅此子模块依赖 pyglet
    │   └─ gesture   ✗     │
    │  window.py    ✓      │  ← 依赖 pyglet
    └──────────────────────┘
```

## 验证方法

1. **功能回归测试**：
   - `python main.py` 启动游戏，验证：WASD 移动、空格跳跃、Tab 飞行、鼠标视角、左右键破坏/放置方块、数字键切换方块、ESC 释放鼠标
   - `python -m mc` 等效验证

2. **库导入测试**：
   ```python
   import mc
   w = mc.World(terrain_generator=mc.FlatWorldGenerator())
   p = mc.Player()
   print(len(w.world))  # > 0
   ```

3. **接口一致性检查**：
   ```python
   from mc.controllers import PlayerController, KeyboardMouseController, GestureController
   assert issubclass(KeyboardMouseController, PlayerController)
   assert issubclass(GestureController, PlayerController)
   ```

4. **无循环导入**：`python -c "import mc"` 不报 ImportError
