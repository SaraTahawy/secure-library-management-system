from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Json

from common.db import conn
from common.errors import register_error_handlers
from common.security import current_user, require_internal_token, require_role

app = FastAPI(title="Audit Service")
register_error_handlers(app)


class AuditIn(BaseModel):
    user_id: str | None = None
    action: str = Field(min_length=2, max_length=80)
    ip_address: str = Field(min_length=1, max_length=80)
    status: str = Field(pattern="^(success|failed|denied|error|info)$")
    details: dict = Field(default_factory=dict)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/internal/audit", dependencies=[Depends(require_internal_token)], status_code=201)
def create_audit(payload: AuditIn):
    with conn() as db:
        row = db.execute(
            """
            INSERT INTO audit_logs(user_id, action, ip_address, status, details)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (payload.user_id, payload.action, payload.ip_address, payload.status, Json(payload.details)),
        ).fetchone()
        db.commit()
    return {"id": row["id"]}


@app.get("/admin/logs")
def logs(_: dict = Depends(require_role("admin"))):
    with conn() as db:
        return db.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 200").fetchall()


@app.get("/admin/metrics")
def metrics(_: dict = Depends(require_role("admin"))):
    with conn() as db:
        return {
            "total_users": db.execute("SELECT count(*) AS c FROM users").fetchone()["c"],
            "total_books": db.execute("SELECT count(*) AS c FROM books").fetchone()["c"],
            "borrowed_books": db.execute("SELECT count(*) AS c FROM loans WHERE status='active'").fetchone()["c"],
            "failed_logins": db.execute("SELECT count(*) AS c FROM audit_logs WHERE action='login' AND status='failed'").fetchone()["c"],
            "unauthorized_attempts": db.execute("SELECT count(*) AS c FROM audit_logs WHERE status='denied'").fetchone()["c"],
            "request_count": db.execute("SELECT count(*) AS c FROM audit_logs").fetchone()["c"],
            "api_usage": db.execute("SELECT count(*) AS c FROM audit_logs WHERE action NOT LIKE 'background_%'").fetchone()["c"],
            "queue_jobs": db.execute("SELECT count(*) AS c FROM documents WHERE processing_status='queued'").fetchone()["c"],
            "processed_jobs": db.execute("SELECT count(*) AS c FROM documents WHERE processing_status='processed'").fetchone()["c"],
            "active_users": db.execute("SELECT count(DISTINCT user_id) AS c FROM audit_logs WHERE created_at > now() - interval '30 minutes'").fetchone()["c"],
        }


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return user
