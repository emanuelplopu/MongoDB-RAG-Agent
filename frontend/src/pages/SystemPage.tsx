import { useState, useEffect, useRef } from 'react'
import {
  ServerIcon,
  CircleStackIcon,
  CpuChipIcon,
  DocumentTextIcon,
  ArrowPathIcon,
  PlayIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  TrashIcon,
  ClockIcon,
} from '@heroicons/react/24/outline'
import { systemApi, ingestionApi, SystemStats, IngestionStatus, LogEntry } from '../api/client'

const MAX_LOG_LINES = 50000

export default function SystemPage() {
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [ingestionStatus, setIngestionStatus] = useState<IngestionStatus | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logIndex, setLogIndex] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isIngesting, setIsIngesting] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const logsContainerRef = useRef<HTMLDivElement>(null)

  const fetchData = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [statsRes, ingestionRes] = await Promise.all([
        systemApi.stats(),
        ingestionApi.getStatus(),
      ])
      setStats(statsRes)
      setIngestionStatus(ingestionRes)
    } catch (err) {
      console.error('Error fetching system data:', err)
      setError('Failed to load system information.')
    } finally {
      setIsLoading(false)
    }
  }

  const fetchLogs = async () => {
    try {
      const logsRes = await ingestionApi.getLogs(logIndex, 1000)
      if (logsRes.logs.length > 0) {
        setLogs(prevLogs => {
          const newLogs = [...prevLogs, ...logsRes.logs]
          // Trim to MAX_LOG_LINES
          if (newLogs.length > MAX_LOG_LINES) {
            return newLogs.slice(-MAX_LOG_LINES)
          }
          return newLogs
        })
        setLogIndex(logsRes.start_index + logsRes.logs.length)
      }
    } catch (err) {
      console.error('Error fetching logs:', err)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  // Auto-scroll logs to bottom
  useEffect(() => {
    if (showLogs && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, showLogs])

  const handleStartIngestion = async (incremental: boolean = true) => {
    setIsIngesting(true)
    setLogs([])
    setLogIndex(0)
    setShowLogs(true)
    try {
      const status = await ingestionApi.start({ incremental })
      setIngestionStatus(status)
      
      // Poll for updates and logs
      const pollInterval = setInterval(async () => {
        try {
          const [updatedStatus] = await Promise.all([
            ingestionApi.getStatus(),
          ])
          setIngestionStatus(updatedStatus)
          await fetchLogs()
          
          if (updatedStatus.status === 'completed' || updatedStatus.status === 'failed') {
            clearInterval(pollInterval)
            setIsIngesting(false)
            fetchData()
            // Fetch final logs
            await fetchLogs()
          }
        } catch (e) {
          clearInterval(pollInterval)
          setIsIngesting(false)
        }
      }, 1000)
    } catch (err) {
      console.error('Error starting ingestion:', err)
      setError('Failed to start ingestion.')
      setIsIngesting(false)
    }
  }

  const handleClearLogs = async () => {
    try {
      await ingestionApi.clearLogs()
      setLogs([])
      setLogIndex(0)
    } catch (err) {
      console.error('Error clearing logs:', err)
    }
  }

  const formatTime = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`
    if (seconds < 3600) {
      const mins = Math.floor(seconds / 60)
      const secs = Math.round(seconds % 60)
      return `${mins}m ${secs}s`
    }
    const hours = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    return `${hours}h ${mins}m`
  }

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900">System Status</h2>
          <p className="text-sm text-secondary">Monitor and manage your RAG system</p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 rounded-xl bg-surface-variant px-4 py-2 text-sm font-medium text-primary-700 transition-all hover:bg-primary-100"
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-2xl bg-red-50 p-4 text-red-700">{error}</div>
      )}

      {/* Stats cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* Documents */}
        <div className="rounded-2xl bg-surface p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 p-2">
              <DocumentTextIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary">Documents</span>
          </div>
          <p className="text-2xl font-semibold text-primary-900">
            {stats?.database.documents.count || 0}
          </p>
          <p className="text-xs text-secondary mt-1">
            {formatBytes(stats?.database.documents.size_bytes || 0)}
          </p>
        </div>

        {/* Chunks */}
        <div className="rounded-2xl bg-surface p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 p-2">
              <CpuChipIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary">Chunks</span>
          </div>
          <p className="text-2xl font-semibold text-primary-900">
            {stats?.database.chunks.count || 0}
          </p>
          <p className="text-xs text-secondary mt-1">
            {formatBytes(stats?.database.chunks.size_bytes || 0)}
          </p>
        </div>

        {/* Database */}
        <div className="rounded-2xl bg-surface p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 p-2">
              <CircleStackIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary">Database</span>
          </div>
          <p className="text-lg font-semibold text-primary-900">
            {stats?.database.database || 'N/A'}
          </p>
        </div>

        {/* Model */}
        <div className="rounded-2xl bg-surface p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 p-2">
              <ServerIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary">LLM Model</span>
          </div>
          <p className="text-sm font-semibold text-primary-900 truncate">
            {stats?.config.llm_model || 'N/A'}
          </p>
          <p className="text-xs text-secondary mt-1">{stats?.config.llm_provider}</p>
        </div>
      </div>

      {/* Indexes */}
      <div className="rounded-2xl bg-surface p-6 shadow-elevation-1">
        <h3 className="text-lg font-medium text-primary-900 mb-4">Search Indexes</h3>
        {stats?.indexes?.error ? (
          <div className="rounded-xl bg-amber-50 p-4 text-amber-700">
            <p className="font-medium">Indexes not available</p>
            <p className="text-sm mt-1">Run ingestion to create the required collections and indexes.</p>
          </div>
        ) : stats?.indexes?.indexes && stats.indexes.indexes.length > 0 ? (
          <div className="grid gap-4 md:grid-cols-2">
            {stats.indexes.indexes.map((index, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded-xl bg-surface-variant p-4"
              >
                <div>
                  <p className="font-medium text-primary-900">{index.name}</p>
                  <p className="text-sm text-secondary">{index.type}</p>
                </div>
                <div className="flex items-center gap-2">
                  {index.status === 'READY' ? (
                    <>
                      <CheckCircleIcon className="h-5 w-5 text-green-500" />
                      <span className="text-sm font-medium text-green-700">Ready</span>
                    </>
                  ) : (
                    <>
                      <ExclamationTriangleIcon className="h-5 w-5 text-amber-500" />
                      <span className="text-sm font-medium text-amber-700">{index.status}</span>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-secondary">No indexes found. Run ingestion to create search indexes.</p>
        )}
      </div>

      {/* Ingestion */}
      <div className="rounded-2xl bg-surface p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-medium text-primary-900">Ingestion</h3>
          <div className="flex gap-2">
            <button
              onClick={() => handleStartIngestion(true)}
              disabled={isIngesting}
              className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-all hover:bg-primary-700 disabled:bg-secondary disabled:cursor-not-allowed"
            >
              <PlayIcon className="h-4 w-4" />
              Incremental
            </button>
            <button
              onClick={() => handleStartIngestion(false)}
              disabled={isIngesting}
              className="flex items-center gap-2 rounded-xl bg-surface-variant px-4 py-2 text-sm font-medium text-primary-700 transition-all hover:bg-primary-100 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Full Reindex
            </button>
          </div>
        </div>

        {ingestionStatus && (
          <div className="space-y-4">
            {/* Status badge and time info */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span
                  className={`inline-flex items-center gap-1 rounded-lg px-3 py-1 text-sm font-medium ${
                    ingestionStatus.status === 'completed'
                      ? 'bg-green-100 text-green-700'
                      : ingestionStatus.status === 'running'
                      ? 'bg-primary-100 text-primary-700'
                      : ingestionStatus.status === 'failed'
                      ? 'bg-red-100 text-red-700'
                      : 'bg-surface-variant text-secondary'
                  }`}
                >
                  {ingestionStatus.status === 'running' && (
                    <ArrowPathIcon className="h-4 w-4 animate-spin" />
                  )}
                  {ingestionStatus.status.charAt(0).toUpperCase() + ingestionStatus.status.slice(1)}
                </span>
              </div>
              
              {/* Time info */}
              {(ingestionStatus.status === 'running' || ingestionStatus.elapsed_seconds > 0) && (
                <div className="flex items-center gap-4 text-sm">
                  <div className="flex items-center gap-1 text-secondary">
                    <ClockIcon className="h-4 w-4" />
                    <span>Elapsed: {formatTime(ingestionStatus.elapsed_seconds)}</span>
                  </div>
                  {ingestionStatus.status === 'running' && ingestionStatus.estimated_remaining_seconds && (
                    <div className="text-secondary">
                      ETA: {formatTime(ingestionStatus.estimated_remaining_seconds)}
                    </div>
                  )}
                </div>
              )}
            </div>

            {ingestionStatus.status === 'running' && (
              <>
                {/* Progress bar */}
                <div className="w-full rounded-full bg-surface-variant h-3">
                  <div
                    className="bg-primary h-3 rounded-full transition-all flex items-center justify-end pr-2"
                    style={{ width: `${Math.max(ingestionStatus.progress_percent, 2)}%` }}
                  >
                    {ingestionStatus.progress_percent > 10 && (
                      <span className="text-[10px] font-medium text-white">
                        {ingestionStatus.progress_percent.toFixed(1)}%
                      </span>
                    )}
                  </div>
                </div>
                
                {/* Stats grid */}
                <div className="grid grid-cols-4 gap-4 text-sm">
                  <div>
                    <p className="text-secondary">Processed</p>
                    <p className="font-medium text-primary-900">
                      {ingestionStatus.processed_files} / {ingestionStatus.total_files}
                    </p>
                  </div>
                  <div>
                    <p className="text-secondary">Chunks Created</p>
                    <p className="font-medium text-primary-900">{ingestionStatus.chunks_created}</p>
                  </div>
                  <div>
                    <p className="text-secondary">Failed Files</p>
                    <p className="font-medium text-primary-900">{ingestionStatus.failed_files}</p>
                  </div>
                  <div>
                    <p className="text-secondary">Current File</p>
                    <p className="font-medium text-primary-900 truncate" title={ingestionStatus.current_file || 'N/A'}>
                      {ingestionStatus.current_file?.split('/').pop() || 'N/A'}
                    </p>
                  </div>
                </div>
              </>
            )}

            {ingestionStatus.errors.length > 0 && (
              <div className="rounded-xl bg-red-50 p-4">
                <p className="font-medium text-red-700 mb-2">Errors ({ingestionStatus.errors.length})</p>
                <ul className="space-y-1 text-sm text-red-600 max-h-32 overflow-y-auto">
                  {ingestionStatus.errors.slice(0, 10).map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Logs Panel */}
      {(showLogs || logs.length > 0) && (
        <div className="rounded-2xl bg-surface p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-medium text-primary-900">
              Ingestion Logs ({logs.length.toLocaleString()} lines)
            </h3>
            <div className="flex gap-2">
              <button
                onClick={() => setShowLogs(!showLogs)}
                className="rounded-xl bg-surface-variant px-3 py-1.5 text-sm font-medium text-primary-700 transition-all hover:bg-primary-100"
              >
                {showLogs ? 'Collapse' : 'Expand'}
              </button>
              <button
                onClick={handleClearLogs}
                className="flex items-center gap-1 rounded-xl bg-surface-variant px-3 py-1.5 text-sm font-medium text-red-600 transition-all hover:bg-red-100"
              >
                <TrashIcon className="h-4 w-4" />
                Clear
              </button>
            </div>
          </div>
          
          {showLogs && (
            <div 
              ref={logsContainerRef}
              className="bg-gray-900 rounded-xl p-4 font-mono text-xs overflow-auto max-h-96"
            >
              {logs.length === 0 ? (
                <p className="text-gray-500">No logs yet. Start ingestion to see logs.</p>
              ) : (
                <div className="space-y-0.5">
                  {logs.map((log, i) => (
                    <div key={i} className="flex gap-2">
                      <span className="text-gray-500 whitespace-nowrap">
                        {new Date(log.timestamp).toLocaleTimeString()}
                      </span>
                      <span className={`whitespace-nowrap font-semibold ${
                        log.level === 'ERROR' ? 'text-red-400' :
                        log.level === 'WARNING' ? 'text-yellow-400' :
                        log.level === 'INFO' ? 'text-blue-400' :
                        'text-gray-400'
                      }`}>
                        [{log.level}]
                      </span>
                      <span className="text-gray-300 break-all">{log.message}</span>
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Configuration */}
      {stats?.config && (
        <div className="rounded-2xl bg-surface p-6 shadow-elevation-1">
          <h3 className="text-lg font-medium text-primary-900 mb-4">Configuration</h3>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-xl bg-surface-variant p-4">
              <p className="text-sm text-secondary">Embedding Model</p>
              <p className="font-medium text-primary-900">{stats.config.embedding_model || 'N/A'}</p>
            </div>
            <div className="rounded-xl bg-surface-variant p-4">
              <p className="text-sm text-secondary">Embedding Dimension</p>
              <p className="font-medium text-primary-900">{stats.config.embedding_dimension || 'N/A'}</p>
            </div>
            <div className="rounded-xl bg-surface-variant p-4">
              <p className="text-sm text-secondary">Default Match Count</p>
              <p className="font-medium text-primary-900">{stats.config.default_match_count || 'N/A'}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
