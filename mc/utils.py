from mc.config import SECTOR_SIZE


def cube_vertices(x, y, z, n):
    """ Return the vertices of the cube at position x, y, z with size 2*n.

    """
    return [
        x-n,y+n,z-n, x-n,y+n,z+n, x+n,y+n,z+n, x+n,y+n,z-n,  # top
        x-n,y-n,z-n, x+n,y-n,z-n, x+n,y-n,z+n, x-n,y-n,z+n,  # bottom
        x-n,y-n,z-n, x-n,y-n,z+n, x-n,y+n,z+n, x-n,y+n,z-n,  # left
        x+n,y-n,z+n, x+n,y-n,z-n, x+n,y+n,z-n, x+n,y+n,z+n,  # right
        x-n,y-n,z+n, x+n,y-n,z+n, x+n,y+n,z+n, x-n,y+n,z+n,  # front
        x+n,y-n,z-n, x-n,y-n,z-n, x-n,y+n,z-n, x+n,y+n,z-n,  # back
    ]


def tex_coord(x, y, n=4):
    """ Return the bounding vertices of the texture square.

    """
    m = 1.0 / n
    dx = x * m
    dy = y * m
    return dx, dy, dx + m, dy, dx + m, dy + m, dx, dy + m


def tex_coords(top, bottom, side):
    """ Return a list of the texture squares for the top, bottom and side.

    """
    top = tex_coord(*top)
    bottom = tex_coord(*bottom)
    side = tex_coord(*side)
    result = []
    result.extend(top)
    result.extend(bottom)
    result.extend(side * 4)
    return result


def wireframe_cube_vertices(x, y, z, n):
    """Return the 24 vertices (12 line segments) for a wireframe cube at
    position x, y, z with size 2*n.  Suitable for drawing with GL_LINES.

    Each edge is represented by 2 vertices (6 floats each).
    """
    # 8 corners of the cube
    v = [
        x-n, y+n, z-n,  # 0: top-back-left
        x-n, y+n, z+n,  # 1: top-front-left
        x+n, y+n, z+n,  # 2: top-front-right
        x+n, y+n, z-n,  # 3: top-back-right
        x-n, y-n, z-n,  # 4: bottom-back-left
        x+n, y-n, z-n,  # 5: bottom-back-right
        x+n, y-n, z+n,  # 6: bottom-front-right
        x-n, y-n, z+n,  # 7: bottom-front-left
    ]
    # 12 edges as pairs of corner indices
    edges = [
        (0,1), (1,2), (2,3), (3,0),   # top face
        (4,5), (5,6), (6,7), (7,4),   # bottom face
        (0,4), (1,7), (2,6), (3,5),   # vertical edges
    ]
    result = []
    for a, b in edges:
        result.extend(v[a*3:a*3+3])
        result.extend(v[b*3:b*3+3])
    return result


# Face corner indices (see cube corner numbering above),
# paired with normals matching module-level FACES.
_FACE_CORNERS = [
    ( 0, 1, 2, 3),   # top    ( 0, 1, 0)
    ( 7, 6, 5, 4),   # bottom ( 0,-1, 0)
    ( 4, 7, 1, 0),   # left   (-1, 0, 0)
    ( 6, 5, 3, 2),   # right  ( 1, 0, 0)
    ( 7, 6, 2, 1),   # front  ( 0, 0, 1)
    ( 5, 4, 0, 3),   # back   ( 0, 0,-1)
]


def visible_block_edges(x, y, z, n, world, camera_pos, max_distance=6):
    """Return line-segment vertices for visible edges of the block at (x,y,z).

    A face's edges are drawn only when the face is:
    1. Front-facing (camera is on the outward-normal side), AND
    2. Not occluded by an adjacent block.

    Returns empty list when the block is farther than *max_distance* from the
    camera or when no faces are visible at all.

    Parameters
    ----------
    x, y, z : float
        Block centre.
    n : float
        Half-size of the block (e.g. 0.51).
    world : dict
        ``{(ix,iy,iz): texture}`` mapping of all existing blocks.
    camera_pos : tuple of 3 floats
        Player / camera position.
    max_distance : float
        Maximum Euclidean distance for outline display.
    """
    px, py, pz = camera_pos
    dx, dy, dz = float(px - x), float(py - y), float(pz - z)
    if dx * dx + dy * dy + dz * dz > max_distance * max_distance:
        return []

    # 8 corners in world space
    c = [
        (x - n, y + n, z - n),  # 0
        (x - n, y + n, z + n),  # 1
        (x + n, y + n, z + n),  # 2
        (x + n, y + n, z - n),  # 3
        (x - n, y - n, z - n),  # 4
        (x + n, y - n, z - n),  # 5
        (x + n, y - n, z + n),  # 6
        (x - n, y - n, z + n),  # 7
    ]

    result = []
    for face_idx, ci in enumerate(_FACE_CORNERS):
        nx, ny, nz = FACES[face_idx]

        # --- back-face culling ---
        # Face is front-facing when dot(normal, camera→block) > 0
        if nx * dx + ny * dy + nz * dz <= 0:
            continue

        # --- occlusion: adjacent block covers this face ---
        if (int(x + nx), int(y + ny), int(z + nz)) in world:
            continue

        # 4 edges of this face (consecutive corner pairs)
        for i in range(4):
            a = c[ci[i]]
            b = c[ci[(i + 1) % 4]]
            result.extend(a)
            result.extend(b)

    return result


FACES = [
    ( 0, 1, 0),
    ( 0,-1, 0),
    (-1, 0, 0),
    ( 1, 0, 0),
    ( 0, 0, 1),
    ( 0, 0,-1),
]


def normalize(position):
    """ Accepts `position` of arbitrary precision and returns the block
    containing that position.

    Parameters
    ----------
    position : tuple of len 3

    Returns
    -------
    block_position : tuple of ints of len 3

    """
    x, y, z = position
    x, y, z = (int(round(x)), int(round(y)), int(round(z)))
    return (x, y, z)


def sectorize(position):
    """ Returns a tuple representing the sector for the given `position`.

    Parameters
    ----------
    position : tuple of len 3

    Returns
    -------
    sector : tuple of len 3

    """
    x, y, z = normalize(position)
    x, y, z = x // SECTOR_SIZE, y // SECTOR_SIZE, z // SECTOR_SIZE
    return (x, 0, z)
