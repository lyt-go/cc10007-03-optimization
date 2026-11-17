import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { usePersistentState } from '@/stores/usePersistentState'

const ENDPOINT = '/api/sequence'
const CHANGE_ENDPOINT = '/api/incremental-cluster/sequence-changes'
const columns = [
  "模式编号",
  "用户分群",
  "事件序列",
  "最大步长",
  "最小支持度",
  "时间窗口天",
  "序列频次",
  "转化率"
]
const sortableColumns = ["模式编号", "序列频次", "转化率", "时间窗口天"]
const actions = [
  "发起挖掘",
  "确认模式",
  "归档模式"
]

type Row = Record<string, string | number | null>
type SortOrder = 'asc' | 'desc'

export default function SequenceView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  // 筛选、排序与定位结果都持久化，重进页面后保留当前定位结果
  const [filters, setFilters] = usePersistentState<Record<string, string>>('sequence:filters', {})
  const [sort, setSort] = usePersistentState<{ field: string; order: SortOrder }>('sequence:sort', { field: '', order: 'asc' })
  const [locatedId, setLocatedId] = usePersistentState<number | null>('sequence:locatedId', null)
  const [changeTarget, setChangeTarget] = useState<Row | null>(null)
  const [changeForm, setChangeForm] = useState<Record<string, string>>({ 转化率: '', 事件序列: '' })
  const [saving, setSaving] = useState(false)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

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
        throw new Error('序列模式列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '行为序列列表读取失败')
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
        throw new Error('行为序列动作未生效，请稍后重试')
      }
      const payload = await response.json()
      setLocatedId(Number(row.id))
      setInfoMessage(payload.message ?? '动作已生效')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '行为序列操作失败')
    }
  }

  function openChangeForm(row: Row) {
    setChangeTarget(row)
    setChangeForm({ 转化率: String(row['转化率'] ?? ''), 事件序列: String(row['事件序列'] ?? '') })
    setErrorMessage('')
    setInfoMessage('')
  }

  async function saveChange() {
    if (!changeTarget) return
    setSaving(true)
    setErrorMessage('')
    setInfoMessage('')
    const changedFields: Record<string, string> = {}
    if (changeForm['转化率'] && changeForm['转化率'] !== String(changeTarget['转化率'] ?? '')) {
      changedFields['转化率'] = changeForm['转化率']
    }
    if (changeForm['事件序列'] && changeForm['事件序列'] !== String(changeTarget['事件序列'] ?? '')) {
      changedFields['事件序列'] = changeForm['事件序列']
    }
    if (Object.keys(changedFields).length === 0) {
      setErrorMessage('没有检测到字段变化，未生成版本记录')
      setSaving(false)
      return
    }
    try {
      const response = await request(CHANGE_ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({
          values: { sequence_code: changeTarget['模式编号'], changed_fields: changedFields },
        }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '序列变化登记失败')
      }
      setLocatedId(Number(changeTarget.id))
      setInfoMessage(`${payload.message}；可到「增量重算」生成增量聚类输入分群明细数据集`)
      setChangeTarget(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '序列变化登记失败')
    } finally {
      setSaving(false)
    }
  }

  const stats = useMemo(() => [
    { label: '序列模式总数', value: total },
    { label: '已确认模式', value: rows.filter((r) => r.status === '已确认').length },
    { label: '挖掘中模式', value: rows.filter((r) => r.status === '挖掘中').length },
  ], [rows, total])

  return (
    <section className="page" data-module="sequence">
      <header className="page-head">
        <div>
          <h2>行为序列管理</h2>
          <p className="page-desc">挖掘用户行为序列模式，识别高频行为路径和转化瓶颈；字段变化登记后进入聚类增量重算链路。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出行为序列清单
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
        {filterFields.map((field) => (
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
                <button className="link" type="button" onClick={() => openChangeForm(row)}>登记变化</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无符合筛选条件的行为序列数据</td>
            </tr>
          )}
        </tbody>
      </table>

      {changeTarget && (
        <div className="modal-mask" role="dialog" aria-modal="true">
          <div className="modal-card">
            <h3>登记序列变化：{String(changeTarget['模式编号'])}</h3>
            <p className="page-desc">只提交发生变化的字段，系统将生成版本记录并作为增量重算输入。</p>
            <label className="filter-item">
              <span>转化率</span>
              <input value={changeForm['转化率']} onChange={(e) => setChangeForm((p) => ({ ...p, 转化率: e.target.value }))} />
            </label>
            <label className="filter-item">
              <span>事件序列</span>
              <textarea
                rows={3}
                value={changeForm['事件序列']}
                onChange={(e) => setChangeForm((p) => ({ ...p, 事件序列: e.target.value }))}
              />
            </label>
            <div className="modal-actions">
              <button className="btn primary" type="button" disabled={saving} onClick={() => void saveChange()}>
                {saving ? '保存中…' : '保存变化'}
              </button>
              <button className="btn ghost" type="button" onClick={() => setChangeTarget(null)}>取消</button>
            </div>
          </div>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条行为序列记录{locatedId !== null ? '；当前定位结果已保存，重进页面后保持高亮' : ''}</span>
        {infoMessage && <span className="info-text">{infoMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
