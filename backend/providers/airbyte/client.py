"""
Airbyte API Client

Provides a Python client for interacting with the Airbyte API to manage
sources, destinations, connections, and sync jobs.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Callable, TypeVar
from functools import wraps
import httpx

logger = logging.getLogger(__name__)

# Type variable for generic retry decorator
T = TypeVar('T')


# ==================== Custom Exceptions ====================

class AirbyteError(Exception):
    """Base exception for all Airbyte errors."""
    pass


class AirbyteConnectionError(AirbyteError):
    """Failed to connect to Airbyte API."""
    pass


class AirbyteNotAvailableError(AirbyteError):
    """Airbyte service is not available or not running."""
    pass


class AirbyteAPIError(AirbyteError):
    """API returned an error response."""
    def __init__(self, message: str, status_code: int = 0, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AirbyteRateLimitError(AirbyteError):
    """Rate limit exceeded."""
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class AirbyteResourceNotFoundError(AirbyteError):
    """Requested resource was not found."""
    pass


class AirbyteValidationError(AirbyteError):
    """Invalid request parameters."""
    pass


class AirbyteTimeoutError(AirbyteError):
    """Operation timed out."""
    pass


class AirbyteConnectionStatus(str, Enum):
    """Airbyte connection status."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"


class AirbyteSyncStatus(str, Enum):
    """Airbyte sync job status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AirbyteSourceConfig:
    """Configuration for an Airbyte source."""
    source_definition_id: str
    name: str
    connection_configuration: dict[str, Any]
    workspace_id: Optional[str] = None


@dataclass
class AirbyteDestinationConfig:
    """Configuration for an Airbyte destination."""
    destination_definition_id: str
    name: str
    connection_configuration: dict[str, Any]
    workspace_id: Optional[str] = None


@dataclass
class AirbyteConnection:
    """Represents an Airbyte connection (source -> destination)."""
    connection_id: str
    name: str
    source_id: str
    destination_id: str
    status: AirbyteConnectionStatus
    schedule: Optional[dict[str, Any]] = None
    sync_catalog: Optional[dict[str, Any]] = None
    namespace_definition: str = "source"
    namespace_format: str = "${SOURCE_NAMESPACE}"
    prefix: str = ""


@dataclass
class AirbyteSyncJob:
    """Represents an Airbyte sync job."""
    job_id: str
    connection_id: str
    status: AirbyteSyncStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    bytes_synced: int = 0
    records_synced: int = 0
    attempts: int = 0


@dataclass
class AirbyteSource:
    """Represents an Airbyte source."""
    source_id: str
    source_definition_id: str
    workspace_id: str
    name: str
    connection_configuration: dict[str, Any] = field(default_factory=dict)


@dataclass
class AirbyteDestination:
    """Represents an Airbyte destination."""
    destination_id: str
    destination_definition_id: str
    workspace_id: str
    name: str
    connection_configuration: dict[str, Any] = field(default_factory=dict)


class AirbyteClient:
    """
    Async client for interacting with the Airbyte API.
    
    This client provides methods to manage sources, destinations, connections,
    and sync jobs through Airbyte's REST API.
    
    Features:
    - Automatic retry with exponential backoff for transient failures
    - Custom exceptions for different error types
    - Health check before operations
    - Graceful error handling
    """
    
    # Well-known Airbyte source definition IDs
    SOURCE_DEFINITIONS = {
        "confluence": "d67e91a1-a6e6-476e-8596-9e16931fb7d3",
        "jira": "68e63de2-bb83-4c7e-93fa-a8a9051f3d29",
        "slack": "00000000-0000-0000-0000-000000000000",  # Will be discovered
        "notion": "6e00b415-b02e-4160-bf02-58176a0ae687",
        "github": "ef69ef6e-aa7f-4af1-a01d-ef775033524e",
        "hubspot": "36c891d9-4bd9-43ac-bad2-10e12756272c",
        "salesforce": "b117307c-14b6-41aa-9422-947e34922962",
        "zendesk": "c7cb421b-942e-4468-99ee-e369bcabaec5",
    }
    
    # MongoDB destination definition ID
    MONGODB_DESTINATION_ID = "8b746512-8c2e-6ac1-4adc-b59faafd473c"
    
    # Retry configuration
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 1.0  # seconds
    DEFAULT_RETRY_BACKOFF = 2.0  # exponential backoff multiplier
    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    
    def __init__(
        self,
        base_url: str = "http://localhost:11021",
        timeout: float = 60.0,
        workspace_id: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        """
        Initialize the Airbyte client.
        
        Args:
            base_url: Airbyte API base URL
            timeout: Request timeout in seconds
            workspace_id: Default workspace ID for operations
            max_retries: Maximum number of retry attempts for transient failures
            retry_delay: Initial delay between retries (exponential backoff applies)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.workspace_id = workspace_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client: Optional[httpx.AsyncClient] = None
        self._is_available: Optional[bool] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=f"{self.base_url}/api/v1",
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client
    
    def _parse_error_response(self, response: httpx.Response) -> str:
        """Parse error message from response."""
        try:
            data = response.json()
            return data.get("message") or data.get("error") or str(data)
        except Exception:
            return response.text or f"HTTP {response.status_code}"
    
    def _handle_response_error(self, response: httpx.Response) -> None:
        """Convert HTTP errors to appropriate exceptions."""
        status = response.status_code
        message = self._parse_error_response(response)
        
        if status == 404:
            raise AirbyteResourceNotFoundError(f"Resource not found: {message}")
        elif status == 400 or status == 422:
            raise AirbyteValidationError(f"Invalid request: {message}")
        elif status == 429:
            retry_after = response.headers.get("Retry-After")
            raise AirbyteRateLimitError(
                f"Rate limit exceeded: {message}",
                retry_after=int(retry_after) if retry_after else None
            )
        elif status >= 500:
            raise AirbyteAPIError(
                f"Server error: {message}",
                status_code=status,
                response_body=response.text
            )
        else:
            raise AirbyteAPIError(
                f"API error: {message}",
                status_code=status,
                response_body=response.text
            )
    
    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict] = None,
        require_available: bool = True,
    ) -> httpx.Response:
        """
        Make an HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            json: JSON body for POST requests
            require_available: Check Airbyte availability before request
            
        Returns:
            HTTP response
            
        Raises:
            AirbyteNotAvailableError: If Airbyte is not running
            AirbyteConnectionError: If connection fails after retries
            AirbyteAPIError: If API returns an error
        """
        # Check availability if required and not already checked
        if require_available and self._is_available is None:
            await self.ensure_available()
        
        client = await self._get_client()
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = await client.get(endpoint)
                else:
                    response = await client.post(endpoint, json=json)
                
                # Check for retryable status codes
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (self.DEFAULT_RETRY_BACKOFF ** attempt)
                        logger.warning(
                            f"Request to {endpoint} returned {response.status_code}, "
                            f"retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                
                # Handle non-success status codes
                if response.status_code >= 400:
                    self._handle_response_error(response)
                
                return response
                
            except httpx.ConnectError as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.retry_delay * (self.DEFAULT_RETRY_BACKOFF ** attempt)
                    logger.warning(
                        f"Connection to Airbyte failed, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries}): {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    self._is_available = False
                    raise AirbyteConnectionError(
                        f"Failed to connect to Airbyte at {self.base_url}: {e}"
                    ) from e
                    
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.retry_delay * (self.DEFAULT_RETRY_BACKOFF ** attempt)
                    logger.warning(
                        f"Request to {endpoint} timed out, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise AirbyteTimeoutError(
                        f"Request to {endpoint} timed out after {self.max_retries} attempts"
                    ) from e
                    
            except (AirbyteRateLimitError, AirbyteAPIError) as e:
                # Don't retry these unless it's a rate limit with retry-after
                if isinstance(e, AirbyteRateLimitError) and e.retry_after:
                    if attempt < self.max_retries:
                        logger.warning(f"Rate limited, waiting {e.retry_after}s before retry")
                        await asyncio.sleep(e.retry_after)
                        continue
                raise
        
        # Should not reach here, but just in case
        raise AirbyteConnectionError(
            f"Failed after {self.max_retries} retries: {last_exception}"
        )
    
    async def ensure_available(self) -> bool:
        """
        Ensure Airbyte is available and running.
        
        Raises:
            AirbyteNotAvailableError: If Airbyte is not available
        """
        is_healthy = await self.health_check()
        if not is_healthy:
            self._is_available = False
            raise AirbyteNotAvailableError(
                f"Airbyte is not available at {self.base_url}. "
                "Please ensure Airbyte is running with: "
                "docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml up -d"
            )
        self._is_available = True
        return True
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    # ==================== Health Check ====================
    
    async def health_check(self) -> bool:
        """Check if Airbyte API is available."""
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"Airbyte health check failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Airbyte health check failed with unexpected error: {e}")
            return False
    
    # ==================== Workspace Management ====================
    
    async def get_workspaces(self) -> list[dict[str, Any]]:
        """List all workspaces."""
        response = await self._request_with_retry("POST", "/workspaces/list")
        return response.json().get("workspaces", [])
    
    async def get_default_workspace(self) -> str:
        """Get the default workspace ID."""
        if self.workspace_id:
            return self.workspace_id
        
        workspaces = await self.get_workspaces()
        if not workspaces:
            raise AirbyteValidationError("No Airbyte workspaces found. Please set up Airbyte first.")
        
        # Use first workspace as default
        self.workspace_id = workspaces[0]["workspaceId"]
        return self.workspace_id
    
    # ==================== Source Definitions ====================
    
    async def list_source_definitions(self) -> list[dict[str, Any]]:
        """List available source definitions."""
        workspace_id = await self.get_default_workspace()
        response = await self._request_with_retry(
            "POST",
            "/source_definitions/list_for_workspace",
            json={"workspaceId": workspace_id}
        )
        return response.json().get("sourceDefinitions", [])
    
    async def get_source_definition(self, source_type: str) -> Optional[dict[str, Any]]:
        """Get source definition by type name."""
        definitions = await self.list_source_definitions()
        source_type_lower = source_type.lower()
        
        for definition in definitions:
            name = definition.get("name", "").lower()
            if source_type_lower in name:
                return definition
        
        return None
    
    async def get_source_definition_specification(
        self, 
        source_definition_id: str
    ) -> dict[str, Any]:
        """Get the configuration specification for a source definition."""
        if not source_definition_id:
            raise AirbyteValidationError("source_definition_id is required")
        
        workspace_id = await self.get_default_workspace()
        response = await self._request_with_retry(
            "POST",
            "/source_definition_specifications/get",
            json={
                "sourceDefinitionId": source_definition_id,
                "workspaceId": workspace_id
            }
        )
        return response.json()
    
    # ==================== Source Management ====================
    
    async def create_source(self, config: AirbyteSourceConfig) -> AirbyteSource:
        """Create a new source."""
        if not config.source_definition_id:
            raise AirbyteValidationError("source_definition_id is required")
        if not config.name:
            raise AirbyteValidationError("source name is required")
        
        workspace_id = config.workspace_id or await self.get_default_workspace()
        
        response = await self._request_with_retry(
            "POST",
            "/sources/create",
            json={
                "sourceDefinitionId": config.source_definition_id,
                "workspaceId": workspace_id,
                "name": config.name,
                "connectionConfiguration": config.connection_configuration or {},
            }
        )
        data = response.json()
        
        return AirbyteSource(
            source_id=data["sourceId"],
            source_definition_id=data["sourceDefinitionId"],
            workspace_id=data["workspaceId"],
            name=data["name"],
            connection_configuration=data.get("connectionConfiguration", {}),
        )
    
    async def get_source(self, source_id: str) -> AirbyteSource:
        """Get a source by ID."""
        if not source_id:
            raise AirbyteValidationError("source_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/sources/get",
            json={"sourceId": source_id}
        )
        data = response.json()
        
        return AirbyteSource(
            source_id=data["sourceId"],
            source_definition_id=data["sourceDefinitionId"],
            workspace_id=data["workspaceId"],
            name=data["name"],
            connection_configuration=data.get("connectionConfiguration", {}),
        )
    
    async def delete_source(self, source_id: str) -> bool:
        """Delete a source."""
        if not source_id:
            raise AirbyteValidationError("source_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/sources/delete",
            json={"sourceId": source_id}
        )
        return response.status_code in (200, 204)
    
    async def check_source_connection(self, source_id: str) -> dict[str, Any]:
        """Check if a source connection is valid."""
        if not source_id:
            raise AirbyteValidationError("source_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/sources/check_connection",
            json={"sourceId": source_id}
        )
        return response.json()
    
    async def list_sources(self) -> list[AirbyteSource]:
        """List all sources in the workspace."""
        workspace_id = await self.get_default_workspace()
        
        response = await self._request_with_retry(
            "POST",
            "/sources/list",
            json={"workspaceId": workspace_id}
        )
        
        sources = []
        for data in response.json().get("sources", []):
            sources.append(AirbyteSource(
                source_id=data.get("sourceId", ""),
                source_definition_id=data.get("sourceDefinitionId", ""),
                workspace_id=data.get("workspaceId", ""),
                name=data.get("name", ""),
                connection_configuration=data.get("connectionConfiguration", {}),
            ))
        return sources
    
    async def discover_source_schema(self, source_id: str) -> dict[str, Any]:
        """
        Discover the schema/catalog for a source.
        
        Note: This operation can take a while for large sources.
        """
        if not source_id:
            raise AirbyteValidationError("source_id is required")
        
        # Use longer timeout for schema discovery
        client = await self._get_client()
        original_timeout = client.timeout
        client.timeout = httpx.Timeout(300.0)  # 5 minutes
        
        try:
            response = await self._request_with_retry(
                "POST",
                "/sources/discover_schema",
                json={"sourceId": source_id}
            )
            return response.json()
        finally:
            client.timeout = original_timeout
    
    # ==================== Destination Management ====================
    
    async def list_destination_definitions(self) -> list[dict[str, Any]]:
        """List available destination definitions."""
        workspace_id = await self.get_default_workspace()
        response = await self._request_with_retry(
            "POST",
            "/destination_definitions/list_for_workspace",
            json={"workspaceId": workspace_id}
        )
        return response.json().get("destinationDefinitions", [])
    
    async def create_destination(
        self, 
        config: AirbyteDestinationConfig
    ) -> AirbyteDestination:
        """Create a new destination."""
        if not config.destination_definition_id:
            raise AirbyteValidationError("destination_definition_id is required")
        if not config.name:
            raise AirbyteValidationError("destination name is required")
        
        workspace_id = config.workspace_id or await self.get_default_workspace()
        
        response = await self._request_with_retry(
            "POST",
            "/destinations/create",
            json={
                "destinationDefinitionId": config.destination_definition_id,
                "workspaceId": workspace_id,
                "name": config.name,
                "connectionConfiguration": config.connection_configuration or {},
            }
        )
        data = response.json()
        
        return AirbyteDestination(
            destination_id=data["destinationId"],
            destination_definition_id=data["destinationDefinitionId"],
            workspace_id=data["workspaceId"],
            name=data["name"],
            connection_configuration=data.get("connectionConfiguration", {}),
        )
    
    async def get_destination(self, destination_id: str) -> AirbyteDestination:
        """Get a destination by ID."""
        if not destination_id:
            raise AirbyteValidationError("destination_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/destinations/get",
            json={"destinationId": destination_id}
        )
        data = response.json()
        
        return AirbyteDestination(
            destination_id=data["destinationId"],
            destination_definition_id=data["destinationDefinitionId"],
            workspace_id=data["workspaceId"],
            name=data["name"],
            connection_configuration=data.get("connectionConfiguration", {}),
        )
    
    async def delete_destination(self, destination_id: str) -> bool:
        """Delete a destination."""
        if not destination_id:
            raise AirbyteValidationError("destination_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/destinations/delete",
            json={"destinationId": destination_id}
        )
        return response.status_code in (200, 204)
    
    async def list_destinations(self) -> list[AirbyteDestination]:
        """List all destinations in the workspace."""
        workspace_id = await self.get_default_workspace()
        
        response = await self._request_with_retry(
            "POST",
            "/destinations/list",
            json={"workspaceId": workspace_id}
        )
        
        destinations = []
        for data in response.json().get("destinations", []):
            destinations.append(AirbyteDestination(
                destination_id=data.get("destinationId", ""),
                destination_definition_id=data.get("destinationDefinitionId", ""),
                workspace_id=data.get("workspaceId", ""),
                name=data.get("name", ""),
                connection_configuration=data.get("connectionConfiguration", {}),
            ))
        return destinations
    
    # ==================== Connection Management ====================
    
    async def create_connection(
        self,
        name: str,
        source_id: str,
        destination_id: str,
        sync_catalog: dict[str, Any],
        schedule: Optional[dict[str, Any]] = None,
        namespace_definition: str = "source",
        prefix: str = "",
    ) -> AirbyteConnection:
        """Create a connection between a source and destination."""
        if not name:
            raise AirbyteValidationError("connection name is required")
        if not source_id:
            raise AirbyteValidationError("source_id is required")
        if not destination_id:
            raise AirbyteValidationError("destination_id is required")
        if not sync_catalog or not sync_catalog.get("streams"):
            raise AirbyteValidationError("sync_catalog with streams is required")
        
        payload = {
            "name": name,
            "sourceId": source_id,
            "destinationId": destination_id,
            "syncCatalog": sync_catalog,
            "namespaceDefinition": namespace_definition,
            "status": "active",
        }
        
        if prefix:
            payload["prefix"] = prefix
        
        if schedule:
            payload["schedule"] = schedule
        
        response = await self._request_with_retry("POST", "/connections/create", json=payload)
        data = response.json()
        
        return AirbyteConnection(
            connection_id=data["connectionId"],
            name=data["name"],
            source_id=data["sourceId"],
            destination_id=data["destinationId"],
            status=AirbyteConnectionStatus(data.get("status", "inactive")),
            schedule=data.get("schedule"),
            sync_catalog=data.get("syncCatalog"),
        )
    
    async def get_connection(self, connection_id: str) -> AirbyteConnection:
        """Get a connection by ID."""
        if not connection_id:
            raise AirbyteValidationError("connection_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/connections/get",
            json={"connectionId": connection_id}
        )
        data = response.json()
        
        return AirbyteConnection(
            connection_id=data["connectionId"],
            name=data["name"],
            source_id=data["sourceId"],
            destination_id=data["destinationId"],
            status=AirbyteConnectionStatus(data.get("status", "inactive")),
            schedule=data.get("schedule"),
            sync_catalog=data.get("syncCatalog"),
        )
    
    async def update_connection(
        self,
        connection_id: str,
        **updates: Any
    ) -> AirbyteConnection:
        """Update a connection."""
        if not connection_id:
            raise AirbyteValidationError("connection_id is required")
        
        # Get current connection first
        current = await self.get_connection(connection_id)
        
        payload = {
            "connectionId": connection_id,
            "syncCatalog": current.sync_catalog,
            "status": updates.get("status", current.status.value),
        }
        
        if "schedule" in updates:
            payload["schedule"] = updates["schedule"]
        elif current.schedule:
            payload["schedule"] = current.schedule
        
        if "name" in updates:
            payload["name"] = updates["name"]
        
        response = await self._request_with_retry("POST", "/connections/update", json=payload)
        data = response.json()
        
        return AirbyteConnection(
            connection_id=data["connectionId"],
            name=data["name"],
            source_id=data["sourceId"],
            destination_id=data["destinationId"],
            status=AirbyteConnectionStatus(data.get("status", "inactive")),
            schedule=data.get("schedule"),
            sync_catalog=data.get("syncCatalog"),
        )
    
    async def delete_connection(self, connection_id: str) -> bool:
        """Delete a connection."""
        if not connection_id:
            raise AirbyteValidationError("connection_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/connections/delete",
            json={"connectionId": connection_id}
        )
        return response.status_code in (200, 204)
    
    async def list_connections(self) -> list[AirbyteConnection]:
        """List all connections in the workspace."""
        workspace_id = await self.get_default_workspace()
        
        response = await self._request_with_retry(
            "POST",
            "/connections/list",
            json={"workspaceId": workspace_id}
        )
        
        connections = []
        for data in response.json().get("connections", []):
            connections.append(AirbyteConnection(
                connection_id=data.get("connectionId", ""),
                name=data.get("name", ""),
                source_id=data.get("sourceId", ""),
                destination_id=data.get("destinationId", ""),
                status=AirbyteConnectionStatus(data.get("status", "inactive")),
                schedule=data.get("schedule"),
                sync_catalog=data.get("syncCatalog"),
            ))
        return connections
    
    # ==================== Sync Job Management ====================
    
    async def trigger_sync(self, connection_id: str) -> AirbyteSyncJob:
        """Trigger a manual sync for a connection."""
        if not connection_id:
            raise AirbyteValidationError("connection_id is required")
        
        response = await self._request_with_retry(
            "POST",
            "/connections/sync",
            json={"connectionId": connection_id}
        )
        data = response.json()
        
        job = data.get("job", {})
        if not job:
            raise AirbyteAPIError("No job returned from sync trigger")
        
        return AirbyteSyncJob(
            job_id=str(job.get("id", "")),
            connection_id=connection_id,
            status=AirbyteSyncStatus(job.get("status", "pending").lower()),
            created_at=self._parse_datetime(job.get("createdAt")),
        )
    
    def _parse_datetime(self, value: Optional[str]) -> datetime:
        """Safely parse datetime string."""
        if not value:
            return datetime.now()
        try:
            # Handle various datetime formats
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return datetime.now()
    
    async def get_job(self, job_id: str) -> AirbyteSyncJob:
        """Get job status by ID."""
        if not job_id:
            raise AirbyteValidationError("job_id is required")
        
        try:
            job_id_int = int(job_id)
        except ValueError:
            raise AirbyteValidationError(f"Invalid job_id: {job_id}")
        
        response = await self._request_with_retry(
            "POST",
            "/jobs/get",
            json={"id": job_id_int}
        )
        data = response.json()
        
        job = data.get("job", {})
        attempts = data.get("attempts", [])
        
        # Calculate totals from attempts (safely handle missing data)
        bytes_synced = sum(a.get("bytesSynced") or 0 for a in attempts)
        records_synced = sum(a.get("recordsSynced") or 0 for a in attempts)
        
        return AirbyteSyncJob(
            job_id=str(job.get("id", "")),
            connection_id=job.get("configId", ""),
            status=AirbyteSyncStatus(job.get("status", "pending").lower()),
            created_at=self._parse_datetime(job.get("createdAt")),
            updated_at=self._parse_datetime(job.get("updatedAt")) if job.get("updatedAt") else None,
            bytes_synced=bytes_synced,
            records_synced=records_synced,
            attempts=len(attempts),
        )
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        if not job_id:
            raise AirbyteValidationError("job_id is required")
        
        try:
            job_id_int = int(job_id)
        except ValueError:
            raise AirbyteValidationError(f"Invalid job_id: {job_id}")
        
        response = await self._request_with_retry(
            "POST",
            "/jobs/cancel",
            json={"id": job_id_int}
        )
        return response.status_code == 200
    
    async def list_jobs(
        self,
        connection_id: Optional[str] = None,
        status: Optional[AirbyteSyncStatus] = None,
        limit: int = 20,
    ) -> list[AirbyteSyncJob]:
        """List sync jobs."""
        payload: dict[str, Any] = {"configTypes": ["sync"], "pagination": {"pageSize": min(limit, 100)}}
        
        if connection_id:
            payload["configId"] = connection_id
        
        response = await self._request_with_retry("POST", "/jobs/list", json=payload)
        
        jobs = []
        for data in response.json().get("jobs", []):
            job = data.get("job", {})
            attempts = data.get("attempts", [])
            
            bytes_synced = sum(a.get("bytesSynced") or 0 for a in attempts)
            records_synced = sum(a.get("recordsSynced") or 0 for a in attempts)
            
            try:
                job_status = AirbyteSyncStatus(job.get("status", "pending").lower())
            except ValueError:
                job_status = AirbyteSyncStatus.PENDING
            
            if status and job_status != status:
                continue
            
            jobs.append(AirbyteSyncJob(
                job_id=str(job.get("id", "")),
                connection_id=job.get("configId", ""),
                status=job_status,
                created_at=self._parse_datetime(job.get("createdAt")),
                updated_at=self._parse_datetime(job.get("updatedAt")) if job.get("updatedAt") else None,
                bytes_synced=bytes_synced,
                records_synced=records_synced,
                attempts=len(attempts),
            ))
        
        return jobs
    
    async def wait_for_job_completion(
        self,
        job_id: str,
        poll_interval: float = 5.0,
        timeout: float = 3600.0,
    ) -> AirbyteSyncJob:
        """Wait for a job to complete."""
        if not job_id:
            raise AirbyteValidationError("job_id is required")
        
        start_time = asyncio.get_event_loop().time()
        
        while True:
            job = await self.get_job(job_id)
            
            if job.status in (
                AirbyteSyncStatus.SUCCEEDED,
                AirbyteSyncStatus.FAILED,
                AirbyteSyncStatus.CANCELLED,
            ):
                return job
            
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise AirbyteTimeoutError(f"Job {job_id} did not complete within {timeout}s")
            
            await asyncio.sleep(poll_interval)
    
    # ==================== Utility Methods ====================
    
    async def get_mongodb_destination_id(self) -> Optional[str]:
        """Find the MongoDB destination definition ID."""
        definitions = await self.list_destination_definitions()
        
        for definition in definitions:
            name = definition.get("name", "").lower()
            if "mongodb" in name:
                return definition.get("destinationDefinitionId")
        
        return None
    
    async def create_mongodb_destination(
        self,
        name: str,
        mongodb_uri: str,
        database: str,
    ) -> AirbyteDestination:
        """Create a MongoDB destination for syncing data."""
        dest_def_id = await self.get_mongodb_destination_id()
        if not dest_def_id:
            raise ValueError("MongoDB destination not found in Airbyte")
        
        config = AirbyteDestinationConfig(
            destination_definition_id=dest_def_id,
            name=name,
            connection_configuration={
                "instance_type": {
                    "instance": "standalone",
                    "host": mongodb_uri.split("://")[1].split(":")[0] if "://" in mongodb_uri else mongodb_uri.split(":")[0],
                    "port": 27017,
                    "tls": False,
                },
                "database": database,
                "auth_type": {"authorization": "none"},
            }
        )
        
        return await self.create_destination(config)
