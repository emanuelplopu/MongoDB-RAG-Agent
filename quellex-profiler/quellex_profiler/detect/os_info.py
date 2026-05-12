"""OS name, version, and CPU architecture detection."""

from __future__ import annotations

import platform


def _normalize_arch(machine: str) -> str:
    m = machine.lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m.startswith("armv"):
        return "arm"
    if m in ("i386", "i686", "x86"):
        return "x86"
    return m or "unknown"


def detect_os() -> tuple[str, str, str]:
    """Return ``(os_name, os_version, arch)``.

    Example returns:
        ("Windows 11", "10.0.22631", "x86_64")
        ("macOS 14.5", "23.5.0", "arm64")
        ("Ubuntu 22.04", "5.15.0", "x86_64")
    """
    system = platform.system()
    machine = _normalize_arch(platform.machine())

    if system == "Windows":
        release = platform.release()  # "10", "11"
        version = platform.version()  # e.g. "10.0.22631"
        # Heuristic: Win11 builds are >= 22000
        try:
            build = int(version.split(".")[-1])
            if build >= 22000:
                release = "11"
        except (ValueError, IndexError):
            pass
        return (f"Windows {release}", version, machine)

    if system == "Darwin":
        mac_ver = platform.mac_ver()[0] or ""
        kernel = platform.release()
        label = f"macOS {mac_ver}" if mac_ver else "macOS"
        return (label, kernel, machine)

    if system == "Linux":
        try:
            import distro  # type: ignore

            name = distro.name(pretty=True) or "Linux"
            version = distro.version(pretty=False) or platform.release()
            return (name, version, machine)
        except Exception:
            return ("Linux", platform.release(), machine)

    return (system or "Unknown", platform.release(), machine)
