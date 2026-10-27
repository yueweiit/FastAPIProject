"""Authentication-only transactions; never retain their locks into FIFO work."""

import asyncio
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

from fastapi import Request, Response
from sqlalchemy import delete, update

from database import async_session
from models import AuthLoginTransaction, AuthSession, User
from services.eims_sso import AuthSettings, SsoError, TokenCipher, get_client, get_settings, is_loopback


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def credential_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cookie_secure(request: Request, settings: AuthSettings) -> bool:
    return (settings.public_base_url.startswith("https://") or request.url.scheme == "https"
            or not is_loopback(request.url.hostname))


def cookie_name(request: Request, settings: AuthSettings, *, flow=False) -> str:
    name = "fifo_sso_flow" if flow else "fifo_session"
    return "__Host-" + name if cookie_secure(request, settings) else name


def set_cookie(response: Response, request: Request, settings: AuthSettings, value: str, *, flow=False):
    response.set_cookie(cookie_name(request, settings, flow=flow), value,
                        max_age=settings.transaction_seconds if flow else settings.session_seconds,
                        path="/", secure=cookie_secure(request, settings), httponly=True, samesite="lax")


def clear_cookie(response: Response, request: Request, settings: AuthSettings, *, flow=False):
    response.delete_cookie(cookie_name(request, settings, flow=flow), path="/",
                           secure=cookie_secure(request, settings), httponly=True, samesite="lax")


def check_origin(request: Request, settings: AuthSettings) -> None:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin is None and referer is None:
        return  # Bearer clients have no browser origin; cookie writes also require CSRF.
    try:
        candidate = urlsplit(origin or referer or "")
        expected = urlsplit(settings.public_base_url or str(request.base_url))
    except ValueError as exc:
        raise SsoError("csrf_rejected", 403) from exc
    if (candidate.username or candidate.password or not candidate.hostname
            or candidate.netloc.lower() != expected.netloc.lower()
            or candidate.scheme not in ("http", "https")
            or (settings.public_base_url and candidate.scheme != expected.scheme)):
        raise SsoError("csrf_rejected", 403)


def check_csrf(request: Request, csrf_token: str, settings: AuthSettings) -> None:
    check_origin(request, settings)
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or not secrets.compare_digest(supplied.encode("utf-8"), csrf_token.encode("utf-8")):
        raise SsoError("csrf_rejected", 403)


@dataclass(frozen=True)
class SessionView:
    user_id: int
    source: str
    csrf_token: str = field(repr=False)


class SessionStore:
    def __init__(self, session_factory=async_session):
        self.session_factory = session_factory
        self._validation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def _validation_lock(self, key: str) -> asyncio.Lock:
        lock = self._validation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._validation_locks[key] = lock
        return lock

    async def create_transaction(self, flow: str, state: str, browser: str, payload: dict,
                                 settings: AuthSettings) -> None:
        now = utcnow()
        async with self.session_factory() as db:
            async with db.begin():
                db.add(AuthLoginTransaction(
                    state_hash=credential_hash(state), flow=flow, browser_hash=credential_hash(browser),
                    payload=TokenCipher(settings).encrypt(payload),
                    expires_at=now + timedelta(seconds=settings.transaction_seconds), created_at=now,
                ))

    async def consume_transaction(self, flow: str, state: str | None, browser: str | None,
                                  settings: AuthSettings) -> dict:
        if not state or not browser or len(state) > 128 or len(browser) > 128:
            raise SsoError("invalid_state")
        async with self.session_factory() as db:
            async with db.begin():
                row = await db.get(AuthLoginTransaction, credential_hash(state), with_for_update=True)
                if (row is None or row.flow != flow or row.consumed_at is not None
                        or row.expires_at <= utcnow()
                        or not secrets.compare_digest(row.browser_hash, credential_hash(browser))):
                    raise SsoError("invalid_state")
                payload = TokenCipher(settings).decrypt(row.payload)
                row.consumed_at = utcnow()
                row.payload = None
        return payload

    async def create_session(self, user_id: int, settings: AuthSettings, *, source="local",
                             tokens: dict | None = None, subject: str | None = None,
                             nonce: str | None = None,
                             expected_password_hash: str | None = None) -> tuple[str, SessionView]:
        cookie = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = utcnow()
        async with self.session_factory() as db:
            async with db.begin():
                # The same user -> sessions lock order is used by password/status changes.
                user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
                if user is None or not user.is_active:
                    raise SsoError("account_unavailable")
                if expected_password_hash is not None and not secrets.compare_digest(
                    user.password_hash, expected_password_hash
                ):
                    raise SsoError("credentials_changed")
                row = AuthSession(
                    session_hash=credential_hash(cookie), user_id=user_id, source=source, csrf_token=csrf,
                    expires_at=now + timedelta(seconds=settings.session_seconds), created_at=now,
                )
                if source == "eims":
                    settings.require_sso()
                    row.issuer, row.client_id, row.subject, row.oidc_nonce = settings.issuer, settings.client_id, subject, nonce
                    row.token_payload = TokenCipher(settings).encrypt(tokens or {})
                    row.token_expires_at = now + timedelta(seconds=(tokens or {})["expires_in"])
                    row.validated_at = now
                db.add(row)
        return cookie, SessionView(user_id, source, csrf)

    @staticmethod
    def _check_row(row: AuthSession | None, settings: AuthSettings) -> AuthSession:
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            raise SsoError("invalid_session")
        if row.source == "eims":
            if settings.mode == "local":
                raise SsoError("invalid_session")
            settings.require_sso()
            if row.issuer != settings.issuer or row.client_id != settings.client_id:
                raise SsoError("invalid_session")
        elif row.source != "local" or not settings.local_enabled:
            raise SsoError("invalid_session")
        return row

    @staticmethod
    def _view(row: AuthSession) -> SessionView:
        return SessionView(row.user_id, row.source, row.csrf_token)

    @staticmethod
    def _needs_validation(row: AuthSession, settings: AuthSettings, write: bool) -> bool:
        now = utcnow()
        return (row.token_expires_at is None or row.token_expires_at <= now + timedelta(seconds=5)
                or write or row.validated_at is None
                or row.validated_at + timedelta(seconds=settings.recheck_seconds) <= now)

    async def authenticate(self, cookie: str, request: Request, settings: AuthSettings) -> SessionView:
        if not cookie or len(cookie) > 128:
            raise SsoError("invalid_session")
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        key = credential_hash(cookie)
        # Serialize the first read too: it is the checkout that fails when a
        # burst from one browser has already filled the connection pool.
        async with self._validation_lock(key):
            return await self._authenticate_locked(key, request, settings, write)

    async def _authenticate_locked(self, key: str, request: Request, settings: AuthSettings,
                                   write: bool) -> SessionView:
        async with self.session_factory() as db:
            row = self._check_row(await db.get(AuthSession, key), settings)
            if write:
                check_csrf(request, row.csrf_token, settings)
            if row.source == "local" or not self._needs_validation(row, settings, write):
                return self._view(row)
        # No inventory/business session or lock is held by this transaction.
        return await self._validate_eims_locked(key, settings, write)

    @staticmethod
    async def _validate_pending_id_token(client, row: AuthSession, tokens: dict) -> None:
        if tokens.get("_id_token_pending"):
            claims = await client.validate_id_token(tokens["id_token"], row.oidc_nonce or "", require_nonce=False)
            if claims["sub"] != row.subject:
                raise SsoError("invalid_identity")
            tokens.pop("_id_token_pending")

    async def _validate_eims(self, key: str, settings: AuthSettings, write: bool) -> SessionView:
        # Wait for the same browser session before opening a DB session. InnoDB's row
        # lock remains the cross-process authority for token rotation.
        async with self._validation_lock(key):
            return await self._validate_eims_locked(key, settings, write)

    async def _validate_eims_locked(self, key: str, settings: AuthSettings, write: bool) -> SessionView:
        failure = None
        async with self.session_factory() as db:
            async with db.begin():
                row = self._check_row(await db.get(AuthSession, key, with_for_update=True), settings)
                if not self._needs_validation(row, settings, write):
                    return self._view(row)
                try:
                    client = get_client()
                    tokens = TokenCipher(settings).decrypt(row.token_payload)
                    await self._validate_pending_id_token(client, row, tokens)
                    if row.token_expires_at is None or row.token_expires_at <= utcnow() + timedelta(seconds=5):
                        old_refresh = tokens.get("refresh_token")
                        if not old_refresh:
                            raise SsoError("identity_rejected")
                        refreshed = await client.token({"grant_type": "refresh_token", "refresh_token": old_refresh})
                        new_refresh = refreshed.get("refresh_token")
                        if not new_refresh or secrets.compare_digest(old_refresh, new_refresh):
                            raise SsoError("invalid_token")
                        tokens.update(refreshed)
                        if refreshed.get("id_token"):
                            tokens["_id_token_pending"] = True
                        row.token_expires_at = utcnow() + timedelta(seconds=refreshed["expires_in"])
                        row.validated_at = None
                        # A remote rotation cannot be undone by a later JWKS/UserInfo outage.
                        row.token_payload = TokenCipher(settings).encrypt(tokens)
                        await self._validate_pending_id_token(client, row, tokens)
                    identity = await client.userinfo(tokens["access_token"], row.subject or "")
                    if identity["local_user_id"] != row.user_id:
                        raise SsoError("identity_rejected")
                    row.token_payload = TokenCipher(settings).encrypt(tokens)
                    row.validated_at = utcnow()
                except SsoError as error:
                    if error.status_code >= 500:
                        row.validated_at = None
                    else:
                        row.revoked_at = utcnow()
                        row.token_payload = None
                    failure = error
                view = self._view(row)
        if failure is not None:
            raise failure
        return view

    async def revoke(self, cookie: str | None, settings: AuthSettings, *, request: Request | None = None) -> dict | None:
        if not cookie or len(cookie) > 128:
            return None
        tokens = None
        async with self.session_factory() as db:
            async with db.begin():
                row = await db.get(AuthSession, credential_hash(cookie), with_for_update=True)
                if row is not None and row.revoked_at is None:
                    if request is not None:
                        check_csrf(request, row.csrf_token, settings)
                    if row.source == "eims" and settings.configured:
                        try:
                            tokens = TokenCipher(settings).decrypt(row.token_payload)
                        except SsoError:
                            pass
                    row.revoked_at = utcnow()
                    row.token_payload = None
        return tokens

    async def cleanup(self) -> None:
        now = utcnow()
        async with self.session_factory() as db:
            async with db.begin():
                await db.execute(delete(AuthLoginTransaction).where(AuthLoginTransaction.expires_at < now))
                await db.execute(delete(AuthSession).where(AuthSession.expires_at < now))


store = SessionStore()


async def revoke_user_sessions(db, user_id: int) -> None:
    await db.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                     .values(revoked_at=utcnow(), token_payload=None))
