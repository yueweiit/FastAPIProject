"""Optional local specification in sales files; platform variant names stay separate."""

LOCAL_SPECIFICATION_HEADERS = ("规格/型号", "本地商品规格", "商品规格", "规格")


def source_local_specification(row):
    for key in LOCAL_SPECIFICATION_HEADERS:
        if key in row:
            value = row[key]
            return str(value).strip() if value is not None else ""
    return ""
