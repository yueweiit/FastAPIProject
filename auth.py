"""JWT 认证与权限控制"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, engine, async_session, Base
from models import User
from services.auth_sessions import cookie_name, store
from services.eims_sso import get_settings

# JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET", "latingo-jwt-secret-key-2024")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_token(user_id: int, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> User:
    """Browser sessions first; legacy Bearer is only accepted in local/hybrid."""
    settings = get_settings()
    session_cookie = cookie_name(request, settings) if request is not None else None
    if request is not None and session_cookie in request.cookies:
        session = await store.authenticate(request.cookies[session_cookie], request, settings)
        request.state.auth_session = session
        user_id = session.user_id
    else:
        if not settings.local_enabled or credentials is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        try:
            payload = decode_token(credentials.credentials)
            user_id = int(payload["sub"])
        except (JWTError, KeyError, ValueError, TypeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的认证令牌")

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在或已停用")
    return user


class RoleChecker:
    """角色权限检查器"""
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    async def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role not in self.allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user


# 常用权限依赖
RequireAdmin = RoleChecker(["admin"])
RequireOperator = RoleChecker(["admin", "operator"])  # 可写权限
RequireAnyRole = RoleChecker(["admin", "operator", "viewer"])


async def seed_users():
    """初始化默认用户"""
    async with async_session() as session:
        result = await session.execute(select(User))
        if result.scalars().first():
            return  # 已有用户则跳过

        users = [
            User(username="admin", password_hash=hash_password("admin123"), role="admin"),
            User(username="operator", password_hash=hash_password("op123"), role="operator"),
            User(username="viewer", password_hash=hash_password("view123"), role="viewer"),
        ]
        session.add_all(users)
        await session.commit()
        print("已创建初始用户，请按运行手册立即设置安全密码")
