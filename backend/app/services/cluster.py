"""聚类分群业务规则：状态流转、字段校验、筛选排序口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "cluster"
REQUIRED_FIELDS = ["任务编号", "聚类算法", "特征维度"]
STATUS_ORDER = ["草稿", "聚类计算中", "已完成", "已归档"]
ACTION_RULES = {"启动聚类": "聚类计算中", "确认完成": "已完成", "归档结果": "已归档"}
NEGATIVE_ACTIONS = []

# 支持按字段模糊筛选与排序的列，与前端列表列保持一致
FILTER_FIELDS = ["任务编号", "聚类算法", "特征维度", "关联序列", "重算状态"]
SORTABLE_FIELDS = ["任务编号", "轮廓系数", "重算状态"]


def _sort_key(value: Any) -> tuple[int, Any]:
    try:
        return 0, float(value)
    except (TypeError, ValueError):
        return 1, str(value or "")


class ClusterService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        filters: dict[str, str] | None = None,
        sort_field: str | None = None,
        sort_order: str = "asc",
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [dict(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        for field, value in (filters or {}).items():
            if field in FILTER_FIELDS and value:
                rows = [row for row in rows if value in str(row.get(field, ""))]
        if sort_field in SORTABLE_FIELDS:
            rows.sort(key=lambda row: _sort_key(row.get(sort_field)), reverse=sort_order == "desc")
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚类任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于聚类分群可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"聚类任务已{action}"
