"""Read the two additional payroll components when FIFO connects directly to OA."""

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import NamedTuple

from services.oa_office_expenses import (
    _APPROVED_OPERATION_FILTER, _cell, _component_name, _decode,
    _department_name, _fetch_oa_forms, _json_list,
)
from services.oa_operating_expenses import OperatingExpenseWarning


_SOCIAL_HOUSING = re.compile(r"^社保\s*[+＋]?\s*公积金")
_EMPLOYER_AMOUNT = re.compile(r"^社保\s*[+＋]\s*公积金\s*[（(]公司[）)]")
_CURRENCY_LABEL = re.compile(r"^(?:(?:币种|货币(?:类型|种类)?)(?:currency|moneda|tipodemoneda)?|currency|moneda|tipodemoneda)$", re.I)
_CNY = re.compile(r"(?:\b(?:CNY|RMB|CNH|yuan)\b|人民币|(?:^|[（(])元(?:[）)]|$))", re.I | re.ASCII)
_FOREIGN = re.compile(
    r"\b(?:MXN|USD|EUR|HKD|GBP|JPY|COP|PEN|CLP|BRL|VES|KRW|CAD|AUD|TWD|SGD|CHF|INR|THB|VND|IDR|MYR|AED|SAR|ZAR|RUB|ARS|NZD)\b"
    r"|比索|美元|美金|欧元|港[币幣元]|英镑|日[元圆]|韩元|加元|澳元|台币|泰铢|日圓|peso|d[oó]lar|dollar|MX\$|US\$", re.I | re.ASCII,
)


def _list(value):
    value = _decode(value)
    return value if isinstance(value, list) else []


def _choice(field):
    value = (field or {}).get("value")
    if isinstance(value, str) and not value.lstrip().startswith("["):
        return value.strip()
    options = _list(value)
    return options[0].strip() if len(options) == 1 and isinstance(options[0], str) else ""


def _selection(form):
    def option(pattern):
        return _choice(next((f for f in form if re.search(pattern, _component_name(f))), None))

    management = option(r"(?i)^管理支出(?:\s*Gastos de operaci[oó]n)?$")
    tax = option(r"(?i)^税费(?:\s*Impuestos)?$")
    if (not management or management.startswith("税费")) and re.match(r"^(?:薪资税/个税|个人所得税|个税)(?:$|[ (（A-Za-z])", tax):
        return "individual_income_tax"
    return "employer_contributions" if _SOCIAL_HOUSING.match(management) else None


def _table_rows(value):
    table = _decode(value)
    if isinstance(table, dict):
        table = next((rows for key in ("rows", "details", "value") if (rows := _list(table.get(key)))), [])
    for raw in _list(table):
        row = _decode(raw)
        number = ""
        if isinstance(row, dict):
            number = str(row["rowNumber"]).strip() if row.get("rowNumber") is not None else ""
            cells = next((items for key in ("rowValue", "values", "value") if (items := _json_list(row.get(key)))), [])
        else:
            cells = _json_list(row)
        yield number, cells


class _PayrollLine(NamedTuple):
    number: str
    shop: str | None
    amounts: list[dict]
    currencies: list[str]


def _field_key(cell):
    return str(cell.get("id") or cell.get("key") or "")


_ZERO_AMOUNT = re.compile(r"[+-]?(?:0+(?:,000)*(?:\.0*)?|\.0+)(?:e[+-]?\d+)?", re.I | re.ASCII)


def _amount_currencies(amount, cells):
    explicit = [v for key in ("currency", "currencyCode") if isinstance(v := amount.get(key), str) and v.strip()]
    hints = [v for key in ("label", "name", "unit", "value") if isinstance(v := amount.get(key), str)
             and (_CNY.search(v) or _FOREIGN.search(v))]
    return explicit + _currencies(cells) + hints


def _payroll_rows(table, kind):
    def lines(value):
        for number, cells in _table_rows(value):
            department = _cell(cells, "部门", "departamento") or next(
                (c for c in cells if _field_key(c).startswith("DepartmentField_")), None)
            shop = _department_name(department)
            money = [c for c in cells if isinstance(c.get("value"), (str, int, float))
                     and not isinstance(c["value"], bool) and str(c["value"]).strip()
                     and (_field_key(c).startswith("MoneyField_")
                          or re.search(r"金额|monto|importe", _component_name(c), re.I)
                          or _SOCIAL_HOUSING.match(_component_name(c)))]
            if shop is None and all(_ZERO_AMOUNT.fullmatch(str(c["value"]).strip()) for c in money):
                continue  # Empty selector and default zeros in a hidden table.
            amounts = money if kind == "individual_income_tax" else [
                c for c in money if _field_key(c) == "MoneyField_IR71DYOSO8O0" or _EMPLOYER_AMOUNT.match(_component_name(c))]
            currencies = _amount_currencies(amounts[0] if len(amounts) == 1 else {}, cells)
            yield _PayrollLine(number, shop, amounts, currencies)

    current = list(lines(table.get("details")))
    if not current:
        return list(lines(table.get("value")))
    archived = list(lines(table.get("value")))
    if not archived:
        return current

    by_number = all(r.number for r in current) and all(r.number for r in archived)

    def unique_rows(rows):
        index = {}
        for row in rows:
            key = row.number if by_number else row.shop
            if key:
                index[key] = None if key in index else row
        return index

    originals, mirrors = unique_rows(current), unique_rows(archived)
    for i, row in enumerate(current):
        if row.shop == "" or len(row.amounts) > 1:
            continue
        key = row.number if by_number else row.shop
        other = mirrors.get(key) if originals.get(key) is row else None
        if not key and not by_number and len(current) == len(archived) == 1:
            other = archived[0]
        if not other or not other.shop or len(other.amounts) != 1 or (row.shop and row.shop != other.shop):
            continue
        if row.amounts and str(row.amounts[0]["value"]) != str(other.amounts[0]["value"]):
            continue
        # Preserve row-level currency metadata even if it exists in only one representation.
        base = row if row.shop and row.amounts else other
        current[i] = base._replace(currencies=row.currencies + other.currencies)
    return current


def _lines(form, kind):
    tax = kind == "individual_income_tax"
    dedicated, generic = [], []
    for table in form:
        label = _component_name(table)
        if (tax and label.startswith("薪酬税费总支出")) or (not tax and (
            table.get("id") == "TableField_G2ELEALN0S80" or _SOCIAL_HOUSING.match(label)
        )):
            dedicated.extend(_payroll_rows(table, kind))
        elif table.get("id") == "TableField_1T6MS9XEQUE80" or re.fullmatch(r"明细(?:detalle)?", "".join(label.split()), re.I):
            generic.extend(_payroll_rows(table, kind))
    rows = dedicated or (generic if tax else [])
    if not rows:
        yield "", None, []
    for row in rows:
        yield row.shop or "", row.amounts[0] if len(row.amounts) == 1 else None, row.currencies


def _currencies(fields):
    return [f["value"].strip() for f in fields if _CURRENCY_LABEL.match("".join(_component_name(f).split()))
            and isinstance(f.get("value"), str) and f["value"].strip()]


def _cny_amount(amount, currencies, form_currencies):
    if not amount or amount.get("value") in ("", None):
        return None, "PAYROLL_DETAIL_UNRESOLVED"
    if any(_FOREIGN.search(v) or not _CNY.search(v) for v in (currencies or form_currencies)):
        return None, "EXPENSE_CURRENCY_UNSUPPORTED"
    original = amount["value"]
    # JSON numeric integers outside this range cannot survive the HTTP reader exactly.
    if isinstance(original, (int, float)) and abs(original) > 2**53 - 1:
        return None, "PAYROLL_DETAIL_UNRESOLVED"
    raw = str(original).strip()
    if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", raw, re.ASCII):
        raw = raw.replace(",", "")
    match = re.fullmatch(r"[+-]?(\d*)(?:\.(\d*))?(?:[Ee][+-]?\d+)?", raw, re.ASCII)
    if not match or not 1 <= len(match[1]) + len(match[2] or "") <= 100:
        return None, "PAYROLL_DETAIL_UNRESOLVED"
    try:
        value = Decimal(raw)
        if value.is_finite() and abs(value.as_tuple().exponent) <= 100:
            return value, None
    except InvalidOperation:
        pass
    return None, "PAYROLL_DETAIL_UNRESOLVED"


async def payroll_supplements_by_store(start: date, end: date) -> dict:
    # end is exclusive. Tax filed in the month starting at end still belongs to this report.
    rows = await _fetch_oa_forms(f"""
        SELECT source.process_instance_id, source.form_component_values,
               date_trunc('month', source.create_time AT TIME ZONE 'Asia/Shanghai')::date AS submitted
          FROM ding_approval_instance source
         WHERE {_APPROVED_OPERATION_FILTER}
           AND (source.create_time IS NULL OR
                (source.create_time >= ($3::date::timestamp AT TIME ZONE 'Asia/Shanghai')
                 AND source.create_time < (($4::date + interval '1 month') AT TIME ZONE 'Asia/Shanghai')))
           AND EXISTS (SELECT 1 FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(source.form_component_values) = 'array'
                    THEN source.form_component_values ELSE '[]'::jsonb END) f
               WHERE btrim(COALESCE(NULLIF(f->>'name',''), f->>'label')) ~ '^(税费|管理支出)'
                 AND (f->>'value') ~ '(个税|个人所得税|公积金)')
    """, start, end)
    values = {"individual_income_tax": {}, "employer_contributions": {}}
    warnings = []
    for row in rows:
        form = _json_list(row["form_component_values"])
        kind = _selection(form)
        if kind is None:
            continue
        month = row["submitted"]
        if month and kind == "individual_income_tax":
            month = (month - timedelta(days=1)).replace(day=1)
        if month and not start <= month < end:
            continue
        problems = defaultdict(set)
        form_currencies = _currencies(form)
        for shop, amount, currencies in _lines(form, kind):
            value, issue = _cny_amount(amount, currencies, form_currencies)
            if not month:
                problems["APPLICATION_MONTH_UNRESOLVED"].add(shop)
            elif not shop or issue:
                problems[issue if shop else "PAYROLL_DETAIL_UNRESOLVED"].add(shop)
            else:
                bucket = values[kind].setdefault(shop, {})
                total = bucket.get(month, Decimal(0))
                with localcontext() as ctx:
                    # Match the provider's exact decimal addition, including carries and differing scales.
                    highest_digit = max(total.adjusted(), value.adjusted())
                    lowest_digit = min(total.as_tuple().exponent, value.as_tuple().exponent)
                    ctx.prec = highest_digit - lowest_digit + 2
                    bucket[month] = total + value
        for code, shops in problems.items():
            warnings.append(OperatingExpenseWarning(
                code=code, expenseType=kind, processInstanceId=row["process_instance_id"],
                departmentNames=[] if "" in shops else sorted(shops), month=month.strftime("%Y-%m") if month else None,
            ))
    return {**values, "expense_review_warnings": warnings}
