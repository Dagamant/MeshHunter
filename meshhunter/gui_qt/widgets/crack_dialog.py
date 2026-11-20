"""Results of a #hashtag channel dictionary attack (core/channel_crack.py),
with a checkbox per recovered channel to subscribe to it.

Opened from the Messages panel's "Find channels". The attack itself is fast
(it only builds keys for names whose hash appears in the capture), so it
runs inline before the dialog is shown; this just presents what was found.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..theme import DIM, pick_mono_font


class CrackResultsDialog(QDialog):
    def __init__(self, results, scanned, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find channels")
        self.setMinimumWidth(460)
        mono = pick_mono_font()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        if results:
            head = f"Recovered {len(results)} channel(s) from {scanned} captured packets. " \
                   "Tick the ones to subscribe to:"
        else:
            head = (
                f"No new channels recovered from {scanned} captured packets.\n\n"
                "The unknown channels are either private channels (random key, "
                "nothing to guess) or #hashtag channels whose name isn't in the "
                "wordlist. Add likely names (your town, club, callsign area) to "
                "channel_wordlist.txt and try again."
            )
        label = QLabel(head)
        label.setWordWrap(True)
        label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        layout.addWidget(label)

        self.list = QListWidget()
        self.list.setStyleSheet(f"font-family: '{mono}'; font-size: 12px;")
        for e in results:
            sample = e["messages"][0] if e["messages"] else ""
            text = f"{e['name']}   (hash {e['hash']}, {e['count']} msg)   {sample[:48]}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, e["name"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setToolTip("\n".join(e["messages"]))
            self.list.addItem(item)
        if results:
            layout.addWidget(self.list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Subscribe" if results else "Close")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if not results:
            buttons.removeButton(buttons.button(QDialogButtonBox.Cancel))
        layout.addWidget(buttons)

    def chosen_names(self):
        return [
            self.list.item(i).data(Qt.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]
