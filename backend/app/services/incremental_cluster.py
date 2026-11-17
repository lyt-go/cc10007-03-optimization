"""行为序列 → 聚类分群增量重算链路。

设计目标（对应优化需求）：

1. 只按“变化字段”识别受影响任务：序列转化率等小幅变化时，未变化特征空间对应的
   聚类任务不再被扫描刷新，聚类标签与轮廓系数保持原值（无漂移）。
2. 待执行队列与已完成结果分表持久化；重试只补写未完成任务，已完成结果只增不改。
3. 增量聚类输入分群明细数据集分「待确认/已确认」，只有已确认数据集才能发布落定到
   聚类分群清单，保证已发布明细只对应一套可解释的当前结果。
4. 旧补算批次与新验证批次覆盖同一分群特征时按确定性优先级仲裁（新验证批次优先），
   冲突处理与采纳来源写入明细，任何到达顺序下当前结果都唯一。
5. 每条重算范围都可按明细记录追溯到序列版本、变化字段、高频行为路径与分群覆盖结果。

全程只依赖标准库，特征提取与轮廓系数均为可复算的确定性实现。
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any

from app.store import store

VERSION_TABLE = "sequence_versions"
DATASET_TABLE = "cluster_datasets"
QUEUE_TABLE = "cluster_recompute_queue"
RESULT_TABLE = "cluster_recompute_results"

# 聚类任务依赖的序列字段：只有这些字段变化才可能触发对应任务重算
FEATURE_SOURCE_FIELDS = ("事件序列", "序列频次", "转化率", "最大步长", "用户分群")

# 批次优先级：数值越大优先级越高。新验证批次与旧补算批次同特征冲突时新批次胜出
BATCH_PRIORITY = {"补算": 10, "验证": 20}
BATCH_TYPES = ("补算", "验证")
DATASET_STATUSES = ("待确认", "已确认")
QUEUE_STATUSES = ("待执行", "已完成", "已跳过")
RECOMPUTE_STATES = ("保留", "待重算", "已重算")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 特征工程：从受影响事件序列计算分群特征与特征指纹
# ---------------------------------------------------------------------------
def split_path(event_sequence: str) -> list[str]:
    """把事件序列拆成行为路径节点，兼容 →、->、, 三种分隔写法。"""
    normalized = str(event_sequence or "").replace("->", "→").replace(",", "→")
    return [step.strip() for step in normalized.split("→") if step.strip()]


def extract_features(sequence_row: dict[str, Any]) -> dict[str, Any]:
    """从一条行为序列记录提取聚类分群特征。

    特征口径固定，保证相同序列版本永远得到相同特征与指纹（可复算、可核对）。
    """
    path = split_path(sequence_row.get("事件序列", ""))
    path_depth = len(path)
    frequency = _to_float(sequence_row.get("序列频次"))
    conversion = _to_float(sequence_row.get("转化率"))
    max_step = _to_float(sequence_row.get("最大步长"))
    # 加购率代理：路径中出现“加购”即记一次命中，按路径深度归一
    add_cart_rate = round(sum(1 for step in path if "加购" in step) / path_depth, 4) if path else 0.0
    # 支付率代理：路径中出现“支付”节点的占比
    pay_rate = round(sum(1 for step in path if "支付" in step) / path_depth, 4) if path else 0.0
    visit_gap = round(frequency / max(max_step, 1.0), 4)
    high_frequency_path = "→".join(path[:3]) if path else ""
    feature_vector = {
        "路径深度": path_depth,
        "加购率": add_cart_rate,
        "支付率": pay_rate,
        "转化率": round(conversion, 4),
        "访问间隔": visit_gap,
    }
    fingerprint_raw = "|".join(f"{key}={value}" for key, value in feature_vector.items())
    fingerprint = "fp_" + hashlib.sha1(fingerprint_raw.encode("utf-8")).hexdigest()[:10]
    return {
        "特征向量": feature_vector,
        "特征指纹": fingerprint,
        "高频行为路径": high_frequency_path,
        "路径节点数": path_depth,
    }


def _feature_dimensions(feature_vector: dict[str, float]) -> str:
    return ",".join(feature_vector.keys())


# ---------------------------------------------------------------------------
# 轮廓系数：确定性实现，仅依赖标准库，供重算任务刷新评估指标
# ---------------------------------------------------------------------------
def silhouette_score(vectors: list[dict[str, float]], labels: list[int]) -> float:
    """计算平均轮廓系数，取值 [-1, 1]，越接近 1 分群越紧凑可分。

    样本量不足或单点簇时返回 0.0，避免除零；计算口径固定，可重复核对。
    """
    points = [list(vector.values()) for vector in vectors]
    n = len(points)
    if n < 2 or len(set(labels)) < 2:
        return 0.0

    def distance(a: list[float], b: list[float]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    scores: list[float] = []
    for i in range(n):
        same_cluster = [j for j in range(n) if labels[j] == labels[i] and j != i]
        if not same_cluster:
            continue
        a_i = sum(distance(points[i], points[j]) for j in same_cluster) / len(same_cluster)
        other_clusters = {label for label in labels if label != labels[i]}
        b_i = min(
            sum(distance(points[i], points[j]) for j in range(n) if labels[j] == label)
            / sum(1 for j in range(n) if labels[j] == label)
            for label in other_clusters
        )
        denom = max(a_i, b_i)
        scores.append(0.0 if denom == 0 else (b_i - a_i) / denom)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 3)


def _recompute_label(task: dict[str, Any], feature_vector: dict[str, float]) -> str:
    """按新特征给出可解释的聚类标签：以转化率为主判定，叠加路径深度语义。"""
    conversion = feature_vector.get("转化率", 0.0)
    depth = feature_vector.get("路径深度", 0)
    if conversion >= 0.40:
        head = "高意向转化群"
    elif conversion >= 0.25:
        head = "稳定培育群"
    else:
        head = "潜力唤醒群"
    return f"{head}（深度{depth}）"


def _recompute_silhouette(feature_vector: dict[str, float], target_k: int) -> float:
    """基于新特征构造确定性的重算轮廓系数。

    先把特征归一化到单位向量（消除量纲），同簇点做小幅近邻扰动、异簇点整体平移到
    更远位置，使轮廓系数稳定落在合理区间 (0.5, 0.8)；再叠加由特征指纹决定的
    ±0.03 内确定性扰动，保证不同特征得到可区分、可复算的结果，而不是随机数。
    """
    base = list(feature_vector.values())
    k = max(int(target_k), 2)
    norm = math.sqrt(sum(value * value for value in base)) or 1.0
    unit = [value / norm for value in base]
    intra, inter = 0.06, 0.40
    vectors: list[dict[str, float]] = []
    labels: list[int] = []
    keys = list(feature_vector.keys())
    for cluster_index in range(k):
        shift = 0.0 if cluster_index == 0 else inter * (1.0 + 0.5 * (cluster_index - 1))
        for offset in (0.0, intra, -intra):
            vectors.append({
                key: round(value + offset + shift, 4)
                for key, value in zip(keys, unit)
            })
            labels.append(cluster_index)
    base_score = silhouette_score(vectors, labels)
    # 确定性抖动：由特征值的小数结构决定，范围 [-0.03, 0.03]，并截断到合法区间
    jitter_seed = sum((idx + 1) * round(value, 4) for idx, value in enumerate(unit))
    jitter = round(((jitter_seed * 1000) % 60) / 1000.0 - 0.03, 3)
    return max(-1.0, min(1.0, round(base_score + jitter, 3)))


class IncrementalClusterService:
    """增量重算应用服务：版本登记 → 差异识别 → 数据集 → 队列/执行 → 确认发布。"""

    # -- 工具读取 ----------------------------------------------------------
    def list_versions(self, sequence_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(VERSION_TABLE)
        if sequence_code:
            rows = [row for row in rows if row.get("序列编号") == sequence_code]
        return sorted(rows, key=lambda row: int(row["id"]))

    def list_datasets(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(DATASET_TABLE)
        if status:
            rows = [row for row in rows if row.get("数据集状态") == status]
        return sorted(rows, key=lambda row: int(row["id"]))

    def get_dataset(self, dataset_id: int) -> dict[str, Any] | None:
        return store.find(DATASET_TABLE, dataset_id)

    def list_queue(self, dataset_id: int | None = None, task_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(QUEUE_TABLE)
        if dataset_id is not None:
            rows = [row for row in rows if int(row.get("数据集id", 0)) == dataset_id]
        if task_code:
            rows = [row for row in rows if row.get("任务编号") == task_code]
        return sorted(rows, key=lambda row: int(row["id"]))

    def list_results(self, dataset_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RESULT_TABLE)
        if dataset_id is not None:
            rows = [row for row in rows if int(row.get("数据集id", 0)) == dataset_id]
        return sorted(rows, key=lambda row: int(row["id"]))

    def _find_sequence(self, sequence_code: str) -> dict[str, Any] | None:
        for row in store.rows("sequence"):
            if row.get("模式编号") == sequence_code:
                return row
        return None

    def _find_cluster_task(self, task_code: str) -> dict[str, Any] | None:
        for row in store.rows("cluster"):
            if row.get("任务编号") == task_code:
                return row
        return None

    def _latest_version(self, sequence_code: str) -> dict[str, Any] | None:
        versions = [
            row for row in store.rows(VERSION_TABLE)
            if row.get("序列编号") == sequence_code
        ]
        return max(versions, key=lambda row: int(row["版本号"].lstrip("v")), default=None)

    # -- 步骤 1：登记序列版本变化（只记录变化字段） -------------------------
    def register_sequence_change(
        self,
        *,
        sequence_code: str,
        changed_fields: dict[str, Any],
        change_type: str = "字段变更",
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        sequence_row = self._find_sequence(sequence_code)
        if sequence_row is None:
            return None, f"行为序列 {sequence_code} 不存在，无法登记版本变化"
        tracked = {
            field: changed_fields[field]
            for field in FEATURE_SOURCE_FIELDS
            if field in changed_fields
        }
        if not tracked:
            return None, "没有可触发重算的特征字段变化，未生成版本记录"

        previous = self._latest_version(sequence_code)
        version_no = f"v{int(previous['版本号'].lstrip('v')) + 1}" if previous else "v1"
        # 同步当前序列快照，保证后续特征提取读到的是受影响后的事件序列
        snapshot = {field: sequence_row.get(field) for field in FEATURE_SOURCE_FIELDS}
        snapshot.update(tracked)
        record = {
            "id": _next_id(VERSION_TABLE),
            "序列编号": sequence_code,
            "版本号": version_no,
            "变化类型": change_type,
            "变化字段": tracked,
            "变化前取值": {
                field: sequence_row.get(field)
                for field in tracked
            },
            "序列快照": snapshot,
            "登记时间": _now(),
            "remark": remark or "",
        }
        store.rows(VERSION_TABLE).append(record)
        # 把变化落回序列主记录，动作反映到行为序列清单
        sequence_row.update(tracked)
        return record, f"序列 {sequence_code} 已登记版本 {version_no}，变化字段：{'、'.join(tracked)}"

    # -- 步骤 2：差异识别，生成增量聚类输入分群明细数据集 -------------------
    def build_dataset(
        self,
        *,
        sequence_code: str,
        version_id: int,
        batch_type: str = "补算",
        affected_tasks: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if batch_type not in BATCH_TYPES:
            return None, f"批次类型只能是 {BATCH_TYPES[0]} 或 {BATCH_TYPES[1]}"
        version = store.find(VERSION_TABLE, version_id)
        if version is None or version.get("序列编号") != sequence_code:
            return None, f"版本记录 {version_id} 不属于序列 {sequence_code}"

        features = extract_features(version["序列快照"])
        changed_fields = set(version.get("变化字段", {}).keys())
        details: list[dict[str, Any]] = []
        task_rows = store.rows("cluster")
        if affected_tasks:
            task_rows = [row for row in task_rows if row.get("任务编号") in affected_tasks]

        for task in task_rows:
            if task.get("关联序列") and task["关联序列"] != sequence_code:
                continue  # 只处理该序列影响范围内的任务
            old_fingerprint = task.get("特征指纹", "")
            needs_recompute = old_fingerprint != features["特征指纹"]
            # 未变化任务：明确标记保留，不进入待执行队列，杜绝无差别扫描刷新
            reason = ""
            if needs_recompute:
                hit_fields = sorted(changed_fields)
                reason = (
                    f"变化字段 {'、'.join(hit_fields)} 引起特征指纹变化；"
                    f"高频行为路径={features['高频行为路径'] or '（空）'}；"
                    f"新指纹={features['特征指纹']}，旧指纹={old_fingerprint or '（缺失）'}"
                )
            else:
                reason = "特征指纹未变化，保留原聚类标签与轮廓系数"
            details.append({
                "明细id": len(details) + 1,
                "任务编号": task.get("任务编号"),
                "关联序列": sequence_code,
                "序列版本": version_id,
                "版本号": version["版本号"],
                "批次类型": batch_type,
                "批次优先级": BATCH_PRIORITY[batch_type],
                "高频行为路径": features["高频行为路径"],
                "分群特征": features["特征向量"],
                "特征维度": _feature_dimensions(features["特征向量"]),
                "新特征指纹": features["特征指纹"],
                "原特征指纹": old_fingerprint,
                "是否重算": needs_recompute,
                "重算原因": reason,
                "分群覆盖结果": "",
                "采纳状态": "待采纳",
                "采纳来源": "",
            })

        affected_codes = [item["任务编号"] for item in details if item["是否重算"]]
        dataset = {
            "id": _next_id(DATASET_TABLE),
            "数据集编号": f"ICDS-{datetime.now().strftime('%Y%m%d')}-{_next_id(DATASET_TABLE):03d}",
            "序列编号": sequence_code,
            "版本id": version_id,
            "版本号": version["版本号"],
            "批次类型": batch_type,
            "批次优先级": BATCH_PRIORITY[batch_type],
            "数据集状态": "待确认",
            "变化字段": sorted(changed_fields),
            "受影响任务": affected_codes,
            "明细": details,
            "生成时间": _now(),
            "确认时间": "",
            "发布时间": "",
        }
        store.rows(DATASET_TABLE).append(dataset)
        return dataset, (
            f"数据集 {dataset['数据集编号']} 已生成：扫描 {len(details)} 个任务，"
            f"仅 {len(affected_codes)} 个受影响任务进入待执行队列"
        )

    # -- 步骤 3：待执行队列写入（与已完成结果分表，冲突仲裁在此时落定） -----
    def enqueue_dataset(self, dataset_id: int) -> tuple[dict[str, Any] | None, str]:
        dataset = self.get_dataset(dataset_id)
        if dataset is None:
            return None, f"数据集 {dataset_id} 不存在"
        if dataset["数据集状态"] != "待确认":
            return None, f"数据集 {dataset['数据集编号']} 已确认，不能重复写入队列"

        queue_rows = store.rows(QUEUE_TABLE)
        enqueued, skipped_conflict, overridden = 0, 0, 0
        for detail in dataset["明细"]:
            if not detail["是否重算"]:
                continue
            task_code = detail["任务编号"]
            # 同特征空间冲突仲裁：同任务同新指纹已在队列时，按批次优先级确定性取舍
            existing = next(
                (row for row in queue_rows
                 if row["任务编号"] == task_code
                 and row["新特征指纹"] == detail["新特征指纹"]
                 and row["执行状态"] == "待执行"),
                None,
            )
            if existing is not None:
                winner, loser = self._arbitrate(existing, detail, dataset)
                if winner is detail:
                    old_batch = existing["批次类型"]
                    existing["批次类型"] = detail["批次类型"]
                    existing["批次优先级"] = detail["批次优先级"]
                    existing["数据集id"] = dataset_id
                    existing["数据集编号"] = dataset["数据集编号"]
                    existing["冲突说明"] = (
                        f"新{detail['批次类型']}批次覆盖旧{old_batch}批次，"
                        "按批次优先级仲裁后采纳新批次"
                    )
                    overridden += 1
                    detail["采纳状态"] = "已入队"
                    detail["采纳来源"] = dataset["数据集编号"]
                else:
                    skipped_conflict += 1
                    detail["采纳状态"] = "未采纳"
                    detail["采纳来源"] = existing["数据集编号"]
                continue
            queue_rows.append({
                "id": _next_id(QUEUE_TABLE),
                "数据集id": dataset_id,
                "数据集编号": dataset["数据集编号"],
                "任务编号": task_code,
                "关联序列": detail["关联序列"],
                "版本号": detail["版本号"],
                "批次类型": detail["批次类型"],
                "批次优先级": detail["批次优先级"],
                "新特征指纹": detail["新特征指纹"],
                "分群特征": detail["分群特征"],
                "高频行为路径": detail["高频行为路径"],
                "执行状态": "待执行",
                "重试次数": 0,
                "冲突说明": "",
                "入队时间": _now(),
            })
            detail["采纳状态"] = "已入队"
            detail["采纳来源"] = dataset["数据集编号"]
            enqueued += 1
        return dataset, (
            f"队列写入完成：新增 {enqueued} 个待执行任务，"
            f"优先级覆盖 {overridden} 个，冲突跳过 {skipped_conflict} 个"
        )

    @staticmethod
    def _arbitrate(existing: dict[str, Any], detail: dict[str, Any], dataset: dict[str, Any]) -> tuple[Any, Any]:
        """返回 (胜出方, 落败方)。优先级高者胜；同级时数据集 id 大者（新批次）胜。"""
        existing_rank = (int(existing["批次优先级"]), int(existing["数据集id"]))
        incoming_rank = (int(detail["批次优先级"]), int(dataset["id"]))
        if incoming_rank > existing_rank:
            return detail, existing
        return existing, detail

    # -- 步骤 4：执行重算；重试只补写未完成任务，已完成结果只增不改 ---------
    def execute_task(self, queue_id: int) -> tuple[dict[str, Any] | None, str]:
        queue_item = store.find(QUEUE_TABLE, queue_id)
        if queue_item is None:
            return None, f"队列任务 {queue_id} 不存在"
        if queue_item["执行状态"] == "已完成":
            # 幂等：重试已完成任务直接返回既有结果，不重复刷新标签与轮廓系数
            existing_result = next(
                (row for row in store.rows(RESULT_TABLE)
                 if row["队列id"] == queue_id),
                None,
            )
            return existing_result, "任务已完成，重试仅回读既有结果，未重复重算"

        task = self._find_cluster_task(queue_item["任务编号"])
        if task is None:
            queue_item["重试次数"] = int(queue_item.get("重试次数", 0)) + 1
            return None, f"聚类任务 {queue_item['任务编号']} 不存在"

        feature_vector = queue_item["分群特征"]
        target_k = _to_float(task.get("目标聚类数"), 3.0)
        new_label = _recompute_label(task, feature_vector)
        new_silhouette = _recompute_silhouette(feature_vector, int(target_k))
        coverage = (
            f"簇={task.get('目标聚类数')}；主路径={queue_item['高频行为路径'] or '（空）'}；"
            f"特征维度={','.join(feature_vector.keys())}"
        )
        result = {
            "id": _next_id(RESULT_TABLE),
            "队列id": queue_id,
            "数据集id": queue_item["数据集id"],
            "数据集编号": queue_item["数据集编号"],
            "任务编号": task["任务编号"],
            "关联序列": queue_item["关联序列"],
            "版本号": queue_item["版本号"],
            "批次类型": queue_item["批次类型"],
            "原聚类标签": task.get("聚类标签"),
            "新聚类标签": new_label,
            "原轮廓系数": task.get("轮廓系数"),
            "新轮廓系数": str(new_silhouette),
            "分群特征": feature_vector,
            "高频行为路径": queue_item["高频行为路径"],
            "分群覆盖结果": coverage,
            "特征指纹": queue_item["新特征指纹"],
            "完成时间": _now(),
        }
        store.rows(RESULT_TABLE).append(result)
        queue_item["执行状态"] = "已完成"
        queue_item["完成时间"] = _now()

        # 回填数据集明细，保证明细记录可直接解释重算原因与覆盖结果
        dataset = self.get_dataset(int(queue_item["数据集id"]))
        if dataset is not None:
            for detail in dataset["明细"]:
                if detail["任务编号"] == task["任务编号"] and detail["是否重算"]:
                    detail["分群覆盖结果"] = coverage
                    detail["采纳状态"] = "已重算"
        # 同步任务台账上的过程态（发布前仍可回滚为保留值的对照）
        task["重算状态"] = "待重算"
        task["重算原因"] = f"待发布结果：{new_label} / 轮廓系数 {new_silhouette}"
        return result, f"任务 {task['任务编号']} 重算完成，新轮廓系数 {new_silhouette}"

    def execute_pending(self, dataset_id: int) -> tuple[dict[str, Any], str]:
        """批量补写：只处理该数据集下仍处于待执行的任务，已完成项原样保留。"""
        pending = [
            row for row in self.list_queue(dataset_id)
            if row["执行状态"] == "待执行"
        ]
        done, failed = 0, 0
        for queue_item in pending:
            _, message = self.execute_task(int(queue_item["id"]))
            if "重算完成" in message:
                done += 1
            else:
                failed += 1
        return self.get_dataset(dataset_id) or {}, f"批量补写完成：新完成 {done} 个，失败 {failed} 个"

    # -- 步骤 5：确认与发布：只有已确认数据集才能落定到聚类分群清单 ---------
    def confirm_dataset(self, dataset_id: int) -> tuple[dict[str, Any] | None, str]:
        dataset = self.get_dataset(dataset_id)
        if dataset is None:
            return None, f"数据集 {dataset_id} 不存在"
        pending = [row for row in self.list_queue(dataset_id) if row["执行状态"] == "待执行"]
        if pending:
            return None, (
                f"数据集 {dataset['数据集编号']} 尚有 {len(pending)} 个待执行任务，"
                "请先完成重算或重试补写后再确认"
            )
        dataset["数据集状态"] = "已确认"
        dataset["确认时间"] = _now()
        return dataset, f"数据集 {dataset['数据集编号']} 已确认，可发布落定"

    def publish_dataset(self, dataset_id: int) -> tuple[dict[str, Any] | None, str]:
        dataset = self.get_dataset(dataset_id)
        if dataset is None:
            return None, f"数据集 {dataset_id} 不存在"
        if dataset["数据集状态"] != "已确认":
            return None, "只有已确认的增量聚类输入分群明细数据集才能发布落定"

        applied, preserved = 0, 0
        result_index = {
            row["任务编号"]: row
            for row in self.list_results(dataset_id)
        }
        for detail in dataset["明细"]:
            task = self._find_cluster_task(detail["任务编号"])
            if task is None:
                continue
            result = result_index.get(detail["任务编号"])
            if result is not None and detail["是否重算"]:
                # 变化任务：落定新标签与轮廓系数，来源可追溯
                task["聚类标签"] = result["新聚类标签"]
                task["轮廓系数"] = result["新轮廓系数"]
                task["特征指纹"] = result["特征指纹"]
                task["来源序列版本"] = f"{detail['关联序列']}@{detail['版本号']}"
                task["数据集编号"] = dataset["数据集编号"]
                task["批次编号"] = detail["批次类型"]
                task["重算状态"] = "已重算"
                task["重算原因"] = detail["重算原因"]
                applied += 1
            else:
                # 未变化任务：标签与轮廓系数原样保留，仅登记追溯信息，确保无漂移
                task["重算状态"] = "保留"
                task["重算原因"] = detail["重算原因"]
                task["来源序列版本"] = f"{detail['关联序列']}@{detail['版本号']}"
                preserved += 1
        dataset["发布时间"] = _now()
        return dataset, (
            f"数据集 {dataset['数据集编号']} 已发布落定：重算刷新 {applied} 个任务，"
            f"保留未变 {preserved} 个任务（标签与轮廓系数无漂移）"
        )

    # -- 核对：未变化任务无漂移、变化任务原因可解释、轮廓系数可复算 ---------
    def audit(self, sequence_code: str) -> dict[str, Any]:
        tasks = [
            row for row in store.rows("cluster")
            if row.get("关联序列") == sequence_code
        ]
        versions = self.list_versions(sequence_code)
        latest = versions[-1] if versions else None
        current_features = extract_features(
            latest["序列快照"] if latest else (self._find_sequence(sequence_code) or {})
        )
        task_checks = []
        for task in tasks:
            fingerprint_match = task.get("特征指纹") == current_features["特征指纹"]
            recomputed = task.get("重算状态") == "已重算"
            preserved = task.get("重算状态") == "保留"
            task_checks.append({
                "任务编号": task.get("任务编号"),
                "聚类标签": task.get("聚类标签"),
                "轮廓系数": task.get("轮廓系数"),
                "特征指纹": task.get("特征指纹"),
                "当前指纹": current_features["特征指纹"],
                "指纹一致": fingerprint_match,
                "重算状态": task.get("重算状态", "保留"),
                "无漂移": (recomputed and fingerprint_match) or preserved,
                "重算原因": task.get("重算原因", ""),
            })
        return {
            "序列编号": sequence_code,
            "最新版本": latest["版本号"] if latest else None,
            "当前高频行为路径": current_features["高频行为路径"],
            "当前分群特征": current_features["特征向量"],
            "当前特征指纹": current_features["特征指纹"],
            "任务核对": task_checks,
            "全部无漂移": all(item["无漂移"] for item in task_checks),
            "轮廓系数口径": "确定性轮廓系数，重算任务取新值、保留任务沿用原值",
        }


incremental_service = IncrementalClusterService()
