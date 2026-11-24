from flask import jsonify

LABELS = {
    "FILE_TOO_LARGE": "文件超过 256 KiB",
    "FILE_EMPTY": "文件没有内容",
    "FILE_BLANK": "文件只有空白",
    "HEADER_ONLY": "文件只有标题行",
    "HEADER_MISMATCH": "列名与字段字典不一致",
    "ENCODING_INVALID": "请使用 UTF-8 CSV",
    "CSV_MALFORMED": "CSV 引号或行格式异常",
    "ROW_LIMIT": "文件超过 2000 行",
    "FILE_REQUIRED": "请选择 CSV 文件",
    "RUN_NOT_FOUND": "批次不存在",
    "REVIEW_FIELDS": "请填写 3 至 200 字复核意见",
    "STALE_REVIEW": "批次已更新，请刷新",
    "RUN_FINALIZED": "批次已签发",
    "UNRESOLVED_ISSUES": "存在待处理差异",
    "SAMPLE_UNKNOWN": "样例不存在",
    "STAGE_UNKNOWN": "处理阶段不存在",
    "SETTLEMENT_PAYLOAD": "结算请求字段不完整",
    "BAD_REGION": "验收区域坐标无效",
    "REGION_TOO_LARGE": "验收区域超过可结算像元上限",
    "BAD_TILE": "瓦片字段无效",
    "TILE_ID_DUPLICATE": "瓦片编号重复",
    "GRADE_INVALID": "验收等级必须是非负整数",
    "TILE_OUT_OF_REGION": "瓦片越出验收区域",
    "PAID_LEDGER_INVALID": "已付款账与当前区域或供应商不符",
    "PARTITION_BROKEN": "已付、可付、争议范围无法守恒，关账被拒绝",
    "LEDGER_NOT_FOUND": "结算账不存在",
    "STALE_SETTLEMENT": "结算结果已过期，请按最新付款账重算",
    "LEDGER_FINALIZED": "结算账已关账，请先重开再受理新证据",
    "LEDGER_NOT_CLOSED": "结算账尚未关账，不能受理迟到瓦片",
    "REGION_MOVED": "迟到证据不允许更换验收区域",
}


def response(error):
    code = error.code
    status = (
        404
        if code in {"RUN_NOT_FOUND", "SAMPLE_UNKNOWN", "STAGE_UNKNOWN", "LEDGER_NOT_FOUND"}
        else (
            409
            if code
            in {
                "STALE_REVIEW",
                "RUN_FINALIZED",
                "UNRESOLVED_ISSUES",
                "STALE_SETTLEMENT",
                "LEDGER_FINALIZED",
                "LEDGER_NOT_CLOSED",
            }
            else 413 if code == "FILE_TOO_LARGE" else 422
        )
    )
    return jsonify(code=code, message=LABELS.get(code, code)), status
