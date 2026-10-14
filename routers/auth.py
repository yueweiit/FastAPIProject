import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Store, User
from schemas import LoginRequest, LoginResponse, UserResponse
from auth import hash_password, verify_password, create_token, get_current_user, RequireAdmin
from services.auth_sessions import check_origin, cookie_name, revoke_user_sessions, set_cookie, store
from services.eims_sso import get_settings

router = APIRouter(prefix="/auth", tags=["认证"])


class UpdateRoleRequest(BaseModel):
    role: str


class PasswordRequest(BaseModel):
    old_password: str | None = None
    new_password: str


class UpdateUserStoreRequest(BaseModel):
    store_id: int | None = None


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "operator"


class UpdateUserStatusRequest(BaseModel):
    is_active: bool


@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """用户登录"""
    settings = get_settings()
    if not settings.local_enabled:
        raise HTTPException(status_code=403, detail="当前系统使用EIMS登录")
    check_origin(request, settings)
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not await asyncio.to_thread(verify_password, data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    token = create_token(user.id, user.role)
    cookie, _ = await store.create_session(user.id, settings, expected_password_hash=user.password_hash)
    await store.revoke(request.cookies.get(cookie_name(request, settings)), settings)
    set_cookie(response, request, settings, cookie)
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(token=token, user_id=user.id, username=user.username, role=user.role)


@router.post("/users", response_model=UserResponse)
async def create_user(
    data: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireAdmin),
):
    """创建用户 - 仅管理员"""
    if data.role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="无效角色")

    username = data.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    if len(data.password) < 3:
        raise HTTPException(status_code=400, detail="密码至少3位")

    user = User(
        username=username,
        password_hash=hash_password(data.password),
        role=data.role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    """用户列表 - 仅管理员"""
    result = await db.execute(
        select(User).options(selectinload(User.store)).order_by(User.id.desc())
    )
    return [
        UserResponse(
            id=item.id,
            username=item.username,
            role=item.role,
            is_active=item.is_active,
            store_id=item.store_id,
            store_name=item.store.name if item.store else None,
            created_at=item.created_at,
        )
        for item in result.scalars().all()
    ]


@router.put("/users/{user_id}/store")
async def update_user_store(
    user_id: int,
    data: UpdateUserStoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireAdmin),
):
    """Bind a non-admin account to one active store."""
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target_user.role == "admin":
        raise HTTPException(status_code=400, detail="管理员账号不能绑定店铺")

    store = None
    if data.store_id is not None:
        store = await db.get(Store, data.store_id)
        if not store:
            raise HTTPException(status_code=404, detail="店铺不存在")
        if not store.is_active:
            raise HTTPException(status_code=400, detail="不能绑定已停用的店铺")

    target_user.store_id = store.id if store else None
    await db.commit()
    return {
        "ok": True,
        "store_id": target_user.store_id,
        "store_name": store.name if store else None,
    }


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    data: UpdateUserStatusRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireAdmin),
):
    if user_id == current_user.id and not data.is_active:
        raise HTTPException(status_code=400, detail="不能停用自己的账号")
    user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = data.is_active
    if not data.is_active:
        await revoke_user_sessions(db, user_id)
    await db.commit()
    return {"ok": True, "is_active": user.is_active}


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
    if data.role == "admin":
        user.store_id = None
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

    user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = hash_password(data.new_password)
    await revoke_user_sessions(db, user_id)
    await db.commit()
    return {"ok": True}


@router.put("/me/password")
async def change_my_password(
    data: PasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """修改自己的密码"""
    user = await db.get(User, user.id, with_for_update=True, populate_existing=True)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(data.new_password) < 3:
        raise HTTPException(status_code=400, detail="密码至少3位")

    user.password_hash = hash_password(data.new_password)
    await revoke_user_sessions(db, user.id)
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
async def me(request: Request, response: Response, user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    session = getattr(request.state, "auth_session", None)
    response.headers["Cache-Control"] = "no-store"
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "store_id": user.store_id,
        "is_active": user.is_active,
        "csrf_token": session.csrf_token if session is not None else None,
        "auth_source": session.source if session is not None else "bearer",
    }
