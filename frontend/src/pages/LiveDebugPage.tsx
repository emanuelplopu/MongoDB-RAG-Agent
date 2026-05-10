import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowPathIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import {
  debugApi,
  DebugSystemState,
  DebugActiveRequest,
  DebugActivityItem,
  DebugActivityEntry,
  DebugRequestDetail,
} from '../api/client'

// Helper to format duration
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.floor((ms % 60000) / 1000)
  return `${minutes}m ${seconds}s`
}

// Helper to format uptime
function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${mins}m`
}

// Status badge component
function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const config: Record<string, { bg: string; text: string; label: string }> = {
    complete: { bg: 'bg-green-100 dark:bg-green-900/30', text: 'text-green-800 dark:text-green-300', label: t('debugPage.complete') },
    error: { bg: 'bg-red-100 dark:bg-red-900/30', text: 'text-red-800 dark:text-red-300', label: t('debugPage.error') },
    in_progress: { bg: 'bg-yellow-100 dark:bg-yellow-900/30', text: 'text-yellow-800 dark:text-yellow-300', label: t('debugPage.inProgress') },
  }
  const c = config[status] || config.error
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c.bg} ${c.text}`}>
      {c.label}
    </span>
  )
}

// Active request row with live elapsed timer
function ActiveRequestRow({ request }: { request: DebugActiveRequest }) {
  const { t } = useTranslation()
  const [elapsed, setElapsed] = useState(request.elapsed_ms)

  useEffect(() => {
    setElapsed(request.elapsed_ms)
    const interval = setInterval(() => {
      setElapsed(prev => prev + 1000)
    }, 1000)
    return () => clearInterval(interval)
  }, [request.elapsed_ms])

  return (
    <div className="flex items-center justify-between rounded-lg border border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-900/20 px-4 py-3">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-yellow-500 animate-pulse" />
          <code className="text-xs font-mono text-primary-700 dark:text-gray-300">
            {request.request_id.slice(0, 12)}...
          </code>
        </div>
        <span className="text-xs text-primary-600 dark:text-gray-400">
          {request.session_id ? `Session: ${request.session_id.slice(0, 8)}...` : ''}
        </span>
        <span className="text-xs font-medium text-primary-700 dark:text-gray-300">
          {request.model}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <span className="font-mono text-sm font-bold text-yellow-700 dark:text-yellow-300">
          {formatDuration(elapsed)} {t('debugPage.elapsed')}
        </span>
        <StatusBadge status="in_progress" />
      </div>
    </div>
  )
}

// Entry type renderer
function EntryRenderer({ entry }: { entry: DebugActivityEntry }) {
  if (entry.type === 'llm_call' || entry.type === 'llm') {
    return (
      <div className="flex items-center gap-3 py-1.5">
        <span className="inline-flex items-center rounded bg-blue-100 dark:bg-blue-900/30 px-1.5 py-0.5 text-xs font-medium text-blue-800 dark:text-blue-300">
          LLM
        </span>
        <span className="text-xs text-primary-600 dark:text-gray-400">{entry.phase || ''}</span>
        <span className="text-xs font-mono text-primary-700 dark:text-gray-300">{entry.model || ''}</span>
        {entry.duration_ms != null && (
          <span className="text-xs text-primary-500 dark:text-gray-500">{formatDuration(entry.duration_ms)}</span>
        )}
        {entry.tokens != null && (
          <span className="text-xs font-medium text-primary-700 dark:text-gray-300">{entry.tokens} tokens</span>
        )}
      </div>
    )
  }

  if (entry.type === 'search') {
    return (
      <div className="flex items-center gap-3 py-1.5">
        <span className="inline-flex items-center rounded bg-purple-100 dark:bg-purple-900/30 px-1.5 py-0.5 text-xs font-medium text-purple-800 dark:text-purple-300">
          Search
        </span>
        <span className="text-xs text-primary-600 dark:text-gray-400 truncate max-w-[200px]">
          {entry.query || ''}
        </span>
        {entry.results_count != null && (
          <span className="text-xs text-primary-700 dark:text-gray-300">{entry.results_count} results</span>
        )}
        {entry.duration_ms != null && (
          <span className="text-xs text-primary-500 dark:text-gray-500">{formatDuration(entry.duration_ms)}</span>
        )}
      </div>
    )
  }

  if (entry.type === 'error') {
    return (
      <div className="flex items-center gap-3 py-1.5">
        <span className="inline-flex items-center rounded bg-red-100 dark:bg-red-900/30 px-1.5 py-0.5 text-xs font-medium text-red-800 dark:text-red-300">
          Error
        </span>
        <span className="text-xs text-red-700 dark:text-red-400">{entry.error || entry.phase || ''}</span>
      </div>
    )
  }

  // Generic/phase transition
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="inline-flex items-center rounded bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 text-xs font-medium text-gray-700 dark:text-gray-300">
        {entry.type}
      </span>
      <span className="text-xs text-primary-600 dark:text-gray-400">{entry.phase || ''}</span>
      {entry.duration_ms != null && (
        <span className="text-xs text-primary-500 dark:text-gray-500">{formatDuration(entry.duration_ms)}</span>
      )}
    </div>
  )
}

// Expandable activity row
function ActivityRow({
  item,
  isExpanded,
  onToggle,
}: {
  item: DebugActivityItem
  isExpanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const [detail, setDetail] = useState<DebugRequestDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  const handleToggle = async () => {
    onToggle()
    if (!isExpanded && !detail) {
      setLoadingDetail(true)
      try {
        const data = await debugApi.getRequestDetail(item.request_id)
        setDetail(data)
      } catch {
        // Silently fail - detail just won't show
      } finally {
        setLoadingDetail(false)
      }
    }
  }

  return (
    <div className="border-b border-gray-100 dark:border-gray-700 last:border-b-0">
      <button
        onClick={handleToggle}
        className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
      >
        <div className="flex items-center gap-4">
          {isExpanded ? (
            <ChevronDownIcon className="h-4 w-4 text-primary-400 dark:text-gray-500" />
          ) : (
            <ChevronRightIcon className="h-4 w-4 text-primary-400 dark:text-gray-500" />
          )}
          <code className="text-xs font-mono text-primary-700 dark:text-gray-300">
            {item.request_id.slice(0, 12)}...
          </code>
          <span className="text-xs text-primary-600 dark:text-gray-400">
            {formatDuration(item.duration_ms)}
          </span>
          {item.model && (
            <span className="text-xs font-medium text-primary-700 dark:text-gray-300">
              {item.model}
            </span>
          )}
          {item.total_tokens != null && (
            <span className="text-xs text-primary-500 dark:text-gray-500">
              {item.total_tokens} {t('debugPage.tokens')}
            </span>
          )}
          {item.phases_count != null && (
            <span className="text-xs text-primary-500 dark:text-gray-500">
              {item.phases_count} {t('debugPage.phases')}
            </span>
          )}
        </div>
        <StatusBadge status={item.status} />
      </button>

      {isExpanded && (
        <div className="border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/30 px-6 py-3">
          {loadingDetail ? (
            <div className="flex items-center gap-2 py-2">
              <ArrowPathIcon className="h-4 w-4 animate-spin text-primary-400" />
              <span className="text-xs text-primary-500 dark:text-gray-400">Loading details...</span>
            </div>
          ) : detail?.entries && detail.entries.length > 0 ? (
            <div className="space-y-0.5">
              {detail.entries.map((entry, idx) => (
                <EntryRenderer key={idx} entry={entry} />
              ))}
              {/* Summary */}
              <div className="mt-3 pt-2 border-t border-gray-200 dark:border-gray-600 flex items-center gap-4 text-xs text-primary-500 dark:text-gray-500">
                <span>{t('debugPage.duration')}: {formatDuration(detail.duration_ms)}</span>
                {detail.total_tokens != null && <span>{t('debugPage.tokens')}: {detail.total_tokens}</span>}
                <span>Status: {detail.status}</span>
              </div>
            </div>
          ) : item.entries && item.entries.length > 0 ? (
            <div className="space-y-0.5">
              {item.entries.map((entry, idx) => (
                <EntryRenderer key={idx} entry={entry} />
              ))}
            </div>
          ) : (
            <span className="text-xs text-primary-500 dark:text-gray-500">No detailed entries available</span>
          )}
        </div>
      )}
    </div>
  )
}

export default function LiveDebugPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { user, isLoading: authLoading } = useAuth()
  const [systemState, setSystemState] = useState<DebugSystemState | null>(null)
  const [activeRequests, setActiveRequests] = useState<DebugActiveRequest[]>([])
  const [recentActivity, setRecentActivity] = useState<DebugActivityItem[]>([])
  const [expandedRequest, setExpandedRequest] = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState(Date.now())
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Admin-only access check
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/dashboard')
    }
  }, [user, authLoading, navigate])

  const fetchAll = useCallback(async () => {
    try {
      const [stateData, liveData] = await Promise.all([
        debugApi.getSystemState(),
        debugApi.getLiveActivity(),
      ])
      setSystemState(stateData)
      setActiveRequests(liveData.active || [])
      setRecentActivity(liveData.recent || [])
      setLastRefresh(Date.now())
      setError('')
    } catch (err) {
      console.error('Debug fetch error:', err)
      setError(err instanceof Error ? err.message : 'Failed to fetch debug data')
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Poll every 5 seconds
  useEffect(() => {
    fetchAll()
    intervalRef.current = setInterval(fetchAll, 5000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [fetchAll])

  // "Last updated Xs ago" ticker
  const [secondsAgo, setSecondsAgo] = useState(0)
  useEffect(() => {
    const tick = setInterval(() => {
      setSecondsAgo(Math.floor((Date.now() - lastRefresh) / 1000))
    }, 1000)
    return () => clearInterval(tick)
  }, [lastRefresh])

  // Loading/auth checks
  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  if (!user?.is_admin) return null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-display font-semibold text-primary-900 dark:text-gray-200">
            {t('debugPage.title')}
          </h2>
          <p className="text-sm text-primary-700 dark:text-gray-400">
            {t('debugPage.lastUpdated')} {secondsAgo}{t('debugPage.secondsAgo')}
          </p>
        </div>
        <button
          onClick={fetchAll}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-600 px-3 py-1.5 text-sm text-primary-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-4 py-3 flex items-center gap-3">
          <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
          <span className="text-sm text-red-700 dark:text-red-300">{error}</span>
        </div>
      )}

      {/* System State Banner */}
      {systemState && (
        <div className="rounded-xl bg-surface dark:bg-gray-800 border border-gray-100 dark:border-gray-700 shadow-elevation-1 p-5">
          <h3 className="text-sm font-semibold text-primary-900 dark:text-gray-200 mb-3">
            {t('debugPage.systemState')}
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <span className="text-xs text-primary-500 dark:text-gray-500">{t('debugPage.orchestrator')}</span>
              <p className="text-sm font-mono font-medium text-primary-800 dark:text-gray-200 truncate">
                {systemState.orchestrator_model}
              </p>
              <p className="text-xs text-primary-500 dark:text-gray-500">{systemState.orchestrator_provider}</p>
            </div>
            <div>
              <span className="text-xs text-primary-500 dark:text-gray-500">{t('debugPage.worker')}</span>
              <p className="text-sm font-mono font-medium text-primary-800 dark:text-gray-200 truncate">
                {systemState.worker_model}
              </p>
              <p className="text-xs text-primary-500 dark:text-gray-500">{systemState.worker_provider}</p>
            </div>
            <div>
              <span className="text-xs text-primary-500 dark:text-gray-500">{t('debugPage.profile')}</span>
              <p className="text-sm font-mono font-medium text-primary-800 dark:text-gray-200">
                {systemState.active_profile}
              </p>
              <p className="text-xs text-primary-500 dark:text-gray-500">{systemState.database}</p>
            </div>
            <div>
              <span className="text-xs text-primary-500 dark:text-gray-500">{t('debugPage.uptime')}</span>
              <p className="text-sm font-mono font-medium text-primary-800 dark:text-gray-200">
                {formatUptime(systemState.uptime_seconds)}
              </p>
              <p className="text-xs text-primary-500 dark:text-gray-500">
                {systemState.active_requests} {t('debugPage.activeRequests').toLowerCase()}
              </p>
            </div>
          </div>
          <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
            <span className="text-xs text-primary-500 dark:text-gray-500">Ollama: </span>
            <code className="text-xs font-mono text-primary-700 dark:text-gray-300">{systemState.ollama_url}</code>
          </div>
        </div>
      )}

      {/* Active Requests */}
      <div>
        <h3 className="text-sm font-semibold text-primary-900 dark:text-gray-200 mb-3">
          {t('debugPage.activeRequests')}
          {activeRequests.length > 0 && (
            <span className="ml-2 inline-flex items-center rounded-full bg-yellow-100 dark:bg-yellow-900/30 px-2 py-0.5 text-xs font-medium text-yellow-800 dark:text-yellow-300">
              {activeRequests.length}
            </span>
          )}
        </h3>
        {activeRequests.length > 0 ? (
          <div className="space-y-2">
            {activeRequests.map((req) => (
              <ActiveRequestRow key={req.request_id} request={req} />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-4 py-6 text-center">
            <span className="text-sm text-primary-500 dark:text-gray-500">
              {t('debugPage.noActiveRequests')}
            </span>
          </div>
        )}
      </div>

      {/* Recent Activity Timeline */}
      <div>
        <h3 className="text-sm font-semibold text-primary-900 dark:text-gray-200 mb-3">
          {t('debugPage.recentActivity')}
        </h3>
        {isLoading && recentActivity.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <ArrowPathIcon className="h-6 w-6 animate-spin text-primary-400" />
          </div>
        ) : recentActivity.length > 0 ? (
          <div className="rounded-xl bg-surface dark:bg-gray-800 border border-gray-100 dark:border-gray-700 shadow-elevation-1 overflow-hidden">
            {recentActivity.map((item) => (
              <ActivityRow
                key={item.request_id}
                item={item}
                isExpanded={expandedRequest === item.request_id}
                onToggle={() =>
                  setExpandedRequest(
                    expandedRequest === item.request_id ? null : item.request_id
                  )
                }
              />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-4 py-6 text-center">
            <span className="text-sm text-primary-500 dark:text-gray-500">
              {t('debugPage.noRecentActivity')}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

// Named export for testing
export { LiveDebugPage }
