import base64

import pytest

from meshhunter.core.sniffer_decode import (
    MT_DEFAULT_PSK,
    decode_meshtastic,
    default_meshtastic_channels,
    expand_meshtastic_psk,
    format_meshtastic,
    meshtastic_channel,
    mt_node_id,
    pb_fields,
)

import packets as P

PRESETS = ["LongFast", "MediumFast", "MediumSlow", "ShortFast", "ShortSlow", "ShortTurbo", "LongMod", "LongSlow"]
CHANNELS = default_meshtastic_channels(PRESETS)


def test_header_fields():
    raw = P.mt_text("hi", sender=0xDEADBEEF, dest=0x01020304, pkt_id=42, hop_limit=1, hop_start=3, relay=0x7F)
    d = decode_meshtastic(raw, CHANNELS)
    assert d["from"] == "!deadbeef"
    assert d["from_num"] == 0xDEADBEEF
    assert d["to"] == "!01020304"
    assert d["id"] == 42
    assert d["hop_limit"] == 1 and d["hop_start"] == 3 and d["hops_away"] == 2
    assert d["relay_node"] == 0x7F


def test_broadcast_text_on_longfast():
    d = decode_meshtastic(P.mt_text("hello from the mesh"), CHANNELS)
    assert d["to"] == "^all"
    assert d["channel"] == "LongFast"
    assert d["portname"] == "TEXT"
    assert d["text"] == "hello from the mesh"
    assert "encrypted" not in d


@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_default_channel_decrypts(preset):
    d = decode_meshtastic(P.mt_text("x", channel=preset), CHANNELS)
    assert d["channel"] == preset
    assert d["text"] == "x"


def test_position():
    d = decode_meshtastic(P.mt_position(38.1542881, -81.1146102, alt=612), CHANNELS)
    assert d["portname"] == "POSITION"
    assert d["lat"] == pytest.approx(38.1542881)
    assert d["lon"] == pytest.approx(-81.1146102)
    assert d["alt"] == 612


def test_position_negative_altitude():
    d = decode_meshtastic(P.mt_position(1.0, 2.0, alt=-12), CHANNELS)
    assert d["alt"] == -12


def test_position_zero_zero_is_ignored():
    d = decode_meshtastic(P.mt_position(0.0, 0.0), CHANNELS)
    assert "lat" not in d and "lon" not in d


def test_nodeinfo():
    pk = bytes(range(32))
    raw = P.mt_nodeinfo("!1234abcd", "Base Camp", "BC", hw_model=43, role=2, public_key=pk)
    d = decode_meshtastic(raw, CHANNELS)
    assert d["portname"] == "NODEINFO"
    assert d["user_id"] == "!1234abcd"
    assert d["long_name"] == "Base Camp"
    assert d["short_name"] == "BC"
    assert d["hw_model"] == 43
    assert d["role"] == "ROUTER"
    assert d["public_key"] == pk.hex()


def test_unknown_channel_is_encrypted():
    raw = P.mt_text("secret", key=bytes(range(16)), channel="Private")
    d = decode_meshtastic(raw, CHANNELS)
    assert d["encrypted"] is True
    assert "text" not in d
    assert "pki" not in d


def test_pki_direct_message():
    raw = P.mt_text("dm", dest=0x55555555, chan_hash=0, key=bytes(range(16)))
    d = decode_meshtastic(raw, CHANNELS)
    assert d["encrypted"] is True and d["pki"] is True


def test_custom_channel_from_config():
    psk = bytes(range(16, 32))
    config = {"sniffer_meshtastic_channels": [{"name": "Club", "psk": base64.b64encode(psk).decode()},
                                              {"bad": "entry"}]}
    channels = default_meshtastic_channels(["LongFast"], config)
    assert [c["name"] for c in channels] == ["LongFast", "Club"]
    d = decode_meshtastic(P.mt_text("members only", key=psk, channel="Club"), channels)
    assert d["channel"] == "Club" and d["text"] == "members only"


def test_short_packet():
    assert decode_meshtastic(b"\x00" * 10)["error"] == "short packet"


def test_wrong_key_with_colliding_hash_falls_through():
    # Same channel hash as LongFast but a different key: decryption yields
    # garbage protobuf, which must be rejected rather than reported.
    raw = P.mt_text("x" * 40, key=bytes(16), chan_hash=P.mt_channel_hash("LongFast"))
    d = decode_meshtastic(raw, CHANNELS)
    assert d.get("text") != "x" * 40


def test_expand_psk():
    assert expand_meshtastic_psk(b"") == b""
    assert expand_meshtastic_psk(b"\x00") == b""
    assert expand_meshtastic_psk(b"\x01") == MT_DEFAULT_PSK
    k2 = expand_meshtastic_psk(b"\x02")
    assert k2[:-1] == MT_DEFAULT_PSK[:-1] and k2[-1] == (MT_DEFAULT_PSK[-1] + 1) & 0xFF
    assert expand_meshtastic_psk(b"abc") == b"abc".ljust(16, b"\x00")
    assert len(expand_meshtastic_psk(b"a" * 20)) == 32
    assert expand_meshtastic_psk(b"a" * 32) == b"a" * 32


def test_channel_hash_matches_firmware_for_longfast():
    # LongFast with the default key hashes to 8 (the well-known value).
    assert meshtastic_channel("LongFast")["hash"] == 8


def test_pb_fields_roundtrip_and_errors():
    buf = P.pb_field(1, 150) + P.pb_field(2, b"abc") + P.pb_sfixed32(3, -5)
    f = pb_fields(buf)
    assert f[1] == [150] and f[2] == [b"abc"] and f[3] == [0xFFFFFFFB]
    with pytest.raises(ValueError):
        pb_fields(P.pb_varint(2 << 3 | 2) + P.pb_varint(10) + b"ab")  # truncated bytes
    with pytest.raises(ValueError):
        pb_fields(b"\x00")  # field 0
    with pytest.raises(ValueError):
        pb_fields(P.pb_varint(1 << 3 | 3))  # group wire type


def test_node_id_and_format():
    assert mt_node_id(0xA) == "!0000000a"
    d = decode_meshtastic(P.mt_text("yo"), CHANNELS)
    text = format_meshtastic(d)
    assert "from=!1234abcd" in text and "msg='yo'" in text
    enc = decode_meshtastic(P.mt_text("yo", key=bytes(range(16)), channel="Other"), CHANNELS)
    assert "(encrypted)" in format_meshtastic(enc)
