"""关账发布入口：预览、发布、版本列表、冻结报告与清单下载。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from . import service

router = APIRouter(prefix="/api/publications", tags=["关账发布"])


@router.get("/preview")
def get_preview(request: Request, cutoff: str):
    store = request.app.state.store
    return service.preview(store, request.app.state.settings, cutoff)


@router.post("")
def post_publication(request: Request, cutoff: str = Form(...)):
    store = request.app.state.store
    return service.publish(store, request.app.state.settings, cutoff)


@router.get("")
def list_publications(request: Request):
    return service.listing(request.app.state.store)


@router.get("/latest")
def get_latest(request: Request):
    data = service.latest(request.app.state.store)
    return data or {"publication_id": None}


@router.get("/{publication_id}")
def get_publication(request: Request, publication_id: str):
    return service.detail(request.app.state.store, publication_id)


@router.get("/{publication_id}/report.csv")
def get_report(request: Request, publication_id: str):
    store = request.app.state.store
    text = service.report_text(store, publication_id)
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="report-{publication_id}.csv"'
        },
    )


@router.get("/{publication_id}/manifest.json")
def get_manifest(request: Request, publication_id: str):
    store = request.app.state.store
    text = service.manifest_text(store, publication_id)
    return Response(
        content=text,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="manifest-{publication_id}.json"'
        },
    )
