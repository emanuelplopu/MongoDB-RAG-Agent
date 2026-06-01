import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDataSource } from '../api'
import { remoteApi } from '../api/remote'
import {
  AdaptiveDecisionRecord,
  LLMCallRecord,
  NodeOutputRecord,
  StrategyRunRecord,
} from '../types/strategy'
import LLMCallViewer from '../components/LLMCallViewer'
import AdaptiveDecisionPanel from '../components/AdaptiveDecisionPanel'
import ContextBudgetPanel from '../components/ContextBudgetPanel'

// ---------- helpers ----------------------------------------------------------

const NODE_COLORS: Record<string, string> = {
  retrieve: 'bg-accent-blue',
  query_expand: 'bg-accent-purple',
  intent_classify: 'bg-accent-teal',
  plan: 'bg-yellow-400',
  synthesize: 'bg-accent-green',
  evidence_cards: 'bg-accent-orange',
  rerank: 'bg-pink-400',
  refine: 'bg-indigo-400',
  judge: 'bg-red-400',
  filter: 'bg-gray-400',
  merge: 'bg-gray-500',
  format: 'bg-cyan-400',
}

const STATUS_BADGE_CLASSES: Record<string, string> = {
  completed: 'bg-accent-green/20 text-accent-green border-accent-green/30',
  failed: 'bg-red-500/20 text-red-400 border-red-500/30',
  timed_out: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  cancelled: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
}

const NODE_STATUS_DOT: Record<string, string> = {
  success: 'bg-accent-green',
  completed: 'bg-accent-green',
  ok: 'bg-accent-green',
  failed: 'bg-red-400',
  error: 'bg-red-400',
  skipped: 'bg-gray-500',
  cancelled: 'bg-gray-500',
}

const RANGE_OPTIONS = [
  { value: '24h', hours: 24 },
  { value: '7d', hours: 24 * 7 },
  { value: '30d', hours: 24 * 30 },
] as const

type RangeValue = (typeof RANGE_OPTIONS)[number]['value']

const STATUS_FILTER_OPTIONS = ['all', 'completed', 'failed', 'timed_out', 'cancelled'] as const
type StatusFilter = (typeof STATUS_FILTER_OPTIONS)[number]

const PAGE_SIZE = 25

function formatRelative(iso: string): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const diff = Date.now() - t
  if (diff < 0) return new Date(iso).toLocaleString()
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const d = Math.floor(hr / 24)
  if (d < 7) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`
  const m = Math.floor(ms / 60_000)
  const s = ((ms % 60_000) / 1000).toFixed(0)
  return `${m}m ${s}s`
}

function shortTrace(trace: string): string {
  return trace ? trace.slice(0, 8) : '—'
}

function isoSinceHours(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

function nodeStatusDotClass(status: string): string {
  return NODE_STATUS_DOT[status] || 'bg-gray-500'
}

// ---------- DAG waterfall ----------------------------------------------------

interface DagWaterfallProps {
  nodes: NodeOutputRecord[]
}

function DagWaterfall({ nodes }: DagWaterfallProps) {
  const { t } = useTranslation()

  if (!nodes || nodes.length === 0) {
    return (
      <div className="text-gray-500 text-sm italic py-4 text-center">
        {t('strategy.detail.noNodes', { defaultValue: 'No node outputs recorded.' })}
      </div>
    )
  }

  const maxDuration = Math.max(...nodes.map(n => n.duration_ms || 0), 1)

  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs text-gray-400 mb-2">
        <span>{t('strategy.detail.node', { defaultValue: 'Node' })}</span>
        <span>
          {t('strategy.detail.maxLabel', { defaultValue: 'max' })}:{' '}
          {formatDurationMs(maxDuration)}
        </span>
      </div>

      {nodes.map((node, i) => {
        const widthPercent = Math.max(((node.duration_ms || 0) / maxDuration) * 100, 1)
        const color = NODE_COLORS[node.node_type] || 'bg-gray-500'
        const dot = nodeStatusDotClass(node.status)

        return (
          <div key={`${node.node_id}-${i}`} className="space-y-0.5">
            <div className="flex items-center gap-2">
              {/* Label column */}
              <div className="w-32 flex-shrink-0 text-right pr-1">
                <div className="text-xs text-gray-200 truncate" title={node.node_type}>
                  {node.node_type}
                </div>
                <div
                  className="text-[10px] text-gray-500 font-mono truncate"
                  title={node.node_id}
                >
                  {node.node_id}
                </div>
              </div>

              {/* Status dot */}
              <span
                className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${dot}`}
                title={node.status}
              />

              {/* Bar container */}
              <div className="flex-1 h-6 bg-surface rounded-md overflow-hidden relative">
                <div
                  className={`h-full ${color} rounded-md transition-all duration-300 flex items-center px-2`}
                  style={{ width: `${widthPercent}%` }}
                >
                  {widthPercent > 18 && (
                    <span className="text-[10px] text-white/90 font-mono whitespace-nowrap">
                      {formatDurationMs(node.duration_ms)}
                    </span>
                  )}
                </div>
              </div>

              {/* Outside duration label */}
              {widthPercent <= 18 && (
                <span className="text-[10px] text-gray-400 font-mono w-16 flex-shrink-0 text-right">
                  {formatDurationMs(node.duration_ms)}
                </span>
              )}
            </div>

            {node.error && (
              <div className="ml-[136px] text-[11px] text-red-400 font-mono break-all pr-2">
                {node.error}
              </div>
            )}
          </div>
        )
      })}

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mt-3 pt-2 border-t border-gray-700/50">
        {Object.entries(NODE_COLORS).map(([nodeType, color]) => (
          <div key={nodeType} className="flex items-center gap-1.5">
            <div className={`w-2.5 h-2.5 rounded-sm ${color}`} />
            <span className="text-[10px] text-gray-400">{nodeType}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------- Run summary stats ------------------------------------------------

interface RunSummaryProps {
  run: StrategyRunRecord
}

function RunSummary({ run }: RunSummaryProps) {
  const { t } = useTranslation()
  const counts = useMemo(() => {
    let success = 0
    let failed = 0
    let skipped = 0
    for (const n of run.node_outputs || []) {
      const s = (n.status || '').toLowerCase()
      if (s === 'success' || s === 'completed' || s === 'ok') success += 1
      else if (s === 'failed' || s === 'error') failed += 1
      else if (s === 'skipped' || s === 'cancelled') skipped += 1
    }
    return { success, failed, skipped }
  }, [run])

  const stat = (label: string, value: React.ReactNode, color = 'text-gray-100') => (
    <div className="bg-surface rounded-md px-3 py-2 border border-gray-700/50">
      <div className="text-[10px] text-gray-400 uppercase tracking-wide">{label}</div>
      <div className={`text-sm font-mono ${color}`}>{value}</div>
    </div>
  )

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
      {stat(
        t('strategy.detail.totalDuration', { defaultValue: 'Total duration' }),
        formatDurationMs(run.duration_ms),
      )}
      {stat(
        t('strategy.detail.totalNodes', { defaultValue: 'Total nodes' }),
        run.node_outputs?.length ?? 0,
      )}
      {stat(
        t('strategy.detail.success', { defaultValue: 'Success' }),
        counts.success,
        'text-accent-green',
      )}
      {stat(
        t('strategy.detail.failed', { defaultValue: 'Failed' }),
        counts.failed,
        'text-red-400',
      )}
      {stat(
        t('strategy.detail.skipped', { defaultValue: 'Skipped' }),
        counts.skipped,
        'text-gray-400',
      )}
      {stat(
        t('strategy.detail.haltReason', { defaultValue: 'Halt reason' }),
        run.halt_reason || '—',
        run.halt_reason ? 'text-yellow-400' : 'text-gray-500',
      )}
    </div>
  )
}

// ---------- Run list row -----------------------------------------------------

interface RunRowProps {
  run: StrategyRunRecord
  selected: boolean
  onSelect: (run: StrategyRunRecord) => void
}

function RunRow({ run, selected, onSelect }: RunRowProps) {
  const badgeCls =
    STATUS_BADGE_CLASSES[run.status] || 'bg-gray-500/20 text-gray-300 border-gray-500/30'

  return (
    <button
      onClick={() => onSelect(run)}
      className={`w-full text-left px-3 py-2.5 border-b border-gray-700/50 transition-colors ${
        selected ? 'bg-accent-blue/10' : 'hover:bg-surface-lighter'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span
          className={`text-[10px] uppercase tracking-wide px-2 py-0.5 rounded border ${badgeCls}`}
        >
          {run.status}
        </span>
        <span className="text-[10px] text-gray-500">{formatRelative(run.started_at)}</span>
      </div>
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-sm font-semibold text-gray-100 truncate" title={run.strategy_id}>
          {run.strategy_id}
        </span>
        <span
          className="text-[11px] font-mono text-gray-400 flex-shrink-0"
          title={run.trace_id}
        >
          {shortTrace(run.trace_id)}
        </span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-gray-400">
        <span className="font-mono">{formatDurationMs(run.duration_ms)}</span>
        <span>
          {(run.node_outputs?.length ?? 0)}{' '}
          {(run.node_outputs?.length ?? 0) === 1 ? 'node' : 'nodes'}
        </span>
      </div>
    </button>
  )
}

// ---------- Page -------------------------------------------------------------

export default function StrategyRunsPage() {
  const { t } = useTranslation()
  const { state } = useDataSource()
  const conn = state.connectionInfo
  const isConnected = !!conn?.connected

  // Filters
  const [range, setRange] = useState<RangeValue>('7d')
  const [strategyFilter, setStrategyFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  // List state
  const [runs, setRuns] = useState<StrategyRunRecord[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loadingList, setLoadingList] = useState(false)
  const [listError, setListError] = useState<string | null>(null)

  // Detail state
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<StrategyRunRecord | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  // LLM calls + adaptive decision for the selected run
  const [llmCalls, setLlmCalls] = useState<LLMCallRecord[]>([])
  const [llmLoading, setLlmLoading] = useState(false)
  const [adaptiveDecision, setAdaptiveDecision] = useState<AdaptiveDecisionRecord | null>(
    null,
  )
  const [adaptiveLoading, setAdaptiveLoading] = useState(false)

  const loadRuns = useCallback(
    async (mode: 'replace' | 'append') => {
      if (!conn?.connected) return
      setLoadingList(true)
      setListError(null)
      try {
        const hours =
          RANGE_OPTIONS.find(r => r.value === range)?.hours ?? 24 * 7
        const params: Record<string, unknown> = {
          since: isoSinceHours(hours),
          limit: PAGE_SIZE,
          offset: mode === 'append' ? offset : 0,
        }
        if (strategyFilter.trim()) params.strategy_id = strategyFilter.trim()
        if (statusFilter !== 'all') params.status = statusFilter

        const result = await remoteApi.fetchStrategyRuns(
          conn.url,
          conn.token,
          params as {
            since?: string
            until?: string
            strategy_id?: string
            status?: string
            limit?: number
            offset?: number
          },
        )

        const fetched: StrategyRunRecord[] = result?.runs || result?.records || result || []
        const moreFlag =
          typeof result?.has_more === 'boolean'
            ? result.has_more
            : fetched.length === PAGE_SIZE

        if (mode === 'append') {
          setRuns(prev => [...prev, ...fetched])
          setOffset(prev => prev + fetched.length)
        } else {
          setRuns(fetched)
          setOffset(fetched.length)
        }
        setHasMore(moreFlag)
      } catch (err: any) {
        setListError(err?.message || 'Failed to load strategy runs')
      } finally {
        setLoadingList(false)
      }
    },
    [conn, range, strategyFilter, statusFilter, offset],
  )

  // Initial + filter-change reload
  useEffect(() => {
    if (!isConnected) return
    void loadRuns('replace')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isConnected, range, strategyFilter, statusFilter])

  // Load detail on selection
  useEffect(() => {
    if (!selectedTraceId || !conn?.connected) {
      setSelectedRun(null)
      return
    }
    let cancelled = false
    setLoadingDetail(true)
    setDetailError(null)
    remoteApi
      .fetchStrategyRun(conn.url, conn.token, selectedTraceId)
      .then((data: StrategyRunRecord) => {
        if (cancelled) return
        setSelectedRun(data)
      })
      .catch((err: any) => {
        if (cancelled) return
        setDetailError(err?.message || 'Failed to load strategy run')
        setSelectedRun(null)
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedTraceId, conn])

  const handleSelectRun = (run: StrategyRunRecord) => {
    setSelectedTraceId(run.trace_id)
    setSelectedRun(run) // optimistic populate from list while detail fetches
  }

  // Fetch LLM calls + adaptive decision when selection changes
  useEffect(() => {
    if (!selectedTraceId || !conn?.connected) {
      setLlmCalls([])
      setAdaptiveDecision(null)
      return
    }
    let cancelled = false

    setLlmLoading(true)
    remoteApi
      .fetchStrategyLLMCalls(conn.url, conn.token, selectedTraceId)
      .then((data: any) => {
        if (cancelled) return
        const calls: LLMCallRecord[] =
          data?.calls || data?.records || (Array.isArray(data) ? data : []) || []
        setLlmCalls(calls)
      })
      .catch(() => {
        if (!cancelled) setLlmCalls([])
      })
      .finally(() => {
        if (!cancelled) setLlmLoading(false)
      })

    const capabilityId = selectedRun?.capability_id
    if (capabilityId) {
      setAdaptiveLoading(true)
      remoteApi
        .fetchAdaptiveDecisions(conn.url, conn.token, {
          capability_id: capabilityId,
          limit: 1,
          since: selectedRun?.started_at,
          until: selectedRun?.completed_at,
        })
        .then((data: any) => {
          if (cancelled) return
          const decisions: AdaptiveDecisionRecord[] =
            data?.decisions || data?.records || (Array.isArray(data) ? data : []) || []
          setAdaptiveDecision(decisions.length > 0 ? decisions[0] : null)
        })
        .catch(() => {
          if (!cancelled) setAdaptiveDecision(null)
        })
        .finally(() => {
          if (!cancelled) setAdaptiveLoading(false)
        })
    } else {
      setAdaptiveDecision(null)
      setAdaptiveLoading(false)
    }

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTraceId, conn?.connected, selectedRun?.capability_id])

  // ---------- render ---------------------------------------------------------

  if (!isConnected) {
    return (
      <div className="h-full flex items-center justify-center text-gray-400">
        <div className="text-center max-w-sm px-4">
          <p className="text-2xl mb-2">🧠</p>
          <p className="text-lg text-gray-200 mb-1">
            {t('strategy.title', { defaultValue: 'Strategy Runs' })}
          </p>
          <p className="text-sm">
            {t('strategy.connectFirst', {
              defaultValue: 'Connect to a remote server first to view strategy runs.',
            })}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex">
      {/* Left panel: list + filters */}
      <div className="w-2/5 border-r border-gray-700/50 flex flex-col overflow-hidden">
        <div className="p-3 border-b border-gray-700/50 bg-surface-light space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-100">
              {t('strategy.list.title', { defaultValue: 'Strategy Runs' })}
            </h2>
            <span className="text-[11px] text-gray-400">
              {runs.length} {runs.length === 1 ? 'run' : 'runs'}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <select
              value={range}
              onChange={e => setRange(e.target.value as RangeValue)}
              className="bg-surface border border-gray-700/50 rounded px-2 py-1 text-xs text-gray-100"
            >
              <option value="24h">
                {t('strategy.filters.last24h', { defaultValue: 'Last 24h' })}
              </option>
              <option value="7d">
                {t('strategy.filters.last7d', { defaultValue: 'Last 7 days' })}
              </option>
              <option value="30d">
                {t('strategy.filters.last30d', { defaultValue: 'Last 30 days' })}
              </option>
            </select>

            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
              className="bg-surface border border-gray-700/50 rounded px-2 py-1 text-xs text-gray-100"
            >
              {STATUS_FILTER_OPTIONS.map(opt => (
                <option key={opt} value={opt}>
                  {opt === 'all'
                    ? t('strategy.filters.statusAll', { defaultValue: 'All statuses' })
                    : opt}
                </option>
              ))}
            </select>
          </div>

          <input
            type="text"
            value={strategyFilter}
            onChange={e => setStrategyFilter(e.target.value)}
            placeholder={t('strategy.filters.strategyId', {
              defaultValue: 'Filter by strategy_id…',
            })}
            className="w-full bg-surface border border-gray-700/50 rounded px-2 py-1 text-xs text-gray-100 placeholder-gray-500"
          />
        </div>

        <div className="flex-1 overflow-y-auto">
          {listError && (
            <div className="p-3 text-xs text-red-400 bg-red-500/10 border-b border-red-500/30">
              {listError}
            </div>
          )}

          {loadingList && runs.length === 0 ? (
            <div className="flex items-center justify-center py-10 text-gray-400 text-sm">
              <span className="animate-spin mr-2">⏳</span>
              {t('strategy.list.loading', { defaultValue: 'Loading runs…' })}
            </div>
          ) : runs.length === 0 ? (
            <div className="flex items-center justify-center py-10 text-gray-500 text-sm">
              {t('strategy.list.empty', { defaultValue: 'No runs match these filters.' })}
            </div>
          ) : (
            runs.map(run => (
              <RunRow
                key={run.trace_id}
                run={run}
                selected={selectedTraceId === run.trace_id}
                onSelect={handleSelectRun}
              />
            ))
          )}

          {runs.length > 0 && hasMore && (
            <div className="p-3">
              <button
                onClick={() => void loadRuns('append')}
                disabled={loadingList}
                className="w-full text-xs px-3 py-2 rounded border border-gray-700/50 text-gray-200 bg-surface hover:bg-surface-lighter disabled:opacity-50"
              >
                {loadingList
                  ? t('strategy.list.loadingMore', { defaultValue: 'Loading…' })
                  : t('strategy.list.loadMore', { defaultValue: 'Load more' })}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Right panel: detail */}
      <div className="w-3/5 overflow-y-auto">
        {!selectedTraceId ? (
          <div className="h-full flex items-center justify-center text-gray-500 text-sm">
            {t('strategy.detail.empty', { defaultValue: 'Select a run to inspect its DAG.' })}
          </div>
        ) : (
          <RunDetail
            run={selectedRun}
            loading={loadingDetail}
            error={detailError}
            llmCalls={llmCalls}
            llmLoading={llmLoading}
            adaptiveDecision={adaptiveDecision}
            adaptiveLoading={adaptiveLoading}
          />
        )}
      </div>
    </div>
  )
}

// ---------- Run detail panel -------------------------------------------------

interface RunDetailProps {
  run: StrategyRunRecord | null
  loading: boolean
  error: string | null
  llmCalls: LLMCallRecord[]
  llmLoading: boolean
  adaptiveDecision: AdaptiveDecisionRecord | null
  adaptiveLoading: boolean
}

function RunDetail({
  run,
  loading,
  error,
  llmCalls,
  llmLoading,
  adaptiveDecision,
  adaptiveLoading,
}: RunDetailProps) {
  const { t } = useTranslation()

  if (error) {
    return (
      <div className="p-4">
        <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded p-3">
          {error}
        </div>
      </div>
    )
  }

  if (!run && loading) {
    return (
      <div className="h-full flex items-center justify-center text-gray-400 text-sm">
        <span className="animate-spin mr-2">⏳</span>
        {t('strategy.detail.loading', { defaultValue: 'Loading run…' })}
      </div>
    )
  }

  if (!run) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500 text-sm">
        {t('strategy.detail.empty', { defaultValue: 'Select a run to inspect its DAG.' })}
      </div>
    )
  }

  const badgeCls =
    STATUS_BADGE_CLASSES[run.status] || 'bg-gray-500/20 text-gray-300 border-gray-500/30'

  return (
    <div className="p-4 space-y-5">
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <h2 className="text-base font-semibold text-gray-100">{run.strategy_id}</h2>
          {run.strategy_version && (
            <span className="text-[11px] text-gray-400 bg-surface px-2 py-0.5 rounded border border-gray-700/50 font-mono">
              v{run.strategy_version}
            </span>
          )}
          <span
            className={`text-[10px] uppercase tracking-wide px-2 py-0.5 rounded border ${badgeCls}`}
          >
            {run.status}
          </span>
          {loading && (
            <span className="text-[11px] text-gray-400">
              <span className="animate-spin mr-1 inline-block">⏳</span>
              {t('strategy.detail.refreshing', { defaultValue: 'Refreshing…' })}
            </span>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
          <div className="flex gap-2">
            <span className="text-gray-500 w-24 flex-shrink-0">trace_id</span>
            <span className="font-mono text-gray-200 break-all">{run.trace_id}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-24 flex-shrink-0">run_id</span>
            <span className="font-mono text-gray-200 break-all">{run.run_id}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-24 flex-shrink-0">capability_id</span>
            <span className="font-mono text-gray-200 break-all">
              {run.capability_id || '—'}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-24 flex-shrink-0">tenant_id</span>
            <span className="font-mono text-gray-200 break-all">
              {run.tenant_id || '—'}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-24 flex-shrink-0">started_at</span>
            <span className="text-gray-300">
              {run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-24 flex-shrink-0">completed_at</span>
            <span className="text-gray-300">
              {run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}
            </span>
          </div>
        </div>
      </div>

      {/* DAG waterfall */}
      <section className="bg-surface-light border border-gray-700/50 rounded-md p-3">
        <h3 className="text-xs uppercase tracking-wide text-gray-400 mb-2">
          {t('strategy.detail.dagTitle', { defaultValue: 'DAG node waterfall' })}
        </h3>
        <DagWaterfall nodes={run.node_outputs || []} />
      </section>

      {/* Summary stats */}
      <section>
        <h3 className="text-xs uppercase tracking-wide text-gray-400 mb-2">
          {t('strategy.detail.summary', { defaultValue: 'Summary' })}
        </h3>
        <RunSummary run={run} />
      </section>

      {/* LLM Calls (T9) */}
      <section className="bg-surface-light border border-gray-700/50 rounded-md p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs uppercase tracking-wide text-gray-400">
            {t('strategy.detail.llmCalls', { defaultValue: 'LLM Calls' })}
          </h3>
          <span className="text-[11px] text-gray-500">
            {llmCalls.length}{' '}
            {t('strategy.detail.callsLabel', { defaultValue: 'calls' })}
          </span>
        </div>
        <LLMCallViewer calls={llmCalls} loading={llmLoading} />
      </section>

      {/* Adaptive Decision (T10) */}
      <section className="bg-surface-light border border-gray-700/50 rounded-md p-3">
        <h3 className="text-xs uppercase tracking-wide text-gray-400 mb-2">
          {t('strategy.detail.adaptiveDecision', { defaultValue: 'Adaptive Decision' })}
        </h3>
        <AdaptiveDecisionPanel decision={adaptiveDecision} loading={adaptiveLoading} />
      </section>

      {/* Context Budget (T10) */}
      <section className="bg-surface-light border border-gray-700/50 rounded-md p-3">
        <h3 className="text-xs uppercase tracking-wide text-gray-400 mb-2">
          {t('strategy.detail.contextBudget', { defaultValue: 'Context Budget' })}
        </h3>
        <ContextBudgetPanel budget={null} loading={false} />
      </section>
    </div>
  )
}
