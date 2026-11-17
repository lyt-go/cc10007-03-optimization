import { useEffect, useState } from 'react'

/**
 * 本地持久化状态：把筛选、排序与当前定位结果存到 localStorage。
 * 保存动作之后重进页面时，列表仍能按上次条件定位并核验关联数据。
 * 读取失败（隐私模式或脏数据）时回退到初始值，不影响页面可用性。
 */
export function usePersistentState<T>(key: string, initial: T): [T, (value: T | ((prev: T) => T)) => void] {
  const storageKey = `smart-ops:${key}`
  const [state, setState] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(storageKey)
      return raw === null ? initial : (JSON.parse(raw) as T)
    } catch {
      return initial
    }
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(state))
    } catch {
      // 存储不可用时静默降级为内存态
    }
  }, [storageKey, state])

  return [state, setState]
}
