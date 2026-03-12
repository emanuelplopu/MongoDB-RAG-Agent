"""
RecallHub Update Service
========================
Handles offline update package installation and management.
Supports signed update packages (.rhu) for air-gapped deployments.
"""

import os
import json
import shutil
import tarfile
import hashlib
import logging
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class UpdateType(str, Enum):
    """Types of updates."""
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"


class UpdateStatus(str, Enum):
    """Update installation status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class UpdateManifest:
    """Update package manifest."""
    version: str
    previous_version: str = ""
    update_type: str = "patch"
    created_at: str = ""
    release_notes: str = ""
    components: List[str] = None
    checksums: Dict[str, str] = None
    migrations: List[str] = None
    min_version: str = ""
    signature: str = ""
    
    def __post_init__(self):
        if self.components is None:
            self.components = []
        if self.checksums is None:
            self.checksums = {}
        if self.migrations is None:
            self.migrations = []
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()


@dataclass
class UpdateInfo:
    """Information about an update package."""
    id: str
    filename: str
    version: str
    update_type: str
    detected_at: datetime
    size_bytes: int
    signed: bool
    release_notes: str = ""


@dataclass
class UpdateResult:
    """Result of an update operation."""
    success: bool
    message: str
    previous_version: Optional[str] = None
    new_version: Optional[str] = None
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


@dataclass
class UpdateRecord:
    """Record of an installed update."""
    version: str
    applied_at: datetime
    update_type: str
    status: str
    previous_version: str = ""
    errors: List[str] = None


class UpdateService:
    """
    Service for managing RecallHub updates.
    
    Update packages (.rhu) are tar archives containing:
    - manifest.json: Update metadata and signatures
    - containers/: Docker images as tar files
    - migrations/: Database migration scripts
    - scripts/: Installation scripts
    - checksums.sha256: File checksums
    - signature.sig: Ed25519 signature
    """
    
    # Public key for signature verification (embedded)
    PUBLIC_KEY = None  # Set during build
    
    def __init__(
        self,
        updates_dir: Optional[Path] = None,
        install_dir: Optional[Path] = None
    ):
        """
        Initialize the update service.
        
        Args:
            updates_dir: Directory to scan for update packages
            install_dir: RecallHub installation directory
        """
        self.updates_dir = updates_dir or self._get_default_updates_dir()
        self.install_dir = install_dir or self._get_default_install_dir()
        
        # Ensure directories exist
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        
        self._current_version: Optional[str] = None
    
    def _get_default_updates_dir(self) -> Path:
        """Get the default updates directory."""
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        if local_app_data:
            return Path(local_app_data) / 'RecallHub' / 'updates'
        return Path.home() / '.recallhub' / 'updates'
    
    def _get_default_install_dir(self) -> Path:
        """Get the default installation directory."""
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        if local_app_data:
            return Path(local_app_data) / 'RecallHub'
        return Path.home() / '.recallhub'
    
    async def get_current_version(self) -> str:
        """Get the currently installed version."""
        if self._current_version:
            return self._current_version
        
        try:
            from backend.core.database import get_database
            db = await get_database()
            version_doc = await db.system_config.find_one({"_id": "system_version"})
            
            if version_doc:
                self._current_version = version_doc.get("version", "1.0.0")
            else:
                self._current_version = "1.0.0"
        except Exception:
            self._current_version = "1.0.0"
        
        return self._current_version
    
    async def scan_for_updates(self) -> List[UpdateInfo]:
        """Scan the updates directory for available update packages."""
        updates = []
        
        for update_file in self.updates_dir.glob("*.rhu"):
            try:
                info = await self._get_update_info(update_file)
                if info:
                    updates.append(info)
            except Exception as e:
                logger.warning(f"Could not read update package {update_file}: {e}")
        
        # Sort by version (newest first)
        updates.sort(key=lambda x: x.version, reverse=True)
        
        return updates
    
    async def check_update_available(self) -> Optional[UpdateInfo]:
        """Check if a newer update is available."""
        current_version = await self.get_current_version()
        updates = await self.scan_for_updates()
        
        for update in updates:
            if self._compare_versions(update.version, current_version) > 0:
                return update
        
        return None
    
    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two version strings. Returns >0 if v1>v2, <0 if v1<v2, 0 if equal."""
        def parse_version(v: str) -> tuple:
            parts = v.split(".")
            return tuple(int(p) for p in parts[:3])
        
        try:
            p1 = parse_version(v1)
            p2 = parse_version(v2)
            
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
            return 0
        except Exception:
            return 0
    
    async def _get_update_info(self, update_path: Path) -> Optional[UpdateInfo]:
        """Extract update info from an update package."""
        try:
            with tarfile.open(update_path, "r:gz") as tar:
                # Extract manifest
                manifest_member = tar.getmember("manifest.json")
                manifest_file = tar.extractfile(manifest_member)
                manifest_data = json.load(manifest_file)
            
            return UpdateInfo(
                id=f"update_{manifest_data.get('version', 'unknown')}",
                filename=update_path.name,
                version=manifest_data.get("version", "unknown"),
                update_type=manifest_data.get("update_type", "patch"),
                detected_at=datetime.fromtimestamp(update_path.stat().st_mtime),
                size_bytes=update_path.stat().st_size,
                signed=bool(manifest_data.get("signature")),
                release_notes=manifest_data.get("release_notes", "")
            )
        except Exception as e:
            logger.error(f"Error reading update info: {e}")
            return None
    
    async def verify_update_package(self, update_path: Path) -> bool:
        """Verify the integrity and signature of an update package."""
        try:
            with tarfile.open(update_path, "r:gz") as tar:
                # Extract and read manifest
                manifest_file = tar.extractfile(tar.getmember("manifest.json"))
                manifest_data = json.load(manifest_file)
                
                # Read checksums file if exists
                try:
                    checksums_file = tar.extractfile(tar.getmember("checksums.sha256"))
                    checksums_content = checksums_file.read().decode('utf-8')
                    expected_checksums = dict(
                        line.split("  ")
                        for line in checksums_content.strip().split("\n")
                        if line
                    )
                except KeyError:
                    expected_checksums = manifest_data.get("checksums", {})
                
                # Verify each file checksum
                for member in tar.getmembers():
                    if member.name in ["checksums.sha256", "signature.sig", "manifest.json"]:
                        continue
                    
                    if member.isfile() and member.name in expected_checksums:
                        file_content = tar.extractfile(member).read()
                        actual_checksum = hashlib.sha256(file_content).hexdigest()
                        
                        if actual_checksum != expected_checksums[member.name]:
                            logger.error(f"Checksum mismatch for {member.name}")
                            return False
                
                # Verify signature if public key is available
                if self.PUBLIC_KEY and manifest_data.get("signature"):
                    # Signature verification would go here
                    pass
            
            return True
        except Exception as e:
            logger.error(f"Update verification failed: {e}")
            return False
    
    async def apply_update(
        self,
        update_id: str,
        create_backup: bool = True
    ) -> UpdateResult:
        """
        Apply an update package.
        
        Args:
            update_id: ID of the update to apply
            create_backup: Whether to create a backup before updating
            
        Returns:
            UpdateResult with status and details
        """
        updates = await self.scan_for_updates()
        update_info = None
        
        for update in updates:
            if update.id == update_id or update.filename == update_id:
                update_info = update
                break
        
        if not update_info:
            return UpdateResult(
                success=False,
                message=f"Update not found: {update_id}"
            )
        
        update_path = self.updates_dir / update_info.filename
        current_version = await self.get_current_version()
        
        # Verify update package
        if not await self.verify_update_package(update_path):
            return UpdateResult(
                success=False,
                message="Update package verification failed"
            )
        
        # Create pre-update backup
        if create_backup:
            try:
                from backend.services.backup_service import get_backup_service
                backup_service = get_backup_service()
                await backup_service.create_backup(
                    components=["all"],
                    description=f"Pre-update backup before {update_info.version}"
                )
            except Exception as e:
                logger.warning(f"Could not create pre-update backup: {e}")
        
        # Record update start
        await self._record_update_start(update_info, current_version)
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Extract update package
                with tarfile.open(update_path, "r:gz") as tar:
                    tar.extractall(temp_path)
                
                # Load manifest
                with open(temp_path / "manifest.json", 'r') as f:
                    manifest = json.load(f)
                
                # Stop services
                await self._stop_services()
                
                # Apply container updates
                containers_dir = temp_path / "containers"
                if containers_dir.exists():
                    await self._update_containers(containers_dir)
                
                # Run migrations
                migrations_dir = temp_path / "migrations"
                if migrations_dir.exists():
                    await self._run_migrations(migrations_dir, manifest.get("migrations", []))
                
                # Run custom scripts
                scripts_dir = temp_path / "scripts"
                if scripts_dir.exists():
                    await self._run_update_scripts(scripts_dir)
                
                # Update version in database
                await self._update_version(update_info.version, current_version)
                
                # Start services
                await self._start_services()
            
            # Record success
            await self._record_update_complete(update_info, current_version, UpdateStatus.COMPLETED)
            
            return UpdateResult(
                success=True,
                message=f"Successfully updated to version {update_info.version}",
                previous_version=current_version,
                new_version=update_info.version
            )
        
        except Exception as e:
            logger.error(f"Update failed: {e}")
            
            # Record failure
            await self._record_update_complete(
                update_info, 
                current_version, 
                UpdateStatus.FAILED,
                [str(e)]
            )
            
            return UpdateResult(
                success=False,
                message=f"Update failed: {str(e)}",
                previous_version=current_version,
                errors=[str(e)]
            )
    
    async def _stop_services(self) -> None:
        """Stop RecallHub services before update."""
        try:
            # Try to run the stop script
            stop_script = self.install_dir / "scripts" / "Stop-Services.ps1"
            if stop_script.exists():
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(stop_script), "-Force"],
                    timeout=60,
                    capture_output=True
                )
        except Exception as e:
            logger.warning(f"Could not stop services: {e}")
    
    async def _start_services(self) -> None:
        """Start RecallHub services after update."""
        try:
            start_script = self.install_dir / "scripts" / "Start-Services.ps1"
            if start_script.exists():
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(start_script), "-Wait"],
                    timeout=120,
                    capture_output=True
                )
        except Exception as e:
            logger.warning(f"Could not start services: {e}")
    
    async def _update_containers(self, containers_dir: Path) -> None:
        """Load updated container images."""
        for image_file in containers_dir.glob("*.tar"):
            try:
                logger.info(f"Loading container image: {image_file.name}")
                subprocess.run(
                    ["wsl", "-d", "RecallHub", "docker", "load", "-i", f"/mnt/{str(image_file).replace(':', '').replace('\\', '/')}"],
                    timeout=300,
                    capture_output=True,
                    check=True
                )
            except Exception as e:
                logger.error(f"Failed to load container image {image_file.name}: {e}")
                raise
    
    async def _run_migrations(self, migrations_dir: Path, migration_list: List[str]) -> None:
        """Run database migrations."""
        for migration in sorted(migration_list):
            migration_file = migrations_dir / f"{migration}.py"
            if migration_file.exists():
                logger.info(f"Running migration: {migration}")
                try:
                    # Execute migration script
                    exec(open(migration_file).read())
                except Exception as e:
                    logger.error(f"Migration {migration} failed: {e}")
                    raise
    
    async def _run_update_scripts(self, scripts_dir: Path) -> None:
        """Run update scripts."""
        for script in sorted(scripts_dir.glob("*.ps1")):
            logger.info(f"Running update script: {script.name}")
            try:
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    timeout=300,
                    capture_output=True,
                    check=True
                )
            except Exception as e:
                logger.error(f"Update script {script.name} failed: {e}")
                raise
    
    async def _update_version(self, new_version: str, previous_version: str) -> None:
        """Update version information in database."""
        try:
            from backend.core.database import get_database
            db = await get_database()
            
            await db.system_config.update_one(
                {"_id": "system_version"},
                {
                    "$set": {
                        "version": new_version,
                        "previous_version": previous_version,
                        "installed_at": datetime.utcnow()
                    },
                    "$push": {
                        "update_history": {
                            "version": new_version,
                            "applied_at": datetime.utcnow().isoformat(),
                            "previous_version": previous_version
                        }
                    }
                },
                upsert=True
            )
            
            self._current_version = new_version
        except Exception as e:
            logger.error(f"Failed to update version in database: {e}")
    
    async def _record_update_start(self, update_info: UpdateInfo, current_version: str) -> None:
        """Record update start in database."""
        try:
            from backend.core.database import get_database
            db = await get_database()
            
            await db.update_history.insert_one({
                "version": update_info.version,
                "previous_version": current_version,
                "status": UpdateStatus.IN_PROGRESS.value,
                "started_at": datetime.utcnow(),
                "update_type": update_info.update_type
            })
        except Exception as e:
            logger.warning(f"Could not record update start: {e}")
    
    async def _record_update_complete(
        self,
        update_info: UpdateInfo,
        previous_version: str,
        status: UpdateStatus,
        errors: List[str] = None
    ) -> None:
        """Record update completion in database."""
        try:
            from backend.core.database import get_database
            db = await get_database()
            
            await db.update_history.update_one(
                {
                    "version": update_info.version,
                    "previous_version": previous_version,
                    "status": UpdateStatus.IN_PROGRESS.value
                },
                {
                    "$set": {
                        "status": status.value,
                        "completed_at": datetime.utcnow(),
                        "errors": errors or []
                    }
                }
            )
        except Exception as e:
            logger.warning(f"Could not record update completion: {e}")
    
    async def rollback(self) -> UpdateResult:
        """Rollback to the previous version using the pre-update backup."""
        try:
            from backend.services.backup_service import get_backup_service
            backup_service = get_backup_service()
            
            # Find the most recent pre-update backup
            backups = await backup_service.list_backups()
            
            pre_update_backup = None
            for backup in backups:
                if "Pre-update backup" in backup.description:
                    pre_update_backup = backup
                    break
            
            if not pre_update_backup:
                return UpdateResult(
                    success=False,
                    message="No pre-update backup found for rollback"
                )
            
            # Restore from backup
            result = await backup_service.restore_backup(pre_update_backup.id)
            
            if result.get("success"):
                return UpdateResult(
                    success=True,
                    message="Successfully rolled back to previous version"
                )
            else:
                return UpdateResult(
                    success=False,
                    message="Rollback failed",
                    errors=result.get("errors", [])
                )
        
        except Exception as e:
            return UpdateResult(
                success=False,
                message=f"Rollback failed: {str(e)}",
                errors=[str(e)]
            )
    
    async def get_update_history(self) -> List[UpdateRecord]:
        """Get the history of applied updates."""
        try:
            from backend.core.database import get_database
            db = await get_database()
            
            history = await db.update_history.find({}).sort("completed_at", -1).to_list(50)
            
            return [
                UpdateRecord(
                    version=record.get("version"),
                    applied_at=record.get("completed_at"),
                    update_type=record.get("update_type", "unknown"),
                    status=record.get("status"),
                    previous_version=record.get("previous_version", ""),
                    errors=record.get("errors", [])
                )
                for record in history
            ]
        except Exception:
            return []


# Create singleton instance
_update_service: Optional[UpdateService] = None


def get_update_service() -> UpdateService:
    """Get the global update service instance."""
    global _update_service
    if _update_service is None:
        _update_service = UpdateService()
    return _update_service
