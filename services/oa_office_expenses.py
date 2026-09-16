"""Read the approved LatínGo office-space allocation from the OA database."""

import json
import logging
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg


logger = logging.getLogger(__name__)

OA_OPERATION_PROCESS_CODE = "PROC-E7BC3316-E618-4812-BDCC-7A655A7C694B"
OA_OFFICE_SPACE_EXPENSE = "办公场地总费用Gastos de local de oficinas"
OA_OFFICE_SPACE_TABLE_ID = "TableField_9KUR3Y1BQYW0"
LATIN_GO_DEPARTMENT_ID = "1089990115"
OFFICE_DETAIL_VALUES = {"租金Alquiler", "电费Electricidad"}


class OaExpenseSourceError(RuntimeError):
    """The configured OA source cannot be read."""


def _oa_database_config() -> dict[str, str] | None:
    required = {
        "host": os.getenv("OA_DB_HOST", "").strip(),
        "database": os.getenv("OA_DB_DATABASE", "").strip(),
        "user": os.getenv("OA_DB_USER", "").strip(),
        "password": os.getenv("OA_DB_PASSWORD", ""),
    }
    if not all(required.values()):
        return None
    required["port"] = os.getenv("OA_DB_PORT", "5432").strip() or "5432"
    return required


def _json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _department_id(cell: dict[str, Any] | None) -> str:
    if not isinstance(cell, dict):
        return ""
    identity = cell.get("extendValue")
    if isinstance(identity, list) and identity and isinstance(identity[0], dict):
        return str(identity[0].get("id") or identity[0].get("itemId") or "").strip()
    return ""


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None


def _office_space_total(form_component_values: Any) -> Decimal:
    total = Decimal("0")
    for table in _json_list(form_component_values):
        if table.get("id") != OA_OFFICE_SPACE_TABLE_ID:
            continue
        for row in _json_list(table.get("value")):
            cells = row.get("rowValue")
            if not isinstance(cells, list):
                continue
            department = next(
                (cell for cell in cells if isinstance(cell, dict) and cell.get("label") == "部门"),
                None,
            )
            detail = next(
                (cell for cell in cells if isinstance(cell, dict) and cell.get("label") == "费用明细Detalle de gastos"),
                None,
            )
            amount = next(
                (cell for cell in cells if isinstance(cell, dict) and cell.get("label") == "金额（元）Monto (yuan)"),
                None,
            )
            if (
                _department_id(department) != LATIN_GO_DEPARTMENT_ID
                or not isinstance(detail, dict)
                or detail.get("value") not in OFFICE_DETAIL_VALUES
                or not isinstance(amount, dict)
            ):
                continue
            value = _decimal(amount.get("value"))
            if value is not None:
                total += value
    return total


async def office_space_totals_by_application_date(
    start_date: date, end_date: date
) -> dict[date, Decimal]:
    """Return completed, agreed office-space totals keyed by application date.

    An unconfigured source is expected in local development and returns no data.
    A configured source that cannot be read raises instead of silently reporting zero.
    """
    config = _oa_database_config()
    if config is None:
        logger.warning("OA database is not configured; office-space expense is unavailable")
        return {}

    query = """
        SELECT
            request_field.value ->> 'value' AS request_date,
            source.form_component_values
        FROM ding_approval_instance AS source
        JOIN LATERAL jsonb_array_elements(
            COALESCE(source.form_component_values, '[]'::jsonb)
        ) AS request_field(value)
          ON request_field.value ->> 'name' = '申请日期Fecha de solicitud'
        WHERE source.deleted_at IS NULL
          AND source.process_code = $1
          AND UPPER(COALESCE(source.status, '')) = 'COMPLETED'
          AND LOWER(COALESCE(source.result, '')) IN ('agree', 'approved', '同意', '通过')
          AND (request_field.value ->> 'value')::date >= $2::date
          AND (request_field.value ->> 'value')::date < $3::date
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(
                  COALESCE(source.form_component_values, '[]'::jsonb)
              ) AS management_field(value)
              WHERE management_field.value ->> 'name' = '管理支出Gastos de operación'
                AND management_field.value ->> 'value' = $4
          )
    """
    try:
        connection = await asyncpg.connect(
            host=config["host"],
            port=int(config["port"]),
            database=config["database"],
            user=config["user"],
            password=config["password"],
            command_timeout=10,
        )
        try:
            rows = await connection.fetch(
                query,
                OA_OPERATION_PROCESS_CODE,
                start_date,
                end_date,
                OA_OFFICE_SPACE_EXPENSE,
            )
        finally:
            await connection.close()
    except Exception as exc:
        raise OaExpenseSourceError("无法读取 OA 办公场地费用数据") from exc

    totals: defaultdict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        try:
            request_date = date.fromisoformat(str(row["request_date"]))
        except (TypeError, ValueError):
            continue
        total = _office_space_total(row["form_component_values"])
        if total:
            totals[request_date] += total
    return dict(totals)
