"""Tenant configuration endpoint for multi-tenant branding."""

import logging
from fastapi import APIRouter
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant", tags=["Tenant"])


# Default tenant configurations (can be extended to read from MongoDB)
TENANT_CONFIGS = {
    "recallhub": {
        "id": "recallhub",
        "branding": {
            "appName": "RecallHub",
            "tagline": "Your intelligent knowledge management platform",
            "logoUrl": "/tenants/recallhub/logo.svg",
            "faviconUrl": "/tenants/recallhub/favicon.svg",
        },
        "theme": {
            "colors": {
                "primary": {
                    "DEFAULT": "#6750A4",
                    "50": "#F6F2FF",
                    "100": "#E9DDFF",
                    "200": "#D0BCFF",
                    "300": "#B69DF8",
                    "400": "#9A82DB",
                    "500": "#7F67BE",
                    "600": "#6750A4",
                    "700": "#4F378B",
                    "800": "#381E72",
                    "900": "#21005D",
                },
                "secondary": {
                    "DEFAULT": "#625B71",
                    "50": "#F9F5FF",
                    "100": "#E8DEF8",
                    "200": "#C9C0D4",
                    "300": "#ABA2B7",
                    "400": "#8D849A",
                    "500": "#71687D",
                    "600": "#625B71",
                    "700": "#4A4458",
                    "800": "#332D41",
                    "900": "#1D192B",
                },
                "surface": {"DEFAULT": "#FFFBFE", "variant": "#E7E0EC"},
                "background": {"DEFAULT": "#FFFBFE", "dark": "#1C1B1F"},
                "gradient": {"from": "#6366f1", "to": "#8b5cf6"},
            },
            "fonts": {
                "sans": ["Inter", "Roboto", "system-ui", "sans-serif"],
            },
            "borderRadius": {
                "base": "16px",
                "button": "28px",
                "input": "16px",
            },
            "darkMode": {
                "background": "#1C1B1F",
                "surface": "#2B2930",
                "text": "#E6E1E5",
            },
        },
        "features": {
            "showCloudSources": True,
            "showEmailConfig": True,
            "showProfiles": True,
            "showStrategies": True,
            "showEmbeddingBenchmark": True,
            "showBackups": True,
            "showApiDocs": True,
            "landingPageVariant": "default",
        },
    },
    "quellex": {
        "id": "quellex",
        "branding": {
            "appName": "Quellex",
            "tagline": "Ihr Kanzleiwissen. Schnell gefunden.",
            "logoUrl": "/tenants/quellex/assets/quellex-logo.png",
            "faviconUrl": "/tenants/quellex/favicon.svg",
        },
        "theme": {
            "colors": {
                "primary": {
                    "DEFAULT": "#C29629",
                    "50": "#FDF8E8",
                    "100": "#F5E6B8",
                    "200": "#EDD488",
                    "300": "#E5C258",
                    "400": "#DDB028",
                    "500": "#C49B1A",
                    "600": "#C29629",
                    "700": "#8B6508",
                    "800": "#5E4406",
                    "900": "#312303",
                },
                "secondary": {
                    "DEFAULT": "#248F8F",
                    "50": "#E6F5F3",
                    "100": "#B3E0DA",
                    "200": "#80CBC2",
                    "300": "#4DB6AA",
                    "400": "#26A69A",
                    "500": "#1B9085",
                    "600": "#248F8F",
                    "700": "#155E55",
                    "800": "#10423C",
                    "900": "#0A2622",
                },
                "accent": {
                    "DEFAULT": "#248F8F",
                    "50": "#E6F5F3",
                    "500": "#248F8F",
                    "700": "#1B6B6B",
                },
                "surface": {"DEFAULT": "#F8F6F2", "variant": "#EFECE7"},
                "background": {"DEFAULT": "#F8F6F2", "dark": "#0A1629"},
                "gradient": {"from": "#C29629", "to": "#248F8F"},
            },
            "fonts": {
                "sans": ["Inter", "system-ui", "sans-serif"],
                "display": ["Playfair Display", "Georgia", "serif"],
            },
            "borderRadius": {
                "base": "12px",
                "button": "12px",
                "input": "8px",
            },
            "darkMode": {
                "background": "#0A1629",
                "surface": "#0F1E33",
                "text": "#FFFFFF",
            },
        },
        "features": {
            "showCloudSources": True,
            "showEmailConfig": True,
            "showProfiles": True,
            "showStrategies": True,
            "showEmbeddingBenchmark": False,
            "showBackups": True,
            "showApiDocs": False,
            "landingPageVariant": "quellex",
        },
    },
}


@router.get("/config")
async def get_tenant_config():
    """Get the current tenant configuration.
    
    Returns the tenant config for the configured TENANT_ID.
    No authentication required - needed before login for branding.
    """
    tenant_id = settings.tenant_id
    if tenant_id not in TENANT_CONFIGS:
        logger.warning(
            "Unknown TENANT_ID '%s' in settings, falling back to 'recallhub'. "
            "Available tenants: %s",
            tenant_id,
            list(TENANT_CONFIGS.keys()),
        )
    config = TENANT_CONFIGS.get(tenant_id, TENANT_CONFIGS["recallhub"])
    return {
        "tenant_id": tenant_id,
        "config": config,
    }
