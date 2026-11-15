"""SQLite 存储：批次、流水行、候选、匹配。

约定：
- 金额与手续费一律存 Decimal 字符串，不落 float；
- 一次运行的所有行在同一个事务里写入，失败整体回滚；
- 同一 run_id 重跑先删后插，行数、合计与首跑一致；
- 大整数编号（流水号、凭证号）按字符串保形，不过 int()。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    bank_sha256 TEXT NOT NULL DEFAULT '',
    ledger_sha256 TEXT NOT NULL DEFAULT '',
    bank_file TEXT NOT NULL DEFAULT '',
    ledger_file TEXT NOT NULL DEFAULT '',
    bank_bytes INTEGER NOT NULL DEFAULT 0,
    ledger_bytes INTEGER NOT NULL DEFAULT 0,
    bank_raw INTEGER NOT NULL DEFAULT 0,
    ledger_raw INTEGER NOT NULL DEFAULT 0,
    bank_valid INTEGER NOT NULL DEFAULT 0,
    ledger_valid INTEGER NOT NULL DEFAULT 0,
    bank_quar INTEGER NOT NULL DEFAULT 0,
    ledger_quar INTEGER NOT NULL DEFAULT 0,
    segments INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'empty',
    input_states_json TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
    run_id TEXT NOT NULL,
    side TEXT NOT NULL,
    seq INTEGER NOT NULL,
    row_no INTEGER NOT NULL,
    txn_no TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    posted_at TEXT NOT NULL DEFAULT '',
    direction INTEGER NOT NULL DEFAULT 0,
    amount TEXT NOT NULL DEFAULT '',
    fee TEXT NOT NULL DEFAULT '',
    memo TEXT NOT NULL DEFAULT '',
    counterparty TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'valid',
    reason TEXT NOT NULL DEFAULT '',
    flags_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '[]',
    raw_amount TEXT NOT NULL DEFAULT '',
    raw_date TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_run ON records(run_id, side, status);
CREATE TABLE IF NOT EXISTS candidates (
    run_id TEXT NOT NULL,
    cand_id TEXT NOT NULL,
    tier INTEGER NOT NULL,
    bank_ids TEXT NOT NULL,
    ledger_ids TEXT NOT NULL,
    bank_sum TEXT NOT NULL,
    ledger_sum TEXT NOT NULL,
    diff TEXT NOT NULL,
    day_gap INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id, tier);
CREATE TABLE IF NOT EXISTS matches (
    run_id TEXT NOT NULL,
    match_id TEXT NOT NULL,
    tier INTEGER NOT NULL,
    bank_ids TEXT NOT NULL,
    ledger_ids TEXT NOT NULL,
    bank_sum TEXT NOT NULL,
    ledger_sum TEXT NOT NULL,
    diff TEXT NOT NULL,
    day_gap INTEGER NOT NULL DEFAULT 0,
    fee_alloc TEXT NOT NULL DEFAULT '0',
    fee_actual TEXT NOT NULL DEFAULT '0',
    bank_signed TEXT NOT NULL DEFAULT '0',
    ledger_signed TEXT NOT NULL DEFAULT '0'
);
CREATE INDEX IF NOT EXISTS idx_matches_run ON matches(run_id, tier);
CREATE TABLE IF NOT EXISTS publications (
    publication_id TEXT PRIMARY KEY,
    version_no INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    cutoff_at TEXT NOT NULL,
    published_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'publishing',
    superseded_by TEXT NOT NULL DEFAULT '',
    report_csv TEXT NOT NULL DEFAULT '',
    manifest_json TEXT NOT NULL DEFAULT '',
    report_sha256 TEXT NOT NULL DEFAULT '',
    manifest_sha256 TEXT NOT NULL DEFAULT '',
    impact_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_publications_version ON publications(version_no);
CREATE INDEX IF NOT EXISTS idx_publications_run ON publications(run_id);
"""

RUN_COLUMNS = (
    "run_id", "created_at", "rule_version", "params_json", "bank_sha256", "ledger_sha256",
    "bank_file", "ledger_file", "bank_bytes", "ledger_bytes", "bank_raw", "ledger_raw",
    "bank_valid", "ledger_valid", "bank_quar", "ledger_quar", "segments", "status",
    "input_states_json", "duration_ms",
)


class Store:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------- 批次 ----------
    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["params"] = json.loads(data.pop("params_json") or "{}")
        data["input_states"] = json.loads(data.pop("input_states_json") or "{}")
        return data

    def latest_run(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return self._run_row(row) if row else None

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def save_run(self, run: dict, records: list[dict], candidates: list[dict],
                 matches: list[dict]) -> None:
        params_json = json.dumps(run.get("params", {}), ensure_ascii=False, sort_keys=True)
        states_json = json.dumps(run.get("input_states", {}), ensure_ascii=False)
        with self._conn() as conn:
            run_id = run["run_id"]
            conn.execute("DELETE FROM records WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM candidates WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM matches WHERE run_id = ?", (run_id,))
            conn.execute(
                """INSERT OR REPLACE INTO runs (
                    run_id, created_at, rule_version, params_json, bank_sha256, ledger_sha256,
                    bank_file, ledger_file, bank_bytes, ledger_bytes, bank_raw, ledger_raw,
                    bank_valid, ledger_valid, bank_quar, ledger_quar, segments, status,
                    input_states_json, duration_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, run["created_at"], run["rule_version"], params_json,
                    run.get("bank_sha256", ""), run.get("ledger_sha256", ""),
                    run.get("bank_file", ""), run.get("ledger_file", ""),
                    run.get("bank_bytes", 0), run.get("ledger_bytes", 0),
                    run.get("bank_raw", 0), run.get("ledger_raw", 0),
                    run.get("bank_valid", 0), run.get("ledger_valid", 0),
                    run.get("bank_quar", 0), run.get("ledger_quar", 0),
                    run.get("segments", 0), run.get("status", "empty"),
                    states_json, run.get("duration_ms", 0),
                ),
            )
            conn.executemany(
                """INSERT INTO records (run_id, side, seq, row_no, txn_no, account, posted_at,
                    direction, amount, fee, memo, counterparty, status, reason, flags_json,
                    raw_json, raw_amount, raw_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, r["side"], r["seq"], r["row_no"], r["txn_no"], r.get("account", ""),
                        r.get("posted_at", ""), r.get("direction", 0), str(r.get("amount", "")),
                        str(r.get("fee", "")), r.get("memo", ""), r.get("counterparty", ""),
                        r.get("status", "valid"), r.get("reason", ""),
                        json.dumps(r.get("flags", []), ensure_ascii=False),
                        json.dumps(r.get("raw", []), ensure_ascii=False),
                        str(r.get("raw_amount", "")), str(r.get("raw_date", "")),
                    )
                    for r in records
                ],
            )
            conn.executemany(
                """INSERT INTO candidates (run_id, cand_id, tier, bank_ids, ledger_ids,
                    bank_sum, ledger_sum, diff, day_gap) VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, c["cand_id"], c["tier"],
                        json.dumps(c["bank_ids"], ensure_ascii=False),
                        json.dumps(c["ledger_ids"], ensure_ascii=False),
                        str(c["bank_sum"]), str(c["ledger_sum"]), str(c["diff"]), c["day_gap"],
                    )
                    for c in candidates
                ],
            )
            conn.executemany(
                """INSERT INTO matches (run_id, match_id, tier, bank_ids, ledger_ids,
                    bank_sum, ledger_sum, diff, day_gap, fee_alloc, fee_actual,
                    bank_signed, ledger_signed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, m["match_id"], m["tier"],
                        json.dumps(m["bank_ids"], ensure_ascii=False),
                        json.dumps(m["ledger_ids"], ensure_ascii=False),
                        str(m["bank_sum"]), str(m["ledger_sum"]), str(m["diff"]), m["day_gap"],
                        str(m.get("fee_alloc", "0")), str(m.get("fee_actual", "0")),
                        str(m.get("bank_signed", "0")), str(m.get("ledger_signed", "0")),
                    )
                    for m in matches
                ],
            )

    def set_status(self, run_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))

    # ---------- 行 ----------
    def records(self, run_id: str, side: str | None = None,
                status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM records WHERE run_id = ?"
        args: list = [run_id]
        if side:
            sql += " AND side = ?"
            args.append(side)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY side, row_no, seq"
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["flags"] = json.loads(item.pop("flags_json") or "[]")
            item["raw"] = json.loads(item.pop("raw_json") or "[]")
            out.append(item)
        return out

    def quarantine(self, run_id: str) -> list[dict]:
        return self.records(run_id, status="quarantine")

    def candidates(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE run_id = ? ORDER BY tier, cand_id", (run_id,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["bank_ids"] = json.loads(item.pop("bank_ids") or "[]")
            item["ledger_ids"] = json.loads(item.pop("ledger_ids") or "[]")
            out.append(item)
        return out

    def matches(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM matches WHERE run_id = ? ORDER BY tier, match_id", (run_id,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["bank_ids"] = json.loads(item.pop("bank_ids") or "[]")
            item["ledger_ids"] = json.loads(item.pop("ledger_ids") or "[]")
            out.append(item)
        return out

    def replace_candidates(self, run_id: str, candidates: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM candidates WHERE run_id = ?", (run_id,))
            conn.executemany(
                """INSERT INTO candidates (run_id, cand_id, tier, bank_ids, ledger_ids,
                    bank_sum, ledger_sum, diff, day_gap) VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, c["cand_id"], c["tier"],
                        json.dumps(c["bank_ids"], ensure_ascii=False),
                        json.dumps(c["ledger_ids"], ensure_ascii=False),
                        str(c["bank_sum"]), str(c["ledger_sum"]), str(c["diff"]), c["day_gap"],
                    )
                    for c in candidates
                ],
            )

    def replace_matches(self, run_id: str, matches: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM matches WHERE run_id = ?", (run_id,))
            conn.executemany(
                """INSERT INTO matches (run_id, match_id, tier, bank_ids, ledger_ids,
                    bank_sum, ledger_sum, diff, day_gap, fee_alloc, fee_actual,
                    bank_signed, ledger_signed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, m["match_id"], m["tier"],
                        json.dumps(m["bank_ids"], ensure_ascii=False),
                        json.dumps(m["ledger_ids"], ensure_ascii=False),
                        str(m["bank_sum"]), str(m["ledger_sum"]), str(m["diff"]), m["day_gap"],
                        str(m.get("fee_alloc", "0")), str(m.get("fee_actual", "0")),
                        str(m.get("bank_signed", "0")), str(m.get("ledger_signed", "0")),
                    )
                    for m in matches
                ],
            )

    # ---------- 关账发布 ----------
    @staticmethod
    def _publication_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["impact"] = json.loads(data.pop("impact_json") or "{}")
        return data

    def get_publication(self, publication_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM publications WHERE publication_id = ?", (publication_id,)
            ).fetchone()
        return self._publication_row(row) if row else None

    def latest_publication(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM publications ORDER BY version_no DESC LIMIT 1"
            ).fetchone()
        return self._publication_row(row) if row else None

    def list_publications(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM publications ORDER BY version_no DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._publication_row(row) for row in rows]

    def save_publication(self, publication: dict) -> None:
        """插入或覆盖一条发布记录；与读取快照不在同一事务时只做单行写入。"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO publications (
                    publication_id, version_no, run_id, cutoff_at, published_at, status,
                    superseded_by, report_csv, manifest_json, report_sha256,
                    manifest_sha256, impact_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    publication["publication_id"], publication["version_no"],
                    publication["run_id"], publication["cutoff_at"],
                    publication["published_at"], publication.get("status", "publishing"),
                    publication.get("superseded_by", ""),
                    publication.get("report_csv", ""),
                    publication.get("manifest_json", ""),
                    publication.get("report_sha256", ""),
                    publication.get("manifest_sha256", ""),
                    json.dumps(publication.get("impact", {}), ensure_ascii=False,
                               sort_keys=True),
                ),
            )

    def publish_transaction(self, publication: dict) -> None:
        """关账发布的串行事务：抢写锁 → 幂等判定 → 旧版置 superseded → 写入新版。

        同一 (run_id, cutoff_at) 重试时 publication_id 相同：
        - 已发布（published）→ 直接返回旧记录，不新增版本、不重复计数；
        - 残留 publishing（上次中断）→ 用同一份快照覆盖，版本号沿用；
        - 不同 cutoff 的新发布 → 版本号 +1，并把上一版标记为 superseded。
        返回应当回传给调用方的发布行（含最终状态）。
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM publications WHERE publication_id = ?",
                (publication["publication_id"],),
            ).fetchone()
            if existing is not None and existing["status"] == "published":
                return self._publication_row(existing)
            if existing is not None:
                version_no = existing["version_no"]
            else:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version_no), 0) AS max_version FROM publications"
                ).fetchone()
                version_no = row["max_version"] + 1
            conn.execute(
                "UPDATE publications SET superseded_by = ?, status = 'superseded' "
                "WHERE status IN ('published', 'publishing')",
                (publication["publication_id"],),
            )
            conn.execute(
                """INSERT OR REPLACE INTO publications (
                    publication_id, version_no, run_id, cutoff_at, published_at, status,
                    superseded_by, report_csv, manifest_json, report_sha256,
                    manifest_sha256, impact_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    publication["publication_id"], version_no,
                    publication["run_id"], publication["cutoff_at"],
                    publication["published_at"], publication.get("status", "published"),
                    "",
                    publication["report_csv"], publication["manifest_json"],
                    publication["report_sha256"], publication["manifest_sha256"],
                    json.dumps(publication.get("impact", {}), ensure_ascii=False,
                               sort_keys=True),
                ),
            )
            row = conn.execute(
                "SELECT * FROM publications WHERE publication_id = ?",
                (publication["publication_id"],),
            ).fetchone()
            return self._publication_row(row)
