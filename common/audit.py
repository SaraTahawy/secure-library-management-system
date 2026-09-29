import logging

import httpx

from .config import env

logger = logging.getLogger("audit-client")


async def audit(user_id: str | None, action: str, ip_address: str, status: str, details: dict | None = None) -> None:
    payload = {
        "user_id": user_id,
        "action": action,
        "ip_address": ip_address,
        "status": status,
        "details": details or {},
    }
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.post(
                "http://audit-service:8000/internal/audit",
                headers={"X-Internal-Token": env("INTERNAL_API_KEY")},
                json=payload,
            )
    except Exception as exc:
        logger.warning("audit service unavailable: %s", exc)
