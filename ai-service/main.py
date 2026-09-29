import hashlib
import math
import os
import asyncio
import uuid
import logging

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from common.audit import audit
from common.db import conn
from common.errors import register_error_handlers
from common.security import client_ip, current_user, require_role

app = FastAPI(title="AI Service")
register_error_handlers(app)
logger = logging.getLogger("ai-service")

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
COLLECTION = os.getenv("QDRANT_COLLECTION", "library_knowledge")
VECTOR_SIZE = 64


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=2, max_length=4000)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=6)


def normalized(text: str) -> str:
    return text.strip().lower()


def has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def embed(text: str) -> list[float]:
    vector = [0.0] * VECTOR_SIZE
    words = [word.strip(".,;:!?()[]{}\"'").lower() for word in text.split()]
    for word in filter(None, words):
        digest = hashlib.sha256(word.encode()).digest()
        index = int.from_bytes(digest[:2], "big") % VECTOR_SIZE
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


async def ensure_collection() -> None:
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(f"{QDRANT_URL}/collections/{COLLECTION}")
        if response.status_code == 200:
            return
        create = await client.put(
            f"{QDRANT_URL}/collections/{COLLECTION}",
            json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}},
        )
        create.raise_for_status()


@app.on_event("startup")
async def startup() -> None:
    for attempt in range(12):
        try:
            await ensure_collection()
            return
        except Exception:
            if attempt == 11:
                raise
            await asyncio.sleep(2)


@app.get("/health")
async def health():
    try:
        await ensure_collection()
        return {"status": "ok", "vector_db": "qdrant"}
    except Exception as exc:
        logger.exception("vector database health check failed")
        raise HTTPException(status_code=503, detail="Vector database unavailable") from exc


@app.post("/rag/index", status_code=201)
async def index_knowledge(payload: KnowledgeIn, request: Request, user: dict = Depends(require_role("admin"))):
    await ensure_collection()
    point_id = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.put(
            f"{QDRANT_URL}/collections/{COLLECTION}/points",
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": embed(f"{payload.title} {payload.content}"),
                        "payload": {
                            "title": payload.title,
                            "content": payload.content,
                            "indexed_by": user["email"],
                        },
                    }
                ]
            },
        )
        response.raise_for_status()
    await audit(user["sub"], "ai_knowledge_index", client_ip(request), "success", {"point_id": point_id})
    return {"id": point_id, "status": "indexed"}


@app.post("/rag/search")
async def search_knowledge(payload: SearchIn, request: Request, user: dict = Depends(current_user)):
    await ensure_collection()
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
            json={"vector": embed(payload.query), "limit": payload.limit, "with_payload": True},
        )
        response.raise_for_status()
    results = response.json().get("result", [])
    await audit(user["sub"], "ai_search", client_ip(request), "success", {"query": payload.query})
    return [
        {
            "id": item["id"],
            "score": item["score"],
            "title": item.get("payload", {}).get("title", "Untitled"),
            "content": item.get("payload", {}).get("content", ""),
        }
        for item in results
    ]


@app.post("/chat")
async def chat(payload: ChatIn, request: Request, user: dict = Depends(current_user)):
    question = normalized(payload.question)
    if has_any(question, ["approval", "approve", "approved", "admin approve", "موافق", "وافق"]):
        return {
            "answer": "Borrow approval flow: the user sends a borrow request, the request appears for the admin, and only after the admin approves it does the system create an active loan and reduce the available copy count. If the admin rejects it, no loan is created.",
            "sources": [{"title": "Loan approval workflow", "score": 1, "content": "Request -> admin decision -> loan"}],
        }

    if has_any(question, ["reject", "rejected", "رفض"]):
        return {
            "answer": "If a borrow request is rejected, it stays marked as rejected and no active loan is created. The user can still see their existing active loans, but that rejected request should not become a borrowed book.",
            "sources": [{"title": "Loan rejection workflow", "score": 1, "content": "Rejected request -> no loan"}],
        }

    if has_any(question, ["borrowed", "my borrowed", "my loans", "my books", "loans", "status", "استعاراتي", "كتبى", "كتبي"]):
        with conn() as db:
            loans = db.execute(
                """
                SELECT b.title, b.author, l.due_date, l.status
                FROM loans l
                JOIN books b ON b.id = l.book_id
                WHERE l.user_id = %s
                ORDER BY l.created_at DESC
                LIMIT 10
                """,
                (user["sub"],),
            ).fetchall()
            requests = db.execute(
                """
                SELECT b.title, r.status, r.created_at
                FROM reservations r
                JOIN books b ON b.id = r.book_id
                WHERE r.user_id = %s AND r.status = 'pending'
                ORDER BY r.created_at DESC
                LIMIT 10
                """,
                (user["sub"],),
            ).fetchall()
        if not loans and not requests:
            answer = "You do not have active borrowed books or pending borrow requests right now."
        else:
            loan_lines = [f"- {loan['title']} by {loan['author']} is {loan['status']} until {loan['due_date']}." for loan in loans]
            request_lines = [f"- {request['title']} is still waiting for admin approval." for request in requests]
            answer = "Here is your borrowing status:\n" + "\n".join(loan_lines + request_lines)
        await audit(user["sub"], "ai_chat_borrowing_status", client_ip(request), "success", {})
        return {"answer": answer, "sources": [{"title": "Loan Service", "score": 1, "content": "Live borrowing data"}]}

    if has_any(question, ["request", "pending", "طلب"]):
        return {
            "answer": "Borrowing starts as a pending request. Pending means the admin has not decided yet. Approved creates a loan; rejected closes the request without creating a loan.",
            "sources": [{"title": "Loan approval workflow", "score": 1, "content": "Request -> admin decision -> loan"}],
        }

    if has_any(question, ["security", "jwt", "hash", "rate", "https", "امن", "حما"]):
        return {
            "answer": "Security controls include HTTPS at Nginx, rate limiting, JWT authentication, bcrypt password hashing, RBAC roles, internal service tokens, audit logs, least-privilege database users, file encryption, and RabbitMQ credentials.",
            "sources": [{"title": "Security controls", "score": 1, "content": "Implemented project controls"}],
        }

    if has_any(question, ["architecture", "services", "distributed", "معمار", "خدمات"]):
        return {
            "answer": "The system is distributed: Nginx API Gateway, Auth Service, Library Service, Loan Service, Notification Service, Audit Service, Worker Service, PostgreSQL, RabbitMQ, AI Service, and Qdrant vector database.",
            "sources": [{"title": "Architecture", "score": 1, "content": "Distributed services"}],
        }

    sources = await search_knowledge(SearchIn(query=payload.question, limit=payload.limit), request, user)
    if not sources:
        answer = "I do not have a specific match for that yet. Try asking about borrowed books, approvals, security controls, or architecture."
    else:
        best = sources[0]
        answer = f"Based on the indexed library knowledge, the closest match is '{best['title']}': {best['content'][:500]}"
    return {"answer": answer, "sources": sources}
