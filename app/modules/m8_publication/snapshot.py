"""发布快照：冻结时刻的报告 CSV、清单 JSON 与各自 sha256。

报告与清单复用 M7 的导出函数，保证「页面看的、差异归因用的、导出下载的」
同一份代码、同一版数字；发布后文本原样落库，之后源批次重算也不影响快照。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from ..m6_diff import differences as differences_mod
from ..m7_export import csv_out, manifest_out


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_safe(value):
    """对账结论里的 Decimal 全部转字符串，保证 JSON 可序列化且不丢精度。"""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def build_snapshot(store, run: dict, settings) -> dict:
    records = store.records(run["run_id"])
    matches = store.matches(run["run_id"])
    reconciliation = differences_mod.reconcile(records, matches, settings)
    report_csv = csv_out.report_text(records, matches, reconciliation)
    manifest_json = manifest_out.manifest_text(run, reconciliation)
    # 在清单末尾附冻结对账结论，供差异接口原样回放（与导出同版）。
    manifest_payload = json.loads(manifest_json)
    manifest_payload["reconciliation"] = _json_safe(reconciliation)
    manifest_json = json.dumps(manifest_payload, ensure_ascii=False, indent=2,
                               sort_keys=True)
    return {
        "report_csv": report_csv,
        "manifest_json": manifest_json,
        "report_sha256": sha256_text(report_csv),
        "manifest_sha256": sha256_text(manifest_json),
        "reconciliation": reconciliation,
    }
