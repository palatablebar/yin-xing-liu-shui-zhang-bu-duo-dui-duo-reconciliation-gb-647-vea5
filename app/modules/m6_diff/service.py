"""差异服务：对账结论、批次历史、单批详情。

已有关账发布时，差异接口默认返回发布版本冻结的对账结论，
与导出文件同版；需要查看工作区最新批次时显式传 scope=workspace。
"""
from __future__ import annotations

import json

from ...errors import DomainError
from . import differences, history


def _frozen_payload(publication: dict) -> dict:
    manifest = json.loads(publication["manifest_json"])
    reconciliation = manifest.get("reconciliation", {})
    return {
        "run_id": publication["run_id"],
        "publication_id": publication["publication_id"],
        "version_no": publication["version_no"],
        "cutoff_at": publication["cutoff_at"],
        "frozen": True,
        "totals": reconciliation.get("totals", manifest.get("totals", {})),
        "counts": reconciliation.get("counts", manifest.get("counts", {})),
        "identities": reconciliation.get("identities", []),
        "differences": reconciliation.get("differences", []),
        "tier_names": reconciliation.get(
            "tier_names", differences.TIER_NAMES
        ),
        "report_sha256": publication["report_sha256"],
        "manifest_sha256": publication["manifest_sha256"],
    }


def payload(store, run: dict | None, settings, scope: str = "published") -> dict:
    if scope == "published":
        publication = store.latest_publication()
        if publication is not None and publication["status"] == "published":
            return _frozen_payload(publication)
    if not run:
        return {
            "run_id": None,
            "publication_id": None,
            "frozen": False,
            "totals": {},
            "counts": {},
            "identities": [],
            "differences": [],
            "tier_names": differences.TIER_NAMES,
        }
    records = store.records(run["run_id"])
    matches = store.matches(run["run_id"])
    result = differences.reconcile(records, matches, settings)
    result["run_id"] = run["run_id"]
    result["publication_id"] = None
    result["frozen"] = False
    # 金额统一转十进制字符串，与冻结版本及导出文件保持同型同值。
    result["totals"] = {
        key: format(value, "f") for key, value in result["totals"].items()
    }
    return result


def batches_payload(store) -> dict:
    return {"count": len(store.list_runs(20)), "batches": history.batches(store)}


def batch_detail(store, run_id: str, settings) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise DomainError("E_NO_RUN", "批次不存在", 404)
    result = payload(store, run, settings, scope="workspace")
    result["manifest"] = history.manifest(store, run, result.get("totals"))
    return result
