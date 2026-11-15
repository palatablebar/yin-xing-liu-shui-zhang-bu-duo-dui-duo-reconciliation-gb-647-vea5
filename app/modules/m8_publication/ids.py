"""发布编号：同一批次 + 同一截止时点重试得到同一 publication_id。

编号只取 run_id 与 cutoff_at，与请求次数无关，因此同一补录重试不会
产生第二个批次；cutoff 或输入批次变化才会得到新编号、新版本。
"""
from __future__ import annotations

import hashlib


def make_publication_id(run_id: str, cutoff_at: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{cutoff_at}".encode("utf-8")).hexdigest()[:16]
    return f"pub-{digest}"
