"""
Cloud Sources Router Package

This package provides API endpoints for managing cloud source connections,
sync configurations, and synchronization jobs.
"""

from backend.routers.cloud_sources.connections import router as connections_router
from backend.routers.cloud_sources.oauth import router as oauth_router
from backend.routers.cloud_sources.sync import router as sync_router
from backend.routers.cloud_sources.providers import router as providers_router

__all__ = [
    "connections_router",
    "oauth_router", 
    "sync_router",
    "providers_router",
]
