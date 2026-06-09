"""Shader programs and custom Group for pyglet 2.x rendering.

Defines GLSL shader sources, compiles them into ShaderProgram instances,
and provides a ShaderTextureGroup that combines shader binding with
texture binding (replacing pyglet 1.x TextureGroup).
"""

import pyglet
from pyglet.graphics.shader import ShaderProgram, Shader
from pyglet.gl import GL_TEXTURE0
from pyglet.gl import glActiveTexture, glBindTexture


# ------------------------------------------------------------------
# GLSL shader sources
# ------------------------------------------------------------------

# ---- 3D block vertex shader ----
block_vertex_source = """#version 330
    in vec3 position;
    in vec2 tex_coords;

    uniform mat4 mvp;
    uniform mat4 view;

    out vec2 frag_tex_coords;
    out float frag_distance;

    void main()
    {
        vec4 eye_pos = view * vec4(position, 1.0);
        gl_Position = mvp * vec4(position, 1.0);
        frag_tex_coords = tex_coords;
        frag_distance = -eye_pos.z;
    }
"""

# ---- 3D block fragment shader ----
block_fragment_source = """#version 330
    in vec2 frag_tex_coords;
    in float frag_distance;

    uniform sampler2D texture_sampler;
    uniform vec4 fog_color;
    uniform float fog_start;
    uniform float fog_end;

    out vec4 final_color;

    void main()
    {
        vec4 texture_sample = texture(texture_sampler, frag_tex_coords);
        float fog_factor = clamp((fog_end - frag_distance) / (fog_end - fog_start), 0.0, 1.0);
        final_color = mix(fog_color, texture_sample, fog_factor);
    }
"""

# ---- 2D/3D line vertex shader ----
line_vertex_source = """#version 330
    in vec3 position;
    uniform mat4 mvp;

    void main()
    {
        gl_Position = mvp * vec4(position, 1.0);
    }
"""

# ---- 2D line fragment shader ----
line_fragment_source = """#version 330
    uniform vec4 color;
    out vec4 final_color;

    void main()
    {
        final_color = color;
    }
"""


# ------------------------------------------------------------------
# ShaderProgram factories (singleton instances, lazy-created)
# ------------------------------------------------------------------

_block_shader = None
_line_shader = None


def create_block_shader() -> ShaderProgram:
    """Compile and return the 3D block shader program (singleton)."""
    global _block_shader
    if _block_shader is not None:
        return _block_shader

    vert = Shader(block_vertex_source, 'vertex')
    frag = Shader(block_fragment_source, 'fragment')
    _block_shader = ShaderProgram(vert, frag)
    return _block_shader


def create_line_shader() -> ShaderProgram:
    """Compile and return the 2D line shader program (singleton)."""
    global _line_shader
    if _line_shader is not None:
        return _line_shader

    vert = Shader(line_vertex_source, 'vertex')
    frag = Shader(line_fragment_source, 'fragment')
    _line_shader = ShaderProgram(vert, frag)
    return _line_shader


# ------------------------------------------------------------------
# Custom Group: shader + texture
# ------------------------------------------------------------------

class ShaderTextureGroup(pyglet.graphics.Group):
    """A pyglet Group that binds both a ShaderProgram and a Texture.

    Used as the ``group`` argument for ``ShaderProgram.vertex_list_indexed()``
    when the vertex list is added to a Batch.  Ensures that the correct
    shader and the correct texture are active before drawing.

    In pyglet 2.x the built-in ``ShaderGroup`` only activates a program
    and ``TextureGroup`` only binds a texture — this class does both.
    """

    def __init__(self, program, texture, order=0, parent=None):
        super().__init__(order, parent)
        self.program = program
        self.texture = texture

    def set_state(self):
        """Called by Batch before drawing vertex lists in this group."""
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(self.texture.target, self.texture.id)
        self.program.use()

    def unset_state(self):
        """Called by Batch after drawing vertex lists in this group."""
        self.program.stop()

    def __eq__(self, other):
        if not isinstance(other, ShaderTextureGroup):
            return False
        return (self.program is other.program and
                self.texture is other.texture and
                self.order == other.order and
                self.parent == other.parent)

    def __hash__(self):
        return hash((id(self.program), id(self.texture),
                     self.order, self.parent))
