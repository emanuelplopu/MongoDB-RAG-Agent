import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TelemetryRecord } from '../types/telemetry'

interface RecordListProps {
  records: TelemetryRecord[]
  selectedId?: string
  selectedForCompare: string[]
  onSelect: (record: TelemetryRecord) => void
  onToggleCompare: (id: string) => void
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ts.slice(11, 19) || ts
  }
}

function shortModel(model: string): string {
  if (!model) return '—'
  const parts = model.split('/')
  const name = parts[parts.length - 1]
  return name.length > 16 ? name.slice(0, 14) + '…' : name
}

function latencyColor(ms: number | undefined, hasError: boolean): string {
  if (hasError || ms === undefined) return 'bg-accent-red/20 text-accent-red'
  if (ms > 5000) return 'bg-accent-red/20 text-accent-red'
  if (ms > 2000) return 'bg-accent-yellow/20 text-accent-yellow'
  return 'bg-accent-green/20 text-accent-green'
}

export function RecordList({
  records,
  selectedId,
  selectedForCompare,
  onSelect,
  onToggleCompare,
}: RecordListProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [modelFilter, setModelFilter] = useState('')
  const [sessionFilter, setSessionFilter] = useState('')
  const [minLatency, setMinLatency] = useState('')
  const [maxLatency, setMaxLatency] = useState('')
  const [collapsedSessions, setCollapsedSessions] = useState<Set<string>>(new Set())

  // Derive filter options
  const models = useMemo(() => {
    const set = new Set<string>()
    records.forEach(r => {
      if (r.orchestrator_model) set.add(r.orchestrator_model)
      if (r.worker_model) set.add(r.worker_model)
    })
    return [...set].sort()
  }, [records])

  const sessions = useMemo(() => {
    const set = new Set<string>()
    records.forEach(r => { if (r.session_id) set.add(r.session_id) })
    return [...set].sort()
  }, [records])

  // Filter records
  const filtered = useMemo(() => {
    return records.filter(r => {
      if (search) {
        const q = search.toLowerCase()
        const haystack = [
          r.prompt_pseudonymized,
          r.response_pseudonymized,
          r.record_id,
          r.session_id,
        ].join(' ').toLowerCase()
        if (!haystack.includes(q)) return false
      }
      if (modelFilter && r.orchestrator_model !== modelFilter && r.worker_model !== modelFilter) return false
      if (sessionFilter && r.session_id !== sessionFilter) return false
      const min = minLatency ? Number(minLatency) : undefined
      const max = maxLatency ? Number(maxLatency) : undefined
      if (min !== undefined && !isNaN(min) && (r.total_duration_ms ?? 0) < min) return false
      if (max !== undefined && !isNaN(max) && (r.total_duration_ms ?? 0) > max) return false
      return true
    })
  }, [records, search, modelFilter, sessionFilter, minLatency, maxLatency])

  // Group by session
  const grouped = useMemo(() => {
    const map = new Map<string, TelemetryRecord[]>()
    filtered.forEach(r => {
      const sid = r.session_id || '_no_session'
      if (!map.has(sid)) map.set(sid, [])
      map.get(sid)!.push(r)
    })
    // Sort sessions by first record timestamp desc
    return [...map.entries()].sort((a, b) => {
      const ta = a[1][0]?.timestamp ?? ''
      const tb = b[1][0]?.timestamp ?? ''
      return tb.localeCompare(ta)
    })
  }, [filtered])

  const toggleSession = (sid: string) => {
    setCollapsedSessions(prev => {
      const next = new Set(prev)
      if (next.has(sid)) next.delete(sid)
      else next.add(sid)
      return next
    })
  }

  return (
    <>
      {/* Filter bar */}
      <div className="p-3 space-y-2 border-b border-gray-200 dark:border-gray-700/50 bg-gray-50 dark:bg-surface-light flex-shrink-0">
        <input
          type="text"
          placeholder={t('browser.filter.search')}
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full px-3 py-1.5 bg-white dark:bg-surface rounded-lg border border-gray-200 dark:border-gray-700/50 text-sm text-gray-800 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-accent-blue/50"
        />
        <div className="flex gap-2">
          <select
            value={modelFilter}
            onChange={e => setModelFilter(e.target.value)}
            className="flex-1 px-2 py-1 bg-white dark:bg-surface rounded border border-gray-200 dark:border-gray-700/50 text-xs text-gray-700 dark:text-gray-300 focus:outline-none focus:border-accent-blue/50"
          >
            <option value="">{t('browser.filter.model')}</option>
            {models.map(m => (
              <option key={m} value={m}>{shortModel(m)}</option>
            ))}
          </select>
          <select
            value={sessionFilter}
            onChange={e => setSessionFilter(e.target.value)}
            className="flex-1 px-2 py-1 bg-white dark:bg-surface rounded border border-gray-200 dark:border-gray-700/50 text-xs text-gray-700 dark:text-gray-300 focus:outline-none focus:border-accent-blue/50"
          >
            <option value="">{t('browser.filter.session')}</option>
            {sessions.map(s => (
              <option key={s} value={s}>{s.slice(0, 8)}…</option>
            ))}
          </select>
        </div>
        <div className="flex gap-2 items-center">
          <input
            type="number"
            placeholder={t('browser.filter.minMs')}
            value={minLatency}
            onChange={e => setMinLatency(e.target.value)}
            className="w-1/2 px-2 py-1 bg-white dark:bg-surface rounded border border-gray-200 dark:border-gray-700/50 text-xs text-gray-700 dark:text-gray-300 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-accent-blue/50"
          />
          <span className="text-gray-400 dark:text-gray-500 text-xs">–</span>
          <input
            type="number"
            placeholder={t('browser.filter.maxMs')}
            value={maxLatency}
            onChange={e => setMaxLatency(e.target.value)}
            className="w-1/2 px-2 py-1 bg-white dark:bg-surface rounded border border-gray-200 dark:border-gray-700/50 text-xs text-gray-700 dark:text-gray-300 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-accent-blue/50"
          />
        </div>
        <div className="text-[10px] text-gray-500 text-right">
          {t('browser.filter.showing', { filtered: filtered.length, total: records.length })}
        </div>
      </div>

      {/* Record list */}
      <div className="flex-1 overflow-y-auto">
        {grouped.map(([sessionId, sessionRecords]) => {
          const collapsed = collapsedSessions.has(sessionId)
          const label = sessionId === '_no_session' ? t('browser.record.noSession') : sessionId.slice(0, 8)
          return (
            <div key={sessionId}>
              {/* Session header */}
              <button
                onClick={() => toggleSession(sessionId)}
                className="w-full flex items-center gap-2 px-3 py-1.5 bg-gray-100/50 dark:bg-surface-lighter/50 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors sticky top-0 z-10 border-b border-gray-200 dark:border-gray-700/30"
              >
                <span className={`transition-transform text-[10px] ${collapsed ? '' : 'rotate-90'}`}>▶</span>
                <span className="font-mono font-medium">{label}</span>
                <span className="px-1.5 py-0.5 rounded-full bg-accent-blue/15 text-accent-blue text-[10px]">
                  {sessionRecords.length}
                </span>
              </button>

              {/* Records */}
              {!collapsed && sessionRecords.map(record => {
                const isSelected = record.record_id === selectedId
                const isCompare = selectedForCompare.includes(record.record_id)
                const hasError = record.llm_calls?.some(c => !c.success) ?? false
                const preview = (record.prompt_pseudonymized || '').slice(0, 80)

                return (
                  <div
                    key={record.record_id}
                    onClick={() => onSelect(record)}
                    className={`flex items-start gap-2 px-3 py-2 cursor-pointer border-l-2 transition-colors ${
                      isSelected
                        ? 'border-accent-blue bg-accent-blue/10'
                        : 'border-transparent hover:bg-gray-50 dark:hover:bg-surface-lighter/40'
                    }`}
                  >
                    {/* Compare checkbox */}
                    <input
                      type="checkbox"
                      checked={isCompare}
                      onChange={e => {
                        e.stopPropagation()
                        onToggleCompare(record.record_id)
                      }}
                      onClick={e => e.stopPropagation()}
                      className="mt-1 flex-shrink-0 accent-accent-blue"
                    />

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span className="text-[11px] font-mono text-gray-400">
                          {formatTime(record.timestamp)}
                        </span>
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-accent-purple/15 text-accent-purple truncate max-w-[100px]">
                          {shortModel(record.orchestrator_model)}
                        </span>
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${latencyColor(record.total_duration_ms, hasError)}`}>
                          {record.total_duration_ms != null ? `${record.total_duration_ms}ms` : '—'}
                        </span>
                      </div>
                      {preview && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 truncate leading-snug">
                          {preview}{(record.prompt_pseudonymized || '').length > 80 ? '…' : ''}
                        </p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })}

        {filtered.length === 0 && (
          <div className="p-6 text-center text-gray-500 text-sm">
            {t('browser.filter.noMatch')}
          </div>
        )}
      </div>
    </>
  )
}
