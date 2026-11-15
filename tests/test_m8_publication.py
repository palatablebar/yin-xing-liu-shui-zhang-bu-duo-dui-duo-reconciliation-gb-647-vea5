"""M8 关账发布：截止时点、补录影响、冻结快照、同版导出、中断恢复与幂等。"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from conftest import read_sample

CUTOFF_1 = "2026-12-01T00:00:00+08:00"
CUTOFF_2 = "2026-12-02T00:00:00+08:00"
CUTOFF_3 = "2026-12-03T00:00:00+08:00"
EARLY_CUTOFF = "2020-01-01T00:00:00+08:00"


def _publish(client, cutoff=CUTOFF_1):
    response = client.post("/api/publications", data={"cutoff": cutoff})
    assert response.status_code == 200, response.text
    return response.json()


def _supplement_bank(client, old: str, new: str) -> bytes:
    raw = read_sample("bank_statement_dirty.csv").decode("utf-8")
    updated = raw.replace(old, new).encode("utf-8")
    response = client.post(
        "/api/statements/import",
        files={"file": ("bank_supplement.csv", updated, "text/csv")},
        data={"side": "bank"},
    )
    assert response.status_code == 200
    # 单侧导入是半成品，补另一侧后才是可发布的 matched 批次。
    ledger = read_sample("ledger_book_dirty.csv")
    response = client.post(
        "/api/statements/import",
        files={"file": ("ledger_book.csv", ledger, "text/csv")},
        data={"side": "ledger"},
    )
    assert response.json()["status"] == "matched"
    return updated


def _supplement_ledger(client, extra_line: str):
    raw = read_sample("ledger_book_dirty.csv").decode("gb18030")
    updated = raw.rstrip("\n") + "\n" + extra_line + "\n"
    response = client.post(
        "/api/statements/import",
        files={"file": ("ledger_supplement.csv", updated.encode("utf-8"), "text/csv")},
        data={"side": "ledger"},
    )
    assert response.status_code == 200
    # 单侧导入是半成品，补另一侧后才是可发布的 matched 批次。
    bank = read_sample("bank_statement_dirty.csv")
    response = client.post(
        "/api/statements/import",
        files={"file": ("bank_statement.csv", bank, "text/csv")},
        data={"side": "bank"},
    )
    assert response.json()["status"] == "matched"
    return response.json()


# ---------- 截止时点 ----------

def test_publish_requires_cutoff(client, demo):
    response = client.post("/api/publications", data={"cutoff": ""})
    assert response.status_code == 400
    assert response.json()["error"] == "E_CUTOFF_REQUIRED"


def test_publish_rejects_bad_cutoff_format(client, demo):
    response = client.post("/api/publications", data={"cutoff": "月底"})
    assert response.status_code == 400
    assert response.json()["error"] == "E_CUTOFF_FORMAT"


def test_cutoff_before_any_run_is_rejected(client, demo):
    response = client.post("/api/publications", data={"cutoff": EARLY_CUTOFF})
    assert response.status_code == 409
    assert response.json()["error"] == "E_CUTOFF_NO_RUN"


def test_naive_cutoff_uses_local_timezone(client, demo):
    body = _publish(client, "2026-12-01T00:00")
    assert body["cutoff_at"] == "2026-11-30T16:00:00+00:00"


def test_publish_locks_latest_matched_run_at_cutoff(client, demo):
    body = _publish(client)
    assert body["run_id"] == demo["run_id"]
    assert body["version_no"] == 1
    assert body["status"] == "published"
    assert body["publication_id"].startswith("pub-")


def test_preview_is_read_only(client, demo):
    before = client.get("/api/publications").json()["count"]
    response = client.get("/api/publications/preview", params={"cutoff": CUTOFF_1})
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == demo["run_id"]
    assert body["baseline_publication_id"] is None
    assert client.get("/api/publications").json()["count"] == before


# ---------- 幂等与版本 ----------

def test_retry_same_cutoff_keeps_single_publication(client, demo):
    first = _publish(client)
    again = _publish(client)
    assert again["publication_id"] == first["publication_id"]
    assert again["version_no"] == 1
    listing = client.get("/api/publications").json()
    assert listing["count"] == 1
    assert listing["publications"][0]["publication_id"] == first["publication_id"]


def test_new_cutoff_creates_new_version_and_supersedes(client, demo):
    first = _publish(client, CUTOFF_1)
    second = _publish(client, CUTOFF_2)
    assert second["version_no"] == 2
    assert second["publication_id"] != first["publication_id"]
    rows = client.get("/api/publications").json()["publications"]
    by_id = {row["publication_id"]: row for row in rows}
    assert by_id[first["publication_id"]]["status"] == "superseded"
    assert by_id[first["publication_id"]]["superseded_by"] == second["publication_id"]
    assert by_id[second["publication_id"]]["status"] == "published"


def test_cutoff_cannot_regress_after_publication(client, demo):
    _publish(client, CUTOFF_2)
    response = client.post("/api/publications", data={"cutoff": CUTOFF_1})
    assert response.status_code == 409
    assert response.json()["error"] == "E_CUTOFF_REGRESS"
    assert client.get("/api/publications").json()["count"] == 1


def test_unknown_publication_returns_404(client, demo):
    assert client.get("/api/publications/pub-doesnotexist").status_code == 404
    assert client.get(
        "/api/publications/pub-doesnotexist/report.csv"
    ).status_code == 404


# ---------- 同版：报告、差异、导出 ----------

def test_export_serves_frozen_snapshot_after_publish(client, demo):
    published = _publish(client)
    export_text = client.get("/api/export/report.csv").text
    frozen = client.get(
        f"/api/publications/{published['publication_id']}/report.csv"
    ).text
    assert export_text == frozen
    manifest_export = client.get("/api/export/manifest.json").text
    manifest_frozen = client.get(
        f"/api/publications/{published['publication_id']}/manifest.json"
    ).text
    assert manifest_export == manifest_frozen


def test_snapshot_hashes_verify(client, demo):
    published = _publish(client)
    report = client.get(
        f"/api/publications/{published['publication_id']}/report.csv"
    ).text
    manifest = client.get(
        f"/api/publications/{published['publication_id']}/manifest.json"
    ).text
    assert hashlib.sha256(report.encode("utf-8")).hexdigest() == published["report_sha256"]
    assert hashlib.sha256(manifest.encode("utf-8")).hexdigest() == published["manifest_sha256"]


def test_differences_frozen_matches_workspace_totals(client, demo):
    published = _publish(client)
    frozen = client.get("/api/differences").json()
    live = client.get("/api/differences", params={"scope": "workspace"}).json()
    assert frozen["frozen"] is True
    assert frozen["publication_id"] == published["publication_id"]
    assert live["frozen"] is False
    assert frozen["totals"] == live["totals"]
    assert frozen["counts"] == live["counts"]
    assert {item["key"] for item in frozen["identities"]} == {
        item["key"] for item in live["identities"]
    }
    assert all(item["balanced"] for item in frozen["identities"])


def test_frozen_report_survives_later_supplement(client, demo):
    first = _publish(client, CUTOFF_1)
    first_text = client.get(
        f"/api/publications/{first['publication_id']}/report.csv"
    ).text
    _supplement_bank(
        client,
        "000004,6222-0034,2026-03-08T09:00:00+08:00,借,500.10,0.00,费用丁,杂费",
        "000004,6222-0034,2026-03-08T09:00:00+08:00,借,500.10,3.00,费用丁,杂费",
    )
    # 旧版报告逐字不变，当前导出已切换到工作区新数字（发布新版前不冻结）。
    assert client.get(
        f"/api/publications/{first['publication_id']}/report.csv"
    ).text == first_text
    second = _publish(client, CUTOFF_2)
    second_text = client.get(
        f"/api/publications/{second['publication_id']}/report.csv"
    ).text
    assert "22.31" in first_text and "25.31" not in first_text
    assert "25.31" in second_text
    assert client.get("/api/export/report.csv").text == second_text


# ---------- 补录影响：手续费 ----------

def test_fee_supplement_impact(client, demo):
    _publish(client, CUTOFF_1)
    _supplement_bank(
        client,
        "000004,6222-0034,2026-03-08T09:00:00+08:00,借,500.10,0.00,费用丁,杂费",
        "000004,6222-0034,2026-03-08T09:00:00+08:00,借,500.10,3.00,费用丁,杂费",
    )
    preview = client.get(
        "/api/publications/preview", params={"cutoff": CUTOFF_2}
    ).json()
    fees = preview["impact"]["fees"]
    assert fees["bank_fee_before"] == "22.31"
    assert fees["bank_fee_after"] == "25.31"
    assert fees["bank_fee_delta"] == "3.00"
    changed = {row["txn_no"]: row for row in fees["fee_changed"]}
    assert changed["000004"]["fee_before"] == "0.00"
    assert changed["000004"]["fee_after"] == "3.00"
    assert preview["impact_summary"]["bank_fee_delta"] == "3.00"


# ---------- 补录影响：匹配组与未匹配项 ----------

def test_voucher_supplement_resolves_unmatched_group(client, demo):
    _publish(client, CUTOFF_1)
    _supplement_ledger(client, "V021,6222-0034,2026-03-09,借,500.10,补录杂费")
    preview = client.get(
        "/api/publications/preview", params={"cutoff": CUTOFF_2}
    ).json()
    impact = preview["impact"]
    assert impact["unmatched"]["baseline_unmatched_count"] == 12
    resolved = {row["txn_no"] for row in impact["unmatched"]["resolved"]}
    # 000004 原本未匹配，补录凭证后被消纳；V021 是新增即匹配，不计入消解。
    assert resolved == {"000004"}
    current_unmatched = {
        (row["side"], row["txn_no"])
        for row in impact["unmatched"]["newly_unmatched"]
    }
    assert ("账簿", "V021") not in current_unmatched
    added = impact["match_groups"]["added"]
    assert len(added) == 1
    assert added[0]["bank_ids"] == ["000004"]
    assert added[0]["ledger_ids"] == ["V021"]
    summary = preview["impact_summary"]
    assert summary["match_groups_added"] == 1
    assert summary["unmatched_resolved"] == 1

    published = _publish(client, CUTOFF_2)
    detail = client.get(
        f"/api/publications/{published['publication_id']}"
    ).json()
    assert detail["impact"]["match_groups"]["current_group_count"] == 12


def test_first_publication_preview_lists_everything_as_added(client, demo):
    preview = client.get(
        "/api/publications/preview", params={"cutoff": CUTOFF_1}
    ).json()
    impact = preview["impact"]
    assert impact["baseline_run_id"] is None
    assert impact["match_groups"]["baseline_group_count"] == 0
    assert impact["match_groups"]["current_group_count"] == 11
    assert len(impact["match_groups"]["added"]) == 11
    assert impact["fees"]["bank_fee_before"] == "0.00"
    assert impact["fees"]["bank_fee_after"] == "22.31"


# ---------- 发布中断 ----------

def test_interrupted_publication_keeps_old_version_and_retries_in_place(
    client, demo, settings
):
    published = _publish(client)
    store = client.app.state.store
    row = store.get_publication(published["publication_id"])
    row["status"] = "publishing"
    row["report_csv"] = ""
    row["manifest_json"] = ""
    store.save_publication(row)

    # 中断期间导出与差异回退实时数据，不返回半个快照。
    assert "合计" in client.get("/api/export/report.csv").text
    assert client.get("/api/differences").json()["frozen"] is False

    retried = _publish(client)
    assert retried["publication_id"] == published["publication_id"]
    assert retried["version_no"] == 1
    assert client.get("/api/publications").json()["count"] == 1
    frozen = client.get(
        f"/api/publications/{published['publication_id']}/report.csv"
    )
    assert frozen.status_code == 200 and "合计" in frozen.text


# ---------- 并发 ----------

def test_concurrent_identical_publishes_create_one_row(client, demo):
    import threading

    errors: list[Exception] = []

    def worker() -> None:
        try:
            client.post("/api/publications", data={"cutoff": CUTOFF_1})
        except Exception as exc:  # pragma: no cover - 仅用于暴露并发失败
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    rows = client.get("/api/publications").json()
    assert rows["count"] == 1
    assert rows["publications"][0]["version_no"] == 1


# ---------- 页面与清单 ----------

def test_publication_page_renders(client, demo):
    response = client.get("/publications")
    assert response.status_code == 200
    assert "关账发布" in response.text
    _publish(client)
    response = client.get("/publications")
    assert "pub-" in response.text and "截止时点" in response.text


def test_manifest_embeds_frozen_reconciliation(client, demo):
    published = _publish(client)
    manifest = json.loads(
        client.get(
            f"/api/publications/{published['publication_id']}/manifest.json"
        ).text
    )
    reconciliation = manifest["reconciliation"]
    assert reconciliation["totals"]["bank_fee"] == "22.31"
    assert Decimal(reconciliation["totals"]["bank_total"]) == Decimal("-246301.82")
    assert len(reconciliation["identities"]) == 6
    # 清单记录来源批次状态，发布状态以 publications 表为准。
    assert manifest["status"] == "matched"
    detail = client.get(
        f"/api/publications/{published['publication_id']}"
    ).json()
    assert detail["status"] == "published"


def test_latest_endpoint_empty_then_published(client, demo):
    assert client.get("/api/publications/latest").json()["publication_id"] is None
    published = _publish(client)
    assert client.get("/api/publications/latest").json()[
        "publication_id"
    ] == published["publication_id"]
