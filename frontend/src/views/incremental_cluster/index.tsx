import { Fragment, useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'
import { usePersistentState } from '@/stores/usePersistentState'

const API = '/api/incremental-cluster'

type AnyRow = Record<string, any>

async function post(path: string): Promise<{ ok: boolean; message: string; entry?: AnyRow }> {
  const response = await request(path, { method: 'POST' })
  return response.json()
}

export default function IncrementalClusterView() {
  const [sequenceCode, setSequenceCode] = usePersistentState<string>('incremental:sequenceCode', 'SEQU-0001')
  const [expandedDataset, setExpandedDataset] = usePersistentState<number | null>('incremental:datasetId', null)
  const [versions, setVersions] = useState<AnyRow[]>([])
  const [datasets, setDatasets] = useState<AnyRow[]>([])
  const [queue, setQueue] = useState<AnyRow[]>([])
  const [results, setResults] = useState<AnyRow[]>([])
  const [audit, setAudit] = useState<AnyRow | null>(null)
  const [message, setMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [busyId, setBusyId] = useState('')

  const refresh = useCallback(async () => {
    setErrorMessage('')
    const query = encodeURIComponent(sequenceCode)
    const [v, d, q, r, a] = await Promise.all([
      request(`${API}/sequence-changes?sequence_code=${query}`).then((x) => x.json()),
      request(`${API}/datasets`).then((x) => x.json()),
      request(`${API}/queue`).then((x) => x.json()),
      request(`${API}/results`).then((x) => x.json()),
      request(`${API}/audit?sequence_code=${query}`).then((x) => x.json()),
    ])
    setVersions(v.items ?? [])
    setDatasets((d.items ?? []).filter((item: AnyRow) => item['序列编号'] === sequenceCode))
    setQueue((q.items ?? []).filter((item: AnyRow) => item['关联序列'] === sequenceCode))
    setResults((r.items ?? []).filter((item: AnyRow) => item['关联序列'] === sequenceCode))
    setAudit(a)
  }, [sequenceCode])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handle(path: string, busyKey: string) {
    setBusyId(busyKey)
    setMessage('')
    setErrorMessage('')
    try {
      const payload = await post(path)
      if (!payload.ok) {
        setErrorMessage(payload.message)
      } else {
        setMessage(payload.message)
      }
      await refresh()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '操作失败')
    } finally {
      setBusyId('')
    }
  }

  async function buildDataset(versionId: number, batchType: string) {
    setBusyId(`build-${versionId}-${batchType}`)
    setMessage('')
    setErrorMessage('')
    try {
      const response = await request(`${API}/datasets`, {
        method: 'POST',
        body: JSON.stringify({ values: { sequence_code: sequenceCode, version_id: versionId, batch_type: batchType } }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message)
      } else {
        setMessage(payload.message)
        setExpandedDataset(payload.entry.id)
      }
      await refresh()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据集生成失败')
    } finally {
      setBusyId('')
    }
  }

  return (
    <section className="page" data-module="incremental-cluster">
      <header className="page-head">
        <div>
          <h2>聚类增量重算控制台</h2>
          <p className="page-desc">
            按序列变化字段只重算受影响任务：待执行队列与已完成结果分表持久化，重试只补写未完成任务；
            仅已确认数据集可发布落定，旧补算与新验证同特征冲突按优先级确定性仲裁。
          </p>
        </div>
        <div className="page-actions">
          <label className="filter-item">
            <span>行为序列编号</span>
            <input value={sequenceCode} onChange={(e) => setSequenceCode(e.target.value || 'SEQU-0001')} />
          </label>
          <button className="btn" type="button" onClick={() => void refresh()}>刷新</button>
        </div>
      </header>

      {audit && (
        <div className="stat-row">
          <article className="stat-card">
            <span className="stat-label">最新版本 / 特征指纹</span>
            <strong className="stat-value" style={{ fontSize: 14 }}>{String(audit['最新版本'] ?? '—')} / {String(audit['当前特征指纹'])}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">高频行为路径</span>
            <strong className="stat-value" style={{ fontSize: 14 }}>{String(audit['当前高频行为路径'] ?? '—')}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">漂移核对</span>
            <strong className="stat-value" style={{ color: audit['全部无漂移'] ? '#027a48' : '#b42318', fontSize: 16 }}>
              {audit['全部无漂移'] ? '全部无漂移' : '存在漂移'}
            </strong>
          </article>
        </div>
      )}

      <h3 className="section-title">① 序列版本变化记录（只含变化字段）</h3>
      <table className="data-table">
        <thead>
          <tr><th>版本号</th><th>变化类型</th><th>变化字段</th><th>变化前 → 变化后</th><th>登记时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          {versions.map((row) => (
            <tr key={String(row.id)}>
              <td>{String(row['版本号'])}</td>
              <td>{String(row['变化类型'])}</td>
              <td>{Object.keys(row['变化字段'] ?? {}).join('、')}</td>
              <td>
                {Object.entries(row['变化字段'] ?? {}).map(([field, value]) => (
                  <span key={field} className="diff-chip">
                    {field}：{String(row['变化前取值']?.[field] ?? '—')} → {String(value)}
                  </span>
                ))}
              </td>
              <td>{String(row['登记时间'])}</td>
              <td className="row-actions">
                <button className="link" type="button" disabled={busyId === `build-${row.id}-补算`}
                  onClick={() => void buildDataset(Number(row.id), '补算')}>生成补算数据集</button>
                <button className="link" type="button" disabled={busyId === `build-${row.id}-验证`}
                  onClick={() => void buildDataset(Number(row.id), '验证')}>生成验证数据集</button>
              </td>
            </tr>
          ))}
          {versions.length === 0 && (
            <tr><td className="empty-state" colSpan={6}>暂无版本变化记录，可先在「行为序列」页登记变化</td></tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">② 增量聚类输入分群明细数据集（确认后才能发布落定）</h3>
      <table className="data-table">
        <thead>
          <tr><th>数据集编号</th><th>版本</th><th>批次</th><th>状态</th><th>受影响任务</th><th>生成/确认/发布时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          {datasets.map((row) => (
            <Fragment key={String(row.id)}>
              <tr className={expandedDataset === Number(row.id) ? 'located-row' : ''}>
                <td>
                  <button className="link" type="button" onClick={() => setExpandedDataset(expandedDataset === Number(row.id) ? null : Number(row.id))}>
                    {String(row['数据集编号'])} {expandedDataset === Number(row.id) ? '▾' : '▸'}
                  </button>
                </td>
                <td>{String(row['版本号'])}</td>
                <td>{String(row['批次类型'])}（优先级 {String(row['批次优先级'])}）</td>
                <td><span className={`status-tag status-${row['数据集状态']}`}>{String(row['数据集状态'])}</span></td>
                <td>{(row['受影响任务'] ?? []).join('、') || '无（全部保留）'}</td>
                <td style={{ fontSize: 12 }}>
                  生成 {String(row['生成时间'])}<br />
                  确认 {String(row['确认时间'] || '—')}<br />
                  发布 {String(row['发布时间'] || '—')}
                </td>
                <td className="row-actions">
                  <button className="link" type="button" disabled={busyId === `enq-${row.id}`}
                    onClick={() => void handle(`${API}/datasets/${row.id}/enqueue`, `enq-${row.id}`)}>写入队列</button>
                  <button className="link" type="button" disabled={busyId === `exec-${row.id}`}
                    onClick={() => void handle(`${API}/datasets/${row.id}/execute-pending`, `exec-${row.id}`)}>批量补写</button>
                  <button className="link" type="button" disabled={busyId === `conf-${row.id}`}
                    onClick={() => void handle(`${API}/datasets/${row.id}/confirm`, `conf-${row.id}`)}>确认</button>
                  <button className="link" type="button" disabled={busyId === `pub-${row.id}`}
                    onClick={() => void handle(`${API}/datasets/${row.id}/publish`, `pub-${row.id}`)}>发布落定</button>
                </td>
              </tr>
              {expandedDataset === Number(row.id) && (
                <tr key={`${String(row.id)}-detail`}>
                  <td colSpan={7}>
                    <table className="data-table sub-table">
                      <thead>
                        <tr><th>任务编号</th><th>高频行为路径</th><th>分群特征</th><th>指纹（原 → 新）</th><th>是否重算</th><th>重算原因 / 覆盖结果</th><th>采纳</th></tr>
                      </thead>
                      <tbody>
                        {(row['明细'] ?? []).map((detail: AnyRow) => (
                          <tr key={String(detail['明细id'])}>
                            <td>{String(detail['任务编号'])}</td>
                            <td>{String(detail['高频行为路径'])}</td>
                            <td style={{ fontSize: 12 }}>{JSON.stringify(detail['分群特征'])}</td>
                            <td style={{ fontSize: 12 }}>{String(detail['原特征指纹'] || '—')} → {String(detail['新特征指纹'])}</td>
                            <td>{detail['是否重算'] ? '重算' : '保留'}</td>
                            <td style={{ fontSize: 12 }}>
                              {String(detail['重算原因'])}
                              {detail['分群覆盖结果'] ? <><br />覆盖：{String(detail['分群覆盖结果'])}</> : null}
                            </td>
                            <td>{String(detail['采纳状态'])}{detail['采纳来源'] ? `（${String(detail['采纳来源'])}）` : ''}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {datasets.length === 0 && (
            <tr><td className="empty-state" colSpan={7}>暂无数据集，请先基于版本变化记录生成</td></tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">③ 聚类分群待执行队列（与已完成结果分表持久化）</h3>
      <table className="data-table">
        <thead>
          <tr><th>队列id</th><th>任务编号</th><th>版本</th><th>批次</th><th>特征指纹</th><th>执行状态</th><th>重试次数</th><th>冲突说明</th><th>操作</th></tr>
        </thead>
        <tbody>
          {queue.map((row) => (
            <tr key={String(row.id)}>
              <td>{String(row.id)}</td>
              <td>{String(row['任务编号'])}</td>
              <td>{String(row['版本号'])}</td>
              <td>{String(row['批次类型'])}</td>
              <td style={{ fontSize: 12 }}>{String(row['新特征指纹'])}</td>
              <td><span className={`status-tag status-${row['执行状态']}`}>{String(row['执行状态'])}</span></td>
              <td>{String(row['重试次数'])}</td>
              <td style={{ fontSize: 12 }}>{String(row['冲突说明'] || '—')}</td>
              <td className="row-actions">
                <button className="link" type="button" disabled={busyId === `q-${row.id}`}
                  onClick={() => void handle(`${API}/queue/${row.id}/execute`, `q-${row.id}`)}>
                  {row['执行状态'] === '已完成' ? '重试（幂等回读）' : '执行重算'}
                </button>
              </td>
            </tr>
          ))}
          {queue.length === 0 && (
            <tr><td className="empty-state" colSpan={9}>待执行队列为空</td></tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">④ 已完成重算结果台账（只增不改，含轮廓系数核对）</h3>
      <table className="data-table">
        <thead>
          <tr><th>任务编号</th><th>聚类标签（原 → 新）</th><th>轮廓系数（原 → 新）</th><th>高频行为路径</th><th>分群覆盖结果</th><th>完成时间</th></tr>
        </thead>
        <tbody>
          {results.map((row) => (
            <tr key={String(row.id)}>
              <td>{String(row['任务编号'])}</td>
              <td>{String(row['原聚类标签'])} → <strong>{String(row['新聚类标签'])}</strong></td>
              <td>{String(row['原轮廓系数'])} → <strong>{String(row['新轮廓系数'])}</strong></td>
              <td style={{ fontSize: 12 }}>{String(row['高频行为路径'])}</td>
              <td style={{ fontSize: 12 }}>{String(row['分群覆盖结果'])}</td>
              <td>{String(row['完成时间'])}</td>
            </tr>
          ))}
          {results.length === 0 && (
            <tr><td className="empty-state" colSpan={6}>暂无已完成结果</td></tr>
          )}
        </tbody>
      </table>

      {audit && (
        <>
          <h3 className="section-title">⑤ 核对与对应（{sequenceCode} → CLUS 任务）</h3>
          <table className="data-table">
            <thead>
              <tr><th>任务编号</th><th>聚类标签</th><th>轮廓系数</th><th>指纹一致</th><th>重算状态</th><th>无漂移</th><th>重算原因</th></tr>
            </thead>
            <tbody>
              {(audit['任务核对'] ?? []).map((item: AnyRow) => (
                <tr key={String(item['任务编号'])}>
                  <td>{String(item['任务编号'])}</td>
                  <td>{String(item['聚类标签'])}</td>
                  <td>{String(item['轮廓系数'])}</td>
                  <td>{item['指纹一致'] ? '一致' : '不一致'}</td>
                  <td>{String(item['重算状态'])}</td>
                  <td style={{ color: item['无漂移'] ? '#027a48' : '#b42318' }}>{item['无漂移'] ? '是' : '否'}</td>
                  <td style={{ fontSize: 12 }}>{String(item['重算原因'] || '—')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="page-desc">轮廓系数口径：{String(audit['轮廓系数口径'])}</p>
        </>
      )}

      <footer className="page-foot">
        <span>当前序列 {sequenceCode}；定位的数据集与筛选条件已保存，重进页面后自动恢复</span>
        {message && <span className="info-text">{message}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
