"""Local Alfred authentication using opaque, revocable server-side sessions."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import get_settings
from .database import get_db
from .models import AuthSession, User

SESSION_COOKIE = "alfred_session"
CSRF_HEADER = "X-CSRF-Token"
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    """SQLite test rows may be naive; PostgreSQL rows are timezone-aware."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def create_session(db: Session, user: User) -> tuple[str, str, AuthSession]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=get_settings().session_ttl_hours)
    session = AuthSession(
        id=secrets.token_urlsafe(24), user_id=user.id, token_hash=_hash(token),
        csrf_token_hash=_hash(csrf_token), expires_at=expiry, last_seen_at=now,
    )
    db.add(session)
    return token, csrf_token, session


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required.")
    now = datetime.now(timezone.utc)
    session = db.scalar(
        select(AuthSession).options(joinedload(AuthSession.user)).where(AuthSession.token_hash == _hash(token))
    )
    if not session or session.revoked_at or _utc(session.expires_at) <= now or not session.user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required.")
    session.last_seen_at = now
    db.commit()
    return session


def current_user(session: AuthSession = Depends(current_session)) -> User:
    return session.user


def require_csrf(request: Request, session: AuthSession = Depends(current_session)) -> AuthSession:
    submitted = request.headers.get(CSRF_HEADER)
    if not submitted or not secrets.compare_digest(_hash(submitted), session.csrf_token_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A valid CSRF token is required.")
    return session


def require_roles(*roles: str):
    """Dependency factory for administrator-only routes."""
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your Alfred role cannot do that.")
        return user
    return dependency
