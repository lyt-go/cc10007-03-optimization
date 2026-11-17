# 智能运营决策分析平台

面向用户行为序列分析、RFM分层、流失预警、智能推荐、归因建模、活动效果评估与实时运营决策的数据分析工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为序列 | `sequence` | 序列模式 | 模式编号、用户分群、事件序列 |
| RFM分层 | `rfm` | 分层规则 | 规则编号、最近消费天界、消费频次界 |
| 流失预警 | `churn` | 预警模型 | 模型编号、模型名称、特征变量 |
| 智能推荐 | `recommend` | 推荐策略 | 策略编号、推荐场景、召回算法 |
| 归因建模 | `attrmodel` | 归因模型 | 模型编号、模型名称、归因逻辑 |
| 活动评估 | `activity` | 活动方案 | 方案编号、活动名称、活动类型 |
| 实时决策 | `realtime` | 决策规则 | 规则编号、触发事件、决策条件 |
| 预算分配 | `budget` | 预算方案 | 方案编号、预算总额、渠道分配比例 |
| LTV估算 | `lifetime` | LTV模型 | 模型编号、模型名称、估算方法 |
| 实验变体 | `abvariant` | 变体方案 | 变体编号、关联实验、变体名称 |
| 运营看板 | `dashboard` | 看板视图 | 视图编号、视图名称、指标组合 |
| 评分卡 | `scorecard` | 评分卡 | 评分编号、评分对象、特征变量组 |
| 聚类分群 | `cluster` | 聚类任务 | 任务编号、聚类算法、特征维度 |
| 增量重算 | `incremental_cluster` | 序列→聚类增量重算链路 | 版本变化、增量数据集、待执行队列、已完成结果 |
| 对比分析 | `comparison` | 对比组 | 对比编号、对比名称、实验组筛选 |
| 数据漂移 | `datadrift` | 漂移检测 | 检测编号、监控数据集、基线版本 |
| 推送计划 | `pushplan` | 推送方案 | 方案编号、推送内容、目标人群 |
| 指标归因 | `metricdrill` | 归因分析 | 分析编号、目标指标、波动方向 |
| 策略库 | `tacticlib` | 运营策略 | 策略编号、策略名称、策略类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 行为序列 → 聚类分群 增量重算链路

针对“序列转化率小幅变化也全量扫描聚类任务、未变标签与轮廓系数反复刷新”的问题，
`incremental_cluster` 模块实现按变化字段只重算受影响任务的增量链路（SEQU-0001 为主样本）：

1. **版本变化登记**（`POST /api/incremental-cluster/sequence-changes`）：只记录变化字段与
   变化前取值，变化同步回行为序列清单。非特征字段变化不触发重算。
2. **差异识别生成增量数据集**（`POST .../datasets`）：从受影响事件序列提取分群特征与
   特征指纹，指纹未变的任务标记“保留”、不进队列；变化任务的重算原因含高频行为路径与
   新旧指纹，可追溯。数据集初始为“待确认”。
3. **待执行队列与已完成结果分表持久化**（`cluster_recompute_queue` /
   `cluster_recompute_results`）：队列只放待重算任务，结果台账只增不改；执行/重试对已
   完成任务幂等回读，批量补写只处理未完成项。
4. **确认后才能发布落定**（`confirm` → `publish`）：仍有待执行任务时禁止确认，未确认
   数据集禁止发布；发布时变化任务刷新标签与轮廓系数，未变化任务原样保留（无漂移）。
5. **同特征空间冲突仲裁**：旧补算批次与新验证批次覆盖同一特征时，按批次优先级
   （验证 > 补算）+ 数据集 id 确定性取舍，与到达顺序无关，已发布明细只对应一套可解释结果。
6. **核对**（`GET .../audit`）：核对 CLUS 任务指纹一致性、无漂移、重算原因与轮廓系数口径。

新增的内存表（沿用 `store` 的“表名 → 记录列表”约定，见 `seed.py`）：
`sequence_versions`、`cluster_datasets`、`cluster_recompute_queue`、`cluster_recompute_results`。
前端在「增量重算」页（`/incremental-cluster`）提供完整控制台；行为序列与聚类分群列表的
筛选、排序与当前定位结果通过 `localStorage` 持久化，保存后重进页面仍保留。

领域：数据工程
子方向：数据分析与数据应用
