# pyglet 1.5.31 → 2.x 升级方案

## Context

项目已完成步骤 1-7 的重构（模块拆分、Controller 抽象、Player/World 解耦）。当前使用 pyglet **1.5.31**（最晚的 1.x 版本），需要升级到 pyglet **2.x**（最新稳定版）。

pyglet 2.0 是一个重大版本升级：**彻底移除了传统固定管线 OpenGL**，必须使用 OpenGL 3.3+ 核心模式（着色器/Shader、矩阵数学库 Mat4）。

## 受影响的文件（仅 2 个文件需改动）

| 文件 | 改动量 | 说明 |
|------|--------|------|
| [mc/world.py](mc/world.py) | 中等 | `Batch.add()` → `ShaderProgram.vertex_list_indexed()`；`TextureGroup` → 自定义 `ShaderGroup`；`GL_QUADS` → `GL_TRIANGLES` + 索引 |
| [mc/window.py](mc/window.py) | 大 | 固定管线矩阵 → `window.view`/`window.projection` (Mat4)；雾效 → 着色器 uniform；`graphics.draw()` → 着色器顶点列表 |
| [mc/controllers/keyboard_mouse.py](mc/controllers/keyboard_mouse.py) | 极小 | 验证 key/mouse 常量兼容性，`set_exclusive_mouse` 行为 |

其余 9 个模块**完全不依赖 pyglet**，无需修改。

---

## 步骤 1：安装 pyglet 2.x

```bash
.venv/Scripts/pip install --upgrade pyglet
```

验证：
```bash
.venv/Scripts/python -c "import pyglet; print(pyglet.version)"
# 预期: 2.0.x 或 2.1.x
```

---

## 步骤 2：创建 `mc/shaders.py` — 着色器模块

新建文件，包含：

### 2a. 定义 GLSL 着色器源码（作为模块级字符串常量）

**3D 方块顶点着色器** (`block_vertex_source`)：
- `in vec3 position` — 顶点位置
- `in vec2 tex_coords` — 纹理坐标
- `uniform mat4 mvp` — 模型-视图-投影组合矩阵
- `out vec2 frag_tex_coords` — 传递给片段着色器
- `out float frag_distance` — 用于雾效计算（相机空间深度）
- `gl_Position = mvp * vec4(position, 1.0)`

**3D 方块片段着色器** (`block_fragment_source`)：
- `in vec2 frag_tex_coords`
- `in float frag_distance`
- `uniform sampler2D texture_sampler`
- `uniform vec4 fog_color` — 雾的颜色
- `uniform float fog_start, fog_end` — 雾的起止距离
- 线性雾：`fog_factor = clamp((fog_end - dist) / (fog_end - fog_start), 0.0, 1.0)`
- `final_color = mix(fog_color, texture_sample, fog_factor)`

**2D 线框着色器**（用于准星和方块高亮）：
- 顶点着色器：`in vec2 position` + `uniform mat4 projection` → `gl_Position`
- 片段着色器：`uniform vec4 color` → 输出纯色

### 2b. 编译着色器程序（模块级单例或工厂函数）

```python
def create_block_shader() -> ShaderProgram:
    """编译并返回 3D 方块着色器程序"""
    ...

def create_line_shader() -> ShaderProgram:
    """编译并返回 2D/3D 线条着色器程序"""
    ...
```

### 2c. 自定义 `TextureGroup` 替代品

```python
class ShaderTextureGroup(pyglet.graphics.Group):
    """同时绑定 ShaderProgram 和纹理的 Group。
    用于 Batch 渲染中的状态管理。
    """
    def __init__(self, program, texture, order=0, parent=None): ...
    def set_state(self): ...  # program.use() + glBindTexture
    def unset_state(self): ...  # program.stop()
    def __eq__(self, other): ...
    def __hash__(self): ...
```

---

## 步骤 3：更新 `mc/world.py`

### 3a. 导入变更

```python
# 删除
from pyglet import image
from pyglet.gl import GL_QUADS
from pyglet.graphics import TextureGroup

# 新增
from pyglet.gl import GL_TRIANGLES
from pyglet.image import load as image_load
from mc.shaders import create_block_shader, ShaderTextureGroup
```

### 3b. `__init__` 变更

```python
# 创建着色器程序
self.shader = create_block_shader()
# 加载纹理
self.texture = image_load(TEXTURE_PATH).get_texture()
# 用自定义 Group 替代 TextureGroup
self.group = ShaderTextureGroup(self.shader, self.texture)
```

### 3c. `_show_block()` 变更 — 关键改动

**之前**：
```python
self._shown[position] = self.batch.add(24, GL_QUADS, self.group,
    ('v3f/static', vertex_data),
    ('t2f/static', texture_data))
```

**之后**：使用 `vertex_list_indexed` + `GL_TRIANGLES`。

每个方块 6 个面，每面 4 顶点 → 24 顶点（保持不变）。每面用 6 个索引（2 个三角形）→ 36 个索引。

预计算一个**模块级常量 `FACE_INDICES`**（所有面通用）：
```python
# 每面的 4 个顶点 (0,1,2,3) → 2 个三角形
_FACE_INDICES = [0, 1, 2, 0, 2, 3]
# 6 个面，每面偏移 4
_CUBE_INDICES = []
for face in range(6):
    offset = face * 4
    for idx in _FACE_INDICES:
        _CUBE_INDICES.append(offset + idx)
```

```python
self._shown[position] = self.shader.vertex_list_indexed(
    24, GL_TRIANGLES, _CUBE_INDICES,
    batch=self.batch,
    group=self.group,
    position=('f', vertex_data),      # 3 分量
    tex_coords=('f', texture_data),   # 2 分量
)
```

注意：shader 中的属性名 (`position`, `tex_coords`) 必须与 GLSL 中的 `in` 变量名完全一致。

---

## 步骤 4：更新 `mc/window.py` — 渲染管线重写

### 4a. 导入变更

```python
# 删除大部分 pyglet.gl 导入（GL_QUADS, glTranslatef, glRotatef 等）
# 新增
from pyglet.math import Mat4
from pyglet.gl import GL_TRIANGLES, GL_LINES, GL_CULL_FACE, GL_DEPTH_TEST
from mc.shaders import create_block_shader, create_line_shader
```

保留的 GL 常量和函数：`GL_CULL_FACE`, `GL_DEPTH_TEST`, `glClearColor`, `glEnable`, `glDisable`, `glViewport`, `glClear`（这些在现代 GL 中仍存在）。

### 4b. `GameWindow.__init__` 变更

```python
# 创建 2D 线着色器用于准星和高亮
self.line_shader = create_line_shader()
# 准星：用 vertex_list 替代 graphics.vertex_list
self.reticle = None  # 在 on_resize 中创建
```

### 4c. `set_2d()` 重写

**之前**：`glMatrixMode(GL_PROJECTION)`, `glLoadIdentity()`, `glOrtho(...)`, `glMatrixMode(GL_MODELVIEW)`, `glLoadIdentity()`

**之后**：
```python
def set_2d(self):
    width, height = self.get_size()
    glDisable(GL_DEPTH_TEST)
    viewport = self.get_viewport_size()
    glViewport(0, 0, max(1, viewport[0]), max(1, viewport[1]))
    # 使用正交投影矩阵
    self.projection = Mat4.orthogonal_projection(0, max(1, width), 0, max(1, height), -1, 1)
    self.view = Mat4.identity()
```

### 4d. `set_3d()` 重写

**之前**：`glMatrixMode(GL_PROJECTION)`, `glLoadIdentity()`, `gluPerspective(...)`, `glMatrixMode(GL_MODELVIEW)`, `glLoadIdentity()`, `glRotatef`, `glTranslatef`

**之后**：
```python
def set_3d(self):
    width, height = self.get_size()
    glEnable(GL_DEPTH_TEST)
    viewport = self.get_viewport_size()
    glViewport(0, 0, max(1, viewport[0]), max(1, viewport[1]))
    # 透视投影
    self.projection = Mat4.perspective_projection(
        max(1, width) / max(1, height), 65.0, 0.1, 60.0)
    # 视图矩阵：先旋转（绕 Y 轴 yaw，绕 X 轴 pitch），再平移
    x, y = self.player.rotation
    self.view = (Mat4.from_rotation(x, (0, 1, 0)) @
                 Mat4.from_rotation(-y, (1, 0, 0)))
    px, py, pz = self.player.position
    self.view = self.view @ Mat4.from_translation((-px, -py, -pz))
```

### 4e. `on_draw()` 变更

```python
def on_draw(self):
    self.clear()
    self.set_3d()
    # 更新方块着色器的 mvp 矩阵
    mvp = self.projection @ self.view
    self.world.shader['mvp'] = mvp
    self.world.batch.draw()
    self.draw_focused_block()
    self.set_2d()
    self.draw_label()
    self.draw_reticle()
```

### 4f. `draw_focused_block()` 重写

**之前**：`glColor3d(0,0,0)`, `glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)`, `pyglet.graphics.draw(24, GL_QUADS, ...)`, `glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)`

**之后**：使用线着色器，以 `GL_LINES` 绘制方块线框。需要将 `cube_vertices` 的 24 个顶点转换为线段的顶点对。或者更简单的：使用线着色器绘制 12 条线段（立方体的 12 条边）。

更好的方案：写一个辅助函数 `wireframe_cube_vertices(x, y, z, n)` 生成 12 条线段的 24 个顶点，然后用 `line_shader.vertex_list(24, GL_LINES, ...)` 绘制。

### 4g. `on_resize()` 变更

```python
def on_resize(self, width, height):
    self.label.y = height - 10
    if self.reticle:
        self.reticle.delete()
    x, y = width // 2, height // 2
    n = 10
    # 使用线着色器
    self.reticle = self.line_shader.vertex_list(
        4, GL_LINES,
        position=('f', (x - n, y, x + n, y, x, y - n, x, y + n))
    )
```

### 4h. `setup_fog()` 重写

**之前**：调用 `glFogfv`, `glFogi`, `glFogf`, `glHint` 等固定管线雾效 API

**之后**：雾效参数作为着色器 uniform 设置。在 `setup()` 中或每帧设置着色器 uniform：
```python
def setup_fog(shader):
    """设置雾效参数到着色器"""
    shader['fog_color'] = (0.5, 0.69, 1.0, 1.0)  # RGBA
    shader['fog_start'] = 20.0
    shader['fog_end'] = 60.0
```

### 4i. `setup()` 变更

```python
def setup(shader):
    glClearColor(0.5, 0.69, 1.0, 1)
    glEnable(GL_CULL_FACE)
    # glTexParameteri 在现代 GL 中仍存在，但对纹理的设置需要用 glTexParameter 或直接在着色器/纹理对象上设置
    setup_fog(shader)
```

`glTexParameteri(GL_TEXTURE_2D, ...)` — 在现代 OpenGL 中纹理参数设置在纹理对象上。pyglet 2.0 的 `Texture` 对象应支持 `glTexParameteri` 或者在纹理加载时设置。需要测试验证。

### 4j. `run()` 变更

```python
def run():
    window = GameWindow(width=800, height=600, caption='Minecraft', resizable=True)
    window.controller.activate()
    setup(window.world.shader)
    pyglet.app.run(interval=1.0 / TICKS_PER_SEC)
```

---

## 步骤 5：更新 `mc/controllers/keyboard_mouse.py`

### 改动点

1. **Key 常量名**：pyglet 2.x 中 key 常量基本不变（如 `key.A`, `key.W`, `key.SPACE`, `key._1` 等）。需要测试验证。
2. **Mouse 常量名**：`mouse.LEFT`, `mouse.RIGHT` 在 2.x 中可能不变；需要验证。
3. **`set_exclusive_mouse`**：在 pyglet 2.0 中此 API 可能被替换为 `set_mouse_visible(False)` + 其他机制。需要查看实际安装的版本 API。
4. **`push_handlers`**：确认仍在 pyglet 2.x 中可用。

预期改动量极小（可能只需 1-2 行调整）。

---

## 步骤 6：添加/更新 `requirements.txt`

项目根目录创建 `requirements.txt`：
```
pyglet~=2.0
```

---

## 步骤 7：测试与修复

1. **启动测试**：`python main.py` 或 `python -m mc`
2. **功能清单**（逐步验证每一项）：
   - [ ] 窗口创建正常
   - [ ] 3D 世界渲染（方块纹理正确显示）
   - [ ] 雾效（远处方块淡入雾色）
   - [ ] WASD 移动
   - [ ] 鼠标视角旋转
   - [ ] 空格跳跃
   - [ ] Tab 飞行切换
   - [ ] 鼠标左键破坏方块
   - [ ] 鼠标右键放置方块
   - [ ] 数字键切换物品栏
   - [ ] ESC 释放/捕获鼠标
   - [ ] 准星显示
   - [ ] 方块高亮线框
   - [ ] FPS/坐标 HUD 文字
   - [ ] 窗口缩放后正常渲染

3. **迭代修复**：根据实际运行效果调整着色器参数（MVP 矩阵、雾参数、纹理坐标等）直到视觉效果与原版一致。

---

## 核心风险点

| 风险 | 缓解措施 |
|------|----------|
| 坐标系统差异导致方块位置错误 | 测试时先渲染单个方块验证 |
| `glTexParameteri` 在现代 GL 中行为不同 | 查阅 pyglet 2.x 纹理 API 文档 |
| `set_exclusive_mouse` API 变化 | 查看 pyglet 2.x Window API 调整 |
| 雾效效果不一致 | 着色器参数可调（start/end/color） |
| 性能下降（着色器切换开销） | 预编译着色器、复用 Batch、减少 uniform 更新 |

---

## 验证方法

```bash
# 1. 无 import 错误
.venv/Scripts/python -c "from mc import run; print('OK')"

# 2. 游戏启动
.venv/Scripts/python main.py
# 手动验证上述功能清单

# 3. 库导入测试
.venv/Scripts/python -c "from mc import World, Player, FlatWorldGenerator; w=World(FlatWorldGenerator()); print(len(w.world))"
```

---

## 升级完成总结

**升级日期**：2026-06-09

**升级版本**：pyglet 1.5.31 → pyglet 2.1.14

### 变更文件清单

| 文件 | 操作 | 改动程度 | 说明 |
|------|------|----------|------|
| `mc/shaders.py` | **新建** | — | GLSL 着色器源码 + `ShaderProgram` 工厂 + 自定义 `ShaderTextureGroup` |
| `mc/world.py` | 修改 | 中等 | `Batch.add()` → `ShaderProgram.vertex_list_indexed()`；`TextureGroup` → `ShaderTextureGroup`；`GL_QUADS` → `GL_TRIANGLES` + 索引缓冲区 |
| `mc/window.py` | 重写 | 大 | 固定管线矩阵 → `Mat4`/`Vec3`；`gluPerspective`/`glOrtho` → `Mat4.perspective_projection`/`orthogonal_projection`；`glRotatef`/`glTranslatef` → `Mat4.from_rotation`/`from_translation`；`glFog*` → 着色器 uniform 雾效；`glPolygonMode` 线框 → `wireframe_cube_vertices` + 线着色器 |
| `mc/utils.py` | 修改 | 小 | 新增 `wireframe_cube_vertices()` 辅助函数 |
| `mc/controllers/keyboard_mouse.py` | **无改动** | — | 所有 key/mouse 常量及 `set_exclusive_mouse` 在 pyglet 2.x 完全兼容 |
| `requirements.txt` | **新建** | — | `pyglet~=2.0` |

### 关键技术细节

1. **着色器**：编写了 GLSL 330 着色器 — 方块着色器（纹理+雾效）和线条着色器（纯色）。方块着色器使用 `uniform mat4 mvp` 和 `uniform mat4 view`（view 用于计算摄像机空间深度做雾效）。

2. **矩阵数学**：
   - `Mat4.from_rotation(angle, axis)` 的 `angle` 参数是**弧度**（不是角度！），需用 `math.radians()` 转换
   - `Mat4.from_translation(vector)` 需要 `Vec3` 类型参数，不能传 tuple
   - `Mat4()` 默认构造为单位矩阵（没有 `Mat4.identity()` 静态方法）

3. **几何绘制**：
   - 预计算常量 `_CUBE_INDICES` — 每个方块 24 顶点、36 索引（6面 × 2三角形 × 3顶点）
   - `ShaderProgram.vertex_list_indexed(count, mode, indices, batch=, group=, **attrs)` 返回的 vertex list 加入 Batch 后由 Batch 统一管理生命周期

4. **自定义 Group**：`ShaderTextureGroup` 继承 `pyglet.graphics.Group`，`set_state()` 中先绑定纹理（`glActiveTexture(GL_TEXTURE0)` + `glBindTexture`），再激活着色器（`program.use()`），确保 Batch 渲染时每个 group 正确切换状态。

5. **线框高亮**：废弃的 `glPolygonMode` 被替换为 `wireframe_cube_vertices()` 辅助函数，该函数根据立方体 8 个角点生成 12 条边的 24 个线段顶点，用线条着色器以 `GL_LINES` 绘制。

6. **雾效**：从固定管线 `glFog*` API 迁移到着色器内线性雾计算：
   ```glsl
   fog_factor = clamp((fog_end - frag_distance) / (fog_end - fog_start), 0.0, 1.0);
   final_color = mix(fog_color, texture_sample, fog_factor);
   ```
   雾参数通过 `setup_fog(shader)` 在初始化时设置。

### 导入测试结果

所有模块级导入验证通过：
- ✅ `from mc import run` — 无 import 错误
- ✅ 着色器编译成功（`ShaderProgram(id=3)` / `ShaderProgram(id=4)`）
- ✅ World 创建成功（~85000 方块，纹理加载正常）
- ✅ 线框顶点生成正确（72 floats = 24 顶点 × 3 分量）
- ✅ 视图矩阵计算正常（弧度转换 + Vec3 翻译 + 相机右轴 pitch）

### 待人工验证

需运行 `python main.py` 进行视觉验证的功能：
- [ ] 窗口创建正常
- [ ] 3D 世界渲染（方块纹理正确显示）
- [ ] 雾效（远处方块淡入雾色）
- [ ] WASD 移动
- [ ] 鼠标视角旋转
- [ ] 空格跳跃
- [ ] Tab 飞行切换
- [ ] 鼠标左键破坏方块
- [ ] 鼠标右键放置方块
- [ ] 数字键切换物品栏
- [ ] ESC 释放/捕获鼠标
- [ ] 准星显示
- [ ] 方块高亮线框
- [ ] FPS/坐标 HUD 文字
- [ ] 窗口缩放后正常渲染

### 已知风险与调试提示

| 风险 | 调试建议 |
|------|----------|
| 坐标系统差异导致方块位置错误 | 检查 `Mat4.from_rotation` 角度是否已转弧度、`perspective_projection` 参数顺序是否正确（aspect, z_near, z_far, fov=） |
| 纹理上下颠倒 | 纹理坐标 v 分量可能需要 `1.0 - v` 翻转 |
| 雾效效果不一致 | 调整 `setup_fog()` 中的 `fog_start`/`fog_end` 参数 |
| 准星或线框不显示 | 检查 `set_2d()`/`set_3d()` 中 mvp 矩阵是否正确传递给线着色器 |
| 性能下降 | 预编译着色器已做（单例模式）、Batch 已复用；`draw_focused_block` 每帧创建/销毁 vertex list 有少量开销但可接受 |
