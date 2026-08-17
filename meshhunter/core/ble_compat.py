"""BLE library workarounds that must be applied before any BLE connection.

Currently just one: a confirmed hang in bleak's start_notify() against this
node's BLE stack. Isolated in its own module (rather than a bare import-time
side effect buried in a huge file) so the requirement to call
apply_ble_workarounds() early is visible and easy to find.
"""

import asyncio

from bleak import BleakClient

# Workaround for a hang confirmed against this node's BLE stack: bleak's
# start_notify() is called on the UART TX characteristic the instant BlueZ
# reports ServicesResolved (observed ~1ms gap), and the node then drops the
# BLE link mid-request -- likely an nRF52/SoftDevice-style peripheral that
# needs a brief settle time after service discovery before it'll accept a
# notify subscription. When that happens, bleak's dbus_fast backend never
# resolves the pending D-Bus call (its future is orphaned once the device
# object disappears), so start_notify() -- and therefore the whole
# connect() -- hangs forever with no exception and no further log output.
# Confirmed live: debug bleak logs show ServicesResolved True -> StartNotify
# -> disconnect all within ~300ms, then the await just never returns (still
# pending past a 45s asyncio.wait_for). A short settle delay plus a hard
# timeout (so a repeat of the race fails fast/cleanly instead of hanging the
# app) fixes it without touching the vendored meshcore/bleak packages.
_BLE_NOTIFY_SETTLE_DELAY = 0.3
_BLE_NOTIFY_TIMEOUT = 10.0

_applied = False
_original_start_notify = BleakClient.start_notify


async def _patched_start_notify(self, char_specifier, callback, **kwargs):
    await asyncio.sleep(_BLE_NOTIFY_SETTLE_DELAY)
    return await asyncio.wait_for(
        _original_start_notify(self, char_specifier, callback, **kwargs),
        timeout=_BLE_NOTIFY_TIMEOUT,
    )


def apply_ble_workarounds():
    """Idempotent. Must be called once, before any BLE connection is
    attempted (both the tkinter and Qt entry points call this at startup)."""
    global _applied
    if _applied:
        return
    BleakClient.start_notify = _patched_start_notify
    _applied = True
