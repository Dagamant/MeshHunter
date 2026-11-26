import base64
import hashlib

import pytest

from meshhunter.core import channels as C


def test_public_channel_hash():
    assert C.make_channel("Public", C.PUBLIC_CHANNEL_KEY)["hash"] == 0x11


def test_hashtag_key_derives_from_name():
    chan = C.make_channel("#test")
    assert chan["key"] == hashlib.sha256(b"#test").digest()[:16]
    assert chan["hash"] == hashlib.sha256(chan["key"]).digest()[0]


def test_non_hashtag_needs_key():
    with pytest.raises(ValueError):
        C.make_channel("Club")
    with pytest.raises(ValueError):
        C.make_channel("")


def test_parse_key_hex_and_base64():
    raw = bytes(range(16))
    assert C.parse_key(raw.hex()) == raw
    assert C.parse_key(base64.b64encode(raw).decode()) == raw
    assert C.parse_key(raw) == raw
    with pytest.raises(ValueError):
        C.parse_key("not a key!")
    with pytest.raises(ValueError):
        C.parse_key(base64.b64encode(b"short").decode())


def test_parse_entry_text():
    assert C.parse_entry_text("hamradio") == {"name": "#hamradio", "enabled": True}
    key = bytes(range(16)).hex()
    assert C.parse_entry_text(f"Club {key}") == {"name": "Club", "enabled": True, "key": key}
    with pytest.raises(ValueError):
        C.parse_entry_text("")


def test_clean_entries_dedupes_and_drops_bad():
    entries = ["#a", {"name": "#A"}, {"name": "Public"}, {"name": "NoKey"}, 42,
               {"name": "#b", "enabled": False}]
    assert C.clean_entries(entries) == [{"name": "#a", "enabled": True}, {"name": "#b", "enabled": False}]


def test_config_channels_migrates_old_list():
    out = C.config_channels({"sniffer_meshcore_channels": ["#old"]})
    names = [e["name"] for e in out]
    assert names[: len(C.DEFAULT_CHANNEL_NAMES)] == list(C.DEFAULT_CHANNEL_NAMES)
    assert "#old" in names
    assert C.config_channels({"meshcore_channels": []}) == []


def test_subscribed_channels_skips_disabled():
    chans = C.subscribed_channels({"meshcore_channels": [{"name": "#on"}, {"name": "#off", "enabled": False}]})
    assert [c["name"] for c in chans] == ["Public", "#on"]


def test_decrypt_group_text_rejects_bad_lengths():
    assert C.decrypt_group_text(b"\x11\x00", []) is None
    assert C.decrypt_group_text(b"\x11\x00\x00" + b"x" * 15, C.subscribed_channels({"meshcore_channels": []})) is None
