import asyncio
import json
import logging
import decimal
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from database import async_session, init_db
from migrate import migrate
from routers import products, batches, sales, reports, auth, finance_masters
from routers import eims_auth
from routers import financial_control
from auth import seed_users
from services.accounting_periods import ensure_period_snapshot, previous_month_end
from services.auth_sessions import store as auth_session_store
from services.eims_sso import SsoError, close_client
from services.auth_logging import protect_oauth_access_logs
from services.product_images import ProductImageFiles

BASE_DIR = Path(__file__).parent
logger = logging.getLogger(__name__)
protect_oauth_access_logs()


async def _monthly_snapshot_worker() -> None:
    while True:
        try:
            async with async_session() as db:
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                await ensure_period_snapshot(db, previous_month_end(now.date()), now)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("月末库存快照生成失败")
        await asyncio.sleep(3600)


async def _auth_session_worker() -> None:
    while True:
        try:
            await auth_session_store.cleanup()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("认证临时记录清理失败")
        await asyncio.sleep(3600)


class DecimalJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=lambda o: float(o) if isinstance(o, decimal.Decimal) else str(o),
        ).encode("utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await migrate()
    await seed_users()
    async with async_session() as db:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        await ensure_period_snapshot(db, previous_month_end(now.date()), now)
    worker = asyncio.create_task(_monthly_snapshot_worker())
    auth_worker = asyncio.create_task(_auth_session_worker())
    try:
        yield
    finally:
        for task in (worker, auth_worker):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await close_client()


app = FastAPI(
    title="跨境电商FIFO库存成本核算系统",
    description="FIFO先进先出 + 批次管理，准确计算每笔订单的真实成本和利润",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=DecimalJSONResponse,
)

app.include_router(auth.router)
app.include_router(eims_auth.router)
app.add_exception_handler(SsoError, eims_auth.sso_error_handler)
app.include_router(products.router)
app.include_router(batches.router)
app.include_router(sales.router)
app.include_router(reports.router)
app.include_router(finance_masters.router)
app.include_router(financial_control.router)

uploads_dir = BASE_DIR / "uploads"
uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", ProductImageFiles(directory=str(uploads_dir)), name="uploads")


@app.get("/", response_class=HTMLResponse)
async def root():
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/financial-control.js", include_in_schema=False)
async def financial_control_script():
    return FileResponse(BASE_DIR / "static" / "financial-control.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
