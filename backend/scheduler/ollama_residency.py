"""Ollama model residency and concurrency management for overnight scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

from backend.scheduler.models import OllamaModelState, OllamaModelStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_THRASHING_WINDOW_SECONDS: float = 600.0  # 10 minutes
_THRASHING_THRESHOLD: int = 5  # loads within the window
_DEFAULT_KEEP_ALIVE: str = "30m"
_DEFAULT_ACQUIRE_TIMEOUT: float = 300.0  # 5 minutes


class OllamaResidencyManager:
    """Manages Ollama model concurrency, residency tracking, and prewarming
    for the overnight exploration scheduler.

    Key behaviors:
    - Per-model async locks: allows parallel execution on DIFFERENT models.
    - Same-model serialization: concurrent requests for the same model are queued.
    - Residency tracking: knows which models are loaded / unloaded.
    - Prewarming: loads models before overnight runs start.
    - Thrashing detection: if >5 model loads in 10 minutes, reorder candidates
      by model to minimise swapping.
    - Interactive priority: yields to interactive user requests via signal flag.
    - Keep-alive: maintains 30-minute ``keep_alive`` to prevent Ollama eviction
      during runs.
    """

    def __init__(self, ollama_base_url: str = "http://localhost:11434") -> None:
        self.base_url = ollama_base_url.rstrip("/")
        self._model_states: dict[str, OllamaModelState] = {}
        self._locks: dict[str, asyncio.Lock] = {}  # per-model locks
        self._interactive_priority: bool = False  # flag for interactive preemption
        self._load_history: list[tuple[str, float]] = []  # (model_name, timestamp)

    # ------------------------------------------------------------------
    # Lock helpers
    # ------------------------------------------------------------------

    def _get_lock(self, model_name: str) -> asyncio.Lock:
        """Return (or create) the per-model asyncio lock."""
        if model_name not in self._locks:
            self._locks[model_name] = asyncio.Lock()
        return self._locks[model_name]

    def _get_state(self, model_name: str) -> OllamaModelState:
        """Return (or create) the state tracker for *model_name*."""
        if model_name not in self._model_states:
            self._model_states[model_name] = OllamaModelState(model_name=model_name)
        return self._model_states[model_name]

    # ------------------------------------------------------------------
    # Public API — acquire / release
    # ------------------------------------------------------------------

    async def acquire_model(
        self,
        model_name: str,
        run_id: str,
        timeout: float = _DEFAULT_ACQUIRE_TIMEOUT,
    ) -> bool:
        """Acquire exclusive access to a model for inference.

        Returns ``True`` if the lock was acquired successfully, ``False`` if
        the request timed out or was preempted by interactive priority.
        """
        lock = self._get_lock(model_name)

        # Fast-fail if interactive user has priority
        if self._interactive_priority:
            logger.info(
                "acquire_model(%s) declined — interactive priority active", model_name
            )
            return False

        try:
            acquired = await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "acquire_model(%s) timed out after %.1fs for run %s",
                model_name,
                timeout,
                run_id,
            )
            return False

        if not acquired:
            return False

        # Check interactive flag again after we waited
        if self._interactive_priority:
            lock.release()
            logger.info(
                "acquire_model(%s) releasing — interactive priority became active",
                model_name,
            )
            return False

        # Ensure the model is loaded in Ollama
        state = self._get_state(model_name)
        if state.status != OllamaModelStatus.LOADED:
            loaded = await self._load_model(model_name)
            if not loaded:
                lock.release()
                logger.error("acquire_model(%s) failed to load model", model_name)
                return False

        state.lock_holder = run_id
        state.last_used = datetime.utcnow()
        logger.debug("acquire_model(%s) succeeded for run %s", model_name, run_id)
        return True

    async def release_model(self, model_name: str, run_id: str) -> None:
        """Release model lock after inference is complete."""
        state = self._get_state(model_name)
        if state.lock_holder == run_id:
            state.lock_holder = None
            state.last_used = datetime.utcnow()

        lock = self._get_lock(model_name)
        try:
            lock.release()
        except RuntimeError:
            # Lock was not held — benign
            pass
        logger.debug("release_model(%s) by run %s", model_name, run_id)

    # ------------------------------------------------------------------
    # Prewarming
    # ------------------------------------------------------------------

    async def prewarm_models(
        self, model_names: list[str], keep_alive_minutes: int = 30
    ) -> dict[str, bool]:
        """Pre-load models before the overnight run starts.

        Returns a ``{model_name: success}`` dict.
        """
        keep_alive = f"{keep_alive_minutes}m"
        results: dict[str, bool] = {}
        for name in model_names:
            ok = await self._load_model(name, keep_alive=keep_alive)
            results[name] = ok
            if ok:
                state = self._get_state(name)
                state.keep_alive_until = datetime.utcnow() + timedelta(
                    minutes=keep_alive_minutes
                )
        logger.info("prewarm_models results: %s", results)
        return results

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------

    async def poll_model_status(self, model_name: str) -> OllamaModelStatus:
        """Check if *model_name* is currently loaded in Ollama."""
        loaded = await self._check_loaded_models()
        state = self._get_state(model_name)
        if model_name in loaded:
            state.status = OllamaModelStatus.LOADED
        else:
            state.status = OllamaModelStatus.UNLOADED
        return state.status

    async def refresh_all_states(self) -> None:
        """Poll the Ollama API and update all known model states."""
        loaded = await self._check_loaded_models()
        loaded_set = set(loaded)

        # Update known states
        for name, state in self._model_states.items():
            if name in loaded_set:
                state.status = OllamaModelStatus.LOADED
            else:
                state.status = OllamaModelStatus.UNLOADED

        # Add any newly-discovered loaded models
        for name in loaded_set:
            if name not in self._model_states:
                self._model_states[name] = OllamaModelState(
                    model_name=name, status=OllamaModelStatus.LOADED
                )

    # ------------------------------------------------------------------
    # Thrashing detection & candidate reordering
    # ------------------------------------------------------------------

    def detect_thrashing(self) -> bool:
        """Return ``True`` if more than 5 model loads occurred in the last
        10 minutes — a sign of excessive model swapping."""
        self._clean_old_loads()
        return len(self._load_history) > _THRASHING_THRESHOLD

    def sort_candidates_by_model(self, candidates: list[dict]) -> list[dict]:
        """Reorder *candidates* to minimise model swapping.

        Groups candidates by ``model_name`` so that all work for one model
        runs consecutively before switching to the next.
        """
        if not candidates:
            return candidates

        groups: dict[str, list[dict]] = {}
        no_model: list[dict] = []
        for c in candidates:
            model = c.get("model_name")
            if model:
                groups.setdefault(model, []).append(c)
            else:
                no_model.append(c)

        # Put models that are already loaded first
        loaded_models = {
            name
            for name, state in self._model_states.items()
            if state.status == OllamaModelStatus.LOADED
        }

        ordered: list[dict] = []
        # Loaded models first
        for model in sorted(groups.keys(), key=lambda m: m not in loaded_models):
            ordered.extend(groups[model])
        ordered.extend(no_model)
        return ordered

    # ------------------------------------------------------------------
    # Interactive priority
    # ------------------------------------------------------------------

    def set_interactive_priority(self, active: bool) -> None:
        """Signal that interactive users need model access.

        When *active* is ``True`` the scheduler will yield on the next
        ``acquire_model`` call.
        """
        self._interactive_priority = active
        logger.info("Interactive priority set to %s", active)

    def is_preempted(self) -> bool:
        """Check if interactive priority is currently active."""
        return self._interactive_priority

    # ------------------------------------------------------------------
    # Internal — Ollama HTTP helpers
    # ------------------------------------------------------------------

    async def _load_model(
        self, model_name: str, keep_alive: str = _DEFAULT_KEEP_ALIVE
    ) -> bool:
        """Call Ollama ``/api/generate`` with an empty prompt to trigger model
        loading / keep-alive refresh.

        Returns ``True`` on success.
        """
        state = self._get_state(model_name)
        state.status = OllamaModelStatus.LOADING
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model_name,
            "prompt": "",
            "keep_alive": keep_alive,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    state.status = OllamaModelStatus.LOADED
                    state.last_used = datetime.utcnow()
                    self._record_load(model_name)
                    logger.info("Loaded model %s (keep_alive=%s)", model_name, keep_alive)
                    return True
                else:
                    state.status = OllamaModelStatus.UNKNOWN
                    logger.error(
                        "Failed to load model %s: HTTP %d — %s",
                        model_name,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
        except httpx.TimeoutException:
            state.status = OllamaModelStatus.UNKNOWN
            logger.error("Timeout loading model %s", model_name)
            return False
        except httpx.ConnectError:
            state.status = OllamaModelStatus.UNKNOWN
            logger.error(
                "Cannot connect to Ollama at %s to load model %s",
                self.base_url,
                model_name,
            )
            return False
        except Exception as exc:
            state.status = OllamaModelStatus.UNKNOWN
            logger.exception("Unexpected error loading model %s: %s", model_name, exc)
            return False

    async def _check_loaded_models(self) -> list[str]:
        """Query ``GET /api/ps`` to discover which models are currently loaded."""
        url = f"{self.base_url}/api/ps"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    return [m.get("name", "") for m in models if m.get("name")]
                logger.warning(
                    "_check_loaded_models: HTTP %d from %s", resp.status_code, url
                )
                return []
        except httpx.ConnectError:
            logger.warning("Cannot connect to Ollama at %s", self.base_url)
            return []
        except Exception as exc:
            logger.warning("_check_loaded_models error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal — load history bookkeeping
    # ------------------------------------------------------------------

    def _record_load(self, model_name: str) -> None:
        """Record a model-load event for thrashing detection."""
        self._load_history.append((model_name, time.monotonic()))

    def _clean_old_loads(self) -> None:
        """Discard load events older than the thrashing window."""
        cutoff = time.monotonic() - _THRASHING_WINDOW_SECONDS
        self._load_history = [
            (name, ts) for name, ts in self._load_history if ts >= cutoff
        ]
