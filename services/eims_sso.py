"""EIMS protocol boundary. Credentials never leave the server-side client."""

import asyncio
import base64
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from jose import jwt
from jose.exceptions import JOSEError


class SsoError(Exception):
    def __init__(self, code: str, status_code: int = 401):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return bool(
            parsed.hostname and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and parsed.path in ("", "/")
            and (parsed.scheme == "https" or (parsed.scheme == "http" and is_loopback(parsed.hostname)))
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class AuthSettings:
    mode: str = "hybrid"
    public_base_url: str = ""
    issuer: str = "https://eims.yueweiportal.com"
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    encryption_key: str = field(default="", repr=False)
    session_seconds: int = 86400
    transaction_seconds: int = 300
    recheck_seconds: int = 60

    @property
    def local_enabled(self) -> bool:
        return self.mode in ("local", "hybrid")

    @property
    def configured(self) -> bool:
        if self.mode not in ("hybrid", "eims"):
            return False
        if not valid_origin(self.public_base_url) or not valid_origin(self.issuer):
            return False
        if self.issuer.startswith("http://") and not self.public_base_url.startswith("http://"):
            return False  # HTTP identity providers are limited to loopback development.
        if not self.client_id or not self.client_secret:
            return False
        try:
            Fernet(self.encryption_key.encode("ascii"))
        except (ValueError, UnicodeError):
            return False
        return True

    def require_sso(self) -> None:
        if not self.configured:
            raise SsoError("sso_unavailable", 503)

    @property
    def callback_uri(self) -> str:
        return self.public_base_url.rstrip("/") + "/auth/eims/callback"

    @property
    def logout_callback_uri(self) -> str:
        return self.public_base_url.rstrip("/") + "/auth/eims/logout/callback"


def get_settings() -> AuthSettings:
    return AuthSettings(
        mode=os.getenv("FIFO_AUTH_MODE", "hybrid").strip().lower(),
        public_base_url=os.getenv("FIFO_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        issuer=os.getenv("EIMS_ISSUER", "https://eims.yueweiportal.com").strip().rstrip("/"),
        client_id=os.getenv("EIMS_CLIENT_ID", "").strip(),
        client_secret=os.getenv("EIMS_CLIENT_SECRET", ""),
        encryption_key=os.getenv("SSO_TOKEN_ENCRYPTION_KEY", ""),
    )


class TokenCipher:
    def __init__(self, settings: AuthSettings):
        try:
            self.cipher = Fernet(settings.encryption_key.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise SsoError("sso_unavailable", 503) from exc

    def encrypt(self, value: dict) -> str:
        return self.cipher.encrypt(json.dumps(value).encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> dict:
        try:
            result = json.loads(self.cipher.decrypt((value or "").encode("ascii")))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise SsoError("invalid_session") from exc


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def app_user_id(value) -> int:
    if isinstance(value, bool):
        raise SsoError("account_unbound")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,9}", value):
        result = int(value)
    else:
        raise SsoError("account_unbound")
    if not 0 < result <= 2147483647:
        raise SsoError("account_unbound")
    return result


class EimsClient:
    def __init__(self, settings: AuthSettings, transport=None):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport)
        self._metadata = None
        self._metadata_until = 0.0
        self._jwks = None
        self._jwks_until = 0.0
        self._jwks_fetched_at = 0.0
        self._metadata_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def _json_request(self, method: str, url: str, **kwargs) -> dict:
        try:
            async with self.http.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    if response.status_code in (400, 401, 403):
                        raise SsoError("identity_rejected")
                    raise SsoError("provider_unavailable", 503)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise SsoError("provider_unavailable", 503)
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError("provider_unavailable", 503) from exc

    def _endpoint(self, value) -> str:
        if not isinstance(value, str):
            raise SsoError("invalid_provider", 503)
        try:
            target, issuer = urlsplit(value), urlsplit(self.settings.issuer)
            valid = (target.scheme, target.hostname, target.port) == (issuer.scheme, issuer.hostname, issuer.port)
        except ValueError as exc:
            raise SsoError("invalid_provider", 503) from exc
        if not valid or target.username or target.password or target.fragment or target.query:
            raise SsoError("invalid_provider", 503)
        return value

    async def metadata(self) -> dict:
        self.settings.require_sso()
        async with self._metadata_lock:
            if self._metadata is None or time.monotonic() >= self._metadata_until:
                metadata = await self._json_request("GET", self.settings.issuer + "/oauth/.well-known/openid-configuration")
                required = {"code_challenge_methods_supported": "S256", "id_token_signing_alg_values_supported": "RS256",
                            "response_types_supported": "code", "grant_types_supported": "authorization_code",
                            "token_endpoint_auth_methods_supported": "client_secret_basic"}
                if metadata.get("issuer") != self.settings.issuer or any(
                    not isinstance(metadata.get(name), list) or value not in metadata[name]
                    for name, value in required.items()
                ):
                    raise SsoError("invalid_provider", 503)
                for name in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri", "end_session_endpoint"):
                    metadata[name] = self._endpoint(metadata.get(name))
                self._metadata = metadata
                self._metadata_until = time.monotonic() + 300
            return self._metadata.copy()

    async def authorization_url(self, state: str, challenge: str, nonce: str) -> str:
        metadata = await self.metadata()
        return metadata["authorization_endpoint"] + "?" + urlencode({
            "client_id": self.settings.client_id, "redirect_uri": self.settings.callback_uri,
            "response_type": "code", "scope": "openid profile", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "nonce": nonce,
        })

    async def token(self, data: dict) -> dict:
        metadata = await self.metadata()
        result = await self._json_request("POST", metadata["token_endpoint"], data=data,
                                          auth=httpx.BasicAuth(self.settings.client_id, self.settings.client_secret))
        expires = result.get("expires_in")
        if (not isinstance(result.get("access_token"), str) or not result["access_token"]
                or str(result.get("token_type", "")).lower() != "bearer"
                or isinstance(expires, bool) or not isinstance(expires, (int, float))
                or not math.isfinite(expires) or not 0 < expires <= 86400):
            raise SsoError("invalid_token")
        for name in ("refresh_token", "id_token"):
            if name in result and (not isinstance(result[name], str) or not result[name]
                                   or len(result[name]) > 8192
                                   or any(not 33 <= ord(character) <= 126 for character in result[name])):
                raise SsoError("invalid_token")
        if (len(result["access_token"]) > 8192
                or any(not 33 <= ord(character) <= 126 for character in result["access_token"])):
            raise SsoError("invalid_token")
        return {name: result[name] for name in ("access_token", "refresh_token", "id_token", "expires_in") if name in result}

    async def _keys(self, force=False) -> list[dict]:
        metadata = await self.metadata()
        async with self._jwks_lock:
            refresh = force and time.monotonic() >= self._jwks_fetched_at + 5
            if refresh or self._jwks is None or time.monotonic() >= self._jwks_until:
                payload = await self._json_request("GET", metadata["jwks_uri"])
                keys = payload.get("keys")
                if not isinstance(keys, list) or len(keys) > 32:
                    raise SsoError("invalid_provider", 503)
                self._jwks = keys
                self._jwks_fetched_at = time.monotonic()
                self._jwks_until = time.monotonic() + 300
            return self._jwks

    async def validate_id_token(self, token: str, nonce: str, *, require_nonce=True) -> dict:
        try:
            if not isinstance(token, str) or len(token) > 16384 or token.count(".") != 2:
                raise ValueError
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (header.get("alg") != "RS256" or header.get("crit")
                    or (kid is not None and (not isinstance(kid, str) or not 0 < len(kid) <= 255))):
                raise ValueError
            key = None
            for force in (False, True):
                matches = [item for item in await self._keys(force) if isinstance(item, dict) and (kid is None or item.get("kid") == kid)
                           and item.get("kty") == "RSA" and item.get("use", "sig") == "sig"
                           and item.get("alg", "RS256") == "RS256" and "d" not in item]
                if len(matches) == 1:
                    key = matches[0]
                    break
            if key is None:
                raise ValueError
            if (not isinstance(key.get("n"), str) or not isinstance(key.get("e"), str)
                    or not 342 <= len(key["n"]) <= 1400 or not 1 <= len(key["e"]) <= 16):
                raise ValueError
            modulus = int.from_bytes(base64.b64decode(key["n"] + "=" * (-len(key["n"]) % 4), altchars=b"-_", validate=True), "big")
            exponent = int.from_bytes(base64.b64decode(key["e"] + "=" * (-len(key["e"]) % 4), altchars=b"-_", validate=True), "big")
            if not 2048 <= modulus.bit_length() <= 8192 or not 3 <= exponent <= 4294967295 or exponent % 2 == 0:
                raise ValueError
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.settings.client_id,
                                issuer=self.settings.issuer, options={"require_exp": True, "require_iat": True,
                                                                     "require_sub": True, "leeway": 30})
            now = time.time()
            for name in ("exp", "iat"):
                value = claims.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError
            if claims["iat"] > now + 30 or not isinstance(claims["sub"], str) or not 0 < len(claims["sub"]) <= 255:
                raise ValueError
            audiences = claims.get("aud")
            if audiences != self.settings.client_id and (not isinstance(audiences, list) or not audiences or any(item != self.settings.client_id for item in audiences)):
                raise ValueError
            if claims.get("azp", self.settings.client_id) != self.settings.client_id:
                raise ValueError
            if require_nonce or "nonce" in claims:
                if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce):
                    raise ValueError
            return claims
        except (JOSEError, ValueError, TypeError, KeyError, OverflowError) as exc:
            raise SsoError("invalid_identity") from exc

    async def userinfo(self, token: str, expected_subject: str) -> dict:
        metadata = await self.metadata()
        result = await self._json_request("GET", metadata["userinfo_endpoint"], headers={"Authorization": f"Bearer {token}"})
        if result.get("sub") != expected_subject:
            raise SsoError("invalid_identity")
        result["local_user_id"] = app_user_id(result.get("app_user_id"))
        return result

    async def logout_url(self, state: str, id_token: str | None) -> str:
        metadata = await self.metadata()
        parameters = {"client_id": self.settings.client_id, "post_logout_redirect_uri": self.settings.logout_callback_uri, "state": state}
        if id_token:
            parameters["id_token_hint"] = id_token
        return metadata["end_session_endpoint"] + "?" + urlencode(parameters)


_client: EimsClient | None = None


def get_client() -> EimsClient:
    global _client
    settings = get_settings()
    if _client is None:
        _client = EimsClient(settings)
    elif _client.settings != settings:
        raise SsoError("configuration_changed", 503)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
