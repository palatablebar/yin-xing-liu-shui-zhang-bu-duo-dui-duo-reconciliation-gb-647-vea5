"""截止时点选择：按发布申请的 cutoff 锁定当时已到的最后一个已匹配批次。

规则：
- 只接受 status == "matched" 的批次，半成品（partial_*）不能关账发布；
- 在 created_at <= cutoff 的批次里取最新一条；没有合格批次时显式报错，
  绝不静默回退到 cutoff 之后才到的数据；
- cutoff 统一用带时区的 ISO-8601 字符串，字典序与时间序一致，直接比较即可。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...errors import DomainError

LOCAL_TZ = timezone(timedelta(hours=8))


def parse_cutoff(raw: str) -> str:
    """校验 cutoff 并归一化为 UTC ISO 字符串。

    接受 ``2026-03-31T18:00`` 这类无时区输入（按 Asia/Shanghai 解释），
    也接受自带偏移量的完整 ISO 时间。
    """
    text = (raw or "").strip()
    if not text:
        raise DomainError("E_CUTOFF_REQUIRED", "发布必须指定截止时点", 400)
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        raise DomainError("E_CUTOFF_FORMAT", "截止时点格式应为 ISO-8601，如 2026-03-31T18:00", 400)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=LOCAL_TZ)
    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def select_run(runs: list[dict], cutoff_at: str) -> dict:
    """从批次列表中选出 cutoff 时刻「已经到齐」的最新已匹配批次。"""
    eligible = [
        run for run in runs
        if run.get("status") == "matched" and run.get("created_at", "") <= cutoff_at
    ]
    if not eligible:
        raise DomainError(
            "E_CUTOFF_NO_RUN",
            "截止时点之前没有已匹配的批次，无法发布；请先完成采信与匹配",
            409,
        )
    return max(eligible, key=lambda run: run["created_at"])
