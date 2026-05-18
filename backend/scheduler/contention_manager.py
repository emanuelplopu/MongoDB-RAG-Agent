"""Resource contention management for the overnight scheduler."""

from __future__ import annotations

import logging
import subprocess
from enum import Enum
from typing import Optional

from backend.scheduler.models import PauseDecision, PauseReason, SchedulerBudget

logger = logging.getLogger(__name__)


class ContentionLevel(str, Enum):
    """Resource contention severity levels."""

    NONE = "none"  # No contention — proceed normally
    LEVEL_1 = "level_1"  # Different models — OK, no action needed
    LEVEL_2 = "level_2"  # Same model needed — pause current node, wait for lock
    LEVEL_3 = "level_3"  # System resource pressure — pause scheduler entirely


class ResourceContentionManager:
    """Manages resource contention between the scheduler and interactive workloads.

    Contention levels
    -----------------
    - **Level 1** – Different models requested (concurrent OK).
    - **Level 2** – Same model contention (pause node, wait for lock).
    - **Level 3** – System resource pressure (pause scheduler entirely).

    Monitors CPU, RAM, and (optionally) GPU usage.
    """

    def __init__(self, budget: SchedulerBudget) -> None:
        self.budget = budget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_contention(
        self,
        current_model: Optional[str] = None,
        requested_model: Optional[str] = None,
    ) -> ContentionLevel:
        """Evaluate the current contention level.

        Parameters
        ----------
        current_model:
            Model name currently in use by the scheduler (if any).
        requested_model:
            Model name an interactive or competing request needs.

        Returns
        -------
        ContentionLevel
            The highest applicable contention level.
        """
        # System resource pressure always takes precedence
        if not self.is_within_budget():
            return ContentionLevel.LEVEL_3

        # Same-model lock contention
        if (
            current_model is not None
            and requested_model is not None
            and current_model == requested_model
        ):
            return ContentionLevel.LEVEL_2

        # Different models or nothing specific requested — concurrent OK
        if current_model is not None and requested_model is not None:
            return ContentionLevel.LEVEL_1

        return ContentionLevel.NONE

    def get_system_metrics(self) -> dict[str, float]:
        """Return current system resource usage.

        Returns ``{"cpu_pct": float, "ram_pct": float, "gpu_pct": float}``.
        """
        return {
            "cpu_pct": self._get_cpu_usage(),
            "ram_pct": self._get_ram_usage(),
            "gpu_pct": self._get_gpu_usage(),
        }

    def is_within_budget(self) -> bool:
        """Return ``True`` if current resource usage is within the scheduler
        budget thresholds."""
        cpu = self._get_cpu_usage()
        ram = self._get_ram_usage()
        gpu = self._get_gpu_usage()

        within = (
            cpu <= self.budget.max_cpu_pct
            and ram <= self.budget.max_ram_pct
            and gpu <= self.budget.max_gpu_pct
        )
        if not within:
            logger.debug(
                "Resource budget exceeded — cpu=%.1f%% (max %.1f%%), "
                "ram=%.1f%% (max %.1f%%), gpu=%.1f%% (max %.1f%%)",
                cpu,
                self.budget.max_cpu_pct,
                ram,
                self.budget.max_ram_pct,
                gpu,
                self.budget.max_gpu_pct,
            )
        return within

    def check_resource_pressure(self) -> Optional[PauseDecision]:
        """Check if system resources exceed scheduler budget thresholds.

        Returns a :class:`PauseDecision` if the scheduler should pause,
        or ``None`` if resources are within budget.
        """
        metrics = self.get_system_metrics()
        cpu = metrics["cpu_pct"]
        ram = metrics["ram_pct"]
        gpu = metrics["gpu_pct"]

        breaches: list[str] = []
        if cpu > self.budget.max_cpu_pct:
            breaches.append(f"CPU {cpu:.1f}% > {self.budget.max_cpu_pct:.1f}%")
        if ram > self.budget.max_ram_pct:
            breaches.append(f"RAM {ram:.1f}% > {self.budget.max_ram_pct:.1f}%")
        if gpu > self.budget.max_gpu_pct:
            breaches.append(f"GPU {gpu:.1f}% > {self.budget.max_gpu_pct:.1f}%")

        if not breaches:
            return None

        return PauseDecision(
            should_pause=True,
            reason=PauseReason.RESOURCE_PRESSURE,
            details="; ".join(breaches),
            cpu_pct=cpu,
            ram_pct=ram,
            gpu_pct=gpu,
        )

    # ------------------------------------------------------------------
    # Internal — metric collection
    # ------------------------------------------------------------------

    def _get_cpu_usage(self) -> float:
        """Return CPU usage as a percentage (0–100).

        Uses ``psutil`` if available; returns ``0.0`` as a safe fallback.
        """
        try:
            import psutil  # noqa: WPS433 — optional runtime import

            return psutil.cpu_percent(interval=0.1)
        except ImportError:
            return 0.0
        except Exception as exc:
            logger.debug("cpu_percent error: %s", exc)
            return 0.0

    def _get_ram_usage(self) -> float:
        """Return RAM usage as a percentage (0–100).

        Uses ``psutil`` if available; returns ``0.0`` as a safe fallback.
        """
        try:
            import psutil  # noqa: WPS433

            return psutil.virtual_memory().percent
        except ImportError:
            return 0.0
        except Exception as exc:
            logger.debug("virtual_memory error: %s", exc)
            return 0.0

    def _get_gpu_usage(self) -> float:
        """Return GPU utilisation as a percentage (0–100).

        Attempts to call ``nvidia-smi``; returns ``0.0`` if no NVIDIA GPU is
        present or the command is unavailable.
        """
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # First line, first GPU
                value = result.stdout.strip().split("\n")[0].strip()
                return float(value)
        except FileNotFoundError:
            pass  # No nvidia-smi — expected on systems without NVIDIA GPU
        except Exception as exc:
            logger.debug("nvidia-smi error: %s", exc)
        return 0.0
