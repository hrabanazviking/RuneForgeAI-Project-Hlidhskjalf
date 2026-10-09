"""Himinbjörg grid layout engine.

Slice 34b — tiles viewports onto the canvas in a configurable grid.
Viewports are placed by name into grid cells; :meth:`GridLayout.layout`
resolves the whole grid to :class:`Rect` tiles in canvas pixels. The
compositor re-applies the layout every tick, so placement switches live.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from hlidskjalf.himinbjorg.compositor import Rect


@dataclass
class _Cell:
    col: int
    row: int
    col_span: int = 1
    row_span: int = 1


class GridLayout:
    """Rectangular grid tiler.

    ``cols``/``rows`` define the grid; ``padding`` is the gap (px) between
    tiles and around the canvas edge.
    """

    def __init__(self, cols: int = 2, rows: int = 2, padding: int = 8) -> None:
        if cols < 1 or rows < 1:
            raise ValueError("cols and rows must be >= 1")
        if padding < 0:
            raise ValueError("padding must be >= 0")
        self.cols = cols
        self.rows = rows
        self.padding = padding
        self._cells: Dict[str, _Cell] = {}

    # -- placement ----------------------------------------------------------
    def place(self, viewport_name: str, col: int, row: int,
              col_span: int = 1, row_span: int = 1) -> "_Cell":
        """Assign a viewport to a grid cell (replaces any prior placement)."""
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            raise ValueError(f"cell ({col}, {row}) outside {self.cols}x{self.rows} grid")
        if col + col_span > self.cols or row + row_span > self.rows:
            raise ValueError("span overflows the grid")
        cell = _Cell(col, row, col_span, row_span)
        self._cells[viewport_name] = cell
        return cell

    def remove(self, viewport_name: str) -> bool:
        if viewport_name in self._cells:
            del self._cells[viewport_name]
            return True
        return False

    def clear(self) -> None:
        self._cells.clear()

    @property
    def placements(self) -> Dict[str, Tuple[int, int, int, int]]:
        return {n: (c.col, c.row, c.col_span, c.row_span)
                for n, c in self._cells.items()}

    # -- resolution ----------------------------------------------------------
    def layout(self, width: int, height: int) -> Dict[str, Rect]:
        """Resolve every placement to a canvas-pixel :class:`Rect`."""
        pad = float(self.padding)
        usable_w = float(width) - pad * (self.cols + 1)
        usable_h = float(height) - pad * (self.rows + 1)
        cell_w = usable_w / self.cols
        cell_h = usable_h / self.rows
        tiles: Dict[str, Rect] = {}
        for name, cell in self._cells.items():
            x = pad + cell.col * (cell_w + pad)
            y = pad + cell.row * (cell_h + pad)
            w = cell.col_span * cell_w + (cell.col_span - 1) * pad
            h = cell.row_span * cell_h + (cell.row_span - 1) * pad
            tiles[name] = Rect(x, y, w, h)
        return tiles


def auto_grid(names, cols: int = 2, padding: int = 8) -> GridLayout:
    """Fill a grid row-major with ``names``; rows grow as needed."""
    names = list(names)
    rows = max(1, (len(names) + cols - 1) // cols)
    layout = GridLayout(cols=cols, rows=rows, padding=padding)
    for i, name in enumerate(names):
        layout.place(name, i % cols, i // cols)
    return layout
