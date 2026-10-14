"""SSO redirect endpoints. No credentials or provider errors are reflected."""

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from services.auth_sessions import check_origin, clear_cookie, cookie_name, set_cookie, store
from services.eims_sso import SsoError, get_client, get_settings, pkce_pair

router = APIRouter(prefix="/auth", tags=["统一登录"])

ERROR_MESSAGES = {
    "sso_unavailable": "EIMS登录尚未配置，请联系管理员；可使用已开放的本地登录。",
    "provider_unavailable": "统一登录服务暂不可用，请稍后重试。",
    "invalid_provider": "统一登录配置异常，请联系管理员。",
    "configuration_changed": "登录配置已变化，请联系管理员重启应用。",
    "account_unbound": "该EIMS账号尚未绑定有效的FIFO账号，请联系管理员。",
    "account_unavailable": "FIFO账号不存在或已停用，请联系管理员。",
    "identity_rejected": "登录授权已失效，请重新登录。",
    "credentials_changed": "账号密码已变更，请重新登录。",
    "csrf_rejected": "请求校验失败，请刷新页面后重试。",
}


async def sso_error_handler(request: Request, error: SsoError):
    return JSONResponse({"detail": ERROR_MESSAGES.get(error.code, "登录状态无效，请重新登录。")},
                        status_code=error.status_code, headers={"Cache-Control": "no-store"})


def failure_redirect(request: Request, code: str, *, clear_flow=True):
    allowed = code if code in ERROR_MESSAGES else "login_failed"
    response = RedirectResponse("/?" + urlencode({"auth_error": allowed}), status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    if clear_flow:
        clear_cookie(response, request, get_settings(), flow=True)
    return response


@router.get("/config")
async def auth_config():
    settings = get_settings()
    return JSONResponse({"mode": settings.mode, "local_enabled": settings.local_enabled,
                         "sso_available": settings.configured}, headers={"Cache-Control": "no-store"})


@router.get("/eims/start")
async def eims_start(request: Request):
    settings = get_settings()
    try:
        settings.require_sso()
        verifier, challenge = pkce_pair()
        state, browser, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        url = await get_client().authorization_url(state, challenge, nonce)
        await store.create_transaction("login", state, browser, {"verifier": verifier, "nonce": nonce}, settings)
        response = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        set_cookie(response, request, settings, browser, flow=True)
        return response
    except SsoError as error:
        return failure_redirect(request, error.code)


@router.get("/eims/callback")
async def eims_callback(request: Request):
    settings = get_settings()
    consumed = False
    try:
        settings.require_sso()
        if len(request.query_params.getlist("state")) != 1 or len(request.query_params.getlist("code")) > 1:
            raise SsoError("invalid_state")
        payload = await store.consume_transaction("login", request.query_params.get("state"),
                                                  request.cookies.get(cookie_name(request, settings, flow=True)), settings)
        consumed = True
        # A validated browser transaction rotates the previous local identity.
        await store.revoke(request.cookies.get(cookie_name(request, settings)), settings)
        code = request.query_params.get("code")
        if request.query_params.get("error") or not code or len(code) > 2048:
            raise SsoError("identity_rejected")
        client = get_client()
        tokens = await client.token({"grant_type": "authorization_code", "code": code,
                                     "redirect_uri": settings.callback_uri, "code_verifier": payload["verifier"]})
        claims = await client.validate_id_token(tokens.get("id_token"), payload["nonce"])
        identity = await client.userinfo(tokens["access_token"], claims["sub"])
        cookie, _ = await store.create_session(identity["local_user_id"], settings, source="eims", tokens=tokens,
                                               subject=claims["sub"], nonce=payload["nonce"])
        response = RedirectResponse("/", status_code=303,
                                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        set_cookie(response, request, settings, cookie)
        clear_cookie(response, request, settings, flow=True)
        return response
    except SsoError as error:
        response = failure_redirect(request, error.code, clear_flow=consumed)
        if consumed:
            clear_cookie(response, request, settings)
        return response


@router.post("/logout")
async def logout(request: Request):
    settings = get_settings()
    check_origin(request, settings)
    tokens = await store.revoke(request.cookies.get(cookie_name(request, settings)), settings, request=request)
    response = JSONResponse({"ok": True, "redirect_url": "/"}, headers={"Cache-Control": "no-store"})
    clear_cookie(response, request, settings)
    if tokens and settings.configured:
        state, ticket, browser = (secrets.token_urlsafe(32) for _ in range(3))
        await store.create_transaction("logout", state, browser, {}, settings)
        await store.create_transaction("logout_redirect", ticket, browser,
                                       {"state": state, "id_token": tokens.get("id_token")}, settings)
        # JavaScript receives only a local one-time navigation ticket, never an ID Token.
        response = JSONResponse({"ok": True, "redirect_url": "/auth/eims/logout/start?" + urlencode({"ticket": ticket})},
                                headers={"Cache-Control": "no-store"})
        clear_cookie(response, request, settings)
        set_cookie(response, request, settings, browser, flow=True)
    return response


@router.get("/eims/logout/start")
async def eims_logout_start(request: Request):
    settings = get_settings()
    consumed = False
    try:
        settings.require_sso()
        payload = await store.consume_transaction("logout_redirect", request.query_params.get("ticket"),
                                                  request.cookies.get(cookie_name(request, settings, flow=True)), settings)
        consumed = True
        url = await get_client().logout_url(payload["state"], payload.get("id_token"))
        return RedirectResponse(url, status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    except SsoError as error:
        return failure_redirect(request, error.code, clear_flow=consumed)


@router.get("/eims/logout/callback")
async def eims_logout_callback(request: Request):
    settings = get_settings()
    try:
        settings.require_sso()
        if len(request.query_params.getlist("state")) != 1:
            raise SsoError("invalid_state")
        await store.consume_transaction("logout", request.query_params.get("state"),
                                        request.cookies.get(cookie_name(request, settings, flow=True)), settings)
        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        clear_cookie(response, request, settings, flow=True)
        return response
    except SsoError as error:
        # Invalid old callbacks must not erase a newer login's browser binding.
        return failure_redirect(request, error.code, clear_flow=False)
