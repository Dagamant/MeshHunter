"""Discovered-node counts, one stat tile per adv type.

Just counts, not a scrollable list of every node -- a few hundred repeaters
is common, and rendering that many as individual rows is what made the
original tkinter list-based attempt feel like a lockup.
"""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ...core.constants import NODE_DISPLAY_TYPES
from .common import section_label
from .stat_tile import StatTile


class NodePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("nodePanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(section_label("NODES"))

        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(10)
        self._tiles = {}
        for node_type, label in NODE_DISPLAY_TYPES:
            tile = StatTile(label)
            self._tiles[node_type] = tile
            tiles_row.addWidget(tile)
        layout.addLayout(tiles_row)

    def update_counts(self, counts):
        for node_type, count in counts.items():
            tile = self._tiles.get(node_type)
            if tile is not None:
                tile.set_value(count)

    def reset(self):
        for tile in self._tiles.values():
            tile.set_value(0)
