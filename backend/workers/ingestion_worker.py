"""
Ingestion Worker Process

Standalone worker that processes document ingestion jobs from MongoDB queue.
Runs as a separate process from the FastAPI backend to guarantee API responsiveness
during heavy ingestion workloads.

Usage:
    python -m backend.workers.ingestion_worker
"""

import os
import sys
import signal
import asyncio
import logging
import yaml
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import deque

from pymongo import AsyncMongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from dotenv import load_dotenv

from backend.core.ingestion_jobs import (
    FILES_STARTED_FIELD, RUNNER_WORKER, TENANT_FIELD, WORKER_OWNED, reached_files, resume_config
)
from backend.services.activity_events import (
    ActivityEventRecorder, get_activity_recorder, set_activity_recorder
)
from backend.services.ingestion_counters import apply_stats_counts
from backend.services.telemetry_store import SHARED_TENANT
from src.ingestion import telemetry_hooks
from src.ingestion.progress import progress_percent_label
from backend.services.ingestion_eta import count_by_extension, extension_of

# Import backup service for post-ingestion backups
try:
    from backend.services.backup_service import BackupService
    BACKUP_SERVICE_AVAILABLE = True
except ImportError:
    BACKUP_SERVICE_AVAILABLE = False

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Collection names
INGESTION_JOBS_COLLECTION = "ingestion_jobs"
INGESTION_LOGS_COLLECTION = "ingestion_logs"
INGESTION_CONFIG_COLLECTION = "ingestion_config"

# How often a paused job re-checks for RESUME/STOP commands and shutdown
_PAUSE_POLL_SECONDS: float = 1.0


def worker_default_tenant() -> str:
    """Tenant label of the worker's own (not job-bound) telemetry.

    The one ingestion-worker container serves every tenant's jobs, so unlike a backend it has
    no tenant of its own.

    Returns:
        ``TENANT_ID`` when the deployment pins the worker to one tenant, else ``"shared"``.
        The settings default (``recallhub``) is deliberately not used: it mislabelled Quellex
        activity on Quellex-only hosts.
    """
    return os.getenv("TENANT_ID", "").strip() or SHARED_TENANT


def job_tenant(job: Dict[str, Any]) -> str:
    """Tenant a job belongs to: the ``tenant`` its creator recorded, else the worker's default.

    Args:
        job: The claimed ``ingestion_jobs`` document.
    """
    tenant = job.get(TENANT_FIELD)
    return tenant.strip() if isinstance(tenant, str) and tenant.strip() else worker_default_tenant()


# Status constants (matching backend schemas)
class IngestionStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


class IngestionWorker:
    """
    Standalone worker process for document ingestion.
    
    Polls MongoDB for pending jobs and processes them using the
    DocumentIngestionPipeline. Communicates with the backend API
    via MongoDB collections for job status, control commands, and logs.
    """
    
    def __init__(self):
        self.shutdown_requested = False
        self.current_job_id: Optional[str] = None
        # State of the current job: a STOP command makes it final, a pause is resumed held
        self._stop_requested = False
        self._paused = False
        self.mongo_client: Optional[AsyncMongoClient] = None
        self.db = None
        self.pid = os.getpid()
        
        # Configuration (loaded from DB or defaults)
        self.config = {
            "max_concurrent_files": 2,
            "embedding_batch_size": 100,
            "thread_pool_workers": 4,
            "process_isolation_enabled": True,
            "file_processing_timeout": 300,
            "job_poll_interval": 1.0,
        }
        
        # Log buffer for batch writes
        self._log_buffer: deque = deque(maxlen=100)
        self._last_log_flush = datetime.now()

        # Telemetry history (activity events); None when disabled or unavailable
        self._telemetry_store = None
        # MongoDB statistics sampler (db_samples); None when disabled or unavailable
        self._db_stats_collector = None
    
    async def initialize(self):
        """Initialize MongoDB connection using active profile."""
        mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
        
        # Load profile configuration to get database name
        profiles_path = os.getenv("PROFILES_PATH", "profiles.yaml")
        mongodb_database = os.getenv("MONGODB_DATABASE", "rag_db")
        
        try:
            with open(profiles_path, 'r') as f:
                profiles_config = yaml.safe_load(f)
                active_profile = profiles_config.get("active_profile", "default")
                profile_data = profiles_config.get("profiles", {}).get(active_profile, {})
                if profile_data and "database" in profile_data:
                    mongodb_database = profile_data["database"]
                    logger.info(f"Using profile '{active_profile}' database: {mongodb_database}")
        except Exception as e:
            logger.warning(f"Could not load profile config: {e}, using env default: {mongodb_database}")
        
        try:
            self.mongo_client = AsyncMongoClient(
                mongodb_uri,
                serverSelectionTimeoutMS=10000
            )
            self.db = self.mongo_client[mongodb_database]
            self.db_name = mongodb_database
            
            # Verify connection
            await self.mongo_client.admin.command("ping")
            logger.info(f"Connected to MongoDB database: {mongodb_database}")
            
            # Load configuration
            await self._load_config()

        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise

        await self._start_telemetry()

    async def _start_telemetry(self) -> None:
        """Record job phases and per-file step timings into the telemetry history (non-fatal)."""
        try:
            from backend.core.config import settings
            if not settings.telemetry_history_enabled:
                return
            from backend.services.telemetry_store import TelemetryStore

            self._telemetry_store = TelemetryStore.from_settings(settings)
            await self._telemetry_store.ensure_collections()
            recorder = ActivityEventRecorder(
                self._telemetry_store, tenant=worker_default_tenant(), source="worker",
                heartbeat_interval=float(settings.process_heartbeat_seconds),
            )
            await recorder.start()
            set_activity_recorder(recorder)
            logger.info(f"Telemetry history enabled ({recorder.key})")
        except Exception as e:
            logger.warning(f"Telemetry history unavailable (non-fatal): {e}")
        await self._start_db_stats()

    async def _start_db_stats(self) -> None:
        """Sample MongoDB statistics on the shared schedule (one process per host takes each slot)."""
        try:
            from backend.core.config import settings
            if self._telemetry_store is None or not settings.db_stats_enabled:
                return
            from backend.services.db_stats import DbStatsCollector

            def _profile_databases() -> Dict[str, str]:
                from src.profile import get_profile_manager

                pm = get_profile_manager()
                pm.reload()
                return {p.database: key for key, p in pm.list_profiles().items()}

            self._db_stats_collector = DbStatsCollector(
                self._telemetry_store,
                holder=f"{worker_default_tenant()}:worker",
                interval_seconds=settings.db_stats_interval_minutes * 60,
                profile_databases=_profile_databases,
                telemetry_database=settings.telemetry_database,
            )
            await self._db_stats_collector.start()
            logger.info(f"db-stats collector started (every {settings.db_stats_interval_minutes} min)")
        except Exception as e:
            logger.warning(f"db-stats collector unavailable (non-fatal): {e}")

    def _emit_job(self, job_id: str, *, status: Optional[str] = None, phase: Optional[str] = None, **fields: Any) -> None:
        """Record a job transition in the telemetry history (no-op when it is disabled)."""
        recorder = get_activity_recorder()
        if recorder is None:
            return
        if status is not None:
            recorder.job_status(job_id, status, **fields)
        if phase is not None:
            recorder.job_phase(job_id, phase, **fields)

    async def close(self):
        """Close MongoDB connection."""
        if self._db_stats_collector is not None:
            await self._db_stats_collector.stop()
            self._db_stats_collector = None
        recorder = get_activity_recorder()
        if recorder is not None:
            await recorder.stop()
            set_activity_recorder(None)
        if self._telemetry_store is not None:
            await self._telemetry_store.close()
            self._telemetry_store = None
        if self.mongo_client:
            await self.mongo_client.close()
            self.mongo_client = None
            self.db = None
            logger.info("MongoDB connection closed")
    
    async def _load_config(self):
        """Load worker configuration from database."""
        try:
            config_doc = await self.db[INGESTION_CONFIG_COLLECTION].find_one(
                {"_id": "performance_config"}
            )
            if config_doc:
                old_concurrent = self.config.get("max_concurrent_files", 1)
                self.config.update({
                    "max_concurrent_files": config_doc.get("max_concurrent_files", 2),
                    "embedding_batch_size": config_doc.get("embedding_batch_size", 100),
                    "thread_pool_workers": config_doc.get("thread_pool_workers", 4),
                    "process_isolation_enabled": bool(config_doc.get("process_isolation_enabled", True)),
                    "file_processing_timeout": config_doc.get("file_processing_timeout", 300),
                    "job_poll_interval": config_doc.get("job_poll_interval_seconds", 1.0),
                })
                new_concurrent = self.config.get("max_concurrent_files", 1)
                if old_concurrent != new_concurrent:
                    logger.info(f"Config changed: max_concurrent_files {old_concurrent} -> {new_concurrent}")
                logger.info(f"Using config: max_concurrent={self.config['max_concurrent_files']}, thread_pool={self.config['thread_pool_workers']}")
            else:
                logger.warning("No performance_config found in ingestion_config collection, using defaults")
        except Exception as e:
            logger.warning(f"Could not load config from DB, using defaults: {e}")
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True
    
    async def run(self):
        """Main worker loop."""
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        
        logger.info(f"Ingestion worker started (PID: {self.pid})")
        
        try:
            await self.initialize()
            
            # Check for interrupted jobs on startup
            await self._resume_interrupted_jobs()
            
            # Main polling loop
            while not self.shutdown_requested:
                job = await self._poll_next_job()
                if job:
                    await self._process_job(job)
                else:
                    await asyncio.sleep(self.config["job_poll_interval"])
                    
        except Exception as e:
            logger.error(f"Worker crashed: {e}", exc_info=True)
            raise
        finally:
            # Mark any running job as interrupted
            if self.current_job_id:
                await self._mark_job_interrupted(self.current_job_id, paused=self._paused)
            
            await self.close()
            logger.info("Ingestion worker stopped")
    
    async def _poll_next_job(self) -> Optional[Dict]:
        """Poll for the next pending job.

        A claimed job is tagged ``runner: "worker"``: from then on only the worker
        resumes it after an interruption.
        """
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        # Find and claim a PENDING job atomically (queued jobs are untagged until claimed;
        # never claim a job the API runs)
        job = await jobs_collection.find_one_and_update(
            {"status": IngestionStatus.PENDING, "runner": {"$in": [RUNNER_WORKER, None]}},
            {
                "$set": {
                    "status": IngestionStatus.RUNNING,
                    "runner": RUNNER_WORKER,
                    "started_at": datetime.now().isoformat(),
                    "worker_pid": self.pid,
                    "worker_heartbeat": datetime.now(),
                }
            },
            sort=[("created_at", 1)],  # FIFO order
            return_document=True
        )
        
        if job:
            job["job_id"] = str(job.pop("_id"))
            logger.info(f"Claimed job: {job['job_id']}")
        
        return job
    
    async def _resume_interrupted_jobs(self) -> int:
        """Re-queue this worker's interrupted jobs on startup so the poll loop resumes them.

        Only jobs the worker ran (``runner: "worker"``) are re-queued: jobs the API ran
        are resumed by the API's own startup check, and re-queuing them here ran them
        twice. ``resumed_at`` makes ``_process_job`` continue the run instead of
        starting it over, and ``resume_paused`` stays on the job so it resumes held.

        Returns:
            Number of jobs re-queued.
        """
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        # Conditional per job: a job that is no longer INTERRUPTED is left alone
        result = await jobs_collection.update_many(
            {"status": IngestionStatus.INTERRUPTED, **WORKER_OWNED},
            {
                "$set": {
                    "status": IngestionStatus.PENDING,
                    "resumed_at": datetime.now().isoformat(),
                }
            }
        )
        if result.modified_count:
            logger.info(f"Re-queued {result.modified_count} interrupted job(s) for resume")
        return result.modified_count
        
    async def _mark_job_interrupted(self, job_id: str, paused: bool = False):
        """Mark a job as interrupted for later resumption.

        Args:
            job_id: Job to mark.
            paused: The job was paused: the next worker start resumes it held until
                a RESUME or STOP command.
        """
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        await jobs_collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": IngestionStatus.INTERRUPTED,
                    "interrupted_at": datetime.now().isoformat(),
                    "resume_paused": paused,
                }
            }
        )
        logger.info(f"Marked job {job_id} as interrupted" + (" (paused)" if paused else ""))
    
    async def _process_job(self, job: Dict):
        """Process a single ingestion job.

        A job re-queued by ``_resume_interrupted_jobs`` continues where it stopped (see
        ``resume_config``: once its run had reached its files it never cleans again and
        skips files already ingested); one that was paused when the worker went down is
        held paused before any work. A STOP ends the job ``STOPPED`` (final); a worker
        shutdown ends it ``INTERRUPTED``.
        """
        job_id = job["job_id"]
        config = job.get("config", {})
        files_started_at = None
        if job.get("resumed_at"):
            # The job keeps the config it was started with; the resumed run derives its own
            config = resume_config(job)
            if reached_files(job):
                files_started_at = job.get(FILES_STARTED_FIELD) or datetime.now().isoformat()
        self.current_job_id = job_id
        self._stop_requested = False
        self._paused = False
        
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        # Reload config at job start
        await self._load_config()
        
        # Initialize job state with phase tracking
        job_state = {
            "total_files": 0,
            "processed_files": 0,
            "failed_files": 0,
            "duplicates_skipped": 0,
            "chunks_created": 0,
            "document_count": 0,
            "image_count": 0,
            "audio_count": 0,
            "video_count": 0,
            "current_file": None,
            "progress_percent": 0.0,
            "errors": [],
            # Per-outcome counters, updated from the per-file stats as they are written
            "image_only_pdfs": 0,
            "no_chunks_files": 0,
            "timeout_files": 0,
            "error_files": 0,
            # Phase tracking
            "phase": "initializing",
            "phase_message": "Starting ingestion...",
            # Discovery progress
            "discovery_progress": {
                "folders_scanned": 0,
                "total_folders": 0,
                "files_found": 0,
                "files_to_process": 0,
                "files_skipped": 0,
                "current_folder": None
            },
            # Processing rate tracking
            "first_file_time": None,
            "processing_rate": 0.0,
            # Set at the first file (any cleaning done): from then on a resume continues the run
            FILES_STARTED_FIELD: files_started_at,
        }
        
        await self._write_log(
            "INFO",
            f"Starting ingestion job {job_id}" if not job.get("resumed_at")
            else f"Resuming interrupted ingestion job {job_id} (no cleaning, already ingested files are skipped)"
            if files_started_at
            else f"Restarting interrupted ingestion job {job_id} from the beginning (it stopped before its first file)"
        )

        # Per-file pipeline events of this job carry its id (inherited by the pipeline's tasks)
        # and, since this worker serves every tenant, the tenant recorded on the job
        telemetry_token = telemetry_hooks.bind_job(job_id)
        recorder = get_activity_recorder()
        if recorder is not None:
            recorder.bind_job_tenant(job_id, job_tenant(job))
        self._emit_job(job_id, status="running", resumed=bool(job.get("resumed_at")), profile=config.get("profile"))
        self._emit_job(job_id, phase="initializing")

        try:
            if job.get("resume_paused"):
                # Paused when the worker went down: hold before pipeline setup, cleaning or discovery
                await self._write_log("INFO", "Job was paused at shutdown - send RESUME or STOP to continue")
                await self._handle_pause(job_id)
                await jobs_collection.update_one({"_id": job_id}, {"$set": {"resume_paused": False}})

            # Apply offline mode config
            await self._apply_offline_mode_config()
            
            # Update phase: initializing
            job_state["phase"] = "initializing"
            job_state["phase_message"] = "Loading configuration..."
            await self._update_job_progress(job_id, job_state)
            
            # Import ingestion components
            from src.ingestion.chunker import chunk_max_tokens_default
            from src.ingestion.ingest import DocumentIngestionPipeline, IngestionConfig, retry_error_types
            from src.profile import get_profile_manager
            
            # Re-read profiles.yaml: the API edits it (new document folders) and this
            # process only holds a read-only copy, so never persist the switch here.
            pm = get_profile_manager()
            if pm.reload():
                await self._write_log("INFO", "Reloaded profiles.yaml (changed since last job)")

            # Switch profile if specified
            if config.get("profile"):
                if not pm.switch_profile(config["profile"], persist=False):
                    raise ValueError(f"Profile '{config['profile']}' not found; nothing was ingested")
                await self._write_log("INFO", f"Switched to profile: {config['profile']}")
            
            # Create ingestion config
            ing_config = IngestionConfig(
                chunk_size=config.get("chunk_size", 1000),
                chunk_overlap=config.get("chunk_overlap", 200),
                max_chunk_size=config.get("chunk_size", 1000) * 2,
                max_tokens=config.get("max_tokens") or chunk_max_tokens_default(),
            )
            
            job_state["phase_message"] = "Initializing pipeline..."
            await self._update_job_progress(job_id, job_state)
            await self._write_log("INFO", "Initializing pipeline...")
            
            # Create pipeline
            pipeline = DocumentIngestionPipeline(
                config=ing_config,
                documents_folder=config.get("documents_folder"),
                clean_before_ingest=config.get("clean_before_ingest", False),
                use_profile=True
            )
            
            # Update phase: discovering
            job_state["phase"] = "discovering"
            job_state["phase_message"] = "Scanning folders for documents..."
            self._emit_job(job_id, phase="discovering")
            folders = pipeline.documents_folders
            job_state["discovery_progress"]["total_folders"] = len(folders)
            await self._update_job_progress(job_id, job_state)
            await self._write_log("INFO", f"Discovering files in {len(folders)} folders...")
            
            # Define progress callback with phase updates
            async def progress_callback(current: int, total: int, current_file: str = None, chunks_in_file: int = 0):
                nonlocal job_state
                
                # Check for control commands
                control_cmd = await self._check_control_command(job_id)
                
                if control_cmd == "STOP":
                    self._stop_requested = True
                    raise asyncio.CancelledError("Stop requested by user")
                
                if control_cmd == "PAUSE":
                    await self._handle_pause(job_id)
                
                # Transition to processing phase on first file
                if current == 1 and job_state["phase"] != "processing":
                    job_state["phase"] = "processing"
                    job_state["phase_message"] = f"Processing files (0/{total}, {progress_percent_label(0, total)})..."
                    job_state["first_file_time"] = datetime.now().isoformat()
                    job_state["discovery_progress"]["files_to_process"] = total
                    self._emit_job(job_id, phase="processing", files=total)

                recorder = get_activity_recorder()
                if recorder is not None:
                    recorder.job_progress(job_id, current, total)

                # Update state
                job_state["processed_files"] = current
                job_state["total_files"] = total
                job_state["progress_percent"] = (current / total * 100) if total > 0 else 0
                job_state["phase_message"] = (
                    f"Processing files ({current}/{total}, {progress_percent_label(current, total)})..."
                )
                
                # Calculate processing rate (files per minute)
                if job_state.get("first_file_time") and current > 0:
                    first_time = datetime.fromisoformat(job_state["first_file_time"])
                    elapsed_minutes = (datetime.now() - first_time).total_seconds() / 60
                    if elapsed_minutes > 0:
                        job_state["processing_rate"] = round(current / elapsed_minutes, 2)
                
                if chunks_in_file > 0:
                    job_state["chunks_created"] += chunks_in_file
                
                if current_file:
                    job_state["current_file"] = current_file

                    # Per-extension progress for the shared ETA estimator (one call per finished file)
                    done_by_ext = job_state.setdefault("processed_by_extension", {})
                    file_ext = extension_of(current_file)
                    done_by_ext[file_ext] = done_by_ext.get(file_ext, 0) + 1

                    # Categorize file
                    ext = current_file.lower().split('.')[-1] if '.' in current_file else ''
                    if ext in ['pdf', 'doc', 'docx', 'txt', 'md', 'html', 'htm', 'xlsx', 'xls', 'pptx', 'ppt']:
                        job_state["document_count"] = job_state.get("document_count", 0) + 1
                    elif ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']:
                        job_state["image_count"] = job_state.get("image_count", 0) + 1
                    elif ext in ['mp3', 'wav', 'flac', 'm4a', 'ogg', 'wma']:
                        job_state["audio_count"] = job_state.get("audio_count", 0) + 1
                    elif ext in ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'webm']:
                        job_state["video_count"] = job_state.get("video_count", 0) + 1
                    
                    await self._write_log(
                        "INFO",
                        f"Processing ({current}/{total}): {current_file}" + 
                        (f" - {chunks_in_file} chunks" if chunks_in_file > 0 else "")
                    )
                
                # Update job in DB (with heartbeat)
                await self._update_job_progress(job_id, job_state)
            
            # Sync wrapper for progress callback
            def sync_progress_callback(current: int, total: int, current_file: str = None, chunks_in_file: int = 0):
                # The pipeline cleans before its file loop: from the first file on, a resume
                # must continue this run instead of cleaning or re-ingesting again
                if job_state[FILES_STARTED_FIELD] is None:
                    job_state[FILES_STARTED_FIELD] = datetime.now().isoformat()
                asyncio.create_task(progress_callback(current, total, current_file, chunks_in_file))
            
            def stats_callback(counts: dict) -> None:
                # Status counters follow the per-file stats as they are written (persisted at once)
                apply_stats_counts(job_state, counts)
                asyncio.create_task(self._update_job_progress(job_id, job_state))

            def queue_callback(files: List[str]) -> None:
                # What this run will process, per extension (the shared ETA estimator reads it)
                job_state["queue_by_extension"] = count_by_extension(files)
                job_state["processed_by_extension"] = {}

            # Run ingestion with configured concurrency
            results = await pipeline.ingest_documents(
                progress_callback=sync_progress_callback,
                incremental=config.get("incremental", True),
                max_concurrent_files=self.config.get("max_concurrent_files", 1),
                thread_pool_workers=self.config.get("thread_pool_workers"),
                process_isolation=self.config.get("process_isolation_enabled"),
                stats_callback=stats_callback,
                job_id=job_id,
                retry_failed_types=retry_error_types(config),
                queue_callback=queue_callback,
            )
            
            # Update phase: finalizing
            job_state["phase"] = "finalizing"
            job_state["phase_message"] = "Computing final statistics..."
            self._emit_job(job_id, phase="finalizing")
            await self._update_job_progress(job_id, job_state)
            
            # Calculate final stats
            duplicates_skipped = sum(
                1 for r in results
                if r.errors and len(r.errors) > 0 and "Duplicate of:" in r.errors[0]
            )
            actual_failures = sum(
                1 for r in results
                if r.errors and len(r.errors) > 0 and "Duplicate of:" not in r.errors[0]
            )
            
            # Update final status
            final_state = {
                "status": IngestionStatus.COMPLETED,
                "completed_at": datetime.now().isoformat(),
                "progress.processed_files": len(results),
                "progress.chunks_created": sum(r.chunks_created for r in results),
                "progress.failed_files": actual_failures,
                "progress.duplicates_skipped": duplicates_skipped,
                "progress.progress_percent": 100.0,
                "phase": "completed",
                "phase_message": f"Completed: {len(results)} files processed",
                "errors": [err for r in results for err in r.errors if "Duplicate of:" not in err][:20],
            }
            
            await jobs_collection.update_one(
                {"_id": job_id},
                {"$set": final_state}
            )
            self._emit_job(job_id, phase="completed")
            self._emit_job(job_id, status="completed", files=len(results), failed=actual_failures,
                           duplicates=duplicates_skipped, chunks=final_state["progress.chunks_created"])

            await self._write_log(
                "INFO",
                f"Ingestion completed. Files: {len(results)}, Chunks: {final_state['progress.chunks_created']}"
            )
            
            # Trigger post-ingestion backup
            if BACKUP_SERVICE_AVAILABLE:
                try:
                    from backend.core.database import DatabaseManager
                    # Create a temporary DB manager for the backup service
                    class TempDBManager:
                        def __init__(self, client, db, db_name):
                            self.client = client
                            self.db = db
                            self._current_database = db_name
                    
                    temp_db = TempDBManager(self.mongo_client, self.db, self.db_name)
                    backup_service = BackupService(temp_db)
                    
                    profile_key = config.get("profile", "default")
                    backup_result = await backup_service.create_post_ingestion_backup(
                        profile_key=profile_key,
                        job_id=job_id,
                        documents_added=len(results),
                        chunks_added=final_state['progress.chunks_created']
                    )
                    if backup_result:
                        await self._write_log(
                            "INFO",
                            f"Post-ingestion backup created: {backup_result.backup_id}"
                        )
                except Exception as backup_error:
                    logger.warning(f"Post-ingestion backup failed (non-critical): {backup_error}")
            
        except asyncio.CancelledError:
            if self.shutdown_requested and not self._stop_requested:
                # Worker shutdown: the next worker start resumes the job (held, if it was paused)
                logger.warning(f"Job {job_id} was interrupted by worker shutdown")
                await self._mark_job_interrupted(job_id, paused=self._paused)
                self._emit_job(job_id, status="interrupted", reason="shutdown", paused=self._paused)
                await self._write_log(
                    "WARNING",
                    "Ingestion interrupted by worker shutdown (will resume on restart"
                    + (", paused)" if self._paused else ")")
                )
            else:
                logger.warning(f"Job {job_id} was cancelled/stopped")
                await jobs_collection.update_one(
                    {"_id": job_id},
                    {
                        "$set": {
                            "status": IngestionStatus.STOPPED,
                            "completed_at": datetime.now().isoformat(),
                        }
                    }
                )
                await self._write_log("WARNING", "Ingestion stopped by user request")
                self._emit_job(job_id, status="stopped", reason="user")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            await jobs_collection.update_one(
                {"_id": job_id},
                {
                    "$set": {
                        "status": IngestionStatus.FAILED,
                        "completed_at": datetime.now().isoformat(),
                        "errors": [str(e)],
                    }
                }
            )
            await self._write_log("ERROR", f"Ingestion failed: {str(e)}")
            self._emit_job(job_id, status="failed", error_type=type(e).__name__, error=str(e)[:300])

        finally:
            self.current_job_id = None
            telemetry_hooks.reset_job(telemetry_token)
            if recorder is not None:
                recorder.release_job_tenant(job_id)
            await self._flush_logs()
    
    async def _check_control_command(self, job_id: str) -> Optional[str]:
        """Check for control commands (pause, stop, etc.)."""
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        job = await jobs_collection.find_one(
            {"_id": job_id},
            {"control_command": 1}
        )
        
        if job and job.get("control_command"):
            # Clear the command after reading
            await jobs_collection.update_one(
                {"_id": job_id},
                {"$set": {"control_command": None}}
            )
            return job["control_command"]
        
        return None
    
    async def _handle_pause(self, job_id: str):
        """Handle pause command - wait until resumed or stopped.

        Raises:
            asyncio.CancelledError: A STOP command or a worker shutdown arrived while paused.
        """
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        await jobs_collection.update_one(
            {"_id": job_id},
            {"$set": {"status": IngestionStatus.PAUSED}}
        )
        self._paused = True
        self._emit_job(job_id, status="paused")
        await self._write_log("INFO", "Ingestion paused")
        
        # Wait for resume or stop
        while True:
            await asyncio.sleep(_PAUSE_POLL_SECONDS)
            
            # Update heartbeat
            await jobs_collection.update_one(
                {"_id": job_id},
                {"$set": {"worker_heartbeat": datetime.now()}}
            )
            
            cmd = await self._check_control_command(job_id)
            
            if cmd == "STOP":
                self._stop_requested = True
                raise asyncio.CancelledError("Stop requested while paused")
            
            if cmd == "RESUME":
                await jobs_collection.update_one(
                    {"_id": job_id},
                    {"$set": {"status": IngestionStatus.RUNNING}}
                )
                self._paused = False
                self._emit_job(job_id, status="running", reason="resumed")
                await self._write_log("INFO", "Ingestion resumed")
                break
            
            if self.shutdown_requested:
                # Still paused: the job is resumed held after the restart
                raise asyncio.CancelledError("Worker shutdown while paused")
    
    async def _update_job_progress(self, job_id: str, state: Dict):
        """Update job progress in database."""
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        await jobs_collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "progress.total_files": state["total_files"],
                    "progress.processed_files": state["processed_files"],
                    "progress.current_file": state["current_file"],
                    "progress.chunks_created": state["chunks_created"],
                    "progress.progress_percent": state["progress_percent"],
                    "progress.document_count": state.get("document_count", 0),
                    "progress.image_count": state.get("image_count", 0),
                    "progress.audio_count": state.get("audio_count", 0),
                    "progress.video_count": state.get("video_count", 0),
                    "progress.failed_files": state.get("failed_files", 0),
                    "progress.duplicates_skipped": state.get("duplicates_skipped", 0),
                    # Per-outcome counters (read top-level by the runs list and the status API)
                    "image_only_pdfs": state.get("image_only_pdfs", 0),
                    "image_only_pdfs_ocr": state.get("image_only_pdfs_ocr", 0),
                    "no_chunks_files": state.get("no_chunks_files", 0),
                    "timeout_files": state.get("timeout_files", 0),
                    "error_files": state.get("error_files", 0),
                    # Per-extension queue and progress (the shared ETA estimator reads them)
                    "queue_by_extension": state.get("queue_by_extension"),
                    "processed_by_extension": state.get("processed_by_extension"),
                    # Phase tracking fields
                    "phase": state.get("phase"),
                    "phase_message": state.get("phase_message"),
                    "discovery_progress": state.get("discovery_progress"),
                    "first_file_time": state.get("first_file_time"),
                    "processing_rate": state.get("processing_rate", 0.0),
                    FILES_STARTED_FIELD: state.get(FILES_STARTED_FIELD),
                    "worker_heartbeat": datetime.now(),
                }
            }
        )
    
    async def _apply_offline_mode_config(self):
        """Apply offline mode configuration from database."""
        try:
            doc = await self.db["offline_config"].find_one({"_id": "config"})
            
            if doc and doc.get("enabled"):
                os.environ["OFFLINE_MODE"] = "true"
                
                if doc.get("audio_url"):
                    os.environ["OFFLINE_AUDIO_URL"] = doc.get("audio_url")
                if doc.get("audio_model"):
                    os.environ["OFFLINE_AUDIO_MODEL"] = doc.get("audio_model")
                if doc.get("vision_url"):
                    os.environ["OFFLINE_VISION_URL"] = doc.get("vision_url")
                if doc.get("vision_model"):
                    os.environ["OFFLINE_VISION_MODEL"] = doc.get("vision_model")
                    
                logger.info(f"Offline mode enabled (audio: {doc.get('audio_model')}, vision: {doc.get('vision_model')})")
            else:
                os.environ["OFFLINE_MODE"] = "false"
                for key in ["OFFLINE_AUDIO_URL", "OFFLINE_AUDIO_MODEL", "OFFLINE_VISION_URL", "OFFLINE_VISION_MODEL"]:
                    os.environ.pop(key, None)
        except Exception as e:
            logger.warning(f"Could not check offline config: {e}")
            os.environ["OFFLINE_MODE"] = "false"
    
    async def _write_log(self, level: str, message: str):
        """Buffer a log entry for batch writing to database."""
        log_entry = {
            "job_id": self.current_job_id,
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
            "worker_pid": self.pid,
        }
        
        self._log_buffer.append(log_entry)
        
        # Also log to console
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(message)
        
        # Flush logs periodically
        if len(self._log_buffer) >= 50 or (datetime.now() - self._last_log_flush).total_seconds() > 5:
            await self._flush_logs()
    
    async def _flush_logs(self):
        """Flush buffered logs to database."""
        if not self._log_buffer:
            return
        
        logs_to_write = list(self._log_buffer)
        self._log_buffer.clear()
        self._last_log_flush = datetime.now()
        
        try:
            logs_collection = self.db[INGESTION_LOGS_COLLECTION]
            await logs_collection.insert_many(logs_to_write)
        except Exception as e:
            logger.warning(f"Failed to write logs to DB: {e}")


async def main():
    """Entry point for running the worker."""
    worker = IngestionWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
