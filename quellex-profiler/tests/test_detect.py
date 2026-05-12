"""Detection parser tests (no real subprocess calls)."""

from quellex_profiler.detect.gpu_nvidia import parse_nvidia_smi
from quellex_profiler.detect.gpu_windows import parse_win32_json
from quellex_profiler.detect.gpu_apple import parse_system_profiler_json


# ---- NVIDIA ----

def test_parse_nvidia_smi_single_rtx4090():
    out = "NVIDIA GeForce RTX 4090, 24564, 551.23, 8.9\n"
    gpus = parse_nvidia_smi(out)
    assert len(gpus) == 1
    g = gpus[0]
    assert g.vendor == "nvidia"
    assert "4090" in g.model
    assert g.vram_gb >= 23.5
    assert g.compute_capability == "8.9"
    assert g.driver_version == "551.23"
    assert g.is_integrated is False


def test_parse_nvidia_smi_multiple():
    out = (
        "NVIDIA RTX A6000, 49140, 550.0, 8.6\n"
        "NVIDIA GeForce RTX 3060, 12288, 550.0, 8.6\n"
    )
    gpus = parse_nvidia_smi(out)
    assert len(gpus) == 2
    assert gpus[0].vram_gb >= 47
    assert gpus[1].vram_gb >= 11.9


def test_parse_nvidia_smi_empty():
    assert parse_nvidia_smi("") == []


# ---- Windows WMI ----

def test_parse_win32_amd_radeon():
    stdout = (
        '[{"Name": "AMD Radeon RX 6700 XT", '
        '"AdapterRAM": 4293918720, '
        '"DriverVersion": "31.0.24033.1003"}]'
    )
    gpus = parse_win32_json(stdout)
    assert len(gpus) == 1
    g = gpus[0]
    assert g.vendor == "amd"
    assert g.is_integrated is False
    assert g.driver_version == "31.0.24033.1003"


def test_parse_win32_intel_uhd_iGPU():
    stdout = (
        '{"Name": "Intel(R) UHD Graphics 630", '
        '"AdapterRAM": 1073741824, "DriverVersion": "27.20.100.8935"}'
    )
    gpus = parse_win32_json(stdout)
    assert len(gpus) == 1
    g = gpus[0]
    assert g.vendor == "intel"
    assert g.is_integrated is True
    assert g.vram_gb <= 2.0  # capped for iGPU


def test_parse_win32_skips_nvidia():
    stdout = (
        '[{"Name": "NVIDIA GeForce RTX 4070", "AdapterRAM": 0, "DriverVersion": ""}]'
    )
    assert parse_win32_json(stdout) == []


def test_parse_win32_invalid_json():
    assert parse_win32_json("not json") == []


# ---- macOS system_profiler ----

def test_parse_apple_silicon_m3_max():
    stdout = '''{
      "SPDisplaysDataType": [
        {"_name": "Apple M3 Max",
         "sppci_model": "Apple M3 Max",
         "spdisplays_vendor": "sppci_vendor_Apple",
         "spdisplays_vram_shared": "48 GB"}
      ]
    }'''
    # Unified memory total = 64 GB
    gpus = parse_system_profiler_json(stdout, unified_memory_gb=64.0)
    assert len(gpus) == 1
    g = gpus[0]
    assert g.vendor == "apple"
    assert g.unified_memory is True
    assert g.is_integrated is True
    # 75% of 64 = 48.0
    assert g.vram_gb == 48.0


def test_parse_apple_discrete_amd():
    stdout = '''{
      "SPDisplaysDataType": [
        {"_name": "AMD Radeon Pro 5500M",
         "sppci_model": "AMD Radeon Pro 5500M",
         "spdisplays_vendor": "sppci_vendor_ATI",
         "spdisplays_vram": "8 GB"}
      ]
    }'''
    gpus = parse_system_profiler_json(stdout, unified_memory_gb=0.0)
    assert len(gpus) == 1
    g = gpus[0]
    assert g.vendor == "amd"
    assert g.vram_gb == 8.0


def test_parse_apple_invalid_json():
    assert parse_system_profiler_json("not json") == []
