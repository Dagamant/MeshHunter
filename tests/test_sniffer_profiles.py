import pytest

from meshhunter.core import sniffer_profiles as S


def keys(assignment):
    return [[p.key for p in radio] for radio in assignment]


def test_meshtastic_default_slots_match_firmware():
    # Well-known US defaults (also checked by the CardputerZero mesh_tests).
    by_key = S.profiles_by_key({})
    assert by_key["mt_longfast"].freq == pytest.approx(906.875)
    assert by_key["mt_mediumfast"].freq == pytest.approx(913.125)
    assert by_key["meshcore_us"].freq == pytest.approx(910.525)
    assert by_key["meshcore_us"].sync == S.SYNC_MESHCORE


def test_djb2():
    assert S.djb2("") == 5381
    assert S.djb2("a") == (5381 * 33 + ord("a")) & 0xFFFFFFFF


def test_profile_helpers():
    p = S.profiles_by_key({})["meshcore_us"]
    assert p.symbol_ms() == pytest.approx(128 / 62.5)
    assert p.auto_dwell_ms() == 21
    assert p.to_command(2) == "P 2 910.5250 62.5 7 5 12 8"
    assert "910.525 MHz" in p.label()


def test_custom_profiles_skip_bad_entries():
    config = {"sniffer_custom_profiles": [
        {"key": "local", "name": "Local MC", "family": "meshcore", "freq": 927.875, "bw": 62.5, "sf": 7, "sync": "12"},
        {"key": "int_sync", "freq": 915, "bw": 250, "sf": 9, "sync": 43},
        {"key": "broken"},
        {"name": "no key", "freq": 1, "bw": 1, "sf": 7},
    ]}
    custom = S.custom_profiles(config)
    assert [p.key for p in custom] == ["local", "int_sync"]
    assert custom[0].sync == 0x12 and custom[1].sync == 43
    assert custom[1].name == "int_sync"
    assert "local" in S.profiles_by_key(config)


def test_selected_profiles():
    assert [p.key for p in S.selected_profiles({})] == S.DEFAULT_HOP_PROFILES
    assert [p.key for p in S.selected_profiles({"sniffer_mode": "fixed"})] == ["meshcore_us"]
    fixed = {"sniffer_mode": "fixed", "sniffer_fixed_profile": "mt_longfast"}
    assert [p.key for p in S.selected_profiles(fixed)] == ["mt_longfast"]
    # Unknown keys fall back to the default fixed profile.
    assert [p.key for p in S.selected_profiles({"sniffer_hop_profiles": ["nope"]})] == ["meshcore_us"]


def test_auto_assign_hop_round_robin():
    assert keys(S.assign_profiles({}, 1)) == [S.DEFAULT_HOP_PROFILES]
    assert keys(S.assign_profiles({}, 2)) == [["meshcore_us", "mt_mediumfast"], ["mt_longfast"]]
    assert keys(S.assign_profiles({}, 4)) == [["meshcore_us"], ["mt_longfast"], ["mt_mediumfast"], []]


def test_auto_assign_fixed_mode():
    config = {"sniffer_mode": "fixed", "sniffer_fixed_profile": "mt_longfast"}
    assert keys(S.assign_profiles(config, 3)) == [["mt_longfast"], ["meshcore_us"], ["mt_mediumfast"]]


def test_pinned_radio_takes_its_profile_and_others_share_the_rest():
    config = {"sniffer_radio_overrides": {"USB-B": "mt_longfast"}}
    out = keys(S.assign_profiles(config, 2, labels=["USB-A", "USB-B"]))
    assert out == [["meshcore_us", "mt_mediumfast"], ["mt_longfast"]]


def test_single_pinned_radio():
    config = {"sniffer_radio_overrides": {"solo": "mt_shortfast"}}
    assert keys(S.assign_profiles(config, 1, labels=["solo"])) == [["mt_shortfast"]]


def test_pinned_to_fixed_profile_in_fixed_mode():
    config = {"sniffer_mode": "fixed", "sniffer_radio_overrides": {"A": "meshcore_us"}}
    out = keys(S.assign_profiles(config, 3, labels=["A", "B", "C"]))
    assert out == [["meshcore_us"], ["mt_longfast"], ["mt_mediumfast"]]


def test_all_profiles_pinned_leaves_others_idle():
    config = {"sniffer_hop_profiles": ["meshcore_us"], "sniffer_radio_overrides": {"A": "meshcore_us"}}
    assert keys(S.assign_profiles(config, 2, labels=["A", "B"])) == [["meshcore_us"], []]


def test_override_with_unknown_profile_is_ignored():
    config = {"sniffer_radio_overrides": {"A": "does_not_exist"}}
    assert keys(S.assign_profiles(config, 2, labels=["A", "B"])) == keys(S.assign_profiles({}, 2))
