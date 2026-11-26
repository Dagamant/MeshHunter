import hashlib
import struct

import pytest

from meshhunter.core.channels import make_channel, subscribed_channels
from meshhunter.core.sniffer_decode import decode_meshcore

import packets as P

KEY = P.ed25519_key("repeater-1")


def test_advert_with_location_and_name():
    raw = P.mc_advert(KEY, adv_type=P.ADV_TYPE_REPEATER, lat=38.154288, lon=-81.11461, name="Gauley Rptr")
    d = decode_meshcore(raw)
    assert "error" not in d
    assert d["proto"] == "meshcore"
    assert d["route_typename"] == "FLOOD"
    assert d["payload_typename"] == "ADVERT"
    assert d["adv_key"] == KEY.public_key().export_key(format="raw").hex()
    assert d["sig_ok"] is True
    assert d["adv_type"] == P.ADV_TYPE_REPEATER
    assert d["adv_lat"] == pytest.approx(38.154288)
    assert d["adv_lon"] == pytest.approx(-81.11461)
    assert d["adv_name"] == "Gauley Rptr"
    assert d["adv_timestamp"] == 1794200000


def test_advert_without_location():
    d = decode_meshcore(P.mc_advert(KEY, adv_type=P.ADV_TYPE_CHAT, name="phone"))
    assert d["sig_ok"] is True
    assert d["adv_type"] == P.ADV_TYPE_CHAT
    assert "adv_lat" not in d and "adv_lon" not in d
    assert d["adv_name"] == "phone"


def test_advert_bad_signature_is_flagged():
    d = decode_meshcore(P.mc_advert(KEY, lat=1.0, lon=2.0, name="x", corrupt_signature=True))
    assert d["sig_ok"] is False
    # Still decoded so the UI can show it, but callers must not trust it.
    assert d["adv_name"] == "x"


def test_advert_tampered_location_fails_signature():
    raw = bytearray(P.mc_advert(KEY, lat=10.0, lon=20.0, name="x"))
    # lat starts right after header, path byte, pubkey, ts, sig and flags.
    raw[2 + 32 + 4 + 64 + 1] ^= 0x01
    assert decode_meshcore(bytes(raw))["sig_ok"] is False


def test_short_advert_reports_error():
    raw = P.mc_packet(P.ROUTE_FLOOD, 0x04, b"\x00" * 50)
    d = decode_meshcore(raw)
    assert d["error"] == "short advert"


def test_path_hops_and_hash_size():
    path = bytes.fromhex("a1b2c3")
    d = decode_meshcore(P.mc_advert(KEY, path=path, name="n"))
    assert d["path_len"] == 3
    assert d["path_hash_size"] == 1
    assert d["path"] == "a1b2c3"
    assert d["path_hops"] == ["a1", "b2", "c3"]

    two_byte = P.mc_packet(P.ROUTE_DIRECT, 0x02, b"\x01\x02rest", path=bytes.fromhex("aabbccdd"), hash_size=2)
    d = decode_meshcore(two_byte)
    assert d["path_hash_size"] == 2
    assert d["path_hops"] == ["aabb", "ccdd"]


def test_transport_codes_on_tc_routes():
    raw = P.mc_packet(P.ROUTE_TC_FLOOD, 0x02, b"\x01\x02xx", transport=b"\xde\xad\xbe\xef")
    d = decode_meshcore(raw)
    assert d["route_typename"] == "TC_FLOOD"
    assert d["transport_codes"] == "deadbeef"
    assert d["dest_hash"] == "01" and d["src_hash"] == "02"


def test_truncated_path_reports_error():
    raw = P.mc_header(P.ROUTE_FLOOD, 0x02) + bytes([5]) + b"\x01\x02"
    assert decode_meshcore(raw)["error"] == "truncated path"


@pytest.mark.parametrize("raw", [b"", b"\x05", b"\x00\x00"])
def test_garbage_never_raises(raw):
    d = decode_meshcore(raw)
    assert d["proto"] == "meshcore"


def test_pkt_hash_ignores_path():
    """Relayed copies of a packet differ only in path, so they share pkt_hash."""
    a = decode_meshcore(P.mc_advert(KEY, name="n"))
    b = decode_meshcore(P.mc_advert(KEY, name="n", path=b"\x11\x22"))
    assert a["pkt_hash"] == b["pkt_hash"]
    payload = P.mc_advert_payload(KEY, name="n")
    assert a["pkt_hash"] == int.from_bytes(hashlib.sha256(payload).digest()[:4], "little")


def test_public_group_text_decrypts():
    raw = P.mc_grp_txt("Alice: hello mesh")
    d = decode_meshcore(raw, subscribed_channels({"meshcore_channels": []}))
    assert d["payload_typename"] == "GRP_TXT"
    assert d["chan_hash"] == "11"
    assert d["chan_name"] == "Public"
    assert d["message"] == "Alice: hello mesh"
    assert d["sender_timestamp"] == 1794200000
    assert "grp_cipher" not in d


def test_hashtag_group_text_decrypts_only_when_subscribed():
    chan = make_channel("#hamradio")
    raw = P.mc_grp_txt("Bob: 73", key=chan["key"])

    without = decode_meshcore(raw, subscribed_channels({"meshcore_channels": []}))
    assert "chan_name" not in without
    assert without["grp_cipher"] == raw[2:].hex()  # kept for channel_crack

    with_sub = decode_meshcore(raw, subscribed_channels({"meshcore_channels": [{"name": "#hamradio"}]}))
    assert with_sub["chan_name"] == "#hamradio"
    assert with_sub["message"] == "Bob: 73"


def test_group_text_wrong_mac_is_not_decrypted():
    payload = bytearray(P.mc_grp_txt_payload("x"))
    payload[1] ^= 0xFF
    raw = P.mc_packet(P.ROUTE_FLOOD, 0x05, bytes(payload))
    d = decode_meshcore(raw, subscribed_channels({"meshcore_channels": []}))
    assert "chan_name" not in d
    assert "grp_cipher" in d


def test_direct_and_anon_req_hashes():
    d = decode_meshcore(P.mc_packet(P.ROUTE_DIRECT, 0x02, b"\xaa\xbbcipher"))
    assert (d["payload_typename"], d["dest_hash"], d["src_hash"]) == ("TEXT_MSG", "aa", "bb")
    sender = bytes(range(32))
    d = decode_meshcore(P.mc_packet(P.ROUTE_FLOOD, 0x07, b"\xcc" + sender + b"rest"))
    assert d["payload_typename"] == "ANON_REQ"
    assert d["dest_hash"] == "cc" and d["sender_key"] == sender.hex()


def test_unknown_payload_type_name():
    d = decode_meshcore(P.mc_packet(P.ROUTE_FLOOD, 0x0C, b""))
    assert d["payload_typename"] == "UNK12"


def test_version_bits():
    raw = bytearray(P.mc_advert(KEY, name="n"))
    raw[0] |= 0x40
    assert decode_meshcore(bytes(raw))["payload_ver"] == 1


def test_lat_lon_are_signed():
    d = decode_meshcore(P.mc_advert(KEY, lat=-33.8688, lon=151.2093, name="syd"))
    assert d["adv_lat"] == pytest.approx(-33.8688)
    assert d["adv_lon"] == pytest.approx(151.2093)
    # The wire format is int32 micro-degrees.
    assert struct.calcsize("<ii") == 8
