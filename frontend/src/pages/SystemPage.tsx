import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
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
  Cog6ToothIcon,
  PauseIcon,
  StopIcon,
  PhotoIcon,
  MusicalNoteIcon,
  VideoCameraIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  SignalIcon,
} from '@heroicons/react/24/outline'
import { 
  systemApi, 
  ingestionApi, 
  SystemStats, 
  IngestionStatus,
  IngestionRunSummary,
  LogEntry,
  LLMModel,
  EmbeddingModel,
  ConfigUpdateRequest,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'

const MAX_LOG_LINES = 50000

// Fallback options
const FALLBACK_MATCH_COUNTS = [5, 10, 15, 20, 25, 50, 100]
const FALLBACK_EMBEDDING_DIMENSIONS = [256, 512, 768, 1024, 1536, 3072]

export default function SystemPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [ingestionStatus, setIngestionStatus] = useState<IngestionStatus | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isIngesting, setIsIngesting] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const logsContainerRef = useRef<HTMLDivElement>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  
  // Ingestion runs state
  const [runs, setRuns] = useState<IngestionRunSummary[]>([])
  const [runsPage, setRunsPage] = useState(1)
  const [runsTotalPages, setRunsTotalPages] = useState(1)
  const [runsTotal, setRunsTotal] = useState(0)
  
  // Control buttons loading states
  const [isPausing, setIsPausing] = useState(false)
  const [isResuming, setIsResuming] = useState(false)
  const [isStopping, setIsStopping] = useState(false)

  // Config state
  const [llmModels, setLlmModels] = useState<LLMModel[]>([])
  const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModel[]>([])
  const [matchCountOptions, setMatchCountOptions] = useState<number[]>(FALLBACK_MATCH_COUNTS)
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [configValues, setConfigValues] = useState({
    llm_model: '',
    embedding_model: '',
    embedding_dimension: 1536,
    default_match_count: 10,
  })
  const [isSavingConfig, setIsSavingConfig] = useState(false)
  const [isSavingToDb, setIsSavingToDb] = useState(false)
  const [configMessage, setConfigMessage] = useState<string | null>(null)
  
  // Admin-only access check
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/')
    }
  }, [user, authLoading, navigate])
  
  // Show loading while checking auth
  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  
  // Redirect non-admins
  if (!user?.is_admin) {
    return null
  }

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
      
      // Check if running
      if (ingestionRes.status === 'running' || ingestionRes.status === 'paused') {
        setIsIngesting(true)
      }
    } catch (err) {
      console.error('Error fetching system data:', err)
      setError('Failed to load system information.')
    } finally {
      setIsLoading(false)
    }
  }
  
  const fetchRuns = useCallback(async (page: number = 1) => {
    try {
      const res = await ingestionApi.getRuns(page, 5)
      setRuns(res.runs)
      setRunsPage(res.page)
      setRunsTotalPages(res.total_pages)
      setRunsTotal(res.total)
    } catch (err) {
      console.error('Error fetching runs:', err)
    }
  }, [])

  useEffect(() => {
    fetchData()
    fetchModels()
    fetchRuns(1)
  }, [fetchRuns])
  
  // Start log streaming
  const startLogStreaming = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
    }
    
    const url = ingestionApi.getLogsStreamUrl()
    const token = localStorage.getItem('auth_token')
    const eventSource = new EventSource(`${url}${token ? `?token=${token}` : ''}`)
    eventSourceRef.current = eventSource
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        
        if (data.type === 'status') {
          // Status update
          return
        }
        
        // Log entry
        setLogs(prevLogs => {
          const newLogs = [...prevLogs, data]
          if (newLogs.length > MAX_LOG_LINES) {
            return newLogs.slice(-MAX_LOG_LINES)
          }
          return newLogs
        })
      } catch (err) {
        console.error('Error parsing SSE data:', err)
      }
    }
    
    eventSource.onerror = () => {
      eventSource.close()
      setIsStreaming(false)
      eventSourceRef.current = null
    }
    
    setIsStreaming(true)
  }, [])
  
  const stopLogStreaming = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
    setIsStreaming(false)
  }, [])
  
  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
    }
  }, [])

  const fetchModels = async () => {
    setIsLoadingModels(true)
    try {
      const [llmRes, embeddingRes, optionsRes] = await Promise.all([
        systemApi.listLLMModels(),
        systemApi.listEmbeddingModels(),
        systemApi.getConfigOptions(),
      ])
      
      setLlmModels(llmRes.models)
      setEmbeddingModels(embeddingRes.models)
      setMatchCountOptions(optionsRes.options.match_count_options)
      
      // Set current values
      setConfigValues({
        llm_model: optionsRes.current.llm_model,
        embedding_model: optionsRes.current.embedding_model,
        embedding_dimension: optionsRes.current.embedding_dimension,
        default_match_count: optionsRes.current.default_match_count,
      })
    } catch (err) {
      console.error('Error fetching models:', err)
    } finally {
      setIsLoadingModels(false)
    }
  }

  const handleSaveConfig = async () => {
    setIsSavingConfig(true)
    setConfigMessage(null)
    try {
      const update: ConfigUpdateRequest = {
        llm_model: configValues.llm_model,
        embedding_model: configValues.embedding_model,
        embedding_dimension: configValues.embedding_dimension,
        default_match_count: configValues.default_match_count,
      }
      const result = await systemApi.updateConfig(update)
      if (result.success) {
        setConfigMessage('Configuration saved to runtime (will reset on restart)')
        fetchData() // Refresh stats
      } else {
        setConfigMessage(result.message || 'Failed to save configuration')
      }
      setTimeout(() => setConfigMessage(null), 4000)
    } catch (err) {
      console.error('Error saving config:', err)
      setConfigMessage('Failed to save configuration')
    } finally {
      setIsSavingConfig(false)
    }
  }

  const handleSaveToDb = async () => {
    setIsSavingToDb(true)
    setConfigMessage(null)
    try {
      const update: ConfigUpdateRequest = {
        llm_model: configValues.llm_model,
        embedding_model: configValues.embedding_model,
        embedding_dimension: configValues.embedding_dimension,
        default_match_count: configValues.default_match_count,
      }
      const result = await systemApi.saveConfigToDb(update)
      if (result.success) {
        setConfigMessage('Configuration saved to database (will persist across restarts)')
        fetchData() // Refresh stats
      } else {
        setConfigMessage(result.message || 'Failed to save configuration to database')
      }
      setTimeout(() => setConfigMessage(null), 4000)
    } catch (err) {
      console.error('Error saving config to DB:', err)
      setConfigMessage('Failed to save configuration to database')
    } finally {
      setIsSavingToDb(false)
    }
  }

  // Auto-scroll logs to bottom
  useEffect(() => {
    if (showLogs && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, showLogs])

  const handleStartIngestion = async (incremental: boolean = true) => {
    setIsIngesting(true)
    setLogs([])
    setShowLogs(true)
    try {
      const status = await ingestionApi.start({ incremental })
      setIngestionStatus(status)
      startLogStreaming()
      
      // Poll for updates
      const pollInterval = setInterval(async () => {
        try {
          const updatedStatus = await ingestionApi.getStatus()
          setIngestionStatus(updatedStatus)
          
          if (updatedStatus.status === 'completed' || updatedStatus.status === 'failed' || updatedStatus.status === 'stopped') {
            clearInterval(pollInterval)
            setIsIngesting(false)
            fetchData()
            fetchRuns(1)
            // Keep streaming for a bit to catch final logs
            setTimeout(() => stopLogStreaming(), 3000)
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
  
  const handlePause = async () => {
    setIsPausing(true)
    try {
      await ingestionApi.pause()
      const status = await ingestionApi.getStatus()
      setIngestionStatus(status)
    } catch (err) {
      console.error('Error pausing:', err)
    } finally {
      setIsPausing(false)
    }
  }
  
  const handleResume = async () => {
    setIsResuming(true)
    try {
      await ingestionApi.resume()
      const status = await ingestionApi.getStatus()
      setIngestionStatus(status)
    } catch (err) {
      console.error('Error resuming:', err)
    } finally {
      setIsResuming(false)
    }
  }
  
  const handleStop = async () => {
    if (!confirm('Are you sure you want to stop the ingestion? This cannot be undone.')) return
    setIsStopping(true)
    try {
      await ingestionApi.stop()
      const status = await ingestionApi.getStatus()
      setIngestionStatus(status)
      setIsIngesting(false)
      fetchRuns(1)
    } catch (err) {
      console.error('Error stopping:', err)
    } finally {
      setIsStopping(false)
    }
  }

  const handleClearLogs = async () => {
    try {
      await ingestionApi.clearLogs()
      setLogs([])
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
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">System Status</h2>
          <p className="text-sm text-secondary dark:text-gray-400">Monitor and manage your RAG system</p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}

      {/* Stats cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* Documents */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 dark:bg-primary-900/50 p-2">
              <DocumentTextIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary dark:text-gray-400">Documents</span>
          </div>
          <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
            {stats?.database.documents.count || 0}
          </p>
          <p className="text-xs text-secondary dark:text-gray-500 mt-1">
            {formatBytes(stats?.database.documents.size_bytes || 0)}
          </p>
        </div>

        {/* Chunks */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 dark:bg-primary-900/50 p-2">
              <CpuChipIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary dark:text-gray-400">Chunks</span>
          </div>
          <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
            {stats?.database.chunks.count || 0}
          </p>
          <p className="text-xs text-secondary dark:text-gray-500 mt-1">
            {formatBytes(stats?.database.chunks.size_bytes || 0)}
          </p>
        </div>

        {/* Database */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 dark:bg-primary-900/50 p-2">
              <CircleStackIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary dark:text-gray-400">Database</span>
          </div>
          <p className="text-lg font-semibold text-primary-900 dark:text-gray-200">
            {stats?.database.database || 'N/A'}
          </p>
        </div>

        {/* Model */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-3">
            <div className="rounded-xl bg-primary-100 dark:bg-primary-900/50 p-2">
              <ServerIcon className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-secondary dark:text-gray-400">LLM Model</span>
          </div>
          <p className="text-sm font-semibold text-primary-900 dark:text-gray-200 truncate">
            {stats?.config.llm_model || 'N/A'}
          </p>
          <p className="text-xs text-secondary dark:text-gray-500 mt-1">{stats?.config.llm_provider}</p>
        </div>
      </div>

      {/* Indexes */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Search Indexes</h3>
        {stats?.indexes?.error ? (
          <div className="rounded-xl bg-amber-50 dark:bg-amber-900/30 p-4 text-amber-700 dark:text-amber-400">
            <p className="font-medium">Indexes not available</p>
            <p className="text-sm mt-1">Run ingestion to create the required collections and indexes.</p>
          </div>
        ) : stats?.indexes?.indexes && stats.indexes.indexes.length > 0 ? (
          <div className="grid gap-4 md:grid-cols-2">
            {stats.indexes.indexes.map((index, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded-xl bg-surface-variant dark:bg-gray-700 p-4"
              >
                <div>
                  <p className="font-medium text-primary-900 dark:text-gray-200">{index.name}</p>
                  <p className="text-sm text-secondary dark:text-gray-400">{index.type}</p>
                </div>
                <div className="flex items-center gap-2">
                  {index.status === 'READY' ? (
                    <>
                      <CheckCircleIcon className="h-5 w-5 text-green-500" />
                      <span className="text-sm font-medium text-green-700 dark:text-green-400">Ready</span>
                    </>
                  ) : (
                    <>
                      <ExclamationTriangleIcon className="h-5 w-5 text-amber-500" />
                      <span className="text-sm font-medium text-amber-700 dark:text-amber-400">{index.status}</span>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-secondary dark:text-gray-400">No indexes found. Run ingestion to create search indexes.</p>
        )}
      </div>

      {/* Ingestion */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Ingestion</h3>
          <div className="flex gap-2">
            {/* Control buttons */}
            {ingestionStatus && (ingestionStatus.status === 'running' || ingestionStatus.status === 'paused') && (
              <>
                {ingestionStatus.status === 'paused' ? (
                  <button
                    onClick={handleResume}
                    disabled={isResuming}
                    className="flex items-center gap-2 rounded-xl bg-green-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-green-700 disabled:opacity-50"
                  >
                    <PlayIcon className="h-4 w-4" />
                    {isResuming ? 'Resuming...' : 'Resume'}
                  </button>
                ) : (
                  <button
                    onClick={handlePause}
                    disabled={isPausing}
                    className="flex items-center gap-2 rounded-xl bg-amber-500 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-amber-600 disabled:opacity-50"
                  >
                    <PauseIcon className="h-4 w-4" />
                    {isPausing ? 'Pausing...' : 'Pause'}
                  </button>
                )}
                <button
                  onClick={handleStop}
                  disabled={isStopping}
                  className="flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-red-700 disabled:opacity-50"
                >
                  <StopIcon className="h-4 w-4" />
                  {isStopping ? 'Stopping...' : 'Stop'}
                </button>
              </>
            )}
            {/* Start buttons */}
            {(!ingestionStatus || (ingestionStatus.status !== 'running' && ingestionStatus.status !== 'paused')) && (
              <>
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
                  className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Full Reindex
                </button>
              </>
            )}
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
                      ? 'bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-400'
                      : ingestionStatus.status === 'running'
                      ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                      : ingestionStatus.status === 'paused'
                      ? 'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400'
                      : ingestionStatus.status === 'stopped'
                      ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-400'
                      : ingestionStatus.status === 'failed'
                      ? 'bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-400'
                      : 'bg-surface-variant dark:bg-gray-700 text-secondary dark:text-gray-400'
                  }`}
                >
                  {ingestionStatus.status === 'running' && (
                    <ArrowPathIcon className="h-4 w-4 animate-spin" />
                  )}
                  {ingestionStatus.status === 'paused' && (
                    <PauseIcon className="h-4 w-4" />
                  )}
                  {ingestionStatus.status.charAt(0).toUpperCase() + ingestionStatus.status.slice(1)}
                </span>
              </div>
              
              {/* Time info */}
              {(ingestionStatus.status === 'running' || ingestionStatus.elapsed_seconds > 0) && (
                <div className="flex items-center gap-4 text-sm">
                  <div className="flex items-center gap-1 text-secondary dark:text-gray-400">
                    <ClockIcon className="h-4 w-4" />
                    <span>Elapsed: {formatTime(ingestionStatus.elapsed_seconds)}</span>
                  </div>
                  {ingestionStatus.status === 'running' && ingestionStatus.estimated_remaining_seconds && (
                    <div className="text-secondary dark:text-gray-400">
                      ETA: {formatTime(ingestionStatus.estimated_remaining_seconds)}
                    </div>
                  )}
                </div>
              )}
            </div>

            {ingestionStatus.status === 'running' && (
              <>
                {/* Progress bar */}
                <div className="w-full rounded-full bg-surface-variant dark:bg-gray-700 h-3">
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
                
                {/* Stats grid with file types */}
                <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 text-sm">
                  <div>
                    <p className="text-secondary dark:text-gray-400">Processed</p>
                    <p className="font-medium text-primary-900 dark:text-gray-200">
                      {ingestionStatus.processed_files} / {ingestionStatus.total_files}
                    </p>
                  </div>
                  <div>
                    <p className="text-secondary dark:text-gray-400">Chunks</p>
                    <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.chunks_created}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <DocumentTextIcon className="h-4 w-4 text-blue-500" />
                    <div>
                      <p className="text-xs text-secondary dark:text-gray-400">Docs</p>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.document_count || 0}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <PhotoIcon className="h-4 w-4 text-green-500" />
                    <div>
                      <p className="text-xs text-secondary dark:text-gray-400">Images</p>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.image_count || 0}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <MusicalNoteIcon className="h-4 w-4 text-purple-500" />
                    <div>
                      <p className="text-xs text-secondary dark:text-gray-400">Audio</p>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.audio_count || 0}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <VideoCameraIcon className="h-4 w-4 text-red-500" />
                    <div>
                      <p className="text-xs text-secondary dark:text-gray-400">Video</p>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.video_count || 0}</p>
                    </div>
                  </div>
                </div>
                
                {/* Current file and failures */}
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <p className="text-secondary dark:text-gray-400">Failed / Excluded</p>
                    <p className="font-medium text-primary-900 dark:text-gray-200">
                      <span className="text-red-500">{ingestionStatus.failed_files}</span>
                      {' / '}
                      <span className="text-gray-500">{ingestionStatus.excluded_files || 0}</span>
                    </p>
                  </div>
                  <div>
                    <p className="text-secondary dark:text-gray-400">Current File</p>
                    <p className="font-medium text-primary-900 dark:text-gray-200 truncate" title={ingestionStatus.current_file || 'N/A'}>
                      {ingestionStatus.current_file?.split(/[\\/]/).pop() || 'N/A'}
                    </p>
                  </div>
                </div>
              </>
            )}
            
            {/* Run History Table */}
            {runs.length > 0 && (
              <div className="mt-4">
                <h4 className="text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Ingestion History ({runsTotal} runs)</h4>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-200 dark:border-gray-700">
                        <th className="text-left py-2 px-2 text-secondary dark:text-gray-400 font-medium">Status</th>
                        <th className="text-left py-2 px-2 text-secondary dark:text-gray-400 font-medium">Started</th>
                        <th className="text-left py-2 px-2 text-secondary dark:text-gray-400 font-medium">Duration</th>
                        <th className="text-center py-2 px-1 text-secondary dark:text-gray-400 font-medium">Docs</th>
                        <th className="text-center py-2 px-1 text-secondary dark:text-gray-400 font-medium">Imgs</th>
                        <th className="text-center py-2 px-1 text-secondary dark:text-gray-400 font-medium">Audio</th>
                        <th className="text-center py-2 px-1 text-secondary dark:text-gray-400 font-medium">Video</th>
                        <th className="text-center py-2 px-1 text-secondary dark:text-gray-400 font-medium">Failed</th>
                        <th className="text-center py-2 px-1 text-secondary dark:text-gray-400 font-medium">Excl.</th>
                        <th className="text-center py-2 px-2 text-secondary dark:text-gray-400 font-medium">Chunks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((run) => (
                        <tr key={run.job_id} className="border-b border-gray-100 dark:border-gray-800">
                          <td className="py-2 px-2">
                            <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${
                              run.status === 'completed'
                                ? 'bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-400'
                                : run.status === 'running'
                                ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                                : run.status === 'stopped'
                                ? 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400'
                                : run.status === 'failed'
                                ? 'bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-400'
                                : 'bg-surface-variant dark:bg-gray-700 text-secondary'
                            }`}>
                              {run.status}
                            </span>
                          </td>
                          <td className="py-2 px-2 text-primary-900 dark:text-gray-300">
                            {run.started_at ? new Date(run.started_at).toLocaleString() : '-'}
                          </td>
                          <td className="py-2 px-2 text-primary-900 dark:text-gray-300">
                            {formatTime(run.elapsed_seconds)}
                          </td>
                          <td className="py-2 px-1 text-center text-primary-900 dark:text-gray-300">{run.document_count}</td>
                          <td className="py-2 px-1 text-center text-primary-900 dark:text-gray-300">{run.image_count}</td>
                          <td className="py-2 px-1 text-center text-primary-900 dark:text-gray-300">{run.audio_count}</td>
                          <td className="py-2 px-1 text-center text-primary-900 dark:text-gray-300">{run.video_count}</td>
                          <td className="py-2 px-1 text-center text-red-500">{run.failed_files}</td>
                          <td className="py-2 px-1 text-center text-gray-500">{run.excluded_files}</td>
                          <td className="py-2 px-2 text-center font-medium text-primary-900 dark:text-gray-200">{run.chunks_created}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                
                {/* Pagination */}
                {runsTotalPages > 1 && (
                  <div className="flex items-center justify-between mt-3">
                    <p className="text-xs text-secondary dark:text-gray-400">
                      Page {runsPage} of {runsTotalPages}
                    </p>
                    <div className="flex gap-1">
                      <button
                        onClick={() => fetchRuns(runsPage - 1)}
                        disabled={runsPage <= 1}
                        className="p-1 rounded bg-surface-variant dark:bg-gray-700 disabled:opacity-50"
                      >
                        <ChevronLeftIcon className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => fetchRuns(runsPage + 1)}
                        disabled={runsPage >= runsTotalPages}
                        className="p-1 rounded bg-surface-variant dark:bg-gray-700 disabled:opacity-50"
                      >
                        <ChevronRightIcon className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {ingestionStatus.errors.length > 0 && (
              <div className="rounded-xl bg-red-50 dark:bg-red-900/30 p-4">
                <p className="font-medium text-red-700 dark:text-red-400 mb-2">Errors ({ingestionStatus.errors.length})</p>
                <ul className="space-y-1 text-sm text-red-600 dark:text-red-400 max-h-32 overflow-y-auto">
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
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">
            Ingestion Logs ({logs.length.toLocaleString()} lines)
          </h3>
          <div className="flex gap-2">
            {!isStreaming ? (
              <button
                onClick={startLogStreaming}
                className="flex items-center gap-1 rounded-xl bg-green-100 dark:bg-green-900/30 px-3 py-1.5 text-sm font-medium text-green-700 dark:text-green-400 transition-all hover:bg-green-200 dark:hover:bg-green-800/50"
              >
                <SignalIcon className="h-4 w-4" />
                Start Streaming
              </button>
            ) : (
              <button
                onClick={stopLogStreaming}
                className="flex items-center gap-1 rounded-xl bg-red-100 dark:bg-red-900/30 px-3 py-1.5 text-sm font-medium text-red-600 dark:text-red-400 transition-all hover:bg-red-200 dark:hover:bg-red-800/50"
              >
                <StopIcon className="h-4 w-4" />
                Stop Streaming
              </button>
            )}
            <button
              onClick={() => setShowLogs(!showLogs)}
              className="rounded-xl bg-surface-variant dark:bg-gray-700 px-3 py-1.5 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
            >
              {showLogs ? 'Collapse' : 'Expand'}
            </button>
            <button
              onClick={handleClearLogs}
              className="flex items-center gap-1 rounded-xl bg-surface-variant dark:bg-gray-700 px-3 py-1.5 text-sm font-medium text-red-600 dark:text-red-400 transition-all hover:bg-red-100 dark:hover:bg-red-900/30"
            >
              <TrashIcon className="h-4 w-4" />
              Clear
            </button>
          </div>
        </div>
        
        {/* Current file indicator */}
        {ingestionStatus && (ingestionStatus.status === 'running' || ingestionStatus.status === 'paused') && ingestionStatus.current_file && (
          <div className="mb-3 flex items-center gap-2 text-sm">
            <span className="text-secondary dark:text-gray-400">Current File:</span>
            <span className="font-mono text-primary-900 dark:text-gray-200 bg-surface-variant dark:bg-gray-700 px-2 py-0.5 rounded truncate max-w-[600px]" title={ingestionStatus.current_file}>
              {ingestionStatus.current_file}
            </span>
            {isStreaming && (
              <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
                Live
              </span>
            )}
          </div>
        )}
        
        {showLogs && (
          <div 
            ref={logsContainerRef}
            className="bg-gray-900 rounded-xl p-4 font-mono text-xs overflow-auto max-h-96"
          >
            {logs.length === 0 ? (
              <p className="text-gray-500">No logs yet. Start ingestion or enable streaming to see logs.</p>
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

      {/* Configuration */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Cog6ToothIcon className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Configuration</h3>
          </div>
          <div className="flex items-center gap-2">
            {isLoadingModels && (
              <ArrowPathIcon className="h-4 w-4 animate-spin text-primary" />
            )}
            <button
              onClick={handleSaveConfig}
              disabled={isSavingConfig || isSavingToDb}
              className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600 disabled:opacity-50"
            >
              {isSavingConfig ? 'Saving...' : 'Save Runtime'}
            </button>
            <button
              onClick={handleSaveToDb}
              disabled={isSavingConfig || isSavingToDb}
              className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-all hover:bg-primary-700 disabled:opacity-50"
            >
              {isSavingToDb ? 'Saving...' : 'Save to DB'}
            </button>
          </div>
        </div>

        {configMessage && (
          <div className={`mb-4 p-3 rounded-xl text-sm ${
            configMessage.includes('Failed') 
              ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
              : 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
          }`}>
            {configMessage}
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {/* LLM Model */}
          <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
            <label className="block text-sm text-secondary dark:text-gray-400 mb-2">LLM Model</label>
            <select
              value={configValues.llm_model}
              onChange={(e) => setConfigValues({ ...configValues, llm_model: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {llmModels.length === 0 && configValues.llm_model && (
                <option value={configValues.llm_model}>{configValues.llm_model}</option>
              )}
              {llmModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id}
                </option>
              ))}
            </select>
            <p className="text-xs text-secondary dark:text-gray-500 mt-1">
              {llmModels.length} models available
            </p>
          </div>

          {/* Embedding Model */}
          <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
            <label className="block text-sm text-secondary dark:text-gray-400 mb-2">Embedding Model</label>
            <select
              value={configValues.embedding_model}
              onChange={(e) => {
                const model = embeddingModels.find(m => m.id === e.target.value)
                setConfigValues({ 
                  ...configValues, 
                  embedding_model: e.target.value,
                  embedding_dimension: model?.dimension || configValues.embedding_dimension
                })
              }}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {embeddingModels.length === 0 && configValues.embedding_model && (
                <option value={configValues.embedding_model}>{configValues.embedding_model}</option>
              )}
              {embeddingModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id} ({model.dimension}d)
                </option>
              ))}
            </select>
          </div>

          {/* Embedding Dimension */}
          <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
            <label className="block text-sm text-secondary dark:text-gray-400 mb-2">Embedding Dimension</label>
            <select
              value={configValues.embedding_dimension}
              onChange={(e) => setConfigValues({ ...configValues, embedding_dimension: Number(e.target.value) })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {FALLBACK_EMBEDDING_DIMENSIONS.map((dim) => (
                <option key={dim} value={dim}>
                  {dim}
                </option>
              ))}
            </select>
          </div>

          {/* Default Match Count */}
          <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
            <label className="block text-sm text-secondary dark:text-gray-400 mb-2">Default Match Count</label>
            <select
              value={configValues.default_match_count}
              onChange={(e) => setConfigValues({ ...configValues, default_match_count: Number(e.target.value) })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {matchCountOptions.map((count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              ))}
            </select>
          </div>
        </div>

        <p className="text-xs text-secondary dark:text-gray-500 mt-4">
          <strong>Save Runtime:</strong> Apply changes immediately (resets on restart).
          <br />
          <strong>Save to DB:</strong> Persist changes to database (loads automatically on restart).
        </p>
      </div>
    </div>
  )
}
