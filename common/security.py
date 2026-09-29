from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from .audit import audit
from .config import env
from .db import conn

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    return bool(password_hash) and pwd_context.verify(password, password_hash)


def create_token(user_id: str, email: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=60)).timestamp()),
    }
    return jwt.encode(payload, env("JWT_SECRET"), algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            env("JWT_SECRET"),
            algorithms=["HS256"],
            options={"verify_iat": False},
            leeway=10,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    if credentials is None:
        await audit(None, "unauthorized_access", client_ip(request), "denied", {"reason": "missing_token", "path": request.url.path})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        user = decode_token(credentials.credentials)
    except HTTPException as exc:
        await audit(None, "unauthorized_access", client_ip(request), "denied", {"reason": exc.detail, "path": request.url.path})
        raise
    with conn() as db:
        row = db.execute("SELECT is_active FROM users WHERE id = %s", (user["sub"],)).fetchone()
    if not row:
        await audit(user.get("sub"), "unauthorized_access", client_ip(request), "denied", {"reason": "missing_user", "path": request.url.path})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
    if row["is_active"] is False:
        await audit(user["sub"], "unauthorized_access", client_ip(request), "denied", {"reason": "suspended", "path": request.url.path})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is suspended")
    return user


def require_role(role: str):
    async def dependency(request: Request, user: Annotated[dict, Depends(current_user)]) -> dict:
        if role not in user.get("roles", []):
            await audit(user["sub"], "unauthorized_access", client_ip(request), "denied", {"reason": "missing_role", "required_role": role, "path": request.url.path})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency


async def require_internal_token(request: Request, x_internal_token: Annotated[str | None, Header()] = None) -> None:
    if x_internal_token != env("INTERNAL_API_KEY"):
        await audit(None, "unauthorized_access", client_ip(request), "denied", {"reason": "invalid_internal_token", "path": request.url.path})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
