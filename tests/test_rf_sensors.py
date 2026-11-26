import csv
import json
import sys
import textwrap

import pytest

from meshhunter.core import rf_sensors as R


@pytest.mark.parametrize("model,category", [
    ("ERT-SCM", "Meter"),
    ("Neptune-R900", "Meter"),
    ("Schrader", "TPMS"),
    ("Toyota", "TPMS"),
    ("Honeywell-Security", "Security"),
    ("Honeywell-ActivLink", "Doorbell"),
    ("Fineoffset-WH65B", "Weather"),
    ("Acurite-Tower", "Weather"),
    ("LaCrosse-TX141THBv2", "Weather"),
    ("Govee-Water", "Leak"),
    ("Generic-Remote", "Remote"),
    ("Something-Unknown", "Other"),
    ("", "Other"),
    (None, "Other"),
])
def test_classify(model, category):
    assert R.classify(model) == category


def test_hop_plan_order_and_extras():
    assert [h[0] for h in R.hop_plan()] == [912_600_000, 915_000_000, 916_600_000]
    plan = R.hop_plan(["433", "315"])
    assert [h[0] for h in plan][-2:] == [315_000_000, 433_920_000]
    assert len(R.hop_plan(["nope"])) == 3  # unknown extras are ignored


def test_device_key():
    assert R.device_key({"model": "Acurite-Tower", "id": 1234, "channel": "A"}) == "Acurite-Tower|1234|A"
    assert R.device_key({}) == "?||"


def test_readings_formatting():
    event = {
        "time": "2026-11-21T00:00:00", "model": "Fineoffset-WH65B", "id": 7,
        "temperature_C": 21.43, "humidity": 56, "battery_ok": 1, "wind_avg_m_s": 3.25,
        "rain_mm": 1234.5, "rssi": -10.0, "extra": {"nested": 1},
    }
    assert R.readings(event) == "21.4°C · 56% · bat ok · wind avg 3.2 m/s · rain 1234 mm"
    assert R.readings({"battery_ok": 0}) == "bat LOW"
    assert R.readings({"consumption_data": 123}) == "consumption data 123"


def test_rtl_433_command():
    cmd = R.rtl_433_command("/usr/bin/rtl_433", 915e6, 2.4e6, 20, gain_db=40.0)
    assert cmd[:7] == ["/usr/bin/rtl_433", "-f", "915000000", "-s", "2400000", "-T", "20"]
    assert cmd[-2:] == ["-g", "40"]
    assert "-g" not in R.rtl_433_command("x", 1, 1, 1)


def test_sensor_log_add_save_load(tmp_path):
    csv_path, raw_path = tmp_path / "rf.csv", tmp_path / "rf.jsonl"
    log = R.SensorLog(csv_path, raw_path)
    base = {"model": "Acurite-Tower", "id": 99, "channel": "B", "temperature_C": 20.0, "hop_hz": 915_000_000}
    log.add({**base, "time": "2026-11-21T10:00:00", "rssi": -20.0, "lat": 1.0, "lon": 2.0})
    log.add({**base, "time": "2026-11-21T10:01:00", "rssi": -5.0, "lat": 3.0, "lon": 4.0})
    row = log.add({**base, "time": "2026-11-21T10:02:00", "rssi": -30.0, "lat": 5.0, "lon": 6.0})
    assert row["count"] == "3"
    assert row["category"] == "Weather"
    assert row["first_seen"] == "2026-11-21T10:00:00"
    assert row["last_seen"] == "2026-11-21T10:02:00"
    assert row["best_rssi"] == "-5.0" and row["last_rssi"] == "-30.0"
    # Location is where the signal was strongest, not the latest.
    assert (row["lat"], row["lon"]) == ("3.000000", "4.000000")
    assert row["freq_mhz"] == "915.000"
    assert row["readings"] == "20.0°C"
    log.save()

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["key"] == "Acurite-Tower|99|B"
    assert len(raw_path.read_text(encoding="utf-8").splitlines()) == 3

    reloaded = R.SensorLog(csv_path, raw_path)
    again = reloaded.add({**base, "time": "2026-11-21T11:00:00", "rssi": -50.0})
    assert again["count"] == "4"
    assert again["first_seen"] == "2026-11-21T10:00:00"
    assert again["best_rssi"] == "-5.0"


def test_sensor_log_freq_from_event(tmp_path):
    log = R.SensorLog(tmp_path / "a.csv", tmp_path / "a.jsonl")
    row = log.add({"model": "ERT-SCM", "id": 1, "freq": 912.38})
    assert row["freq_mhz"] == "912.380"
    assert row["best_rssi"] == "" and row["last_rssi"] == ""


@pytest.mark.skipif(sys.platform == "win32", reason="fake rtl_433 is a POSIX script")
def test_worker_parses_rtl_433_output(tmp_path):
    """Run the worker against a stand-in rtl_433 that prints JSON lines."""
    fake = tmp_path / "rtl_433"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        print("rtl_433 version x")
        print("not json {{")
        print("{{broken")
        print(json.dumps({{"no_model": 1}}))
        print(json.dumps({{"model": "Acurite-Tower", "id": 5, "temperature_C": 3.5, "time": "2026-11-21T00:00:00"}}))
        sys.stdout.flush()
        """))
    fake.chmod(0o755)

    class Gps:
        fix = (40.5, -80.25)

    events, hops, errors = [], [], []
    worker = R.Rtl433Worker(events.append, lambda *a: hops.append(a), errors.append,
                            exe=str(fake), gps_state=Gps())
    # One hop is enough; stop the loop once it has run.
    original = worker._run_hop

    def run_once(*args):
        result = original(*args)
        worker._stop.set()
        return result

    worker._run_hop = run_once
    worker.run()
    assert errors == []
    assert hops[0][0] == 912_600_000
    assert len(events) == 1
    ev = events[0]
    assert ev["model"] == "Acurite-Tower" and ev["hop_hz"] == 912_600_000
    assert (ev["lat"], ev["lon"]) == (40.5, -80.25)


def test_worker_without_rtl_433_reports_install_hint():
    errors = []
    R.Rtl433Worker(lambda e: None, lambda *a: None, errors.append, exe="").run()
    assert errors == [R.INSTALL_HINT]
