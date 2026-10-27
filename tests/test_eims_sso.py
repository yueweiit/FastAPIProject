"""Offline protocol/API verification. SQLite locks here simulate, not prove InnoDB."""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from jose import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import auth
from database import Base, get_db
from models import AuthLoginTransaction, AuthSession, User
from routers import auth as auth_routes, eims_auth
from services.auth_logging import OAuthAccessLogFilter
from services.auth_sessions import SessionStore, credential_hash, utcnow
from services.eims_sso import AuthSettings, EimsClient, SsoError, TokenCipher, app_user_id, pkce_pair, valid_origin


class AsyncTransaction:
    def __init__(self, facade):
        self.facade = facade

    async def __aenter__(self):
        await self.facade.lock.acquire()
        self.transaction = self.facade.session.begin()
        self.transaction.__enter__()
        return self.facade

    async def __aexit__(self, exc_type, exc, tb):
        try:
            self.transaction.__exit__(exc_type, exc, tb)
        finally:
            self.facade.lock.release()


class AsyncSQLiteSession:
    def __init__(self, engine, lock):
        self.session = Session(engine, expire_on_commit=False)
        self.lock = lock

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.session.close()

    def begin(self):
        return AsyncTransaction(self)

    def add(self, row):
        self.session.add(row)

    async def get(self, model, key, **kwargs):
        return self.session.get(model, key, **kwargs)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def commit(self):
        self.session.commit()

    async def refresh(self, row):
        self.session.refresh(row)

    async def delete(self, row):
        self.session.delete(row)


def b64_integer(value):
    return base64.urlsafe_b64encode(value.to_bytes((value.bit_length()+7)//8, 'big')).rstrip(b'=').decode('ascii')


class FakeProvider:
    def __init__(self, settings, private_key):
        self.settings, self.private_key = settings, private_key
        numbers = private_key.public_key().public_numbers()
        self.jwk = {'kid': 'test-key', 'kty': 'RSA', 'alg': 'RS256', 'use': 'sig',
                    'n': b64_integer(numbers.n), 'e': b64_integer(numbers.e)}
        self.nonce, self.challenge = '', ''
        self.claim_overrides = {}
        self.user_id = 2
        self.subject = 'eims-user-2'
        self.userinfo_subject = None
        self.metadata_overrides = {}
        self.token_overrides = {}
        self.status = 200
        self.refresh_count = 0
        self.userinfo_count = 0
        self.jwks_count = 0
        self.refresh_delay = 0
        self.userinfo_status = 200
        self.jwks_status = 200
        self.valid_refresh_token = None

    def id_token(self, **changes):
        claims = {'iss': self.settings.issuer, 'aud': self.settings.client_id, 'sub': self.subject,
                  'exp': int(time.time())+3600, 'iat': int(time.time()), 'nonce': self.nonce}
        claims.update(self.claim_overrides)
        claims.update(changes)
        key = self.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})

    def begin(self, url):
        query = parse_qs(urlsplit(url).query)
        self.nonce, self.challenge = query['nonce'][0], query['code_challenge'][0]
        return query['state'][0]

    async def handle(self, request):
        path = request.url.path
        if path.endswith('/.well-known/openid-configuration'):
            metadata = {
                'issuer': self.settings.issuer,
                **{name: self.settings.issuer+'/oauth/'+suffix for name,suffix in (
                    ('authorization_endpoint','authorize'),('token_endpoint','token'),
                    ('userinfo_endpoint','userinfo'),('jwks_uri','jwks'),('end_session_endpoint','logout'))},
                'code_challenge_methods_supported':['S256'], 'id_token_signing_alg_values_supported':['RS256'],
                'response_types_supported':['code'], 'grant_types_supported':['authorization_code','refresh_token'],
                'token_endpoint_auth_methods_supported':['client_secret_basic'],
                **self.metadata_overrides,
            }
            return httpx.Response(200, json=metadata)
        if path == '/oauth/jwks':
            self.jwks_count += 1
            return httpx.Response(self.jwks_status, json={'keys':[self.jwk]})
        if path == '/oauth/token':
            expected='Basic '+base64.b64encode((self.settings.client_id+':'+self.settings.client_secret).encode()).decode()
            if request.headers.get('Authorization') != expected:
                return httpx.Response(401,json={'error':'invalid_client'})
            if self.status != 200:
                return httpx.Response(self.status, json={'error':'invalid_grant','error_description':'must-not-reflect'})
            fields = parse_qs(request.content.decode('ascii'))
            if fields['grant_type'] == ['authorization_code']:
                challenge = base64.urlsafe_b64encode(hashlib.sha256(fields['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
                if fields.get('code') != ['valid-code'] or challenge != self.challenge:
                    return httpx.Response(400, json={'error':'invalid_grant'})
            else:
                if fields.get('refresh_token') != [self.valid_refresh_token]:
                    return httpx.Response(400, json={'error':'invalid_grant'})
                self.refresh_count += 1
                await asyncio.sleep(self.refresh_delay)
            tokens = {'access_token':'fixture-access', 'refresh_token':f'fixture-refresh-{self.refresh_count}',
                      'id_token':self.id_token(), 'token_type':'Bearer', 'expires_in':3600,
                      **self.token_overrides}
            self.valid_refresh_token = tokens['refresh_token']
            return httpx.Response(200, json=tokens)
        if path == '/oauth/userinfo':
            self.userinfo_count += 1
            return httpx.Response(self.userinfo_status, json={'sub':self.userinfo_subject or self.subject,
                'app_user_id':self.user_id, 'app_username':'ignored-provider-name', 'app_roles':['admin']})
        return httpx.Response(404)


class SsoFixture(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.password_hash = auth.hash_password('unit-test-password')

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration=2
        output = Path.cwd()/'output'
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='sso-unit-', dir=output)
        self.engine = create_engine('sqlite:///'+(Path(self.temp.name)/'auth.sqlite').as_posix())
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add_all([User(id=1,username='fixture-admin',role='admin',password_hash=self.password_hash,is_active=True),
                        User(id=2,username='fixture-operator',role='operator',password_hash=self.password_hash,is_active=True),
                        User(id=3,username='fixture-inactive',role='viewer',password_hash=self.password_hash,is_active=False)])
            db.commit()
        self.lock = asyncio.Lock()
        self.factory = lambda: AsyncSQLiteSession(self.engine, self.lock)
        self.store = SessionStore(self.factory)
        self.settings = AuthSettings(public_base_url='https://fifo.test', issuer='https://identity.test',
            client_id='fixture-client',client_secret=secrets.token_urlsafe(32),encryption_key=Fernet.generate_key().decode())
        self.provider = FakeProvider(self.settings, self.private_key)
        self.provider_client = EimsClient(self.settings, transport=httpx.MockTransport(self.provider.handle))
        self.patches = []
        for module in (auth, auth_routes, eims_auth):
            self.patches.extend([patch.object(module,'get_settings',lambda:self.settings), patch.object(module,'store',self.store)])
        self.patches.extend([patch('services.auth_sessions.get_client',return_value=self.provider_client),
                             patch.object(eims_auth,'get_client',return_value=self.provider_client),
                             patch.object(auth,'SECRET_KEY',secrets.token_urlsafe(32))])
        for item in self.patches:
            item.start()
        app = FastAPI()  # Deliberately omit main.lifespan and all production DB access.
        app.include_router(auth_routes.router)
        app.include_router(eims_auth.router)
        app.add_exception_handler(SsoError, eims_auth.sso_error_handler)
        async def test_db():
            async with self.factory() as db:
                yield db
        app.dependency_overrides[get_db] = test_db
        self.browser = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=self.settings.public_base_url)

    async def asyncTearDown(self):
        await self.browser.aclose()
        await self.provider_client.close()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        resolved = Path(self.temp.name).resolve()
        assert resolved.parent == (Path.cwd()/'output').resolve()
        self.temp.cleanup()

    async def sso_login(self):
        start = await self.browser.get('/auth/eims/start')
        self.assertEqual(start.status_code,303)
        state = self.provider.begin(start.headers['location'])
        response = await self.browser.get('/auth/eims/callback',params={'state':state,'code':'valid-code'})
        return response, state

    async def local_login(self, username='fixture-admin'):
        return await self.browser.post('/auth/login',json={'username':username,'password':'unit-test-password'},
                                       headers={'Origin':self.settings.public_base_url})

    def modify_session(self, **values):
        cookie = self.browser.cookies.get('__Host-fifo_session')
        with Session(self.engine) as db:
            row = db.get(AuthSession,credential_hash(cookie))
            for key,value in values.items():
                setattr(row,key,value)
            db.commit()


class ProtocolTests(SsoFixture):
    async def test_pkce_challenge_matches_verifier(self):
        verifier,challenge = pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge,expected)

    async def test_binding_ids_reject_coercion_and_missing(self):
        for value in (None,False,True,0,-1,1.0,'01','+1',' 1','1_0','2147483648',[],{}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(SsoError):
                    app_user_id(value)
        self.assertEqual(app_user_id('2'),2)
        self.assertEqual(app_user_id(2),2)

    async def test_configuration_requires_clean_origins_and_valid_cipher(self):
        self.assertTrue(self.settings.configured)
        for value in ('https://user:secret@fifo.test','https://fifo.test/path','https://fifo.test/?x=1',
                      'https://fifo.test/#fragment','http://fifo.test','https://[invalid'):
            self.assertFalse(valid_origin(value))
        self.assertTrue(valid_origin('http://127.0.0.1:8010'))
        self.assertFalse(replace(self.settings,encryption_key='invalid').configured)
        self.assertFalse(replace(self.settings,issuer='http://127.0.0.1:8000').configured)
        self.assertTrue(replace(self.settings,public_base_url='http://127.0.0.1:8010',
                                issuer='http://127.0.0.1:8000').configured)

    async def test_token_response_rejects_control_characters(self):
        await self.provider_client.metadata()
        for name in ('access_token','refresh_token','id_token'):
            for value in ('bad\x00token','bad\x7ftoken','bad token'):
                response={'access_token':'fixture-access','refresh_token':'fixture-refresh',
                          'id_token':'fixture-id','token_type':'Bearer','expires_in':3600,name:value}
                with patch.object(self.provider_client,'_json_request',AsyncMock(return_value=response)):
                    with self.assertRaises(SsoError) as caught:
                        await self.provider_client.token({'grant_type':'authorization_code'})
                    self.assertEqual(caught.exception.code,'invalid_token')

    async def test_cipher_authentication_detects_tampering(self):
        cipher = TokenCipher(self.settings)
        encoded = cipher.encrypt({'private':'fixture-value'})
        self.assertNotIn('fixture-value',encoded)
        self.assertEqual(cipher.decrypt(encoded),{'private':'fixture-value'})
        with self.assertRaises(SsoError):
            cipher.decrypt(encoded[:-3]+'bad')

    async def test_discovery_rejects_cross_origin_endpoints(self):
        self.provider.metadata_overrides['token_endpoint']='https://attacker.test/token'
        with self.assertRaises(SsoError):
            await self.provider_client.metadata()

    async def test_discovery_rejects_malformed_support_lists(self):
        self.provider.metadata_overrides['code_challenge_methods_supported']=None
        with self.assertRaises(SsoError):
            await self.provider_client.metadata()

    async def test_id_token_signature_issuer_audience_nonce_and_expiry(self):
        self.provider.nonce='expected-nonce'
        claims = await self.provider_client.validate_id_token(self.provider.id_token(),'expected-nonce')
        self.assertEqual(claims['sub'],self.provider.subject)
        cases = ({'iss':'https://attacker.test'}, {'aud':'other-client'}, {'nonce':'wrong'},
                 {'exp':int(time.time())-90}, {'iat':int(time.time())+90}, {'sub':False},
                 {'aud':[self.settings.client_id,'other-client']}, {'azp':'other-client'}, {'exp':True})
        for changes in cases:
            with self.subTest(changed=list(changes)):
                with self.assertRaises(SsoError):
                    await self.provider_client.validate_id_token(self.provider.id_token(**changes),'expected-nonce')

    async def test_unsigned_or_hs_token_is_not_accepted_as_oidc(self):
        token = jwt.encode({'sub':'x','exp':int(time.time())+3600},'fixture-secret',algorithm='HS256')
        with self.assertRaises(SsoError):
            await self.provider_client.validate_id_token(token,'nonce')
        with self.assertRaises(SsoError):
            await self.provider_client.validate_id_token('not-a-jwt','nonce')

    async def test_no_kid_is_only_accepted_when_one_signing_key_exists(self):
        self.provider.nonce='nonce'
        claims=jwt.get_unverified_claims(self.provider.id_token())
        key=self.private_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        token=jwt.encode(claims,key,algorithm='RS256')
        await self.provider_client.validate_id_token(token,'nonce')
        self.provider_client._jwks=[self.provider.jwk,{**self.provider.jwk,'kid':'another-key'}]
        with self.assertRaises(SsoError):
            await self.provider_client.validate_id_token(token,'nonce')

    async def test_signature_failure_and_unknown_kid_are_rejected(self):
        self.provider.nonce='nonce'
        token = self.provider.id_token()
        header,payload,signature = token.split('.')
        signature = ('A' if signature[0]!='A' else 'B')+signature[1:]
        with self.assertRaises(SsoError):
            await self.provider_client.validate_id_token('.'.join((header,payload,signature)),'nonce')
        self.provider.jwk['kid']='different-key'
        self.provider_client._jwks = None
        with self.assertRaises(SsoError):
            await self.provider_client.validate_id_token(token,'nonce')

    async def test_refresh_id_token_may_omit_original_nonce(self):
        self.provider.nonce='nonce'
        token = self.provider.id_token()
        claims = jwt.get_unverified_claims(token)
        del claims['nonce']
        key = self.private_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        token = jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test-key'})
        await self.provider_client.validate_id_token(token,'nonce',require_nonce=False)
        with self.assertRaises(SsoError):
            await self.provider_client.validate_id_token(token,'nonce')

    async def test_oauth_access_log_redacts_query_values(self):
        record = logging.LogRecord('uvicorn.access',logging.INFO,'',1,'%s %s %s %s %s',
                                   ('client','GET','/auth/eims/callback?code=private&state=private','1.1',303),None)
        OAuthAccessLogFilter().filter(record)
        self.assertNotIn('private',record.getMessage())
        self.assertIn('/auth/eims/callback',record.getMessage())


class AuthFlowTests(SsoFixture):
    async def test_public_config_does_not_expose_credentials(self):
        response=await self.browser.get('/auth/config')
        self.assertNotIn(self.settings.client_secret,response.text)
        self.assertNotIn(self.settings.encryption_key,response.text)
        self.assertEqual(set(response.json()),{'mode','local_enabled','sso_available'})

    async def test_local_login_rejects_wrong_credentials_and_inactive_user(self):
        wrong=await self.browser.post('/auth/login',json={'username':'fixture-admin','password':'incorrect'})
        self.assertEqual(wrong.status_code,401)
        self.assertEqual((await self.local_login('fixture-inactive')).status_code,401)

    async def test_login_rejects_cross_site_origin(self):
        response=await self.browser.post('/auth/login',json={'username':'fixture-admin','password':'unit-test-password'},
                                          headers={'Origin':'https://identity.test'})
        self.assertEqual(response.status_code,403)

    async def test_local_login_response_remains_compatible_and_cookie_rotates(self):
        response=await self.local_login()
        self.assertEqual(set(response.json()),{'token','user_id','username','role'})
        old=self.browser.cookies.get('__Host-fifo_session')
        await self.local_login()
        self.assertNotEqual(old,self.browser.cookies.get('__Host-fifo_session'))
        reused=await self.browser.get('/auth/me',headers={'Cookie':'__Host-fifo_session='+old})
        self.assertEqual(reused.status_code,401)

    async def test_http_loopback_uses_development_cookie_without_secure_prefix(self):
        self.settings=replace(self.settings,public_base_url='',client_id='',client_secret='',encryption_key='')
        async with httpx.AsyncClient(transport=self.browser._transport,base_url='http://127.0.0.1:8010') as browser:
            response=await browser.post('/auth/login',json={'username':'fixture-admin','password':'unit-test-password'})
            self.assertIn('fifo_session=',response.headers['set-cookie'])
            self.assertNotIn('__Host-',response.headers['set-cookie'])
            self.assertNotIn('; Secure',response.headers['set-cookie'])
            self.assertEqual((await browser.get('/auth/me')).status_code,200)

    async def test_local_mode_rejects_previous_eims_session(self):
        await self.sso_login()
        self.settings=replace(self.settings,mode='local')
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_refresh_cannot_reuse_old_refresh_token(self):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider.token_overrides['refresh_token']='fixture-refresh-0'
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_changed_client_rejects_existing_cookie(self):
        await self.sso_login()
        self.settings=replace(self.settings,client_id='a-different-client')
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_password_change_revokes_browser_sessions(self):
        await self.local_login()
        me=(await self.browser.get('/auth/me')).json()
        response=await self.browser.put('/auth/me/password',json={'old_password':'unit-test-password','new_password':'new-unit-password'},
                                         headers={'X-CSRF-Token':me['csrf_token']})
        self.assertEqual(response.status_code,200)
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_password_reset_rejects_inflight_old_password_login(self):
        await self._password_change_races_login(self_change=False)

    async def test_self_password_change_rejects_inflight_old_password_login(self):
        await self._password_change_races_login(self_change=True)

    async def _password_change_races_login(self, *, self_change):
        await self.local_login('fixture-operator' if self_change else 'fixture-admin')
        csrf = (await self.browser.get('/auth/me')).json()['csrf_token']
        waiting, release = asyncio.Event(), asyncio.Event()
        original = self.store.create_session
        async def paused_create(user_id, *args, **kwargs):
            if user_id == 2:
                waiting.set()
                await asyncio.wait_for(release.wait(),10)
            return await original(user_id, *args, **kwargs)
        async with httpx.AsyncClient(transport=self.browser._transport,base_url=self.settings.public_base_url) as browser:
            with patch.object(self.store,'create_session',paused_create):
                task = asyncio.create_task(browser.post('/auth/login',
                    json={'username':'fixture-operator','password':'unit-test-password'},
                    headers={'Origin':self.settings.public_base_url}))
                try:
                    await asyncio.wait_for(waiting.wait(),10)
                    route = '/auth/me/password' if self_change else '/auth/users/2/password'
                    changed = await self.browser.put(route,json={'new_password':'replacement-unit-test-password',
                        'old_password':'unit-test-password'},headers={'X-CSRF-Token':csrf})
                    self.assertEqual(changed.status_code,200)
                finally:
                    release.set()
                response = await asyncio.wait_for(task,10)
            self.assertEqual(response.status_code,401)
            self.assertNotIn('__Host-fifo_session',browser.cookies)
            self.assertEqual((await browser.get('/auth/me')).status_code,401)
            good = await browser.post('/auth/login',json={'username':'fixture-operator',
                'password':'replacement-unit-test-password'},headers={'Origin':self.settings.public_base_url})
            self.assertEqual(good.status_code,200)
            self.assertEqual((await browser.get('/auth/me')).status_code,200)

    async def test_created_session_then_password_reset_is_revoked(self):
        await self.local_login('fixture-admin')
        csrf=(await self.browser.get('/auth/me')).json()['csrf_token']
        async with httpx.AsyncClient(transport=self.browser._transport,base_url=self.settings.public_base_url) as browser:
            response=await browser.post('/auth/login',json={'username':'fixture-operator','password':'unit-test-password'})
            self.assertEqual(response.status_code,200)
            reset=await self.browser.put('/auth/users/2/password',json={'new_password':'replacement-unit-password'},
                                         headers={'X-CSRF-Token':csrf})
            self.assertEqual(reset.status_code,200)
            self.assertEqual((await browser.get('/auth/me')).status_code,401)

    async def test_pending_local_login_rechecks_disabled_account(self):
        await self.local_login('fixture-admin')
        csrf=(await self.browser.get('/auth/me')).json()['csrf_token']
        with Session(self.engine) as db:
            old_hash=db.get(User,2).password_hash
        response=await self.browser.put('/auth/users/2/status',json={'is_active':False},headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code,200)
        with self.assertRaises(SsoError) as caught:
            await self.store.create_session(2,self.settings,expected_password_hash=old_hash)
        self.assertEqual(caught.exception.code,'account_unavailable')

    async def test_duplicate_callback_values_are_rejected(self):
        start=await self.browser.get('/auth/eims/start')
        state=self.provider.begin(start.headers['location'])
        response=await self.browser.get('/auth/eims/callback',params=[('state',state),('state',state),('code','valid-code')])
        self.assertIn('auth_error=',response.headers['location'])
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_expired_auth_cleanup_preserves_users(self):
        await self.local_login()
        self.modify_session(expires_at=utcnow()-timedelta(seconds=1))
        await self.store.cleanup()
        with Session(self.engine) as db:
            self.assertFalse(db.scalars(select(AuthSession)).all())
            self.assertEqual(len(db.scalars(select(User)).all()),3)

    async def test_hybrid_local_login_works_without_sso_credentials(self):
        self.settings=replace(self.settings,client_id='',client_secret='',encryption_key='')
        config = (await self.browser.get('/auth/config')).json()
        self.assertTrue(config['local_enabled'])
        self.assertFalse(config['sso_available'])
        self.assertEqual((await self.local_login()).status_code,200)
        me = (await self.browser.get('/auth/me')).json()
        self.assertEqual(me['auth_source'],'local')
        self.assertTrue(me['csrf_token'])

    async def test_eims_only_blocks_password_and_legacy_bearer(self):
        self.settings=replace(self.settings,mode='eims')
        self.assertEqual((await self.local_login()).status_code,403)
        token = auth.create_token(1,'admin')
        self.assertEqual((await self.browser.get('/auth/me',headers={'Authorization':'Bearer '+token})).status_code,401)

    async def test_legacy_bearer_is_compatible_in_hybrid(self):
        token=auth.create_token(1,'admin')
        me=await self.browser.get('/auth/me',headers={'Authorization':'Bearer '+token})
        self.assertEqual(me.status_code,200)
        self.assertEqual(me.json()['auth_source'],'bearer')

    async def test_invalid_cookie_never_falls_back_to_other_bearer_identity(self):
        token=auth.create_token(1,'admin')
        response=await self.browser.get('/auth/me',headers={'Authorization':'Bearer '+token,'Cookie':'__Host-fifo_session=invalid'})
        self.assertEqual(response.status_code,401)

    async def test_sso_maps_operator_and_ignores_remote_admin_role(self):
        response,_=await self.sso_login()
        self.assertEqual(response.headers['location'],'/')
        me=(await self.browser.get('/auth/me')).json()
        self.assertEqual((me['id'],me['username'],me['role'],me['auth_source']),
                         (2,'fixture-operator','operator','eims'))
        forbidden=await self.browser.put('/auth/users/1/status',json={'is_active':False},
            headers={'X-CSRF-Token':me['csrf_token'],'Origin':self.settings.public_base_url})
        self.assertEqual(forbidden.status_code,403)
        cookie=response.headers['set-cookie']
        self.assertIn('HttpOnly',cookie)
        self.assertIn('Secure',cookie)
        self.assertIn('SameSite=lax',cookie)
        self.assertNotIn('fixture-access',response.text)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            self.assertNotIn('fixture-access',row.token_payload)

    async def test_sso_can_bind_to_existing_admin_without_role_inference(self):
        self.provider.user_id=1
        response,_=await self.sso_login()
        self.assertEqual(response.headers['location'],'/')
        self.assertEqual((await self.browser.get('/auth/me')).json()['role'],'admin')

    async def test_unbound_disabled_and_missing_users_do_not_create_session(self):
        for user_id in (None,True,3,999999):
            with self.subTest(user_id=user_id):
                self.provider.user_id=user_id
                response,_=await self.sso_login()
                self.assertIn('auth_error=',response.headers['location'])
                self.assertEqual((await self.browser.get('/auth/me')).status_code,401)
        with Session(self.engine) as db:
            self.assertFalse(db.scalars(select(AuthSession)).all())

    async def test_userinfo_subject_mismatch_is_rejected(self):
        self.provider.userinfo_subject='another-eims-user'
        response,_=await self.sso_login()
        self.assertIn('auth_error=',response.headers['location'])
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_callback_replay_is_rejected(self):
        response,state=await self.sso_login()
        self.assertEqual(response.headers['location'],'/')
        replay=await self.browser.get('/auth/eims/callback',params={'state':state,'code':'valid-code'})
        self.assertIn('auth_error=',replay.headers['location'])

    async def test_wrong_state_browser_and_expired_state_are_rejected(self):
        start=await self.browser.get('/auth/eims/start')
        state=self.provider.begin(start.headers['location'])
        bad=await self.browser.get('/auth/eims/callback',params={'state':'wrong','code':'valid-code'})
        self.assertIn('auth_error=',bad.headers['location'])
        bad=await self.browser.get('/auth/eims/callback',params={'state':state,'code':'valid-code'},headers={'Cookie':'__Host-fifo_sso_flow=wrong'})
        self.assertIn('auth_error=',bad.headers['location'])
        # An invalid callback must not erase the browser binding for a newer flow.
        start=await self.browser.get('/auth/eims/start')
        state=self.provider.begin(start.headers['location'])
        with Session(self.engine) as db:
            row=db.get(AuthLoginTransaction,credential_hash(state))
            row.expires_at=utcnow()-timedelta(seconds=1)
            db.commit()
        expired=await self.browser.get('/auth/eims/callback',params={'state':state,'code':'valid-code'})
        self.assertIn('auth_error=',expired.headers['location'])

    async def test_provider_error_is_not_reflected_and_old_identity_is_cleared(self):
        await self.local_login()
        self.provider.status=400
        response,_=await self.sso_login()
        self.assertNotIn('must-not-reflect',response.text+response.headers['location'])
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_cookie_mutations_require_csrf_and_exact_origin(self):
        await self.local_login()
        me=(await self.browser.get('/auth/me')).json()
        route='/auth/users/2/status'
        self.assertEqual((await self.browser.put(route,json={'is_active':False})).status_code,403)
        bad=await self.browser.put(route,json={'is_active':False},headers={'X-CSRF-Token':me['csrf_token'],'Origin':'https://identity.test'})
        self.assertEqual(bad.status_code,403)
        bad=await self.browser.put(route,json={'is_active':False},headers={'X-CSRF-Token':'invalid'})
        self.assertEqual(bad.status_code,403)
        good=await self.browser.put(route,json={'is_active':False},headers={'X-CSRF-Token':me['csrf_token'],'Origin':self.settings.public_base_url})
        self.assertEqual(good.status_code,200)

    async def test_admin_cannot_disable_self(self):
        await self.local_login()
        me=(await self.browser.get('/auth/me')).json()
        response=await self.browser.put('/auth/users/1/status',json={'is_active':False},headers={'X-CSRF-Token':me['csrf_token']})
        self.assertEqual(response.status_code,400)

    async def test_local_role_is_read_again_for_each_request(self):
        await self.sso_login()
        with Session(self.engine) as db:
            db.get(User,2).role='viewer'
            db.commit()
        self.assertEqual((await self.browser.get('/auth/me')).json()['role'],'viewer')

    async def test_inactive_account_rejects_existing_cookie_and_bearer(self):
        await self.local_login('fixture-operator')
        token=auth.create_token(2,'operator')
        with Session(self.engine) as db:
            db.get(User,2).is_active=False
            db.commit()
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)
        self.browser.cookies.clear()
        self.assertEqual((await self.browser.get('/auth/me',headers={'Authorization':'Bearer '+token})).status_code,401)

    async def test_session_expiry_is_enforced(self):
        await self.local_login()
        self.modify_session(expires_at=utcnow()-timedelta(seconds=1))
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_refresh_rotates_token_and_concurrent_reads_share_one_refresh(self):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider.refresh_delay=0.01
        responses=await asyncio.gather(self.browser.get('/auth/me'),self.browser.get('/auth/me'))
        self.assertEqual([item.status_code for item in responses],[200,200])
        self.assertEqual(self.provider.refresh_count,1)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            tokens=TokenCipher(self.settings).decrypt(row.token_payload)
            self.assertEqual(tokens['refresh_token'],'fixture-refresh-1')

    async def test_parallel_validation_waits_before_opening_database_sessions(self):
        await self.sso_login()
        self.modify_session(validated_at=utcnow()-timedelta(seconds=61))
        with Session(self.engine) as db:
            key=db.scalars(select(AuthSession.session_hash)).one()
        entered, release = asyncio.Event(), asyncio.Event()
        original_userinfo=self.provider_client.userinfo
        async def delayed_userinfo(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_userinfo(*args, **kwargs)
        self.provider_client.userinfo=delayed_userinfo
        sessions_opened=0
        def counted_factory():
            nonlocal sessions_opened
            sessions_opened+=1
            return self.factory()
        self.store.session_factory=counted_factory
        first=asyncio.create_task(self.store._validate_eims(key,self.settings,False))
        await asyncio.wait_for(entered.wait(),2)
        waiting=[asyncio.create_task(self.store._validate_eims(key,self.settings,False)) for _ in range(20)]
        await asyncio.sleep(0.02)
        self.assertEqual(sessions_opened,1)
        release.set()
        views=await asyncio.wait_for(asyncio.gather(first,*waiting),3)
        self.assertEqual({view.user_id for view in views},{2})
        self.assertEqual(self.provider.userinfo_count,2)  # Login, then one recheck.
        self.assertEqual(sessions_opened,21)

    async def test_authenticate_waits_before_first_database_checkout(self):
        await self.sso_login()
        self.modify_session(validated_at=utcnow()-timedelta(seconds=61))
        cookie = self.browser.cookies.get('__Host-fifo_session')
        request = Request({'type': 'http', 'method': 'GET', 'path': '/auth/me',
                           'scheme': 'https', 'server': ('fifo.test', 443), 'headers': []})
        entered, release = asyncio.Event(), asyncio.Event()
        original_userinfo = self.provider_client.userinfo

        async def delayed_userinfo(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_userinfo(*args, **kwargs)

        self.provider_client.userinfo = delayed_userinfo
        sessions_opened = 0

        def counted_factory():
            nonlocal sessions_opened
            sessions_opened += 1
            return self.factory()

        self.store.session_factory = counted_factory
        first = asyncio.create_task(self.store.authenticate(cookie, request, self.settings))
        waiting = []
        try:
            await asyncio.wait_for(entered.wait(), 2)
            waiting = [asyncio.create_task(self.store.authenticate(cookie, request, self.settings))
                       for _ in range(20)]
            await asyncio.sleep(0.02)
            self.assertEqual(sessions_opened, 2)  # First read and locked EIMS recheck only.
        finally:
            release.set()
        views = await asyncio.wait_for(asyncio.gather(first, *waiting), 3)
        self.assertEqual({view.user_id for view in views}, {2})
        self.assertEqual(self.provider.userinfo_count, 2)  # Login, then one recheck.
        self.assertEqual(sessions_opened, 22)

    async def test_failed_refresh_revokes_cookie(self):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider.status=400
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            self.assertIsNotNone(row.revoked_at)
            self.assertIsNone(row.token_payload)

    async def test_provider_outage_does_not_grant_access_or_consume_refresh_again(self):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider.status=503
        self.assertEqual((await self.browser.get('/auth/me')).status_code,503)
        with Session(self.engine) as db:
            self.assertIsNone(db.scalars(select(AuthSession)).one().revoked_at)

    async def test_rotated_tokens_survive_userinfo_outage_and_retry_without_refresh(self):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider.userinfo_status=503
        self.assertEqual((await self.browser.get('/auth/me')).status_code,503)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            tokens=TokenCipher(self.settings).decrypt(row.token_payload)
            self.assertEqual(tokens['refresh_token'],self.provider.valid_refresh_token)
            self.assertEqual(self.provider.refresh_count,1)
            self.assertIsNone(row.validated_at)
            self.assertIsNone(row.revoked_at)
        self.assertEqual((await self.browser.get('/auth/me')).status_code,503)
        self.assertEqual(self.provider.refresh_count,1)
        self.provider.userinfo_status=200
        self.assertEqual((await self.browser.get('/auth/me')).status_code,200)
        self.assertEqual(self.provider.refresh_count,1)

    async def test_userinfo_outage_after_rotation_still_rejects_rebinding(self):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider.userinfo_status=503
        self.assertEqual((await self.browser.get('/auth/me')).status_code,503)
        self.provider.userinfo_status=200
        self.provider.user_id=1
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            self.assertIsNotNone(row.revoked_at)
            self.assertIsNone(row.token_payload)

    async def test_pending_id_token_is_validated_after_jwks_recovery(self):
        await self._jwks_outage_after_rotation()

    async def test_pending_id_token_subject_is_not_bypassed_after_jwks_recovery(self):
        await self._jwks_outage_after_rotation(bad_subject=True)

    async def _jwks_outage_after_rotation(self, *, bad_subject=False):
        await self.sso_login()
        self.modify_session(token_expires_at=utcnow()-timedelta(seconds=1))
        self.provider_client._jwks_until=0
        self.provider.jwks_status=503
        if bad_subject:
            self.provider.claim_overrides['sub']='another-subject'
        self.assertEqual((await self.browser.get('/auth/me')).status_code,503)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            tokens=TokenCipher(self.settings).decrypt(row.token_payload)
            self.assertTrue(tokens['_id_token_pending'])
            self.assertEqual(tokens['refresh_token'],self.provider.valid_refresh_token)
            self.assertIsNone(row.validated_at)
        self.provider.jwks_status=200
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401 if bad_subject else 200)
        self.assertEqual(self.provider.refresh_count,1)
        with Session(self.engine) as db:
            row=db.scalars(select(AuthSession)).one()
            if bad_subject:
                self.assertIsNotNone(row.revoked_at)
                self.assertIsNone(row.token_payload)
            else:
                self.assertIsNotNone(row.validated_at)
                self.assertNotIn('_id_token_pending',TokenCipher(self.settings).decrypt(row.token_payload))

    async def test_rebound_identity_revokes_existing_session(self):
        await self.sso_login()
        self.modify_session(validated_at=utcnow()-timedelta(seconds=61))
        self.provider.user_id=1
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)

    async def test_provider_revocation_is_rechecked_on_write(self):
        await self.sso_login()
        me=(await self.browser.get('/auth/me')).json()
        self.provider.userinfo_status=403
        response=await self.browser.put('/auth/users/1/status',json={'is_active':False},headers={'X-CSRF-Token':me['csrf_token']})
        self.assertEqual(response.status_code,401)

    async def test_local_logout_is_csrf_protected_and_cookie_cannot_be_reused(self):
        await self.local_login()
        cookie=self.browser.cookies.get('__Host-fifo_session')
        self.assertEqual((await self.browser.post('/auth/logout')).status_code,403)
        me=(await self.browser.get('/auth/me')).json()
        response=await self.browser.post('/auth/logout',headers={'X-CSRF-Token':me['csrf_token']})
        self.assertEqual(response.json()['redirect_url'],'/')
        reused=await self.browser.get('/auth/me',headers={'Cookie':'__Host-fifo_session='+cookie})
        self.assertEqual(reused.status_code,401)

    async def test_sso_logout_uses_one_time_navigation_and_state_without_json_token(self):
        await self.sso_login()
        me=(await self.browser.get('/auth/me')).json()
        response=await self.browser.post('/auth/logout',headers={'X-CSRF-Token':me['csrf_token']})
        body=response.json()
        self.assertTrue(body['redirect_url'].startswith('/auth/eims/logout/start?'))
        self.assertNotIn('id_token',json.dumps(body))
        self.assertNotIn('fixture-access',json.dumps(body))
        self.assertEqual((await self.browser.get('/auth/me')).status_code,401)
        redirect=await self.browser.get(body['redirect_url'])
        self.assertEqual(redirect.status_code,303)
        query=parse_qs(urlsplit(redirect.headers['location']).query)
        self.assertEqual(query['post_logout_redirect_uri'],[self.settings.logout_callback_uri])
        self.assertIn('id_token_hint',query)
        replay=await self.browser.get(body['redirect_url'])
        self.assertIn('auth_error=',replay.headers['location'])
        # Retry the whole flow to test the legitimate callback's cookie/state pairing.
        await self.sso_login()
        me=(await self.browser.get('/auth/me')).json()
        body=(await self.browser.post('/auth/logout',headers={'X-CSRF-Token':me['csrf_token']})).json()
        redirect=await self.browser.get(body['redirect_url'])
        query=parse_qs(urlsplit(redirect.headers['location']).query)
        callback=await self.browser.get('/auth/eims/logout/callback',params={'state':query['state'][0]})
        self.assertEqual(callback.headers['location'],'/')
        self.assertNotIn('__Host-fifo_sso_flow',self.browser.cookies)

    async def test_delayed_logout_callback_preserves_new_local_session(self):
        state=await self._start_logout()
        await self.local_login('fixture-admin')
        cookie=self.browser.cookies.get('__Host-fifo_session')
        callback=await self.browser.get('/auth/eims/logout/callback',params={'state':state})
        self.assertEqual(callback.headers['location'],'/')
        self.assertEqual(self.browser.cookies.get('__Host-fifo_session'),cookie)
        self.assertEqual((await self.browser.get('/auth/me')).status_code,200)
        replay=await self.browser.get('/auth/eims/logout/callback',params={'state':state})
        self.assertIn('auth_error=',replay.headers['location'])
        self.assertEqual((await self.browser.get('/auth/me')).status_code,200)

    async def test_old_logout_callback_preserves_new_sso_flow(self):
        state=await self._start_logout()
        start=await self.browser.get('/auth/eims/start')
        new_state=self.provider.begin(start.headers['location'])
        binding=self.browser.cookies.get('__Host-fifo_sso_flow')
        old=await self.browser.get('/auth/eims/logout/callback',params={'state':state})
        self.assertIn('auth_error=',old.headers['location'])
        self.assertEqual(self.browser.cookies.get('__Host-fifo_sso_flow'),binding)
        callback=await self.browser.get('/auth/eims/callback',params={'state':new_state,'code':'valid-code'})
        self.assertEqual(callback.headers['location'],'/')
        self.assertEqual((await self.browser.get('/auth/me')).status_code,200)

    async def _start_logout(self):
        await self.sso_login()
        csrf=(await self.browser.get('/auth/me')).json()['csrf_token']
        body=(await self.browser.post('/auth/logout',headers={'X-CSRF-Token':csrf})).json()
        redirect=await self.browser.get(body['redirect_url'])
        return parse_qs(urlsplit(redirect.headers['location']).query)['state'][0]
