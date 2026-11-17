import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'
import { usePersistentState } from '@/stores/usePersistentState'

const ENDPOINT = '/api/cluster'
const baseColumns = [
  "任务编号",
  "聚类算法",
  "特征维度",
  "目标聚类数",
  "距离度量",
  "迭代次数上限",
  "聚类标签",
  "轮廓系数"
]
const extraColumns = ["关联序列", "重算状态"]
const columns = [...baseColumns, ...extraColumns]
const sortableColumns = ["任务编号", "轮廓系数", "重算状态"]
const filterColumns = ["任务编号", "聚类算法", "特征维度", "关联序列", "重算状态"]
const actions = [
  "启动聚类",
  "确认完成",
  "归档结果"
]

type Row = Record<string, string | number | null>
type SortOrder = 'asc' | 'desc'

export default function ClusterView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  // 筛选、排序与定位结果持久化：重进页面后仍可在列表中核验关联数据
  const [filters, setFilters] = usePersistentState<Record<string, string>>('cluster:filters', {})
  const [sort, setSort] = usePersistentState<{ field: string; order: SortOrder }>('cluster:sort', { field: '', order: 'asc' })
  const [locatedId, setLocatedId] = usePersistentState<number | null>('cluster:locatedId', null)

  const buildQuery = useCallback(() => {
    const params = new URLSearchParams(filters)
    if (sort.field) {
      params.set('sort_field', sort.field)
      params.set('sort_order', sort.order)
    }
    return params.toString()
  }, [filters, sort])

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}?${buildQuery()}`)
      if (!response.ok) {
        throw new Error('聚类任务列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚类分群列表读取失败')
    }
  }, [buildQuery])

  useEffect(() => {
    void reload()
  }, [reload])

  function toggleSort(field: string) {
    setSort((prev) => {
      if (prev.field !== field) return { field, order: 'asc' }
      return { field, order: prev.order === 'asc' ? 'desc' : 'asc' }
    })
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('聚类分群动作未生效，请稍后重试')
      }
      const payload = await response.json()
      setLocatedId(Number(row.id))
      setInfoMessage(payload.message ?? '动作已生效')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚类分群操作失败')
    }
  }

  const stats = useMemo(() => [
    { label: '聚类任务总数', value: total },
    { label: '已完成任务', value: rows.filter((r) => r.status === '已完成').length },
    { label: '计算中任务', value: rows.filter((r) => r.status === '聚类计算中').length },
  ], [rows, total])

  return (
    <section className="page" data-module="cluster">
      <header className="page-head">
        <div>
          <h2>聚类分群管理</h2>
          <p className="page-desc">管理无监督聚类分群任务；增量重算只刷新变化特征对应的任务，未变化任务保留原标签与轮廓系数。</p>
        </div>
        <div className="page-actions">
          <Link className="btn primary" to="/incremental-cluster">进入增量重算</Link>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出聚类分群清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterColumns.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setFilters({}); setSort({ field: '', order: 'asc' }) }}>重置条件</button>
        {sort.field && <span className="sort-badge">排序：{sort.field} {sort.order === 'asc' ? '升序' : '降序'}</span>}
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>
                {sortableColumns.includes(column) ? (
                  <button className="link" type="button" onClick={() => toggleSort(column)}>
                    {column}{sort.field === column ? (sort.order === 'asc' ? ' ↑' : ' ↓') : ''}
                  </button>
                ) : column}
              </th>
            ))}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={locatedId === Number(row.id) ? 'located-row' : ''}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无符合筛选条件的聚类分群数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条聚类分群记录{locatedId !== null ? '；当前定位结果已保存，重进页面后保持高亮' : ''}</span>
        {infoMessage && <span className="info-text">{infoMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
