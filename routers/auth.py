from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import User
from schemas import LoginRequest, LoginResponse, UserResponse
from auth import hash_password, verify_password, create_token, get_current_user, RequireAdmin

router = APIRouter(prefix="/auth", tags=["认证"])


class UpdateRoleRequest(BaseModel):
    role: str


class PasswordRequest(BaseModel):
    old_password: str | None = None
    new_password: str


@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录"""
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    token = create_token(user.id, user.role)
    return LoginResponse(token=token, user_id=user.id, username=user.username, role=user.role)


@router.post("/register", response_model=LoginResponse)
async def register(
    username: str,
    password: str,
    role: str = "operator",
    db: AsyncSession = Depends(get_db),
):
    """注册 - 默认运营身份，只能选 operator 或 viewer"""
    if role not in ("operator", "viewer"):
        raise HTTPException(status_code=400, detail="注册角色只能是operator或viewer")

    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    if len(password) < 3:
        raise HTTPException(status_code=400, detail="密码至少3位")

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_token(user.id, user.role)
    return LoginResponse(token=token, user_id=user.id, username=user.username, role=user.role)


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    """用户列表 - 仅管理员"""
    result = await db.execute(select(User).order_by(User.id.desc()))
    return result.scalars().all()


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    data: UpdateRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireAdmin),
):
    """修改用户角色 - 仅管理员，不能改自己"""
    if data.role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="无效角色")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.role = data.role
    await db.commit()
    return {"ok": True}


@router.put("/users/{user_id}/password")
async def reset_user_password(
    user_id: int,
    data: PasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireAdmin),
):
    """管理员重置用户密码"""
    if len(data.new_password) < 3:
        raise HTTPException(status_code=400, detail="密码至少3位")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"ok": True}


@router.put("/me/password")
async def change_my_password(
    data: PasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """修改自己的密码"""
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(data.new_password) < 3:
        raise HTTPException(status_code=400, detail="密码至少3位")

    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireAdmin),
):
    """删除用户 - 仅管理员，不能删除自己"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.delete(user)
    await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return {"id": user.id, "username": user.username, "role": user.role}
