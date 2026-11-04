"""Authentication: bcrypt password hashing + JWT bearer tokens.

A token carries the user id, the admin flag and a `ver` (token version). The
version is bumped on every password change and on "sign out everywhere", so a
stolen token dies with the next legitimate security action instead of living
out its full 72 hours.
"""
import re
import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import database as db
from .config import settings

_bearer = HTTPBearer(auto_error=False)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def make_token(user_id: str, is_admin: bool, token_version: int = 0) -> str:
    payload = {
        "sub": user_id,
        "adm": bool(is_admin),
        "ver": int(token_version),
        "exp": time.time() + settings.jwt_expire_hours * 3600,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def current_user(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if not cred:
        raise HTTPException(401, "Missing authorization token")
    try:
        payload = jwt.decode(cred.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(401, "User not found")
    # tokens minted before the version counter existed count as version 0
    if int(payload.get("ver") or 0) != int(user.get("token_version") or 0):
        raise HTTPException(401, "Session revoked — sign in again")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin only")
    return user
