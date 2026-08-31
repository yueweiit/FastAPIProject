"""JWT 认证与权限控制"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, engine, async_session, Base
from models import User

# JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET", "latingo-jwt-secret-key-2024")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_token(user_id: int, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从JWT解析当前用户"""
    try:
        payload = decode_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的认证令牌")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
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
        print("已创建默认用户: admin/admin123, operator/op123, viewer/view123")
