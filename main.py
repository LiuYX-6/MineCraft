"""Backward-compatible entry point.

Prefer ``python -m mc`` for new usage.
"""
from mc import run

if __name__ == '__main__':
    run()
