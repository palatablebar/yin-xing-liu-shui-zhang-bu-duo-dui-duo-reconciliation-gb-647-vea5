"""导出入口：报告 CSV 与运行清单 JSON。

存在已发布版本时，下载接口直接返回发布时冻结的同版文本，
保证正式报告、差异归因页面与导出文件三者同版；未发布时回退到最新批次实时导出。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import service

router = APIRouter(tags=["导出"])


@router.get("/api/export/summary")
def get_summary(request: Request):
    store = request.app.state.store
    return service.summary(store, store.latest_run(), request.app.state.settings)


@router.get("/api/export/report.csv")
def get_report(request: Request):
    store = request.app.state.store
    publication = store.latest_publication()
    if publication is not None and publication["status"] == "published":
        text = publication["report_csv"]
        publication_id = publication["publication_id"]
    else:
        text = service.report(store, store.latest_run(), request.app.state.settings)
        publication_id = ""
    filename = (
        f"report-{publication_id}.csv" if publication_id else "report.csv"
    )
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/export/manifest.json")
def get_manifest(request: Request):
    store = request.app.state.store
    publication = store.latest_publication()
    if publication is not None and publication["status"] == "published":
        text = publication["manifest_json"]
        publication_id = publication["publication_id"]
    else:
        text = service.manifest(store, store.latest_run(), request.app.state.settings)
        publication_id = ""
    filename = (
        f"manifest-{publication_id}.json" if publication_id else "manifest.json"
    )
    return Response(
        content=text,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
