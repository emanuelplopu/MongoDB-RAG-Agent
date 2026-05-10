"""User support request endpoints.

Allows users to submit diagnostic information for support.
Supports external delivery via email and webhook.
"""

import asyncio
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.core.config import settings
from backend.routers.auth import get_current_user, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support", tags=["support"])


class SupportRequest(BaseModel):
    """Support request payload."""
    diagnostic_text: str
    session_id: Optional[str] = None
    user_description: Optional[str] = None


class SupportResponse(BaseModel):
    """Support request response."""
    success: bool
    message: str


@router.post("/request", response_model=SupportResponse)
async def submit_support_request(
    request: Request,
    payload: SupportRequest,
    user: UserResponse = Depends(get_current_user)
):
    """Submit a support request with diagnostic data.

    Saves to DB for admin review and optionally delivers externally.
    """
    db = request.app.state.db.db

    # Save to MongoDB
    doc = {
        "user_id": user.id,
        "user_email": user.email,
        "submitted_at": datetime.utcnow(),
        "diagnostic_data": payload.diagnostic_text,
        "user_description": payload.user_description,
        "session_id": payload.session_id,
        "status": "new"
    }

    try:
        await db["support_requests"].insert_one(doc)
    except Exception as e:
        logger.error(f"Failed to save support request: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit support request")

    # External delivery (fire-and-forget)
    asyncio.create_task(_deliver_externally(payload, user, settings))

    return SupportResponse(success=True, message="Support request submitted successfully")


@router.get("/diagnostic/{session_id}")
async def get_session_diagnostic(
    session_id: str,
    request: Request,
    user: UserResponse = Depends(get_current_user)
):
    """Get diagnostic data for the user's current session.

    Returns sanitized diagnostic information safe to show to end users.
    """
    db = request.app.state.db.db

    # Find the most recent activity log for this session and user
    doc = await db["agent_activity_log"].find_one(
        {"session_id": session_id, "user_id": user.id},
        sort=[("started_at", -1)]
    )

    if not doc:
        return {"diagnostic": _format_no_activity_diagnostic(session_id)}

    return {"diagnostic": _format_user_diagnostic(doc)}


def _format_no_activity_diagnostic(session_id: str) -> str:
    """Format diagnostic when no activity is found."""
    return (
        f"Session: {session_id}\n"
        f"Timestamp: {datetime.utcnow().isoformat()}\n"
        f"Status: No recent activity recorded for this session.\n"
        f"Note: Diagnostic data is available after sending at least one message."
    )


def _format_user_diagnostic(doc: dict) -> str:
    """Format activity log document into user-readable diagnostic text."""
    lines = [
        "=== Diagnostic Report ===",
        f"Request ID: {doc.get('_id', 'unknown')}",
        f"Session: {doc.get('session_id', 'unknown')}",
        f"Timestamp: {doc.get('started_at', datetime.utcnow()).isoformat()}",
        f"Duration: {doc.get('duration_ms', 0):.0f}ms",
        "",
    ]

    summary = doc.get("summary", {})
    if summary:
        lines.extend([
            "--- Summary ---",
            f"LLM Calls: {summary.get('total_llm_calls', 0)}",
            f"Searches: {summary.get('total_searches', 0)}",
            f"Tokens Used: {summary.get('total_tokens', 0)}",
            f"Phases: {', '.join(summary.get('phases_completed', []))}",
            f"Models: {', '.join(summary.get('models_used', []))}",
            f"Errors: {summary.get('total_errors', 0)}",
            "",
        ])

    # Include non-sensitive entries
    entries = doc.get("entries", [])
    errors = [e for e in entries if e.get("category") == "error"]
    if errors:
        lines.append("--- Errors ---")
        for err in errors[-5:]:  # Last 5 errors
            lines.append(
                f"  [{err.get('data', {}).get('phase', '?')}] "
                f"{err.get('data', {}).get('error', 'Unknown error')}"
            )
        lines.append("")

    # Phase timeline
    phases = [e for e in entries if e.get("category") == "phase"]
    if phases:
        lines.append("--- Phase Timeline ---")
        for p in phases:
            data = p.get("data", {})
            lines.append(
                f"  {data.get('phase', '?')}: {data.get('status', '?')} "
                f"({data.get('duration_ms', 0):.0f}ms)"
            )
        lines.append("")

    lines.append("=== End Report ===")
    return "\n".join(lines)


async def _deliver_externally(payload: SupportRequest, user: UserResponse, config):
    """Send support request to configured external channels.

    Fire-and-forget -- errors are logged but don't affect the user response.
    """
    # Email delivery
    if getattr(config, 'support_email_enabled', False) and config.support_email_to:
        try:
            await _send_email(payload, user, config)
            logger.info(f"Support request email sent for user {user.email}")
        except Exception as e:
            logger.error(f"Failed to send support email: {e}")

    # Webhook delivery
    if getattr(config, 'support_webhook_enabled', False) and config.support_webhook_url:
        try:
            await _send_webhook(payload, user, config)
            logger.info(f"Support request webhook sent for user {user.email}")
        except Exception as e:
            logger.error(f"Failed to send support webhook: {e}")


async def _send_email(payload: SupportRequest, user: UserResponse, config):
    """Send support request via SMTP email."""
    subject = (
        f"[Support Request] {user.email} - "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
    )

    body = (
        f"Support Request from: {user.email}\n"
        f"Session: {payload.session_id or 'N/A'}\n"
        f"Time: {datetime.utcnow().isoformat()}\n"
        f"\n"
        f"--- User Description ---\n"
        f"{payload.user_description or 'No description provided'}\n"
        f"\n"
        f"--- Diagnostic Data ---\n"
        f"{payload.diagnostic_text}\n"
    )

    msg = MIMEMultipart()
    msg['From'] = config.support_email_smtp_user or "noreply@recallhub.io"
    msg['To'] = config.support_email_to
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Run SMTP in thread pool to not block
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _smtp_send, msg, config)


def _smtp_send(msg: MIMEMultipart, config):
    """Synchronous SMTP send (runs in thread pool)."""
    with smtplib.SMTP(config.support_email_smtp_host, config.support_email_smtp_port) as server:
        server.starttls()
        if config.support_email_smtp_user and config.support_email_smtp_pass:
            server.login(config.support_email_smtp_user, config.support_email_smtp_pass)
        server.send_message(msg)


async def _send_webhook(payload: SupportRequest, user: UserResponse, config):
    """Send support request to webhook URL."""
    import httpx

    webhook_data = {
        "type": "support_request",
        "timestamp": datetime.utcnow().isoformat(),
        "user_email": user.email,
        "session_id": payload.session_id,
        "user_description": payload.user_description,
        "diagnostic_text": payload.diagnostic_text,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            config.support_webhook_url,
            json=webhook_data,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
