"""Resource sampler: nvidia-smi, docker stats and typeperf parsers, the sampler loop and the summary (no hardware needed)."""

from __future__ import annotations

import json
import time

from backend.evaluation.synthetic_corpus import resources as rs

FULL_LINE = ("97, 41, 9662, 16311, 70.04, 70.00, 92, [N/A], 3, 2137, 7001, 65, P0, 0x0000000000000004, Active, Not Active, Not Active, "
             "Not Active, Not Active, 5348889672, 0, 0, 0, 5, 8")


def test_parse_gpu_line_full_fields() -> None:
    g = rs.parse_gpu_line(rs.GPU_FIELDS_FULL, FULL_LINE)
    assert g["util_pct"] == 97 and g["mem_util_pct"] == 41 and g["mem_mb"] == 9662 and g["mem_total_mb"] == 16311
    assert g["power_w"] == 70.04 and g["power_limit_w"] == 70 and g["temp_c"] == 92 and "mem_temp_c" not in g
    assert g["tlimit_c"] == 3 and g["sm_mhz"] == 2137 and g["mem_mhz"] == 7001 and g["fan_pct"] == 65 and g["pstate"] == "P0"
    assert g["clock_reasons"] == "0x0000000000000004" and g["reasons"] == ["power_cap"]
    assert g["throttle_us"] == {"power_cap": 5348889672, "sw_thermal": 0, "hw_thermal": 0, "hw_power_brake": 0}
    assert g["pcie_gen"] == 5 and g["pcie_width"] == 8


def test_parse_gpu_line_basic_fields_and_thermal_mask() -> None:
    line = "50, 10, 1000, 16311, 30.5, 70.00, 80, 1500, 7001, [N/A], P2, 0x0000000000000044"
    g = rs.parse_gpu_line(rs.GPU_FIELDS_BASIC, line)
    assert g["reasons"] == ["hw_thermal", "power_cap"] and "fan_pct" not in g and "throttle_us" not in g
    assert rs.parse_gpu_line(rs.GPU_FIELDS_BASIC, "1, 2") is None
    assert rs.reasons_from_mask("0x0000000000000001") == [] and rs.reasons_from_mask("N/A") == [] and rs.reasons_from_mask(None) == []


def test_gpu_probe_falls_back_to_basic_fields() -> None:
    def runner(args):
        fields = tuple(next(a for a in args if a.startswith("--query-gpu="))[len("--query-gpu="):].split(","))
        if fields == rs.GPU_FIELDS_BASIC:
            return "50, 10, 1000, 16311, 30.5, 70.00, 80, 1500, 7001, 40, P2, 0x0000000000000000"
        if fields == rs.GPU_STATIC_BASIC:
            return "NVIDIA RTX PRO 2000 Blackwell, 597.16, 16311, 70.00"
        return None  # an older driver rejects the newer fields

    probe = rs.GpuProbe(runner=runner)
    assert probe.available and probe.fields == rs.GPU_FIELDS_BASIC
    assert probe.static["name"].startswith("NVIDIA") and probe.static["mem_total_mb"] == 16311 and probe.static["fields"] == "basic"
    assert probe.read()["temp_c"] == 80 and probe.read()["reasons"] == []
    assert not rs.GpuProbe(runner=lambda args: None).available


def test_parse_gpu_static_full() -> None:
    s = rs.parse_gpu_static(rs.GPU_STATIC_FULL, "NVIDIA RTX PRO 2000 Blackwell, 597.16, 98.06.4d.00.01, 16311, 70.00, 70.00, 3090, 9001, 5, 8")
    assert s["name"] == "NVIDIA RTX PRO 2000 Blackwell" and s["driver"] == "597.16" and s["vbios"] == "98.06.4d.00.01"
    assert s["mem_total_mb"] == 16311 and s["power_max_limit_w"] == 70 and s["sm_max_mhz"] == 3090 and s["pcie_width_max"] == 8


def test_parse_docker_stats_units() -> None:
    text = ("rag-mongodb;2.08%;1.464GiB / 23.47GiB;1.16GB / 18.2MB;23.2MB / 18.8MB\n"
            "rag-backend;0.15%;224.3MiB / 23.47GiB;98.9MB / 0B;733kB / 102kB\n")
    s = rs.parse_docker_stats(text)
    assert s["rag-mongodb"]["cpu_pct"] == 2.08 and round(s["rag-mongodb"]["mem_mb"]) == 1572 and round(s["rag-mongodb"]["mem_limit_mb"]) == 25201
    assert s["rag-mongodb"]["blk_read_mb"] == 1160 and s["rag-mongodb"]["net_tx_mb"] == 18.8
    assert s["rag-backend"]["blk_write_mb"] == 0 and s["rag-backend"]["net_rx_mb"] == 0.73
    assert rs.size_mb("x") is None and rs.parse_docker_stats("") == {}


def test_parse_typeperf_thermal_skips_status_lines() -> None:
    text = ('\n"(PDH-CSV 4.0)","\\\\HOST\\Thermal Zone Information(\\_TZ.TZ00)\\Temperature","\\\\HOST\\Thermal Zone Information(\\_TZ.TZ01)\\Temperature"\n'
            '"10/15/2026 01:22:09.855","301.150000","0.000000"\nExiting, please wait...\nThe command completed successfully.\n')
    assert rs.parse_typeperf_thermal(text) == {"TZ00": 28.0}
    assert rs.parse_typeperf_thermal("") == {} and rs.parse_typeperf_thermal("Exiting, please wait...\n") == {}


def test_normalize_sample_v1_layout() -> None:
    row = {"t": 14.3, "ts": "2026-10-11T22:59:41+00:00", "rag-backend-quellex": {"cpu_pct": 0.17, "mem_mb": 267.8},
           "gpu": {"util_pct": 97.0, "mem_mb": 9662.0, "power_w": 70.04, "temp_c": 70.0, "sm_mhz": 2137.0, "clock_reasons": "0x0000000000000004"},
           "ollama": [], "health": {"ms": 15.0, "ok": True}}
    n = rs.normalize_sample(row)
    assert n["v"] == 2 and n["containers"]["rag-backend-quellex"]["mem_mb"] == 267.8 and n["gpu"]["reasons"] == ["power_cap"]
    assert "rag-backend-quellex" not in n and n["health"]["ok"]
    assert rs.normalize_sample(n) == n


def _sample(t: float, temp: float, reasons: list[str], counters: dict[str, int], ram: float, core: float) -> dict:
    return {"v": 2, "t": t, "ts": f"2026-10-15T00:00:{int(t):02d}+00:00",
            "containers": {"rag-mongodb": {"cpu_pct": 2.0 + t, "mem_mb": 1000.0 + t * 10, "blk_read_mb": 100.0 + t}},
            "gpu": {"util_pct": 90.0, "mem_mb": 9000.0, "mem_total_mb": 16311.0, "power_w": 70.0, "power_limit_w": 70.0, "temp_c": temp,
                    "tlimit_c": 95 - temp, "sm_mhz": 2000.0, "mem_mhz": 7001.0, "fan_pct": 60.0, "pstate": "P0",
                    "clock_reasons": "0x4" if reasons else "0x0", "reasons": reasons, "throttle_us": counters},
            "host": {"cpu_pct": 10.0, "cpu_per_core": [core] * 4, "ram_used_gb": ram, "ram_total_gb": 256.0, "ram_pct": ram / 2.56,
                     "swap_used_gb": 0.0, "disk_read_mb": 1000.0 + t * 5, "net_recv_mb": 10.0,
                     "procs": {"ollama": {"rss_gb": 1.0, "cpu_pct": 2.0, "count": 2}}},
            "ollama": [{"name": "gemma4:12b", "size_gib": 7.8, "vram_gib": 7.8, "context_length": 32768, "offloaded": False}],
            "health": {"ms": 15.0, "ok": t < 8}, "sample_ms": 300}


def test_summarize_samples_throttle_time_and_host() -> None:
    samples = [_sample(0, 60, [], {"power_cap": 0, "hw_thermal": 0}, 30.0, 5),
               _sample(5, 80, ["power_cap"], {"power_cap": 5_000_000, "hw_thermal": 0}, 31.0, 50),
               _sample(10, 94, ["power_cap", "hw_thermal"], {"power_cap": 10_000_000, "hw_thermal": 3_000_000}, 32.0, 90)]
    s = rs.summarize_samples(samples, ["rag-mongodb"])
    assert s["samples"] == 3 and s["interval_s"] == 5.0 and s["duration_s"] == 10.0
    assert s["rag-mongodb"]["mem_mb"]["max"] == 1100.0 and s["rag-mongodb"]["blk_read_mb"] == 10.0
    g = s["gpu"]
    assert g["temp_c"]["max"] == 94 and g["throttled_samples"] == 2 and g["reasons_s"] == {"power_cap": 10.0, "hw_thermal": 5.0}
    assert g["thermal_s"] == 5.0 and g["throttle_counters_s"] == {"power_cap": 10.0, "hw_thermal": 3.0}
    assert g["tlimit_min_c"] == 1 and g["mem_total_mb"] == 16311 and g["pstates"] == {"P0": 3} and g["fan_pct"]["max"] == 60
    h = s["host"]
    assert h["ram_used_gb"]["max"] == 32.0 and h["ram_total_gb"] == 256.0 and h["disk_read_mb"] == 50.0
    assert h["procs"]["ollama"]["rss_gb"]["max"] == 1.0 and h["procs"]["ollama"]["cpu_pct"]["mean"] == 2.0
    assert s["ollama"]["models_seen"] == ["gemma4:12b"] and s["ollama"]["vram_gib_max"] == 7.8 and s["ollama"]["samples_with_offload"] == 0
    assert s["health"]["failures"] == 1 and s["sample_ms"]["max"] == 300
    assert rs.summarize_samples([]) == {"samples": 0}


class _FakeGpu:
    available = True
    static = {"name": "Fake GPU", "mem_total_mb": 16311.0, "fields": "full"}

    def read(self) -> dict:
        return {"util_pct": 50.0, "mem_mb": 1000.0, "temp_c": 70.0, "reasons": [], "clock_reasons": "0x0"}


class _FakeHost:
    def static(self) -> dict:
        return {"hostname": "test", "cores_logical": 4, "ram_total_gb": 16.0}

    def sample(self) -> dict:
        return {"cpu_pct": 12.5, "cpu_per_core": [10, 15, 10, 15], "ram_used_gb": 4.0, "ram_total_gb": 16.0, "ram_pct": 25.0}


class _FakeThermal:
    available = True
    source = "fake"

    def sample(self) -> dict:
        return {"TZ00": 40.5}


def test_sampler_writes_series_and_meta(tmp_path) -> None:
    out = tmp_path / "resources_chat_fast.jsonl"
    sampler = rs.ResourceSampler(["c1"], 0.05, out_path=out, gpu_probe=_FakeGpu(), host_probe=_FakeHost(), thermal_probe=_FakeThermal(),
                                 docker_probe=lambda: {"c1": {"cpu_pct": 1.0, "mem_mb": 10.0}, "c2": {"cpu_pct": 2.0, "mem_mb": 20.0}},
                                 docker_info_probe=lambda: {"cpus": 4, "mem_gb": 8.0})
    with sampler:
        time.sleep(0.4)
    assert len(sampler.samples) >= 3
    row = sampler.samples[0]
    assert row["v"] == 2 and row["containers"]["c2"]["mem_mb"] == 20.0 and row["gpu"]["temp_c"] == 70.0
    assert row["host"]["cpu_temp_c"] == 40.5 and row["host"]["thermal_zones"] == {"TZ00": 40.5} and row["sample_ms"] >= 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(sampler.samples) and json.loads(lines[0])["host"]["ram_used_gb"] == 4.0
    assert sampler.meta_path == tmp_path / "resources_chat_fast.meta.json"
    meta = json.loads(sampler.meta_path.read_text(encoding="utf-8"))
    assert meta["gpu"]["name"] == "Fake GPU" and meta["docker"]["cpus"] == 4 and meta["host"]["hostname"] == "test"
    assert meta["thermal_source"] == "fake" and meta["containers"] == ["c1"] and meta["interval_s"] == 0.05
    s = sampler.summary()
    assert s["c1"]["cpu_pct"]["max"] == 1.0 and s["c2"]["mem_mb"]["max"] == 20.0 and s["gpu"]["temp_c"]["max"] == 70.0
    assert s["host"]["cpu_temp_c"]["max"] == 40.5 and s["static"]["gpu"]["name"] == "Fake GPU"


def test_sampler_survives_failing_probe() -> None:
    def boom() -> dict:
        raise RuntimeError("docker down")

    sampler = rs.ResourceSampler([], 0.05, gpu_probe=_FakeGpu(), host_probe=_FakeHost(), thermal_probe=_FakeThermal(),
                                 docker_probe=boom, docker_info_probe=dict)
    with sampler:
        time.sleep(0.2)
    assert sampler.samples and sampler.samples[0]["errors"]["containers"].startswith("RuntimeError")
    assert "containers" not in sampler.samples[0] and sampler.samples[0]["gpu"]["util_pct"] == 50.0
    assert sampler.summary()["probe_errors"] == {"containers": len(sampler.samples)}


def test_load_series_skips_bad_lines(tmp_path) -> None:
    p = tmp_path / "resources_x.jsonl"
    p.write_text('{"t": 0, "ts": "2026-10-15T00:00:00+00:00", "gpu": {"temp_c": 50, "clock_reasons": "0x0"}}\nnot json\n\n', encoding="utf-8")
    rows = rs.load_series(p)
    assert len(rows) == 1 and rows[0]["gpu"]["reasons"] == [] and rows[0]["containers"] == {}
