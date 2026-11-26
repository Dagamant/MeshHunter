from meshhunter.core import band_plan as B


def test_bands_are_well_formed():
    for band in B.BANDS:
        assert band.lo < band.hi, band
        assert band.kind in B.KIND_COLORS, band


def test_bands_in_sorted_widest_first():
    found = B.bands_in(900e6, 930e6)
    widths = [b.hi - b.lo for b in found]
    assert widths == sorted(widths, reverse=True)
    assert found[0].label.startswith("ISM 902")
    assert B.bands_in(1e9, 1.1e9) == []


def test_bands_at():
    labels = {b.label for b in B.bands_at(908.42e6)}
    assert "Z-Wave" in labels
    assert any(lbl.startswith("ISM 902") for lbl in labels)


def test_assign_lanes_never_overlaps():
    placed = B.assign_lanes(B.bands_in(890e6, 945e6))
    lanes = {}
    for band, lane in placed:
        for other in lanes.setdefault(lane, []):
            assert band.lo >= other.hi or band.hi <= other.lo, (band, other)
        lanes[lane].append(band)
    assert len(placed) == len(B.bands_in(890e6, 945e6))


def test_assign_lanes_min_gap():
    a = B.Band(1.0, 2.0, "a", "", "ism")
    b = B.Band(2.0, 3.0, "b", "", "ism")
    assert [lane for _, lane in B.assign_lanes([a, b])] == [0, 0]
    assert [lane for _, lane in B.assign_lanes([a, b], min_gap=0.5)] == [0, 1]
