"""Minecraft — a simple Minecraft-like voxel game built with pyglet.

Usage::

    python -m mc          # recommended
    python main.py        # backward-compatible shim

As a library::

    from mc import World, Player, FlatWorldGenerator, run
    w = World(terrain_generator=FlatWorldGenerator())
    p = Player()
    print(len(w.world))
"""

from mc.world import World
from mc.terrain import TerrainGenerator, FlatWorldGenerator
from mc.player import Player
from mc.controllers import PlayerController, KeyboardMouseController, GestureController
from mc.window import GameWindow, run
from mc.blocks import GRASS, SAND, BRICK, STONE

__all__ = [
    'World',
    'Player',
    'GameWindow',
    'TerrainGenerator',
    'FlatWorldGenerator',
    'PlayerController',
    'KeyboardMouseController',
    'GestureController',
    'GRASS',
    'SAND',
    'BRICK',
    'STONE',
    'run',
]
