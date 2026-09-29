import uuid
from datetime import date, timedelta

import pika
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from common.audit import audit
from common.config import env
from common.db import conn
from common.errors import register_error_handlers
from common.security import client_ip, current_user, require_role

app = FastAPI(title="Loan Service")
register_error_handlers(app)


class LoanIn(BaseModel):
    book_id: str
    due_date: date

    @field_validator("due_date")
    @classmethod
    def due_date_future(cls, value: date) -> date:
        if value <= date.today():
            raise ValueError("due_date must be in the future")
        return value


class ReservationIn(BaseModel):
    book_id: str
    due_date: date | None = None

    @field_validator("due_date")
    @classmethod
    def due_date_future(cls, value: date | None) -> date | None:
        if value is not None and value <= date.today():
            raise ValueError("due_date must be in the future")
        return value


class DecisionIn(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    due_date: date | None = None

    @field_validator("due_date")
    @classmethod
    def due_date_future(cls, value: date | None) -> date | None:
        if value is not None and value <= date.today():
            raise ValueError("due_date must be in the future")
        return value


def publish_notification_job(user_id: str, message: str) -> None:
    credentials = pika.PlainCredentials(env("RABBITMQ_USER"), env("RABBITMQ_PASSWORD"))
    params = pika.ConnectionParameters("rabbitmq", 5672, "/", credentials, heartbeat=30)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue="notification_jobs", durable=True)
    channel.basic_publish(
        exchange="",
        routing_key="notification_jobs",
        body=f"{user_id}|{message}".encode(),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()


def close_active_loan(db, loan_id: str) -> dict:
    loan = db.execute(
        """
        SELECT l.*, b.title
        FROM loans l
        JOIN books b ON b.id = l.book_id
        WHERE l.id = %s
        FOR UPDATE
        """,
        (loan_id,),
    ).fetchone()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan["status"] != "active":
        raise HTTPException(status_code=409, detail="Loan is already closed")
    row = db.execute(
        """
        UPDATE loans
        SET status = 'returned'
        WHERE id = %s
        RETURNING *
        """,
        (loan_id,),
    ).fetchone()
    db.execute(
        "UPDATE books SET available_copies = available_copies + 1 WHERE id = %s",
        (loan["book_id"],),
    )
    return {**row, "title": loan["title"]}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/loans", status_code=201)
async def create_loan(payload: LoanIn, request: Request, user: dict = Depends(current_user)):
    request_payload = ReservationIn(book_id=payload.book_id, due_date=payload.due_date)
    return await create_reservation(request_payload, request, user)


@app.post("/reservations", status_code=201)
async def create_reservation(payload: ReservationIn, request: Request, user: dict = Depends(current_user)):
    with conn() as db:
        book = db.execute("SELECT * FROM books WHERE id = %s", (payload.book_id,)).fetchone()
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        duplicate = db.execute(
            """
            SELECT id FROM reservations
            WHERE user_id = %s AND book_id = %s AND status = 'pending'
            LIMIT 1
            """,
            (user["sub"], payload.book_id),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="You already have a pending request for this book")
        row = db.execute(
            """
            INSERT INTO reservations(user_id, book_id, requested_due_date)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (user["sub"], payload.book_id, payload.due_date),
        ).fetchone()
        db.commit()
    await audit(user["sub"], "borrow_request_create", client_ip(request), "success", {"reservation_id": str(row["id"])})
    return row


@app.get("/loans")
def my_loans(user: dict = Depends(current_user)):
    with conn() as db:
        return db.execute(
            """
            SELECT l.*, b.title, b.author
            FROM loans l JOIN books b ON b.id = l.book_id
            WHERE l.user_id = %s
            ORDER BY l.created_at DESC
            """,
            (user["sub"],),
        ).fetchall()


@app.get("/reservations")
def my_reservations(user: dict = Depends(current_user)):
    with conn() as db:
        return db.execute(
            """
            SELECT r.*, b.title
            FROM reservations r JOIN books b ON b.id = r.book_id
            WHERE r.user_id = %s
            ORDER BY
              CASE r.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              r.created_at DESC
            """,
            (user["sub"],),
        ).fetchall()


@app.post("/{loan_id}/return")
@app.post("/loans/{loan_id}/return")
async def return_my_loan(loan_id: str, request: Request, user: dict = Depends(current_user)):
    with conn() as db:
        owner = db.execute("SELECT user_id FROM loans WHERE id = %s", (loan_id,)).fetchone()
        if not owner:
            raise HTTPException(status_code=404, detail="Loan not found")
        if str(owner["user_id"]) != user["sub"]:
            await audit(user["sub"], "loan_return", client_ip(request), "denied", {"loan_id": loan_id})
            raise HTTPException(status_code=403, detail="Forbidden")
        row = close_active_loan(db, loan_id)
        db.commit()
    await audit(user["sub"], "loan_return", client_ip(request), "success", {"loan_id": loan_id, "book_id": str(row["book_id"])})
    return row


@app.get("/users/{user_id}/loans")
def user_loans(user_id: str, user: dict = Depends(current_user)):
    if user_id != user["sub"] and "admin" not in user.get("roles", []):
        raise HTTPException(status_code=403, detail="Forbidden")
    with conn() as db:
        return db.execute("SELECT * FROM loans WHERE user_id = %s ORDER BY created_at DESC", (user_id,)).fetchall()


@app.get("/admin/loans")
def admin_loans(_: dict = Depends(require_role("admin"))):
    with conn() as db:
        return db.execute(
            """
            SELECT l.*, u.email, u.full_name, b.title, b.author,
                   (l.due_date < CURRENT_DATE AND l.status = 'active') AS overdue
            FROM loans l
            JOIN users u ON u.id = l.user_id
            JOIN books b ON b.id = l.book_id
            ORDER BY l.created_at DESC
            LIMIT 200
            """
        ).fetchall()


@app.post("/admin/loans/{loan_id}/return")
async def admin_return_loan(loan_id: str, request: Request, admin: dict = Depends(require_role("admin"))):
    with conn() as db:
        row = close_active_loan(db, loan_id)
        db.commit()
    await audit(admin["sub"], "admin_loan_return", client_ip(request), "success", {"loan_id": loan_id, "book_id": str(row["book_id"]), "user_id": str(row["user_id"])})
    publish_notification_job(str(row["user_id"]), f"Your loan for '{row['title']}' was returned by an admin.")
    return row


@app.delete("/admin/loans/{loan_id}", status_code=204)
async def admin_delete_loan(loan_id: str, request: Request, admin: dict = Depends(require_role("admin"))):
    with conn() as db:
        loan = db.execute("SELECT * FROM loans WHERE id = %s FOR UPDATE", (loan_id,)).fetchone()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found")
        if loan["status"] == "active":
            db.execute("UPDATE books SET available_copies = available_copies + 1 WHERE id = %s", (loan["book_id"],))
        db.execute("DELETE FROM loans WHERE id = %s", (loan_id,))
        db.commit()
    await audit(admin["sub"], "admin_loan_delete", client_ip(request), "success", {"loan_id": loan_id, "book_id": str(loan["book_id"]), "user_id": str(loan["user_id"])})


@app.get("/admin/reservations")
def admin_reservations(_: dict = Depends(require_role("admin"))):
    with conn() as db:
        return db.execute(
            """
            SELECT r.*, u.email, u.full_name, b.title, b.author
            FROM reservations r
            JOIN users u ON u.id = r.user_id
            JOIN books b ON b.id = r.book_id
            ORDER BY r.created_at DESC
            LIMIT 200
            """
        ).fetchall()


@app.patch("/admin/reservations/{reservation_id}")
async def decide_reservation(reservation_id: str, payload: DecisionIn, request: Request, admin: dict = Depends(require_role("admin"))):
    with conn() as db:
        reservation = db.execute(
            """
            SELECT r.*, b.title
            FROM reservations r
            JOIN books b ON b.id = r.book_id
            WHERE r.id = %s
            FOR UPDATE
            """,
            (reservation_id,),
        ).fetchone()
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")
        if reservation["status"] != "pending":
            if reservation["status"] == payload.status:
                return {**reservation, "loan_id": None}
            raise HTTPException(status_code=409, detail="Request is already decided")

        loan_id = None
        if payload.status == "approved":
            book = db.execute("SELECT * FROM books WHERE id = %s FOR UPDATE", (reservation["book_id"],)).fetchone()
            if book["available_copies"] <= 0:
                raise HTTPException(status_code=409, detail="No copies available")
            due_date = payload.due_date or reservation.get("requested_due_date") or (date.today() + timedelta(days=7))
            loan = db.execute(
                """
                INSERT INTO loans(user_id, book_id, due_date)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (reservation["user_id"], reservation["book_id"], due_date),
            ).fetchone()
            loan_id = str(loan["id"])
            db.execute("UPDATE books SET available_copies = available_copies - 1 WHERE id = %s", (reservation["book_id"],))
            publish_notification_job(str(reservation["user_id"]), f"Borrow request approved for '{reservation['title']}' until {due_date.isoformat()}")
        else:
            publish_notification_job(str(reservation["user_id"]), f"Borrow request rejected for '{reservation['title']}'")

        row = db.execute(
            "UPDATE reservations SET status = %s WHERE id = %s RETURNING *",
            (payload.status, reservation_id),
        ).fetchone()
        db.commit()
    await audit(admin["sub"], "reservation_decision", client_ip(request), "success", {"reservation_id": reservation_id, "status": payload.status, "loan_id": loan_id})
    return {**row, "loan_id": loan_id}
