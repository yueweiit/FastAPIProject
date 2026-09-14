import asyncio
import json
import logging
import decimal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import async_session, init_db
from migrate import migrate
from routers import products, batches, sales, reports, auth, finance_masters
from auth import seed_users
from services.accounting_periods import auto_confirm_expired_periods

BASE_DIR = Path(__file__).parent
logger = logging.getLogger(__name__)


async def _accounting_period_worker() -> None:
    while True:
        try:
            async with async_session() as db:
                await auto_confirm_expired_periods(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("会计期间自动确认检查失败")
        await asyncio.sleep(60)


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
        await auto_confirm_expired_periods(db)
    worker = asyncio.create_task(_accounting_period_worker())
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="跨境电商FIFO库存成本核算系统",
    description="FIFO先进先出 + 批次管理，准确计算每笔订单的真实成本和利润",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=DecimalJSONResponse,
)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(batches.router)
app.include_router(sales.router)
app.include_router(reports.router)
app.include_router(finance_masters.router)

uploads_dir = BASE_DIR / "uploads"
uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")


@app.get("/", response_class=HTMLResponse)
async def root():
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)
