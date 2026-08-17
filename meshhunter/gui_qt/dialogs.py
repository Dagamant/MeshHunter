"""Confirmation dialogs."""

from PySide6.QtWidgets import QMessageBox


def confirm_clear_device_contacts(parent):
    result = QMessageBox.question(
        parent,
        "Clear device contacts",
        "Remove ALL contacts stored on the connected device, including "
        "Chat/companion contacts, not just Repeater/Room/Sensor nodes?\n\n"
        "Removed nodes will only reappear once they're heard (or re-added) again.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return result == QMessageBox.Yes
