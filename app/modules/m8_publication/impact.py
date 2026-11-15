"""补录影响分析：让复核人看清新批次相对已发布版本会改动什么。

对比基准是最近一次 published 的发布快照；没有历史发布时，基准为空，
此时所有内容都归类为「新增」。输出三类影响：

- 匹配组：新增 / 移除 / 成员变动 / 手续费变动（按 match_id 对齐）；
- 未匹配项：按 (side, txn_no) 对齐，列出新出现与已消失的未匹配行；
- 手续费：逐笔银行手续费的新增与变动，以及分摊合计差额。

全部金额用 Decimal 字符串输出，不走浮点。
"""
from __future__ import annotations

from decimal import Decimal

from ...config import dec
from ..m5_matching.subset import TIER_NAMES
from ..m6_diff import differences as differences_mod

ZERO = Decimal("0.00")


def _record_key(record: dict) -> tuple[str, str]:
    return record["side"], record["txn_no"]


def _valid_index(records: list[dict]) -> dict[tuple[str, str], dict]:
    return {
        _record_key(record): record
        for record in records
        if record["status"] == "valid"
    }


def _match_index(matches: list[dict]) -> dict[str, dict]:
    return {group["match_id"]: group for group in matches}


def _unmatched_keys(records: list[dict], matches: list[dict]) -> set[tuple[str, str]]:
    matched_bank = {key for group in matches for key in group["bank_ids"]}
    matched_ledger = {key for group in matches for key in group["ledger_ids"]}
    out: set[tuple[str, str]] = set()
    for record in records:
        if record["status"] != "valid":
            continue
        key = _record_key(record)
        if record["side"] == "bank" and record["txn_no"] not in matched_bank:
            out.add(key)
        if record["side"] == "ledger" and record["txn_no"] not in matched_ledger:
            out.add(key)
    return out


def _signed(record: dict) -> Decimal:
    return dec(record["amount"]) * int(record["direction"])


def analyze(baseline: dict | None, current: dict, store, settings) -> dict:
    """baseline/current 为 run 行；baseline 为 None 表示首次发布。"""
    current_records = store.records(current["run_id"])
    current_matches = store.matches(current["run_id"])
    current_result = differences_mod.reconcile(current_records, current_matches, settings)

    if baseline is None:
        base_records: list[dict] = []
        base_matches: list[dict] = []
        base_result = None
    else:
        base_records = store.records(baseline["run_id"])
        base_matches = store.matches(baseline["run_id"])
        base_result = differences_mod.reconcile(base_records, base_matches, settings)

    return {
        "baseline_run_id": baseline["run_id"] if baseline else None,
        "current_run_id": current["run_id"],
        "match_groups": _match_impacts(base_matches, current_matches),
        "unmatched": _unmatched_impacts(base_records, base_matches,
                                        current_records, current_matches),
        "fees": _fee_impacts(base_records, current_records, base_result, current_result),
        "totals_delta": _totals_delta(base_result, current_result),
    }


def _match_impacts(base_matches: list[dict], current_matches: list[dict]) -> dict:
    base = _match_index(base_matches)
    current = _match_index(current_matches)
    added, removed, changed = [], [], []
    for match_id in sorted(set(current) - set(base)):
        group = current[match_id]
        added.append(_group_brief(group, "新增匹配组"))
    for match_id in sorted(set(base) - set(current)):
        group = base[match_id]
        removed.append(_group_brief(group, "匹配组消失"))
    for match_id in sorted(set(base) & set(current)):
        old, new = base[match_id], current[match_id]
        changes: list[str] = []
        if old["bank_ids"] != new["bank_ids"] or old["ledger_ids"] != new["ledger_ids"]:
            changes.append("成员变动")
        if dec(old.get("fee_alloc", 0)) != dec(new.get("fee_alloc", 0)):
            changes.append("手续费分摊变动")
        if dec(old.get("diff", 0)) != dec(new.get("diff", 0)):
            changes.append("匹配差额变动")
        if changes:
            brief = _group_brief(new, "、".join(changes))
            brief["fee_alloc_before"] = format(dec(old.get("fee_alloc", 0)), "f")
            changed.append(brief)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "baseline_group_count": len(base_matches),
        "current_group_count": len(current_matches),
    }


def _group_brief(group: dict, change: str) -> dict:
    return {
        "match_id": group["match_id"],
        "tier": int(group["tier"]),
        "tier_name": TIER_NAMES.get(int(group["tier"]), ""),
        "bank_ids": list(group["bank_ids"]),
        "ledger_ids": list(group["ledger_ids"]),
        "fee_alloc": format(dec(group.get("fee_alloc", 0)), "f"),
        "change": change,
    }


def _unmatched_impacts(base_records, base_matches, current_records, current_matches) -> dict:
    base_keys = _unmatched_keys(base_records, base_matches)
    current_keys = _unmatched_keys(current_records, current_matches)
    current_index = _valid_index(current_records)
    base_index = _valid_index(base_records)

    def describe(keys: set[tuple[str, str]], index: dict) -> list[dict]:
        rows = []
        for side, txn_no in sorted(keys):
            record = index.get((side, txn_no))
            if record is None:
                continue
            rows.append({
                "side": "银行" if side == "bank" else "账簿",
                "txn_no": txn_no,
                "amount": format(_signed(record), "f"),
                "memo": record.get("memo", ""),
            })
        return rows

    return {
        "newly_unmatched": describe(current_keys - base_keys, current_index),
        "resolved": describe(base_keys - current_keys, base_index),
        "baseline_unmatched_count": len(base_keys),
        "current_unmatched_count": len(current_keys),
    }


def _fee_impacts(base_records, current_records, base_result, current_result) -> dict:
    base_index = _valid_index(base_records)
    current_index = _valid_index(current_records)
    fee_added, fee_changed = [], []
    for key in sorted(set(current_index) - set(base_index)):
        record = current_index[key]
        if record["side"] != "bank" or dec(record["fee"]) <= 0:
            continue
        fee_added.append({
            "txn_no": record["txn_no"],
            "fee": format(dec(record["fee"]), "f"),
            "change": "手续费补录",
        })
    for key in sorted(set(base_index) & set(current_index)):
        old_fee, new_fee = dec(base_index[key]["fee"]), dec(current_index[key]["fee"])
        if old_fee != new_fee:
            fee_changed.append({
                "txn_no": current_index[key]["txn_no"],
                "fee_before": format(old_fee, "f"),
                "fee_after": format(new_fee, "f"),
                "delta": format(new_fee - old_fee, "f"),
                "change": "手续费调整",
            })
    base_fee = base_result["totals"]["bank_fee"] if base_result else ZERO
    current_fee = current_result["totals"]["bank_fee"]
    base_alloc = base_result["totals"]["fee_alloc_total"] if base_result else ZERO
    current_alloc = current_result["totals"]["fee_alloc_total"]
    return {
        "fee_added": fee_added,
        "fee_changed": fee_changed,
        "bank_fee_before": format(base_fee, "f"),
        "bank_fee_after": format(current_fee, "f"),
        "bank_fee_delta": format(current_fee - base_fee, "f"),
        "fee_alloc_before": format(base_alloc, "f"),
        "fee_alloc_after": format(current_alloc, "f"),
        "fee_alloc_delta": format(current_alloc - base_alloc, "f"),
    }


def _totals_delta(base_result, current_result) -> dict:
    if base_result is None:
        return {}
    keys = current_result["totals"].keys()
    return {
        key: format(current_result["totals"][key] - base_result["totals"][key], "f")
        for key in keys
    }


def impact_summary(impact: dict) -> dict:
    """页面与清单共用的计数摘要。"""
    groups = impact["match_groups"]
    unmatched = impact["unmatched"]
    fees = impact["fees"]
    return {
        "match_groups_added": len(groups["added"]),
        "match_groups_removed": len(groups["removed"]),
        "match_groups_changed": len(groups["changed"]),
        "newly_unmatched": len(unmatched["newly_unmatched"]),
        "unmatched_resolved": len(unmatched["resolved"]),
        "fee_rows_added": len(fees["fee_added"]),
        "fee_rows_changed": len(fees["fee_changed"]),
        "bank_fee_delta": fees["bank_fee_delta"],
    }
