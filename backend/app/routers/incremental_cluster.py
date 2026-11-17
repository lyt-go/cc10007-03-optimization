"""行为序列 → 聚类分群增量重算接口。

链路：登记序列版本变化 → 生成增量聚类输入分群明细数据集 → 待执行队列写入 →
执行/重试重算 → 确认数据集 → 发布落定到聚类分群清单。
待执行队列与已完成结果分表持久化，重试只补写未完成任务。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload
from app.services.incremental_cluster import incremental_service as service

router = APIRouter(prefix="/api/incremental-cluster", tags=["增量聚类重算"])


@router.post("/sequence-changes", response_model=ActionResult)
def register_sequence_change(payload: EntryPayload) -> ActionResult:
    """登记行为序列版本变化，只记录变化字段，并同步到行为序列清单。"""
    values = payload.values
    sequence_code = str(values.get("sequence_code") or values.get("序列编号") or "").strip()
    changed_fields = values.get("changed_fields") or values.get("变化字段") or {}
    if not sequence_code or not isinstance(changed_fields, dict) or not changed_fields:
        return ActionResult(ok=False, message="必须提供 sequence_code 与非空 changed_fields")
    record, message = service.register_sequence_change(
        sequence_code=sequence_code,
        changed_fields=changed_fields,
        change_type=str(values.get("change_type") or "字段变更"),
        remark=payload.remark,
    )
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/sequence-changes", response_model=dict)
def list_sequence_changes(
    sequence_code: str | None = Query(default=None, description="按序列编号过滤版本记录"),
) -> dict[str, Any]:
    items = service.list_versions(sequence_code)
    return {"items": items, "total": len(items)}


@router.post("/datasets", response_model=ActionResult)
def build_dataset(payload: EntryPayload) -> ActionResult:
    """按版本变化记录做差异识别，生成增量聚类输入分群明细数据集（待确认）。"""
    values = payload.values
    sequence_code = str(values.get("sequence_code") or values.get("序列编号") or "").strip()
    version_id = values.get("version_id")
    if not sequence_code or version_id is None:
        return ActionResult(ok=False, message="必须提供 sequence_code 与 version_id")
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="version_id 必须是整数")
    batch_type = str(values.get("batch_type") or "补算")
    affected_tasks = values.get("affected_tasks")
    dataset, message = service.build_dataset(
        sequence_code=sequence_code,
        version_id=version_id,
        batch_type=batch_type,
        affected_tasks=list(affected_tasks) if isinstance(affected_tasks, list) else None,
    )
    if dataset is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=dataset)


@router.get("/datasets", response_model=dict)
def list_datasets(
    status: str | None = Query(default=None, description="待确认、已确认"),
) -> dict[str, Any]:
    items = service.list_datasets(status)
    return {"items": items, "total": len(items)}


@router.get("/datasets/{dataset_id}", response_model=dict)
def get_dataset(dataset_id: int) -> dict[str, Any]:
    dataset = service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")
    return dataset


@router.post("/datasets/{dataset_id}/enqueue", response_model=ActionResult)
def enqueue_dataset(dataset_id: int) -> ActionResult:
    """把受影响任务写入聚类分群待执行队列；同特征冲突按批次优先级仲裁。"""
    dataset, message = service.enqueue_dataset(dataset_id)
    if dataset is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=dataset)


@router.get("/queue", response_model=dict)
def list_queue(
    dataset_id: int | None = Query(default=None),
    task_code: str | None = Query(default=None),
) -> dict[str, Any]:
    """待执行队列：与已完成结果分开持久化，支持按数据集/任务追溯。"""
    items = service.list_queue(dataset_id, task_code)
    return {"items": items, "total": len(items)}


@router.get("/results", response_model=dict)
def list_results(dataset_id: int | None = Query(default=None)) -> dict[str, Any]:
    """已完成重算结果台账：只增不改，重试不会覆盖既有结果。"""
    items = service.list_results(dataset_id)
    return {"items": items, "total": len(items)}


@router.post("/queue/{queue_id}/execute", response_model=ActionResult)
def execute_queue_task(queue_id: int) -> ActionResult:
    """执行单条待重算任务；对已完成任务重试时只回读既有结果。"""
    result, message = service.execute_task(queue_id)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.post("/datasets/{dataset_id}/execute-pending", response_model=ActionResult)
def execute_pending(dataset_id: int) -> ActionResult:
    """批量补写未完成任务，已完成任务保留原结果不重复计算。"""
    dataset, message = service.execute_pending(dataset_id)
    if not dataset:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=dataset)


@router.post("/datasets/{dataset_id}/confirm", response_model=ActionResult)
def confirm_dataset(dataset_id: int) -> ActionResult:
    """确认增量聚类输入分群明细数据集；仍有待执行任务时不允许确认。"""
    dataset, message = service.confirm_dataset(dataset_id)
    if dataset is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=dataset)


@router.post("/datasets/{dataset_id}/publish", response_model=ActionResult)
def publish_dataset(dataset_id: int) -> ActionResult:
    """发布落定：只允许已确认数据集，变化任务刷新、未变化任务保留（无漂移）。"""
    dataset, message = service.publish_dataset(dataset_id)
    if dataset is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=dataset)


@router.get("/audit", response_model=dict)
def audit(
    sequence_code: str = Query(default="SEQU-0001", description="核对的行为序列编号"),
) -> dict[str, Any]:
    """核对未变化任务无漂移、变化任务重算原因可解释，并给出轮廓系数口径。"""
    return service.audit(sequence_code)
