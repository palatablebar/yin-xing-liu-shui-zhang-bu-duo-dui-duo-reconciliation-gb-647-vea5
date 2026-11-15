"""关账发布服务：截止时点选择、补录影响预览、幂等发布与版本查询。

发布流程（全部在单个串行事务内完成，中断只可能留下同 id 的 publishing 残留，
重试时沿用原版本号覆盖，不会产生第二个批次）：

1. 解析并归一化 cutoff；
2. 选出 cutoff 之前最新的已匹配批次，截止后才到的流水/凭证一律不进本版；
3. 以最近一次发布为基准计算补录影响（匹配组、未匹配项、手续费）；
4. 复用 M7 导出函数冻结报告 CSV 与清单 JSON，计算 sha256；
5. 落库并把上一版标记为 superseded，旧版完整文本保留可下载。
"""
from __future__ import annotations

from ...errors import DomainError
from ...manifest import utc_now_iso
from . import cutoff as cutoff_mod
from . import ids, impact as impact_mod
from . import snapshot as snapshot_mod

LIST_FIELDS = (
    "publication_id", "version_no", "run_id", "cutoff_at", "published_at",
    "status", "superseded_by", "report_sha256", "manifest_sha256",
)


def _brief(row: dict) -> dict:
    data = {field: row[field] for field in LIST_FIELDS}
    data["impact_summary"] = impact_mod.impact_summary(row.get("impact") or {})
    return data


def _select_baseline_run(store, latest: dict | None):
    if not latest:
        return None
    return store.get_run(latest["run_id"])


def preview(store, settings, cutoff_raw: str) -> dict:
    """只读预览：本次 cutoff 会发布哪个批次，相对上一版影响哪些内容。"""
    cutoff_at = cutoff_mod.parse_cutoff(cutoff_raw)
    run = cutoff_mod.select_run(store.list_runs(100), cutoff_at)
    latest = store.latest_publication()
    baseline = _select_baseline_run(store, latest)
    impact = impact_mod.analyze(baseline, run, store, settings)
    return {
        "cutoff_at": cutoff_at,
        "run_id": run["run_id"],
        "run_created_at": run["created_at"],
        "baseline_publication_id": latest["publication_id"] if latest else None,
        "impact_summary": impact_mod.impact_summary(impact),
        "impact": impact,
    }


def publish(store, settings, cutoff_raw: str) -> dict:
    cutoff_at = cutoff_mod.parse_cutoff(cutoff_raw)
    run = cutoff_mod.select_run(store.list_runs(100), cutoff_at)
    latest = store.latest_publication()
    publication_id = ids.make_publication_id(run["run_id"], cutoff_at)

    if latest is not None and latest["publication_id"] != publication_id:
        if latest["cutoff_at"] > cutoff_at:
            raise DomainError(
                "E_CUTOFF_REGRESS",
                "截止时点不能早于最近一次发布；已发布版本不可回退",
                409,
            )

    baseline = _select_baseline_run(store, latest)
    impact = impact_mod.analyze(baseline, run, store, settings)
    frozen = snapshot_mod.build_snapshot(store, run, settings)

    publication = {
        "publication_id": publication_id,
        "run_id": run["run_id"],
        "cutoff_at": cutoff_at,
        "published_at": utc_now_iso(),
        "status": "published",
        "report_csv": frozen["report_csv"],
        "manifest_json": frozen["manifest_json"],
        "report_sha256": frozen["report_sha256"],
        "manifest_sha256": frozen["manifest_sha256"],
        "impact": impact,
    }
    saved = store.publish_transaction(publication)
    return detail(store, saved["publication_id"])


def detail(store, publication_id: str) -> dict:
    row = store.get_publication(publication_id)
    if row is None:
        raise DomainError("E_NO_PUBLICATION", "发布版本不存在", 404)
    data = _brief(row)
    data["impact"] = row.get("impact") or {}
    return data


def latest(store) -> dict | None:
    row = store.latest_publication()
    return _brief(row) if row else None


def listing(store, limit: int = 20) -> dict:
    rows = store.list_publications(limit)
    return {"count": len(rows), "publications": [_brief(row) for row in rows]}


def report_text(store, publication_id: str) -> str:
    row = store.get_publication(publication_id)
    if row is None:
        raise DomainError("E_NO_PUBLICATION", "发布版本不存在", 404)
    return row["report_csv"]


def manifest_text(store, publication_id: str) -> str:
    row = store.get_publication(publication_id)
    if row is None:
        raise DomainError("E_NO_PUBLICATION", "发布版本不存在", 404)
    return row["manifest_json"]
