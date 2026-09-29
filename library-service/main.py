import hashlib
import os
import re
import uuid
from datetime import datetime

import pika
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from common.audit import audit
from common.config import env
from common.db import conn
from common.errors import register_error_handlers
from common.security import client_ip, current_user, require_internal_token, require_role

app = FastAPI(title="Library Service")
register_error_handlers(app)
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_COVER_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_COVER_BYTES = 2 * 1024 * 1024
BLOCKED_EXTENSIONS = {".exe", ".php", ".js", ".bat", ".sh"}


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    author: str = Field(min_length=1, max_length=120)
    isbn: str = Field(pattern=r"^[0-9Xx-]{10,17}$")
    total_copies: int = Field(ge=0, le=500)
    category: str | None = Field(default=None, max_length=80)


class BookUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    author: str | None = Field(default=None, min_length=1, max_length=120)
    isbn: str | None = Field(default=None, pattern=r"^[0-9Xx-]{10,17}$")
    total_copies: int | None = Field(default=None, ge=0, le=500)
    category: str | None = Field(default=None, max_length=80)


def fernet() -> Fernet:
    return Fernet(env("FILE_ENCRYPTION_KEY").encode())


def safe_name(filename: str) -> str:
    base, ext = os.path.splitext(filename)
    ext = ext.lower()
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", base)[:80]
    return f"{clean or 'file'}-{uuid.uuid4().hex}{ext}"


def file_extension(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower()


def allowed_document_extension(filename: str) -> bool:
    ext = file_extension(filename)
    return ext not in BLOCKED_EXTENSIONS and ext in ALLOWED_EXTENSIONS


def publish_document_job(document_id: str) -> None:
    credentials = pika.PlainCredentials(env("RABBITMQ_USER"), env("RABBITMQ_PASSWORD"))
    params = pika.ConnectionParameters("rabbitmq", 5672, "/", credentials, heartbeat=30)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue="document_jobs", durable=True)
    channel.basic_publish(
        exchange="",
        routing_key="document_jobs",
        body=document_id.encode(),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/books", status_code=201)
async def create_book(payload: BookIn, request: Request, user: dict = Depends(require_role("admin"))):
    with conn() as db:
        row = db.execute(
            """
            INSERT INTO books(owner_id, title, author, isbn, total_copies, available_copies, category)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (user["sub"], payload.title, payload.author, payload.isbn, payload.total_copies, payload.total_copies, payload.category),
        ).fetchone()
        db.commit()
    await audit(user["sub"], "book_create", client_ip(request), "success", {"book_id": str(row["id"])})
    return row


@app.get("/books")
def list_books(search: str = "", user: dict = Depends(current_user)):
    with conn() as db:
        if "admin" in user.get("roles", []):
            if search:
                return db.execute(
                    "SELECT * FROM books WHERE title ILIKE %s OR author ILIKE %s ORDER BY created_at DESC",
                    (f"%{search}%", f"%{search}%"),
                ).fetchall()
            return db.execute("SELECT * FROM books ORDER BY created_at DESC").fetchall()
        # Regular users can see all books for borrowing
        if search:
            return db.execute(
                "SELECT * FROM books WHERE title ILIKE %s OR author ILIKE %s ORDER BY created_at DESC",
                (f"%{search}%", f"%{search}%"),
            ).fetchall()
        return db.execute("SELECT * FROM books ORDER BY created_at DESC").fetchall()


@app.patch("/books/{book_id}")
async def update_book(book_id: str, payload: BookUpdate, request: Request, user: dict = Depends(require_role("admin"))):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No changes supplied")
    with conn() as db:
        current = db.execute("SELECT * FROM books WHERE id = %s FOR UPDATE", (book_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Book not found")
        total_copies = changes.get("total_copies")
        if total_copies is not None:
            borrowed = current["total_copies"] - current["available_copies"]
            if total_copies < borrowed:
                raise HTTPException(status_code=409, detail="Total copies cannot be lower than borrowed copies")
            changes["available_copies"] = total_copies - borrowed
        assignments = ", ".join(f"{field} = %s" for field in changes)
        row = db.execute(
            f"UPDATE books SET {assignments} WHERE id = %s RETURNING *",
            (*changes.values(), book_id),
        ).fetchone()
        db.commit()
    await audit(user["sub"], "book_update", client_ip(request), "success", {"book_id": book_id})
    return row


@app.delete("/books/{book_id}", status_code=204)
async def delete_book(book_id: str, request: Request, user: dict = Depends(require_role("admin"))):
    with conn() as db:
        book = db.execute("SELECT id FROM books WHERE id = %s", (book_id,)).fetchone()
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        active = db.execute(
            "SELECT 1 FROM loans WHERE book_id = %s AND status = 'active' LIMIT 1",
            (book_id,),
        ).fetchone()
        if active:
            raise HTTPException(status_code=409, detail="Cannot delete a book with active loans")
        db.execute("DELETE FROM reservations WHERE book_id = %s", (book_id,))
        db.execute("DELETE FROM loans WHERE book_id = %s", (book_id,))
        row = db.execute("DELETE FROM books WHERE id = %s RETURNING id", (book_id,)).fetchone()
        db.commit()
    await audit(user["sub"], "book_delete", client_ip(request), "success", {"book_id": book_id})
    return Response(status_code=204)


@app.post("/books/{book_id}/cover", status_code=201)
async def upload_cover(book_id: str, request: Request, file: UploadFile = File(...), user: dict = Depends(require_role("admin"))):
    original = file.filename or "cover"
    _, ext = os.path.splitext(original)
    ext = ext.lower()
    if ext not in ALLOWED_COVER_EXTENSIONS or file.content_type not in ALLOWED_COVER_MIME_TYPES:
        await audit(user["sub"], "cover_upload", client_ip(request), "failed", {"book_id": book_id})
        raise HTTPException(status_code=400, detail="Cover image type is not allowed")
    content = await file.read(MAX_COVER_BYTES + 1)
    if len(content) > MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="Cover image is too large")
    stored_name = safe_name(original)
    os.makedirs(os.getenv("UPLOAD_DIR", "/data/uploads"), exist_ok=True)
    encrypted = fernet().encrypt(content)
    with open(os.path.join(os.getenv("UPLOAD_DIR", "/data/uploads"), stored_name), "wb") as stored:
        stored.write(encrypted)
    with conn() as db:
        row = db.execute(
            """
            UPDATE books
            SET cover_image_name = %s, cover_mime_type = %s
            WHERE id = %s
            RETURNING id, cover_image_name, cover_mime_type
            """,
            (stored_name, file.content_type, book_id),
        ).fetchone()
        db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    await audit(user["sub"], "cover_upload", client_ip(request), "success", {"book_id": book_id})
    return row


@app.post("/documents/upload", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)):
    original = file.filename or "upload"
    if not allowed_document_extension(original):
        await audit(user["sub"], "file_upload", client_ip(request), "failed", {"reason": "extension"})
        raise HTTPException(status_code=400, detail="File type is not allowed")

    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File is too large")
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="MIME type is not allowed")

    digest = hashlib.sha256(content).hexdigest()
    stored_name = safe_name(original)
    os.makedirs(os.getenv("UPLOAD_DIR", "/data/uploads"), exist_ok=True)
    encrypted = fernet().encrypt(content)
    with open(os.path.join(os.getenv("UPLOAD_DIR", "/data/uploads"), stored_name), "wb") as stored:
        stored.write(encrypted)

    with conn() as db:
        row = db.execute(
            """
            INSERT INTO documents(owner_id, original_name, stored_name, mime_type, size_bytes, sha256_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (user["sub"], original, stored_name, file.content_type, len(content), digest),
        ).fetchone()
        db.commit()
    publish_document_job(str(row["id"]))
    await audit(user["sub"], "file_upload", client_ip(request), "success", {"document_id": str(row["id"])})
    return {
        "id": row["id"],
        "name": row["original_name"],
        "status": row["processing_status"],
        "sha256": row["sha256_hash"],
    }


@app.get("/documents")
def list_documents(user: dict = Depends(current_user)):
    with conn() as db:
        if "admin" in user.get("roles", []):
            return db.execute(
                """
                SELECT d.id, d.original_name, d.mime_type, d.size_bytes, d.processing_status, d.sha256_hash,
                       d.created_at, u.email AS owner_email
                FROM documents d
                JOIN users u ON u.id = d.owner_id
                ORDER BY d.created_at DESC
                LIMIT 100
                """
            ).fetchall()
        return db.execute(
            """
            SELECT id, original_name, mime_type, size_bytes, processing_status, sha256_hash, created_at
            FROM documents
            WHERE owner_id = %s
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (user["sub"],),
        ).fetchall()


@app.get("/documents/{document_id}/download")
async def download_document(document_id: str, request: Request, user: dict = Depends(current_user)):
    with conn() as db:
        row = db.execute("SELECT * FROM documents WHERE id = %s", (document_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    if row["owner_id"] != uuid.UUID(user["sub"]) and "admin" not in user.get("roles", []):
        await audit(user["sub"], "file_download", client_ip(request), "denied", {"document_id": document_id})
        raise HTTPException(status_code=403, detail="Forbidden")
    path = os.path.join(os.getenv("UPLOAD_DIR", "/data/uploads"), row["stored_name"])
    with open(path, "rb") as stored:
        content = fernet().decrypt(stored.read())
    await audit(user["sub"], "file_download", client_ip(request), "success", {"document_id": document_id})
    return Response(content, media_type=row["mime_type"], headers={"Content-Disposition": f"attachment; filename={row['original_name']}"})


@app.get("/documents/{document_id}/verify")
def verify_document(document_id: str, user: dict = Depends(current_user)):
    with conn() as db:
        row = db.execute("SELECT * FROM documents WHERE id = %s", (document_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    if row["owner_id"] != uuid.UUID(user["sub"]) and "admin" not in user.get("roles", []):
        raise HTTPException(status_code=403, detail="Forbidden")
    path = os.path.join(os.getenv("UPLOAD_DIR", "/data/uploads"), row["stored_name"])
    with open(path, "rb") as stored:
        try:
            content = fernet().decrypt(stored.read())
        except InvalidToken:
            return {"valid": False}
    return {"valid": hashlib.sha256(content).hexdigest() == row["sha256_hash"]}


@app.post("/internal/documents/{document_id}/processed", dependencies=[Depends(require_internal_token)])
def mark_processed(document_id: str):
    with conn() as db:
        row = db.execute(
            "UPDATE documents SET processing_status = 'processed' WHERE id = %s RETURNING id",
            (document_id,),
        ).fetchone()
        db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "processed"}


@app.get("/admin/dashboard", response_class=HTMLResponse)
def dashboard(_: dict = Depends(require_role("admin"))):
    return """
    <!doctype html><html><head><title>Secure Library Dashboard</title>
    <style>body{font-family:Arial;margin:32px;background:#f7f7fb;color:#111}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{background:white;border:1px solid #ddd;border-radius:8px;padding:16px}code{display:block;white-space:pre-wrap}</style></head>
    <body><h1>Secure Library Dashboard</h1><div id="grid" class="grid"></div>
    <script>
    const token = prompt("Admin JWT");
    fetch("/audit/admin/metrics",{headers:{Authorization:"Bearer "+token}})
      .then(r=>r.json()).then(data=>{
        grid.innerHTML = Object.entries(data).map(([k,v])=>`<div class="card"><b>${k}</b><h2>${v}</h2></div>`).join("");
      });
    </script></body></html>
    """
