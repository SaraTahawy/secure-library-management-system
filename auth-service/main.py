import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from common.audit import audit
from common.config import env
from common.db import conn
from common.errors import register_error_handlers
from common.security import client_ip, create_token, current_user, hash_password, require_role, verify_password

app = FastAPI(title="Auth Service")
register_error_handlers(app)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
ADMIN_PASSWORD = env("ADMIN_PASSWORD")
ADMIN_FULL_NAME = os.getenv("ADMIN_FULL_NAME", "System Admin")
verification_codes: dict[str, dict] = {}


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    role: str | None = Field(default=None, pattern="^(admin|user)$")


class VerifyEmailIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class ChangePasswordIn(BaseModel):
    new_password: str = Field(min_length=10, max_length=128)


@app.on_event("startup")
def ensure_single_admin() -> None:
    with conn() as db:
        admin_role = db.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()
        user_role = db.execute("SELECT id FROM roles WHERE name = 'user'").fetchone()
        admin = db.execute("SELECT * FROM users WHERE lower(email) = %s", (ADMIN_EMAIL,)).fetchone()
        if admin:
            admin = db.execute(
                """
                UPDATE users
                SET password_hash = %s, full_name = %s, is_active = true
                WHERE id = %s
                RETURNING *
                """,
                (hash_password(ADMIN_PASSWORD), ADMIN_FULL_NAME, admin["id"]),
            ).fetchone()
        else:
            admin = db.execute(
                """
                INSERT INTO users(email, password_hash, full_name)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (ADMIN_EMAIL, hash_password(ADMIN_PASSWORD), ADMIN_FULL_NAME),
            ).fetchone()

        db.execute("DELETE FROM user_roles WHERE role_id = %s", (admin_role["id"],))
        db.execute(
            """
            INSERT INTO user_roles(user_id, role_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (admin["id"], admin_role["id"]),
        )
        db.execute(
            """
            INSERT INTO user_roles(user_id, role_id)
            SELECT id, %s FROM users
            WHERE id <> %s
            ON CONFLICT DO NOTHING
            """,
            (user_role["id"], admin["id"]),
        )
        db.commit()


def user_roles(connection, user_id: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT r.name
        FROM roles r
        JOIN user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = %s
        """,
        (user_id,),
    ).fetchall()
    return [row["name"] for row in rows]


def issue_for_user(connection, user: dict) -> dict:
    roles = user_roles(connection, user["id"])
    return {
        "access_token": create_token(str(user["id"]), user["email"], roles),
        "token_type": "bearer",
        "expires_in_seconds": 3600,
        "roles": roles,
    }


def oauth_redirect(provider: str, authorize_url: str, params: dict) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    query = urlencode({**params, "state": state})
    response = RedirectResponse(f"{authorize_url}?{query}")
    response.set_cookie(
        f"{provider}_oauth_state",
        state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
    )
    return response


def verify_oauth_state(provider: str, expected: str | None, actual: str | None) -> None:
    if not expected or not actual or not secrets.compare_digest(expected, actual):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")


def oauth_user(connection, provider: str, subject: str, email: str, full_name: str) -> dict:
    user = connection.execute(
        """
        SELECT * FROM users
        WHERE (oauth_provider = %s AND oauth_subject = %s) OR email = %s
        """,
        (provider, subject, email),
    ).fetchone()
    if user:
        if not user["oauth_provider"]:
            user = connection.execute(
                """
                UPDATE users
                SET oauth_provider = %s, oauth_subject = %s
                WHERE id = %s
                RETURNING *
                """,
                (provider, subject, user["id"]),
            ).fetchone()
            connection.commit()
        return user

    user = connection.execute(
        """
        INSERT INTO users(email, full_name, oauth_provider, oauth_subject)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (email, full_name, provider, subject),
    ).fetchone()
    role = connection.execute("SELECT id FROM roles WHERE name = 'user'").fetchone()
    connection.execute("INSERT INTO user_roles(user_id, role_id) VALUES (%s, %s)", (user["id"], role["id"]))
    connection.commit()
    return user


def oauth_token_response(token: str, provider: str) -> HTMLResponse:
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
          <head><meta charset="utf-8"><title>OAuth Login</title></head>
          <body>
            <script>
              sessionStorage.setItem("secureLibraryToken", {token!r});
              localStorage.removeItem("secureLibraryToken");
              window.location.replace("/");
            </script>
          </body>
        </html>
        """,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/register", status_code=201)
async def register(payload: RegisterIn, request: Request):
    if payload.email.lower() == ADMIN_EMAIL:
        raise HTTPException(status_code=409, detail="Admin account already exists")
    with conn() as db:
        exists = db.execute("SELECT id FROM users WHERE email = %s", (payload.email,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Email already registered")
        user = db.execute(
            """
            INSERT INTO users(email, password_hash, full_name)
            VALUES (%s, %s, %s)
            RETURNING id, email, full_name
            """,
            (payload.email, hash_password(payload.password), payload.full_name),
        ).fetchone()
        role = db.execute("SELECT id FROM roles WHERE name = 'user'").fetchone()
        db.execute("INSERT INTO user_roles(user_id, role_id) VALUES (%s, %s)", (user["id"], role["id"]))
        db.commit()
    await audit(str(user["id"]), "register", client_ip(request), "success", {"email": user["email"]})
    return {"id": user["id"], "email": user["email"], "full_name": user["full_name"]}


@app.post("/login")
async def login(payload: LoginIn, request: Request):
    with conn() as db:
        user = db.execute("SELECT * FROM users WHERE email = %s", (payload.email,)).fetchone()
        if not user or not verify_password(payload.password, user["password_hash"]):
            await audit(None, "login", client_ip(request), "failed", {"email": payload.email})
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if user["is_active"] is False:
            await audit(str(user["id"]), "login", client_ip(request), "denied", {"reason": "suspended"})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is suspended")
        token = issue_for_user(db, user)
    await audit(str(user["id"]), "login", client_ip(request), "success", {})
    return token


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@app.post("/logout")
async def logout(request: Request, user: dict = Depends(current_user)):
    await audit(user["sub"], "logout", client_ip(request), "success", {})
    return {"status": "logged_out"}


@app.post("/profile/send-verification")
async def send_profile_verification(request: Request, user: dict = Depends(current_user)):
    code = f"{secrets.randbelow(1_000_000):06d}"
    verification_codes[user["sub"]] = {
        "code": code,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "verified": False,
    }
    await audit(user["sub"], "email_verification_requested", client_ip(request), "success", {})
    return {
        "status": "sent",
        "email": user["email"],
        "expires_in_seconds": 600,
        "demo_code": code,
    }


@app.post("/profile/verify-email")
async def verify_profile_email(payload: VerifyEmailIn, request: Request, user: dict = Depends(current_user)):
    record = verification_codes.get(user["sub"])
    if not record or record["expires_at"] < datetime.now(timezone.utc):
        await audit(user["sub"], "email_verification", client_ip(request), "failed", {"reason": "expired"})
        raise HTTPException(status_code=400, detail="Verification code expired")
    if not secrets.compare_digest(record["code"], payload.code):
        await audit(user["sub"], "email_verification", client_ip(request), "failed", {"reason": "invalid_code"})
        raise HTTPException(status_code=400, detail="Invalid verification code")
    record["verified"] = True
    await audit(user["sub"], "email_verification", client_ip(request), "success", {})
    return {"status": "verified"}


@app.post("/profile/change-password")
async def change_profile_password(payload: ChangePasswordIn, request: Request, user: dict = Depends(current_user)):
    with conn() as db:
        db.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(payload.new_password), user["sub"]))
        db.commit()
    verification_codes.pop(user["sub"], None)
    await audit(user["sub"], "password_change", client_ip(request), "success", {})
    return {"status": "password_changed"}


@app.get("/admin/users")
def admin_users(_: dict = Depends(require_role("admin"))):
    with conn() as db:
        return db.execute(
            """
            SELECT u.id, u.email, u.full_name, u.oauth_provider, u.is_active, u.created_at,
                   COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
        ).fetchall()


@app.patch("/admin/users/{user_id}")
async def admin_update_user(user_id: str, payload: AdminUserUpdate, request: Request, admin: dict = Depends(require_role("admin"))):
    with conn() as db:
        target = db.execute("SELECT id, email FROM users WHERE id = %s", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if payload.is_active is not None:
            if payload.is_active is False:
                admin_role = db.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()
                target_is_admin = db.execute(
                    "SELECT 1 FROM user_roles WHERE user_id = %s AND role_id = %s",
                    (user_id, admin_role["id"]),
                ).fetchone()
                if target_is_admin:
                    active_admin_count = db.execute(
                        """
                        SELECT count(*) AS c
                        FROM users u
                        JOIN user_roles ur ON ur.user_id = u.id
                        WHERE ur.role_id = %s AND u.is_active = true
                        """,
                        (admin_role["id"],),
                    ).fetchone()["c"]
                    if active_admin_count <= 1:
                        raise HTTPException(status_code=409, detail="Cannot suspend the last active admin")
            db.execute("UPDATE users SET is_active = %s WHERE id = %s", (payload.is_active, user_id))
        if payload.role is not None:
            role = db.execute("SELECT id FROM roles WHERE name = %s", (payload.role,)).fetchone()
            if not role:
                raise HTTPException(status_code=400, detail="Invalid role")
            admin_role = db.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()
            admin_count = db.execute(
                "SELECT count(*) AS c FROM user_roles WHERE role_id = %s",
                (admin_role["id"],),
            ).fetchone()["c"]
            target_is_admin = db.execute(
                "SELECT 1 FROM user_roles WHERE user_id = %s AND role_id = %s",
                (user_id, admin_role["id"]),
            ).fetchone()
            if payload.role != "admin" and target_is_admin and admin_count <= 1:
                raise HTTPException(status_code=409, detail="Cannot remove the last admin")
            db.execute("DELETE FROM user_roles WHERE user_id = %s", (user_id,))
            db.execute("INSERT INTO user_roles(user_id, role_id) VALUES (%s, %s)", (user_id, role["id"]))
        row = db.execute(
            """
            SELECT u.id, u.email, u.full_name, u.oauth_provider, u.is_active, u.created_at,
                   COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE u.id = %s
            GROUP BY u.id
            """,
            (user_id,),
        ).fetchone()
        db.commit()
    await audit(admin["sub"], "admin_user_update", client_ip(request), "success", {"user_id": user_id, "changes": payload.model_dump(exclude_none=True)})
    return row


@app.delete("/admin/users/{user_id}", status_code=204)
async def admin_delete_user(user_id: str, request: Request, admin: dict = Depends(require_role("admin"))):
    if user_id == admin["sub"]:
        raise HTTPException(status_code=409, detail="Cannot delete your own account")

    with conn() as db:
        target = db.execute("SELECT id, email FROM users WHERE id = %s", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        admin_role = db.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()
        target_is_admin = db.execute(
            "SELECT 1 FROM user_roles WHERE user_id = %s AND role_id = %s",
            (user_id, admin_role["id"]),
        ).fetchone()
        if target_is_admin:
            admin_count = db.execute(
                "SELECT count(*) AS c FROM user_roles WHERE role_id = %s",
                (admin_role["id"],),
            ).fetchone()["c"]
            if admin_count <= 1:
                raise HTTPException(status_code=409, detail="Cannot delete the last admin")

        owned_books = db.execute("SELECT count(*) AS c FROM books WHERE owner_id = %s", (user_id,)).fetchone()["c"]
        if owned_books:
            raise HTTPException(status_code=409, detail="Cannot delete a user who owns books")

        active_loans = db.execute(
            "SELECT count(*) AS c FROM loans WHERE user_id = %s AND status = 'active'",
            (user_id,),
        ).fetchone()["c"]
        if active_loans:
            raise HTTPException(status_code=409, detail="Cannot delete a user with active loans")

        db.execute("DELETE FROM reservations WHERE user_id = %s", (user_id,))
        db.execute("DELETE FROM loans WHERE user_id = %s", (user_id,))
        db.execute("DELETE FROM documents WHERE owner_id = %s", (user_id,))
        db.execute("DELETE FROM user_roles WHERE user_id = %s", (user_id,))
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))
        db.commit()

    await audit(admin["sub"], "admin_user_delete", client_ip(request), "success", {"user_id": user_id, "email": target["email"]})


@app.get("/oauth/google/login")
def google_login():
    if not os.getenv("GOOGLE_CLIENT_ID"):
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")
    return oauth_redirect(
        "google",
        "https://accounts.google.com/o/oauth2/v2/auth",
        {
            "client_id": env("GOOGLE_CLIENT_ID"),
            "redirect_uri": f"{env('OAUTH_REDIRECT_BASE')}/auth/oauth/google/callback",
            "response_type": "code",
            "scope": "openid email profile",
            "prompt": "select_account",
        },
    )


@app.get("/oauth/google/callback")
async def google_callback(
    code: str,
    state: str,
    request: Request,
    google_oauth_state: str | None = Cookie(default=None),
):
    if not os.getenv("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")
    verify_oauth_state("google", google_oauth_state, state)
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": env("GOOGLE_CLIENT_ID"),
                "client_secret": env("GOOGLE_CLIENT_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{env('OAUTH_REDIRECT_BASE')}/auth/oauth/google/callback",
            },
        )
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="OAuth login failed")
        profile = (
            await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        ).json()
    email = profile.get("email")
    subject = profile.get("sub")
    if not email or not subject:
        raise HTTPException(status_code=400, detail="Google account email is required")
    with conn() as db:
        user = oauth_user(db, "google", subject, email, profile.get("name") or email)
        token = issue_for_user(db, user)
    await audit(str(user["id"]), "oauth_login", client_ip(request), "success", {"provider": "google"})
    response = oauth_token_response(token["access_token"], "google")
    response.delete_cookie("google_oauth_state")
    return response
