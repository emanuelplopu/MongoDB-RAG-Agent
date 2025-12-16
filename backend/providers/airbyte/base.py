"""
Airbyte Base Provider

Abstract base class for Airbyte-backed cloud source providers.
Provides common functionality for managing Airbyte sources, destinations,
and sync operations.
"""

import asyncio
import logging
from abc import abstractmethod
from datetime import datetime
from typing import AsyncIterator, Optional, Any

from backend.providers.base import (
    CloudSourceProvider,
    ProviderType,
    ProviderCapabilities,
    AuthType,
    ConnectionCredentials,
    RemoteFile,
    RemoteFolder,
    SyncDelta,
    CloudSourceError,
)
from backend.providers.airbyte.client import (
    AirbyteClient,
    AirbyteSourceConfig,
    AirbyteConnection,
    AirbyteSyncJob,
    AirbyteSyncStatus,
    AirbyteError,
    AirbyteNotAvailableError,
    AirbyteConnectionError,
    AirbyteAPIError,
    AirbyteValidationError,
)

logger = logging.getLogger(__name__)


class AirbyteProviderError(CloudSourceError):
    """Error from Airbyte provider operations."""
    pass


class AirbyteProvider(CloudSourceProvider):
    """
    Base class for providers that use Airbyte connectors.
    
    This class provides the infrastructure for managing Airbyte sources,
    destinations, and sync jobs. Subclasses implement source-specific
    configuration and data transformation.
    """
    
    def __init__(
        self,
        credentials: Optional[ConnectionCredentials] = None,
        airbyte_url: str = "http://localhost:11021",
        mongodb_uri: str = "mongodb://mongodb:27017",
        mongodb_database: str = "rag_db",
    ):
        super().__init__(credentials)
        self.airbyte_url = airbyte_url
        self.mongodb_uri = mongodb_uri
        self.mongodb_database = mongodb_database
        self._client: Optional[AirbyteClient] = None
        
        # Airbyte resource IDs (set after setup)
        self._source_id: Optional[str] = None
        self._destination_id: Optional[str] = None
        self._connection_id: Optional[str] = None
    
    @property
    def client(self) -> AirbyteClient:
        """Get or create the Airbyte client."""
        if self._client is None:
            self._client = AirbyteClient(base_url=self.airbyte_url)
        return self._client
    
    # ==================== Abstract Methods ====================
    
    @property
    @abstractmethod
    def source_definition_id(self) -> str:
        """Return the Airbyte source definition ID."""
        pass
    
    @property
    @abstractmethod
    def source_display_name(self) -> str:
        """Return a display name for the source."""
        pass
    
    @abstractmethod
    def build_source_config(
        self, 
        credentials: ConnectionCredentials
    ) -> dict[str, Any]:
        """
        Build the Airbyte source connection configuration.
        
        This method should return a dictionary matching the Airbyte
        source's expected connectionConfiguration schema.
        """
        pass
    
    @abstractmethod
    def get_default_streams(self) -> list[str]:
        """
        Return the default stream names to sync.
        
        These should match the stream names in the Airbyte catalog.
        """
        pass
    
    @abstractmethod
    def transform_record(
        self, 
        stream_name: str, 
        record: dict[str, Any]
    ) -> Optional[RemoteFile]:
        """
        Transform an Airbyte record to a RemoteFile.
        
        Returns None if the record should be skipped.
        """
        pass
    
    # ==================== Setup Methods ====================
    
    async def setup_airbyte_resources(
        self,
        source_name: Optional[str] = None,
        streams: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """
        Set up all required Airbyte resources.
        
        This creates or updates:
        - Source (from credentials)
        - Destination (MongoDB)
        - Connection (source -> destination)
        
        Returns:
            Dict with 'source_id', 'destination_id', 'connection_id'
            
        Raises:
            AirbyteProviderError: If setup fails
            AirbyteNotAvailableError: If Airbyte is not running
        """
        if not self.credentials:
            raise AirbyteProviderError("Credentials required for Airbyte setup")
        
        # Ensure Airbyte is available
        try:
            await self.client.ensure_available()
        except AirbyteNotAvailableError:
            raise AirbyteProviderError(
                "Airbyte is not available. Please start Airbyte with: "
                "docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml up -d"
            )
        
        try:
            # Build source configuration
            source_config_dict = self.build_source_config(self.credentials)
            if not source_config_dict:
                raise AirbyteProviderError("build_source_config returned empty configuration")
            
            # Create source
            source_config = AirbyteSourceConfig(
                source_definition_id=self.source_definition_id,
                name=source_name or f"{self.source_display_name} Source",
                connection_configuration=source_config_dict,
            )
            
            source = await self.client.create_source(source_config)
            self._source_id = source.source_id
            logger.info(f"Created Airbyte source: {source.source_id}")
            
        except AirbyteAPIError as e:
            raise AirbyteProviderError(f"Failed to create source: {e}") from e
        except AirbyteValidationError as e:
            raise AirbyteProviderError(f"Invalid source configuration: {e}") from e
        
        try:
            # Check if MongoDB destination exists, create if not
            destinations = await self.client.list_destinations()
            mongo_dest = next(
                (d for d in destinations if "mongodb" in d.name.lower() and "rag" in d.name.lower()),
                None
            )
            
            if mongo_dest:
                self._destination_id = mongo_dest.destination_id
                logger.info(f"Using existing MongoDB destination: {mongo_dest.destination_id}")
            else:
                dest = await self.client.create_mongodb_destination(
                    name="RAG MongoDB Destination",
                    mongodb_uri=self.mongodb_uri,
                    database=self.mongodb_database,
                )
                self._destination_id = dest.destination_id
                logger.info(f"Created MongoDB destination: {dest.destination_id}")
                
        except AirbyteAPIError as e:
            # Clean up source if destination creation fails
            if self._source_id:
                try:
                    await self.client.delete_source(self._source_id)
                except Exception:
                    pass
            raise AirbyteProviderError(f"Failed to create/find destination: {e}") from e
        
        try:
            # Discover schema and create connection
            schema = await self.client.discover_source_schema(self._source_id)
            catalog = schema.get("catalog", {})
            
            if not catalog.get("streams"):
                raise AirbyteProviderError(
                    "No streams found in source schema. Check source credentials and permissions."
                )
            
            # Filter catalog to only include desired streams
            streams_to_sync = streams or self.get_default_streams()
            filtered_streams = []
            
            for stream in catalog.get("streams", []):
                stream_name = stream.get("stream", {}).get("name", "")
                if not stream_name:
                    continue
                    
                if stream_name in streams_to_sync:
                    # Enable the stream with appropriate sync mode
                    supported_modes = stream.get("stream", {}).get("supportedSyncModes", [])
                    sync_mode = "incremental" if "incremental" in supported_modes else "full_refresh"
                    
                    stream["config"] = {
                        "syncMode": sync_mode,
                        "cursorField": stream.get("stream", {}).get("defaultCursorField", []),
                        "destinationSyncMode": "append_dedup" if sync_mode == "incremental" else "append",
                        "selected": True,
                    }
                    filtered_streams.append(stream)
            
            if not filtered_streams:
                available_streams = [s.get('stream', {}).get('name', '') for s in catalog.get('streams', [])]
                logger.warning(
                    f"No matching streams found for {streams_to_sync}. "
                    f"Available streams: {available_streams}"
                )
                # Use all streams if none matched
                filtered_streams = catalog.get("streams", [])
                for stream in filtered_streams:
                    stream["config"] = {
                        "syncMode": "full_refresh",
                        "destinationSyncMode": "append",
                        "selected": True,
                    }
            
            sync_catalog = {"streams": filtered_streams}
            
            # Create connection
            connection = await self.client.create_connection(
                name=f"{source_name or self.source_display_name} -> MongoDB",
                source_id=self._source_id,
                destination_id=self._destination_id,
                sync_catalog=sync_catalog,
                prefix=f"{self.provider_type.value}_",
            )
            self._connection_id = connection.connection_id
            logger.info(f"Created Airbyte connection: {connection.connection_id}")
            
        except AirbyteAPIError as e:
            # Clean up on failure
            await self._cleanup_on_error()
            raise AirbyteProviderError(f"Failed to create connection: {e}") from e
        
        return {
            "source_id": self._source_id,
            "destination_id": self._destination_id,
            "connection_id": self._connection_id,
        }
    
    async def _cleanup_on_error(self) -> None:
        """Clean up resources after a setup failure."""
        try:
            if self._connection_id:
                await self.client.delete_connection(self._connection_id)
                self._connection_id = None
        except Exception:
            pass
        
        try:
            if self._source_id:
                await self.client.delete_source(self._source_id)
                self._source_id = None
        except Exception:
            pass
    
    async def get_or_create_resources(
        self,
        source_id: Optional[str] = None,
        destination_id: Optional[str] = None,
        connection_id: Optional[str] = None,
    ) -> dict[str, str]:
        """
        Get existing resources or create new ones.
        
        Use this when reconnecting to existing Airbyte resources.
        """
        self._source_id = source_id
        self._destination_id = destination_id
        self._connection_id = connection_id
        
        if source_id and destination_id and connection_id:
            return {
                "source_id": source_id,
                "destination_id": destination_id,
                "connection_id": connection_id,
            }
        
        return await self.setup_airbyte_resources()
    
    # ==================== Authentication ====================
    
    async def authenticate(self, credentials: ConnectionCredentials) -> bool:
        """Authenticate by setting up Airbyte source."""
        self.credentials = credentials
        
        # Test by creating a source and checking connection
        try:
            resources = await self.setup_airbyte_resources()
            check_result = await self.client.check_source_connection(
                resources["source_id"]
            )
            
            if check_result.get("status") == "succeeded":
                self._authenticated = True
                return True
            else:
                logger.error(f"Source connection check failed: {check_result}")
                return False
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise
    
    async def validate_credentials(self) -> bool:
        """Validate that Airbyte source connection is valid."""
        if not self._source_id:
            return False
        
        try:
            result = await self.client.check_source_connection(self._source_id)
            return result.get("status") == "succeeded"
        except Exception:
            return False
    
    async def refresh_credentials(self) -> ConnectionCredentials:
        """Airbyte manages credential refresh internally."""
        return self.credentials
    
    # ==================== Sync Operations ====================
    
    async def trigger_sync(self) -> AirbyteSyncJob:
        """Trigger a sync job for this provider's connection."""
        if not self._connection_id:
            raise AirbyteProviderError("Connection not set up. Call authenticate() first.")
        
        return await self.client.trigger_sync(self._connection_id)
    
    async def get_sync_status(self, job_id: str) -> AirbyteSyncJob:
        """Get the status of a sync job."""
        return await self.client.get_job(job_id)
    
    async def wait_for_sync(
        self,
        job_id: str,
        poll_interval: float = 5.0,
        timeout: float = 3600.0,
    ) -> AirbyteSyncJob:
        """Wait for a sync job to complete."""
        return await self.client.wait_for_job_completion(
            job_id, poll_interval, timeout
        )
    
    async def sync_and_wait(self) -> AirbyteSyncJob:
        """Trigger a sync and wait for completion."""
        job = await self.trigger_sync()
        return await self.wait_for_sync(job.job_id)
    
    # ==================== File/Folder Browsing ====================
    # These are adapted for non-file-based sources like Confluence/Jira
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """
        List root-level containers.
        
        For Confluence: Spaces
        For Jira: Projects
        """
        # Override in subclasses to return appropriate containers
        return []
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """
        List contents of a container.
        
        For Confluence: Pages in a space
        For Jira: Issues in a project
        """
        # Override in subclasses
        return [], []
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a single item."""
        raise NotImplementedError("Override in subclass")
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """
        Download file/content as a stream.
        
        For Confluence: Page content
        For Jira: Issue details + attachments
        """
        raise NotImplementedError("Override in subclass")
    
    async def list_all_files(
        self,
        folder_id: str,
        recursive: bool = True,
        file_types: Optional[list[str]] = None
    ) -> AsyncIterator[RemoteFile]:
        """
        List all items in a container.
        
        For Airbyte-based providers, this typically queries MongoDB
        for synced data rather than the source directly.
        """
        raise NotImplementedError("Override in subclass")
    
    # ==================== Cleanup ====================
    
    async def delete_resources(self) -> bool:
        """Delete all Airbyte resources associated with this provider."""
        try:
            if self._connection_id:
                await self.client.delete_connection(self._connection_id)
                logger.info(f"Deleted connection: {self._connection_id}")
            
            if self._source_id:
                await self.client.delete_source(self._source_id)
                logger.info(f"Deleted source: {self._source_id}")
            
            # Don't delete shared destination
            
            self._connection_id = None
            self._source_id = None
            
            return True
        except Exception as e:
            logger.error(f"Failed to delete resources: {e}")
            return False
    
    async def close(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.close()
            self._client = None
