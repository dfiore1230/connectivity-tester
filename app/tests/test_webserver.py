import json
from pathlib import Path

import pytest

from app import webserver


FIXTURE_DIR = Path(__file__).parent / "data"


def copy_fixture(tmp_path, filename):
    src = FIXTURE_DIR / filename
    dst = tmp_path / filename
    dst.write_bytes(src.read_bytes())
    return dst


def test_build_daily_summary_from_file(monkeypatch, tmp_path):
    log_path = copy_fixture(tmp_path, "sample_connectivity.log")
    monkeypatch.setattr(webserver, "LOG_FILE", str(log_path))

    summary = webserver.build_daily_summary_from_file()

    assert [row["date"] for row in summary] == ["2024-06-01", "2024-06-02"]

    june_first = summary[0]
    assert june_first["total_probes"] == 3
    assert june_first["good_probes"] == 1
    assert june_first["degraded_probes"] == 1
    assert june_first["down_probes"] == 1
    assert pytest.approx(june_first["uptime_pct"], rel=1e-6) == (2 / 3) * 100
    assert pytest.approx(june_first["avg_loss_pct"], rel=1e-6) == 40.0
    assert pytest.approx(june_first["avg_rtt_ms"], rel=1e-6) == 15.3
    assert june_first["min_rtt_ms"] == 12.5
    assert june_first["max_rtt_ms"] == 18.1
    assert june_first["targets"] == ["Cloudflare", "GoogleDNS"]
    assert june_first["public_ips"] == ["203.0.113.5", "203.0.113.6"]

    june_second = summary[1]
    assert june_second["total_probes"] == 2
    assert june_second["down_probes"] == 0
    assert pytest.approx(june_second["uptime_pct"], rel=1e-6) == 100.0
    assert pytest.approx(june_second["avg_loss_pct"], rel=1e-6) == 0.0
    assert pytest.approx(june_second["avg_rtt_ms"], rel=1e-6) == 13.0
    assert june_second["targets"] == ["GoogleDNS", "Quad9"]
    assert june_second["public_ips"] == ["203.0.113.7"]


def test_read_recent_records_paginates(monkeypatch, tmp_path):
    log_file = tmp_path / "connectivity.log"
    records = [
        {"timestamp": f"2024-06-0{i}T00:00:00Z", "loss_pct": i, "sent": i}
        for i in range(1, 7)
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records))

    monkeypatch.setattr(webserver, "LOG_FILE", str(log_file))
    monkeypatch.setattr(webserver, "MAX_RECORDS", 3)

    result = webserver.read_recent_records()

    assert len(result) == 3
    assert [r["timestamp"] for r in result] == [
        "2024-06-04T00:00:00Z",
        "2024-06-05T00:00:00Z",
        "2024-06-06T00:00:00Z",
    ]


def test_read_records_for_day_ignores_invalid_and_other_days(monkeypatch, tmp_path):
    log_file = tmp_path / "connectivity.log"
    log_file.write_text(
        "\n".join(
            [
                "",
                "not-json",
                json.dumps({"timestamp": "2024-06-02T01:00:00Z", "loss_pct": 0}),
                json.dumps({"timestamp": "2024-06-01T23:59:00Z", "loss_pct": 50}),
                "{bad",
                json.dumps({"timestamp": "2024-06-02T02:00:00Z", "loss_pct": 10}),
            ]
        )
    )

    monkeypatch.setattr(webserver, "LOG_FILE", str(log_file))

    records = webserver.read_records_for_day("2024-06-02")

    assert [r["timestamp"] for r in records] == [
        "2024-06-02T01:00:00Z",
        "2024-06-02T02:00:00Z",
    ]
    assert all(r.get("loss_pct") is not None for r in records)


def test_js_csv_export_formatting():
    try:
        from py_mini_racer import py_mini_racer
    except Exception:  # pragma: no cover - dependency issues reported by pytest
        pytest.skip("py-mini-racer is required for JS helper tests")

    helpers_path = Path(__file__).parents[1] / "static" / "js" / "helpers.js"
    ctx = py_mini_racer.MiniRacer()
    ctx.eval("var module = {exports:{}};")
    ctx.eval(helpers_path.read_text())
    ctx.eval("var helpers = module.exports || this.ConnectivityHelpers;")

    daily_rows = [
        {
            "date": "2024-06-03",
            "total_probes": 10,
            "uptime_pct": 99.5,
            "avg_loss_pct": 0.5,
            "avg_rtt_ms": 14.2,
            "min_rtt_ms": 10.1,
            "max_rtt_ms": 20.9,
            "down_probes": 0,
            "targets": ["A,Inc", 'B"Corp'],
            "public_ips": ["198.51.100.1", "198.51.100.2"],
        }
    ]

    csv_output = ctx.call("helpers.buildDailyCsv", daily_rows)
    lines = csv_output.split("\n")

    assert lines[0].startswith("date,total_probes,uptime_pct")
    assert lines[1].startswith("2024-06-03,10,99.5")
    assert '"A,Inc; B""Corp"' in lines[1]
    assert "198.51.100.1; 198.51.100.2" in lines[1]


def test_read_config_supports_mtr(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.env"
    cfg_file.write_text(
        """
TARGETS=Example=9.9.9.9
INTERVAL_SECONDS=45
ENABLE_MTR=1
MTR_CYCLES=3
MTR_MAX_HOPS=20
MTR_TIMEOUT_SECONDS=8
""".strip()
    )

    monkeypatch.setattr(webserver, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(webserver, "ENV_TARGETS", "")
    monkeypatch.setattr(webserver, "ENV_TARGET_HOST", "8.8.8.8")
    monkeypatch.setattr(webserver, "ENV_INTERVAL", "30")
    monkeypatch.setattr(webserver, "ENV_ENABLE_MTR", "0")
    monkeypatch.setattr(webserver, "ENV_MTR_CYCLES", "1")
    monkeypatch.setattr(webserver, "ENV_MTR_MAX_HOPS", "32")
    monkeypatch.setattr(webserver, "ENV_MTR_TIMEOUT", "6")

    cfg = webserver.read_config()

    assert cfg["targets_display"] == "Example=9.9.9.9"
    assert cfg["interval"] == "45"
    assert cfg["enable_mtr"] == "1"
    assert cfg["mtr_cycles"] == "3"
    assert cfg["mtr_max_hops"] == "20"
    assert cfg["mtr_timeout"] == "8"
