import os
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from common.audit import audit
from common.db import conn
from common.errors import register_error_handlers
from common.security import current_user, require_internal_token

app = FastAPI(title="Notification Service")
register_error_handlers(app)


class NotificationIn(BaseModel):
    user_id: str
    message: str
    type: str  # email, in_app, etc.


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/internal/notifications", dependencies=[Depends(require_internal_token)], status_code=201)
def send_notification(payload: NotificationIn):
    # For demo, just log the notification
    print(f"Notification to {payload.user_id}: {payload.message} ({payload.type})")
    # In real implementation, send email, push notification, etc.
    return {"status": "sent"}


@app.get("/notifications")
def my_notifications(user: dict = Depends(current_user)):
    # For demo, return empty list
    return []
