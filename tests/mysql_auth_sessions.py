"""Explicitly opt-in local MySQL auth schema/locking check; never production."""

import asyncio
import hashlib
import json
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)
EXPECTED_TARGET = {"MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "3309",
                   "MYSQL_DATABASE": "inventory_fifo_test", "MYSQL_USER": "fifo_local_app"}
if not all(os.getenv(key) == value for key,value in EXPECTED_TARGET.items()):
    raise SystemExit("拒绝执行：不是已核实的独立本地测试库")
if "--apply-local-auth-schema" not in sys.argv:
    raise SystemExit("本脚本会增加本地认证字段/表及临时测试记录，需要显式 --apply-local-auth-schema")

from cryptography.fernet import Fernet
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from database import async_session, engine
from models import AuthLoginTransaction, AuthSession, User
from services.auth_sessions import SessionStore, credential_hash, revoke_user_sessions, utcnow
from services.eims_sso import AuthSettings, SsoError


OUTPUT = ROOT / "output" / "eims-sso"
PHASE = "开始"


async def fingerprint():
    async with async_session() as db:
        names = [row[0] for row in (await db.execute(text("SHOW TABLES"))).all()
                 if not row[0].startswith("auth_")]
        checksums = {}
        for name in names:
            if not name.replace("_", "").isalnum():
                raise RuntimeError("Unexpected table name")
            if name == "users":
                rows = (await db.execute(text("SELECT id,username,role,store_id,created_at FROM users ORDER BY id"))).all()
                checksums[name] = {"rows":len(rows), "digest":hashlib.sha256(json.dumps([list(row) for row in rows], default=str, ensure_ascii=False).encode()).hexdigest()}
            else:
                result = (await db.execute(text(f"CHECKSUM TABLE `{name}` EXTENDED"))).one()
                if result[1] is None:
                    raise RuntimeError("Checksum unavailable")
                count = await db.scalar(text(f"SELECT COUNT(*) FROM `{name}`"))
                checksums[name] = {"rows":count, "checksum":result[1]}
        return checksums


async def verify_password_lock_orders(user_id, settings, request):
    """Only the owned test user is changed; assert both real InnoDB lock orders."""
    original_get = AsyncSession.get
    locked, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def observed_get(db, model, key, *args, **kwargs):
        probe = db.info.get("sso_lock_probe") if model is User and key == user_id else None
        if probe:
            assert kwargs.get("with_for_update") is True
            attempted.set()
        row = await original_get(db, model, key, *args, **kwargs)
        if probe == "hold":
            locked.set()
            await asyncio.wait_for(release.wait(),10)
        return row

    async def must_wait(task):
        try:
            await asyncio.wait_for(asyncio.shield(task),0.1)
        except TimeoutError:
            return
        raise AssertionError("Expected user row lock was not held")

    async with async_session() as db:
        initial_hash = (await db.get(User,user_id)).password_hash
    change_started = asyncio.Event()

    async def change_password():
        async with async_session() as db:
            change_started.set()
            user = await db.get(User,user_id,with_for_update=True,populate_existing=True)
            user.password_hash = "changed-first-test-only"
            await revoke_user_sessions(db,user_id)
            await db.commit()

    gated_store = SessionStore(lambda: async_session(info={"sso_lock_probe":"hold"}))
    creation = mutation = None
    with patch.object(AsyncSession,"get",observed_get):
        try:
            creation = asyncio.create_task(gated_store.create_session(user_id,settings,expected_password_hash=initial_hash))
            await asyncio.wait_for(locked.wait(),10)
            mutation = asyncio.create_task(change_password())
            await asyncio.wait_for(change_started.wait(),10)
            await must_wait(mutation)
            release.set()
            (cookie,_),_ = await asyncio.wait_for(asyncio.gather(creation,mutation),10)
        finally:
            release.set()
            for task in (creation,mutation):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (creation,mutation) if task is not None),return_exceptions=True)
    try:
        await SessionStore(async_session).authenticate(cookie,request(),settings)
    except SsoError as error:
        assert error.code == "invalid_session"
    else:
        raise AssertionError("A session committed before reset was not revoked")

    attempted.clear()
    waiting_store = SessionStore(lambda: async_session(info={"sso_lock_probe":"observe"}))
    creation = None
    with patch.object(AsyncSession,"get",observed_get):
        try:
            async with async_session() as db:
                user = await db.get(User,user_id,with_for_update=True,populate_existing=True)
                prior_hash = user.password_hash
                user.password_hash = "changed-second-test-only"
                await revoke_user_sessions(db,user_id)
                creation = asyncio.create_task(waiting_store.create_session(user_id,settings,expected_password_hash=prior_hash))
                await asyncio.wait_for(attempted.wait(),10)
                await must_wait(creation)
                await db.commit()
            try:
                await asyncio.wait_for(creation,10)
            except SsoError as error:
                assert error.code == "credentials_changed"
            else:
                raise AssertionError("A stale password check created a new session")
        finally:
            if creation is not None and not creation.done():
                creation.cancel()
            if creation is not None:
                await asyncio.gather(creation,return_exceptions=True)
    return {"password_change_waited_for_session_creation":True,
            "session_creation_waited_for_password_change":True,
            "session_created_before_change_revoked":True,"inflight_old_password_rejected":True}


async def verify():
    global PHASE
    OUTPUT.mkdir(parents=True,exist_ok=True)
    before = await fingerprint()
    if "users" not in before:
        raise RuntimeError("Expected local backup schema is missing")
    (OUTPUT/"mysql-before.json").write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding="utf-8")
    PHASE = "本地认证加法型迁移"
    async with engine.begin() as connection:
        column = (await connection.execute(text("SHOW COLUMNS FROM users LIKE 'is_active'"))).first()
        existing_auth_tables = await connection.scalar(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('auth_sessions','auth_login_transactions')"))
        schema_added = column is None or existing_auth_tables < 2
        if column is None:
            await connection.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
        await connection.run_sync(lambda sync: AuthLoginTransaction.__table__.create(sync,checkfirst=True))
        await connection.run_sync(lambda sync: AuthSession.__table__.create(sync,checkfirst=True))
        storage = (await connection.execute(text("SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('auth_sessions','auth_login_transactions')"))).all()
        assert len(storage)==2 and all(row[1]=='InnoDB' for row in storage)
    prefix = "sso-check-" + secrets.token_hex(12)
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    cookie = None
    user_id = None
    settings = AuthSettings(public_base_url="http://127.0.0.1:8010", issuer="https://provider.invalid",
                            client_id=prefix,client_secret=secrets.token_urlsafe(32),encryption_key=Fernet.generate_key().decode())
    store = SessionStore(async_session)
    try:
        PHASE = "创建本地临时测试账号"
        async with async_session() as db:
            user = User(username=prefix,password_hash="not-a-login-credential",role="viewer",is_active=True)
            db.add(user)
            await db.commit()
            user_id = user.id
        PHASE = "InnoDB一次性事务竞争"
        await store.create_transaction("login",state,browser,{"verifier":"test-only","nonce":"test-only"},settings)
        consumed = await asyncio.gather(
            store.consume_transaction("login",state,browser,settings),
            store.consume_transaction("login",state,browser,settings),return_exceptions=True)
        assert sum(isinstance(item,dict) for item in consumed) == 1
        assert sum(isinstance(item,SsoError) and item.code=="invalid_state" for item in consumed) == 1
        PHASE = "InnoDB并发刷新竞争"
        cookie,_ = await store.create_session(user_id,settings,source="eims",subject=prefix,nonce="test-only",
                                             tokens={"access_token":"test-only","refresh_token":"old-test-only","expires_in":3600})
        async with async_session() as db:
            row=await db.get(AuthSession,credential_hash(cookie))
            row.token_expires_at=utcnow()-timedelta(seconds=1)
            await db.commit()
        class Provider:
            calls=0
            current_refresh="old-test-only"
            current_access=None
            userinfo_status=200
            async def token(self,data):
                if data['refresh_token'] != self.current_refresh:
                    raise SsoError("identity_rejected")
                self.calls+=1
                await asyncio.sleep(0.15)
                self.current_access=f"access-test-only-{self.calls}"
                self.current_refresh=f"refresh-test-only-{self.calls}"
                return {"access_token":self.current_access,"refresh_token":self.current_refresh,"expires_in":3600}
            async def userinfo(self,token,subject):
                assert token==self.current_access and subject==prefix
                if self.userinfo_status == 503:
                    raise SsoError("provider_unavailable",503)
                return {"local_user_id":user_id}
        provider=Provider()
        def request():
            return Request({"type":"http","method":"GET","path":"/auth/me","scheme":"http",
                            "server":("127.0.0.1",8010),"headers":[],"query_string":b""})
        with patch("services.auth_sessions.get_client",return_value=provider):
            views=await asyncio.gather(store.authenticate(cookie,request(),settings),store.authenticate(cookie,request(),settings))
        assert provider.calls==1 and all(view.user_id==user_id for view in views)
        parallel_calls=provider.calls
        PHASE = "轮换成功后的UserInfo临时故障恢复"
        async with async_session() as db:
            row=await db.get(AuthSession,credential_hash(cookie))
            row.token_expires_at=utcnow()-timedelta(seconds=1)
            await db.commit()
        provider.userinfo_status=503
        with patch("services.auth_sessions.get_client",return_value=provider):
            for _ in range(2):
                try:
                    await store.authenticate(cookie,request(),settings)
                except SsoError as error:
                    assert error.status_code==503
                else:
                    raise AssertionError("Unverified session was accepted")
            assert provider.calls==parallel_calls+1
            provider.userinfo_status=200
            view=await store.authenticate(cookie,request(),settings)
            assert view.user_id==user_id and provider.calls==parallel_calls+1
        PHASE = "撤销会话校验"
        await store.revoke(cookie,settings)
        try:
            await store.authenticate(cookie,request(),settings)
        except SsoError as error:
            assert error.code=="invalid_session"
        else:
            raise AssertionError("Revoked cookie was accepted")
        PHASE = "InnoDB用户锁与密码变更的双向竞争"
        password_proof=await verify_password_lock_orders(user_id,settings,request)
    finally:
        PHASE = "清理本脚本创建的认证测试记录"
        async with async_session() as db:
            await db.execute(delete(AuthLoginTransaction).where(AuthLoginTransaction.state_hash==credential_hash(state)))
            if user_id is not None:
                await db.execute(delete(AuthSession).where(AuthSession.user_id==user_id))
                await db.execute(delete(User).where(User.id==user_id,User.username==prefix))
            await db.commit()
    PHASE = "核对既有业务数据"
    after=await fingerprint()
    assert after==before, "Existing business rows changed"
    proof={"target":EXPECTED_TARGET,"auth_schema_added":schema_added,"original_tables_checked":len(before),
           "original_business_rows_unchanged":True,"storage_engine":"InnoDB","transaction_consumers_succeeded":1,
           "parallel_refresh_calls":parallel_calls,"rotation_outage_recovered_without_second_refresh":True,
           "revoked_cookie_rejected":True,"test_records_removed":True,**password_proof}
    (OUTPUT/"mysql-verification.json").write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(proof,ensure_ascii=False))


async def run():
    try:
        await verify()
    except Exception as error:
        # Do not render SQL parameters, connection strings, or encrypted credentials.
        print(json.dumps({"failed_phase":PHASE,"error_type":type(error).__name__},ensure_ascii=False))
        raise SystemExit(1) from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
