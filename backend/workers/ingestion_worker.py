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
        self.mongo_client: Optional[AsyncMongoClient] = None
        self.db = None
        self.pid = os.getpid()
        
        # Configuration (loaded from DB or defaults)
        self.config = {
            "max_concurrent_files": 2,
            "embedding_batch_size": 100,
            "thread_pool_workers": 4,
            "file_processing_timeout": 300,
            "job_poll_interval": 1.0,
        }
        
        # Log buffer for batch writes
        self._log_buffer: deque = deque(maxlen=100)
        self._last_log_flush = datetime.now()
    
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
    
    async def close(self):
        """Close MongoDB connection."""
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
                self.config.update({
                    "max_concurrent_files": config_doc.get("max_concurrent_files", 2),
                    "embedding_batch_size": config_doc.get("embedding_batch_size", 100),
                    "thread_pool_workers": config_doc.get("thread_pool_workers", 4),
                    "file_processing_timeout": config_doc.get("file_processing_timeout", 300),
                    "job_poll_interval": config_doc.get("job_poll_interval_seconds", 1.0),
                })
                logger.info(f"Loaded config from DB: {self.config}")
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
                await self._mark_job_interrupted(self.current_job_id)
            
            await self.close()
            logger.info("Ingestion worker stopped")
    
    async def _poll_next_job(self) -> Optional[Dict]:
        """Poll for the next pending job."""
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        # Find and claim a PENDING job atomically
        job = await jobs_collection.find_one_and_update(
            {"status": IngestionStatus.PENDING},
            {
                "$set": {
                    "status": IngestionStatus.RUNNING,
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
    
    async def _resume_interrupted_jobs(self):
        """Check for and resume any interrupted jobs on startup."""
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        # Find interrupted jobs
        interrupted = await jobs_collection.find_one(
            {"status": IngestionStatus.INTERRUPTED}
        )
        
        if interrupted:
            job_id = str(interrupted["_id"])
            logger.info(f"Found interrupted job {job_id}, resuming...")
            
            # Re-queue as PENDING so it gets picked up
            await jobs_collection.update_one(
                {"_id": interrupted["_id"]},
                {
                    "$set": {
                        "status": IngestionStatus.PENDING,
                        "resumed_at": datetime.now().isoformat(),
                    }
                }
            )
    
    async def _mark_job_interrupted(self, job_id: str):
        """Mark a job as interrupted for later resumption."""
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        await jobs_collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": IngestionStatus.INTERRUPTED,
                    "interrupted_at": datetime.now().isoformat(),
                }
            }
        )
        logger.info(f"Marked job {job_id} as interrupted")
    
    async def _process_job(self, job: Dict):
        """Process a single ingestion job."""
        job_id = job["job_id"]
        config = job.get("config", {})
        self.current_job_id = job_id
        
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        # Reload config at job start
        await self._load_config()
        
        # Initialize job state
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
        }
        
        await self._write_log("INFO", f"Starting ingestion job {job_id}")
        
        try:
            # Apply offline mode config
            await self._apply_offline_mode_config()
            
            # Import ingestion components
            from src.ingestion.ingest import DocumentIngestionPipeline, IngestionConfig
            from src.profile import get_profile_manager
            
            # Switch profile if specified
            if config.get("profile"):
                pm = get_profile_manager()
                pm.switch_profile(config["profile"])
                await self._write_log("INFO", f"Switched to profile: {config['profile']}")
            
            # Create ingestion config
            ing_config = IngestionConfig(
                chunk_size=config.get("chunk_size", 1000),
                chunk_overlap=config.get("chunk_overlap", 200),
                max_chunk_size=config.get("chunk_size", 1000) * 2,
                max_tokens=config.get("max_tokens", 512)
            )
            
            await self._write_log("INFO", "Initializing pipeline...")
            
            # Create pipeline
            pipeline = DocumentIngestionPipeline(
                config=ing_config,
                documents_folder=config.get("documents_folder"),
                clean_before_ingest=config.get("clean_before_ingest", False),
                use_profile=True
            )
            
            # Define progress callback
            async def progress_callback(current: int, total: int, current_file: str = None, chunks_in_file: int = 0):
                nonlocal job_state
                
                # Check for control commands
                control_cmd = await self._check_control_command(job_id)
                
                if control_cmd == "STOP":
                    raise asyncio.CancelledError("Stop requested by user")
                
                if control_cmd == "PAUSE":
                    await self._handle_pause(job_id)
                
                # Update state
                job_state["processed_files"] = current
                job_state["total_files"] = total
                job_state["progress_percent"] = (current / total * 100) if total > 0 else 0
                
                if chunks_in_file > 0:
                    job_state["chunks_created"] += chunks_in_file
                
                if current_file:
                    job_state["current_file"] = current_file
                    
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
                asyncio.create_task(progress_callback(current, total, current_file, chunks_in_file))
            
            # Run ingestion with configured concurrency
            results = await pipeline.ingest_documents(
                progress_callback=sync_progress_callback,
                incremental=config.get("incremental", True),
                max_concurrent_files=self.config.get("max_concurrent_files", 1)
            )
            
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
                "errors": [err for r in results for err in r.errors if "Duplicate of:" not in err][:20],
            }
            
            await jobs_collection.update_one(
                {"_id": job_id},
                {"$set": final_state}
            )
            
            await self._write_log(
                "INFO",
                f"Ingestion completed. Files: {len(results)}, Chunks: {final_state['progress.chunks_created']}"
            )
            
        except asyncio.CancelledError:
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
            
        finally:
            self.current_job_id = None
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
        """Handle pause command - wait until resumed or stopped."""
        jobs_collection = self.db[INGESTION_JOBS_COLLECTION]
        
        await jobs_collection.update_one(
            {"_id": job_id},
            {"$set": {"status": IngestionStatus.PAUSED}}
        )
        await self._write_log("INFO", "Ingestion paused")
        
        # Wait for resume or stop
        while True:
            await asyncio.sleep(1)
            
            # Update heartbeat
            await jobs_collection.update_one(
                {"_id": job_id},
                {"$set": {"worker_heartbeat": datetime.now()}}
            )
            
            cmd = await self._check_control_command(job_id)
            
            if cmd == "STOP":
                raise asyncio.CancelledError("Stop requested while paused")
            
            if cmd == "RESUME":
                await jobs_collection.update_one(
                    {"_id": job_id},
                    {"$set": {"status": IngestionStatus.RUNNING}}
                )
                await self._write_log("INFO", "Ingestion resumed")
                break
            
            if self.shutdown_requested:
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
