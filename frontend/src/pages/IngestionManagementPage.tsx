import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  PlayIcon,
  PauseIcon,
  StopIcon,
  ArrowPathIcon,
  QueueListIcon,
  CalendarIcon,
  PlusIcon,
  TrashIcon,
  DocumentTextIcon,
  PhotoIcon,
  MusicalNoteIcon,
  VideoCameraIcon,
  SignalIcon,
  CogIcon,
  MagnifyingGlassIcon,
  FunnelIcon,
  CheckCircleIcon,
  ExclamationCircleIcon,
  ClipboardIcon,
  XMarkIcon,
  DocumentPlusIcon,
  InformationCircleIcon,
  ChartBarIcon,
  ExclamationTriangleIcon,
  ClockIcon,
} from '@heroicons/react/24/outline'
import {
  ingestionApi, ingestionQueueApi, profilesApi, fileRegistryApi,
  QueueStatus, ScheduledIngestionJob, IngestionStatus, LogEntry,
  Profile, IngestionStreamEvent, FileRegistryStats, IngestEstimate,
  IngestionEta, IngestionEtaExtension
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { formatDuration, formatPercent } from '../utils/format'

// ---- Pre-ingestion estimate helpers ----

function formatEstCost(usd: number): string {
  if (!usd) return '$0'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

interface AggregatedEstimate {
  fileCount: number
  sizeGb: number
  timeSeconds: number
  costUsd: number
  tokens: number
  chunks: number
  skipped: number
  isRough: boolean
  tokenMethod: string
  benchmarkDate: string | null
  missingFolders: boolean
  assumptions: string[]
}

function aggregateEstimates(results: IngestEstimate[]): AggregatedEstimate {
  const agg: AggregatedEstimate = {
    fileCount: 0, sizeGb: 0, timeSeconds: 0, costUsd: 0, tokens: 0, chunks: 0,
    skipped: 0, isRough: false, tokenMethod: '', benchmarkDate: null,
    missingFolders: false, assumptions: [],
  }
  for (const r of results) {
    agg.fileCount += r.file_count
    agg.sizeGb += r.total_size_gb
    agg.timeSeconds += r.estimated_time_seconds
    agg.costUsd += r.estimated_cost_usd
    agg.tokens += r.estimated_tokens
    agg.chunks += r.estimated_chunks
    agg.skipped += r.skipped_existing
    agg.isRough = agg.isRough || r.is_rough
    agg.tokenMethod = agg.tokenMethod || r.token_method
    agg.benchmarkDate = agg.benchmarkDate || r.benchmark_date || null
    agg.missingFolders = agg.missingFolders || r.missing_folders.length > 0
    for (const note of r.assumptions) {
      if (!agg.assumptions.includes(note)) agg.assumptions.push(note)
    }
  }
  return agg
}

function IngestEstimateLine({ profileKeys, incremental }: { profileKeys: string[]; incremental: boolean }) {
  const { t } = useTranslation()
  const [estimate, setEstimate] = useState<AggregatedEstimate | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const keyStr = profileKeys.join(',')

  useEffect(() => {
    if (profileKeys.length === 0) {
      setEstimate(null)
      setError(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(false)
    const handle = setTimeout(() => {
      Promise.all(profileKeys.map(k => ingestionApi.estimate(k, incremental)))
        .then(results => {
          if (cancelled) return
          setEstimate(aggregateEstimates(results))
        })
        .catch(() => {
          if (cancelled) return
          setError(true)
          setEstimate(null)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyStr, incremental])

  if (profileKeys.length === 0) {
    return <p className="text-xs text-secondary dark:text-gray-400">{t('ingestion.estimate.selectProfile')}</p>
  }

  return (
    <div className="rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-900/40 px-3 py-2">
      <div className="flex items-center gap-2 flex-wrap">
        <ClockIcon className="h-4 w-4 text-blue-600 dark:text-blue-300 shrink-0" />
        {loading && (
          <span className="text-xs text-blue-700 dark:text-blue-300">{t('ingestion.estimate.loading')}</span>
        )}
        {!loading && error && (
          <span className="text-xs text-blue-700 dark:text-blue-300">{t('ingestion.estimate.error')}</span>
        )}
        {!loading && !error && estimate && (
          <>
            <span className="text-xs font-medium text-blue-900 dark:text-blue-200">
              {t('ingestion.estimate.summary', {
                files: estimate.fileCount.toLocaleString(),
                size: estimate.sizeGb.toFixed(2),
                time: formatDuration(estimate.timeSeconds, true),
                cost: formatEstCost(estimate.costUsd),
              })}
            </span>
            {estimate.isRough && (
              <span className="text-[10px] uppercase tracking-wide bg-blue-200 dark:bg-blue-800 text-blue-800 dark:text-blue-200 px-1.5 py-0.5 rounded-sm">
                {t('ingestion.estimate.rough')}
              </span>
            )}
          </>
        )}
      </div>
      {!loading && !error && estimate && (
        <div
          className="mt-1 text-[11px] text-blue-700/80 dark:text-blue-300/80"
          title={estimate.assumptions.join('\n')}
        >
          {t('ingestion.estimate.tokensChunks', {
            tokens: estimate.tokens.toLocaleString(),
            chunks: estimate.chunks.toLocaleString(),
            method: estimate.tokenMethod,
          })}
          {estimate.skipped > 0 && <> · {t('ingestion.estimate.skipped', { skipped: estimate.skipped.toLocaleString() })}</>}
          {' · '}
          {estimate.benchmarkDate
            ? t('ingestion.estimate.benchmark', { date: new Date(estimate.benchmarkDate).toLocaleDateString() })
            : t('ingestion.estimate.noBenchmark')}
          {estimate.missingFolders && <> · {t('ingestion.estimate.missingFolders')}</>}
        </div>
      )}
    </div>
  )
}

// Phase configuration
const PHASES = [
  { key: 'initializing', label: 'Initializing', icon: CogIcon },
  { key: 'cleaning', label: 'Cleaning', icon: TrashIcon },
  { key: 'discovering', label: 'Discovering', icon: MagnifyingGlassIcon },
  { key: 'filtering', label: 'Filtering', icon: FunnelIcon },
  { key: 'processing', label: 'Processing', icon: DocumentTextIcon },
  { key: 'finalizing', label: 'Finalizing', icon: ArrowPathIcon },
  { key: 'completed', label: 'Complete', icon: CheckCircleIcon },
]

// Status explanations for novice users
const STATUS_HELP: Record<string, string> = {
  running: 'Documents are being processed. You can pause or stop anytime.',
  paused: 'Processing is paused. Resume to continue where you left off.',
  completed: 'All documents have been processed successfully.',
  failed: 'Processing encountered an error. Check the logs for details.',
  stopped: 'Processing was stopped by user.',
  pending: 'Waiting to start processing.',
}

const FILE_TYPE_OPTIONS = [
  { value: 'all', label: 'All Files', icon: DocumentTextIcon },
  { value: 'documents', label: 'Documents', icon: DocumentTextIcon },
  { value: 'images', label: 'Images', icon: PhotoIcon },
  { value: 'audio', label: 'Audio', icon: MusicalNoteIcon },
  { value: 'video', label: 'Video', icon: VideoCameraIcon },
]

const FREQUENCY_OPTIONS = [
  { value: 'hourly', label: 'Hourly' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
]

export default function IngestionManagementPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  const { t } = useTranslation()
  
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null)
  const [schedules, setSchedules] = useState<ScheduledIngestionJob[]>([])
  const [ingestionStatus, setIngestionStatus] = useState<IngestionStatus | null>(null)
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [registryStats, setRegistryStats] = useState<FileRegistryStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [showAddToQueue, setShowAddToQueue] = useState(false)
  const [showAddSchedule, setShowAddSchedule] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  
  const eventSourceRef = useRef<EventSource | null>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const eventsSourceRef = useRef<EventSource | null>(null)
  
  // Form states
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([])
  const [selectedFileTypes, setSelectedFileTypes] = useState<string[]>(['all'])
  const [incremental, setIncremental] = useState(true)
  const [scheduleFrequency, setScheduleFrequency] = useState('daily')
  const [scheduleHour, setScheduleHour] = useState(0)
  
  // Selective ingestion filter states
  const [retryImageOnlyPdfs, setRetryImageOnlyPdfs] = useState(false)
  const [retryTimeouts, setRetryTimeouts] = useState(false)
  const [retryErrors, setRetryErrors] = useState(false)
  const [retryNoChunks, setRetryNoChunks] = useState(false)
  const [skipImageOnlyPdfs, setSkipImageOnlyPdfs] = useState(false)
  
  // Safeguards - operation locking and cooldown
  const [isOperationPending, setIsOperationPending] = useState(false)
  const [actionCooldown, setActionCooldown] = useState(false)
  const COOLDOWN_MS = 2000
  
  // Streaming state
  const [streamEvent, setStreamEvent] = useState<IngestionStreamEvent | null>(null)
  const [isEventsStreaming, setIsEventsStreaming] = useState(false)
  
  // Confirmation modal state
  const [confirmModal, setConfirmModal] = useState<{
    open: boolean
    title: string
    message: string
    onConfirm: () => void
    destructive?: boolean
  }>({ open: false, title: '', message: '', onConfirm: () => {} })
  
  const fetchData = useCallback(async () => {
    try {
      const [queueRes, schedulesRes, ingestionRes, profilesRes, registryRes] = await Promise.all([
        ingestionQueueApi.getQueue(),
        ingestionQueueApi.getSchedules(),
        ingestionApi.getStatus(),
        profilesApi.list(),
        fileRegistryApi.getStats().catch(() => null)
      ])
      setQueueStatus(queueRes)
      setSchedules(schedulesRes.schedules)
      setIngestionStatus(ingestionRes)
      setProfiles(profilesRes.profiles)
      if (registryRes) setRegistryStats(registryRes)
    } catch (err) {
      console.error('Error fetching data:', err)
    } finally {
      setIsLoading(false)
    }
  }, [])
  
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/dashboard')
    }
  }, [user, authLoading, navigate])
  
  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      fetchData()
    }
  }, [authLoading, user, fetchData])
  
  // Poll for status updates when ingestion is running
  useEffect(() => {
    if (ingestionStatus?.status === 'running' || ingestionStatus?.status === 'paused') {
      if (!pollIntervalRef.current) {
        pollIntervalRef.current = setInterval(async () => {
          try {
            const [status, queue] = await Promise.all([
              ingestionApi.getStatus(),
              ingestionQueueApi.getQueue()
            ])
            setIngestionStatus(status)
            setQueueStatus(queue)
          } catch (e) { console.error(e) }
        }, 2000)
      }
    } else {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    }
  }, [ingestionStatus?.status])
  
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) eventSourceRef.current.close()
      if (eventsSourceRef.current) eventsSourceRef.current.close()
    }
  }, [])
  
  // Auto-expand logs and start streaming when ingestion starts running
  useEffect(() => {
    if (ingestionStatus?.status === 'running' && !showLogs) {
      setShowLogs(true)
      if (!isStreaming) {
        startLogStreaming()
      }
    }
  }, [ingestionStatus?.status])
  
  // Start events streaming when ingestion is running
  useEffect(() => {
    if ((ingestionStatus?.status === 'running' || ingestionStatus?.status === 'paused') && !isEventsStreaming) {
      startEventsStreaming()
    } else if (ingestionStatus?.status !== 'running' && ingestionStatus?.status !== 'paused' && isEventsStreaming) {
      stopEventsStreaming()
    }
  }, [ingestionStatus?.status])
  
  if (authLoading || isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  
  if (!user?.is_admin) return null

  const startLogStreaming = () => {
    if (eventSourceRef.current) eventSourceRef.current.close()
    const url = ingestionApi.getLogsStreamUrl()
    const token = localStorage.getItem('auth_token')
    const eventSource = new EventSource(`${url}${token ? `?token=${token}` : ''}`)
    eventSourceRef.current = eventSource
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type !== 'status') {
          setLogs(prev => [...prev.slice(-500), data])
        }
      } catch {}
    }
    eventSource.onerror = () => { eventSource.close(); setIsStreaming(false) }
    setIsStreaming(true)
  }

  const stopLogStreaming = () => {
    if (eventSourceRef.current) eventSourceRef.current.close()
    setIsStreaming(false)
  }

  const startEventsStreaming = () => {
    if (eventsSourceRef.current) eventsSourceRef.current.close()
    const url = ingestionApi.getEventsStreamUrl()
    const token = localStorage.getItem('auth_token')
    const eventSource = new EventSource(`${url}${token ? `?token=${token}` : ''}`)
    eventsSourceRef.current = eventSource
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as IngestionStreamEvent
        setStreamEvent(data)
      } catch {}
    }
    eventSource.onerror = () => { eventSource.close(); setIsEventsStreaming(false) }
    setIsEventsStreaming(true)
  }

  const stopEventsStreaming = () => {
    if (eventsSourceRef.current) eventsSourceRef.current.close()
    setIsEventsStreaming(false)
    setStreamEvent(null)
  }

  const handleAddToQueue = async () => {
    if (selectedProfiles.length === 0 || isOperationPending || actionCooldown) return
    
    // Check for duplicates in queue
    const existingProfiles = queueStatus?.queue.map(j => j.profile_key) || []
    const duplicates = selectedProfiles.filter(p => existingProfiles.includes(p))
    
    if (duplicates.length > 0) {
      const profileNames = duplicates.map(k => profiles[k]?.name || k).join(', ')
      setConfirmModal({
        open: true,
        title: 'Duplicate Jobs',
        message: `${profileNames} ${duplicates.length === 1 ? 'is' : 'are'} already in the queue. Add anyway?`,
        onConfirm: async () => {
          setConfirmModal(prev => ({ ...prev, open: false }))
          await addToQueueInternal()
        }
      })
      return
    }
    
    await addToQueueInternal()
  }
  
  const addToQueueInternal = async () => {
    setIsOperationPending(true)
    setActionCooldown(true)
    
    try {
      const jobs = selectedProfiles.map(profile_key => ({
        profile_key,
        file_types: selectedFileTypes,
        incremental,
        priority: 0,
        // Selective ingestion filters
        retry_image_only_pdfs: retryImageOnlyPdfs,
        retry_timeouts: retryTimeouts,
        retry_errors: retryErrors,
        retry_no_chunks: retryNoChunks,
        skip_image_only_pdfs: skipImageOnlyPdfs
      }))
      await ingestionQueueApi.addMultipleToQueue(jobs)
      setShowAddToQueue(false)
      setSelectedProfiles([])
      // Reset filter states
      setRetryImageOnlyPdfs(false)
      setRetryTimeouts(false)
      setRetryErrors(false)
      setRetryNoChunks(false)
      setSkipImageOnlyPdfs(false)
      fetchData()
    } catch (err) {
      console.error('Error adding to queue:', err)
    } finally {
      setIsOperationPending(false)
      setTimeout(() => setActionCooldown(false), COOLDOWN_MS)
    }
  }

  const handleRemoveFromQueue = async (jobId: string) => {
    if (isOperationPending) return
    setIsOperationPending(true)
    try {
      await ingestionQueueApi.removeFromQueue(jobId)
      fetchData()
    } catch (err) {
      console.error('Error removing from queue:', err)
    } finally {
      setIsOperationPending(false)
    }
  }

  const handleCreateSchedule = async () => {
    if (selectedProfiles.length === 0 || isOperationPending || actionCooldown) return
    
    setIsOperationPending(true)
    setActionCooldown(true)
    
    try {
      for (const profile_key of selectedProfiles) {
        await ingestionQueueApi.createSchedule({
          profile_key,
          file_types: selectedFileTypes,
          incremental,
          frequency: scheduleFrequency,
          hour: scheduleHour
        })
      }
      setShowAddSchedule(false)
      setSelectedProfiles([])
      fetchData()
    } catch (err) {
      console.error('Error creating schedule:', err)
    } finally {
      setIsOperationPending(false)
      setTimeout(() => setActionCooldown(false), COOLDOWN_MS)
    }
  }

  const handleDeleteSchedule = async (scheduleId: string) => {
    setConfirmModal({
      open: true,
      title: 'Delete Schedule?',
      message: 'This will permanently delete this scheduled ingestion job.',
      destructive: true,
      onConfirm: async () => {
        setConfirmModal(prev => ({ ...prev, open: false }))
        if (isOperationPending) return
        setIsOperationPending(true)
        try {
          await ingestionQueueApi.deleteSchedule(scheduleId)
          fetchData()
        } catch (err) {
          console.error('Error deleting schedule:', err)
        } finally {
          setIsOperationPending(false)
        }
      }
    })
  }

  const handleToggleSchedule = async (scheduleId: string) => {
    if (isOperationPending) return
    setIsOperationPending(true)
    try {
      await ingestionQueueApi.toggleSchedule(scheduleId)
      fetchData()
    } catch (err) {
      console.error('Error toggling schedule:', err)
    } finally {
      setIsOperationPending(false)
    }
  }

  const handleRunScheduleNow = async (scheduleId: string) => {
    if (isOperationPending || actionCooldown) return
    setIsOperationPending(true)
    setActionCooldown(true)
    try {
      await ingestionQueueApi.runScheduleNow(scheduleId)
      fetchData()
    } catch (err) {
      console.error('Error running schedule:', err)
    } finally {
      setIsOperationPending(false)
      setTimeout(() => setActionCooldown(false), COOLDOWN_MS)
    }
  }

  const handlePause = async () => {
    if (isOperationPending) return
    setIsOperationPending(true)
    try {
      await ingestionApi.pause()
      fetchData()
    } catch (err) {
      console.error('Error pausing:', err)
    } finally {
      setIsOperationPending(false)
    }
  }

  const handleResume = async () => {
    if (isOperationPending) return
    setIsOperationPending(true)
    try {
      await ingestionApi.resume()
      fetchData()
    } catch (err) {
      console.error('Error resuming:', err)
    } finally {
      setIsOperationPending(false)
    }
  }

  const handleStop = async () => {
    setConfirmModal({
      open: true,
      title: 'Stop Ingestion?',
      message: 'This will stop the current ingestion job. Progress is saved and you can resume later with a new incremental ingestion.',
      destructive: true,
      onConfirm: async () => {
        setConfirmModal(prev => ({ ...prev, open: false }))
        if (isOperationPending) return
        setIsOperationPending(true)
        try {
          await ingestionApi.stop()
          fetchData()
        } catch (err) {
          console.error('Error stopping:', err)
        } finally {
          setIsOperationPending(false)
        }
      }
    })
  }

  const formatRate = (rate: number) => {
    if (rate < 1) return `${(rate * 60).toFixed(1)}/hour`
    return `${rate.toFixed(1)}/min`
  }

  /** The extensions with the most files left (the ETA is their sum). */
  const etaRows = (eta: IngestionEta | null | undefined): IngestionEtaExtension[] =>
    eta?.method === 'per_extension' ? (eta.by_extension ?? []).slice(0, 6) : []

  const etaBreakdownText = (eta: IngestionEta | null | undefined): string | null => {
    if (!eta || !eta.by_extension?.length) return null
    const lines = eta.by_extension.map(row =>
      `${row.extension || '(none)'}: ${row.remaining.toLocaleString()} left` +
      (row.files_per_minute != null ? ` at ${row.files_per_minute.toFixed(2)}/min (${row.pace_source})` : ' (no pace yet)')
    )
    if (eta.unknown_remaining > 0) lines.push(`${eta.unknown_remaining.toLocaleString()} files of unknown pace are not in the ETA`)
    return lines.join('\n')
  }

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch (err) {
      console.error('Failed to copy:', err)
    }
  }

  const copyAllLogs = async () => {
    const logText = logs.map(l => `[${l.timestamp}] [${l.level}] ${l.message}`).join('\n')
    await copyToClipboard(logText)
  }

  // Get current phase index for stepper
  const getCurrentPhaseIndex = () => {
    const phase = streamEvent?.phase || ingestionStatus?.phase || 'initializing'
    const idx = PHASES.findIndex(p => p.key === phase)
    return idx >= 0 ? idx : 0
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">{t('ingestion.title')}</h2>
          <p className="text-sm text-secondary dark:text-gray-400">{t('ingestion.subtitle')}</p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-600"
        >
          <ArrowPathIcon className="h-4 w-4" />
          {t('common.refresh')}
        </button>
      </div>

      {/* Sub-navigation */}
      <div className="flex gap-2 border-b border-gray-200 dark:border-gray-700 pb-2">
        <Link
          to={`/${localStorage.getItem('i18nextLng') || 'en'}/system/ingestion`}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg bg-primary text-white"
        >
          <CogIcon className="h-4 w-4" />
          Management
        </Link>
        <Link
          to={`/${localStorage.getItem('i18nextLng') || 'en'}/system/ingestion/failed`}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg text-secondary hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <ExclamationTriangleIcon className="h-4 w-4" />
          Failed Documents
        </Link>
        <Link
          to={`/${localStorage.getItem('i18nextLng') || 'en'}/system/ingestion/analytics`}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg text-secondary hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <ChartBarIcon className="h-4 w-4" />
          Analytics
        </Link>
        <Link
          to={`/${localStorage.getItem('i18nextLng') || 'en'}/system/ingestion/history`}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg text-secondary hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <ClockIcon className="h-4 w-4" />
          History
        </Link>
      </div>

      {/* Current Ingestion Status */}
      {ingestionStatus && (ingestionStatus.status === 'running' || ingestionStatus.status === 'paused') && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">{t('ingestion.currentIngestion')}</h3>
              <div className="group relative">
                <InformationCircleIcon className="h-4 w-4 text-gray-400 cursor-help" />
                <div className="absolute left-0 top-6 z-10 hidden group-hover:block w-64 p-2 text-xs bg-gray-900 text-white rounded-lg shadow-lg">
                  {STATUS_HELP[ingestionStatus.status] || 'Processing documents...'}
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              {ingestionStatus.status === 'paused' ? (
                <button 
                  onClick={handleResume} 
                  disabled={isOperationPending || !ingestionStatus.is_paused}
                  className="flex items-center gap-2 rounded-xl bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <PlayIcon className="h-4 w-4" /> {t('ingestion.resume')}
                </button>
              ) : (
                <button 
                  onClick={handlePause} 
                  disabled={isOperationPending || !ingestionStatus.can_pause}
                  className="flex items-center gap-2 rounded-xl bg-amber-500 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <PauseIcon className="h-4 w-4" /> Pause
                </button>
              )}
              <button 
                onClick={handleStop} 
                disabled={isOperationPending || !ingestionStatus.can_stop}
                className="flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <StopIcon className="h-4 w-4" /> Stop
              </button>
            </div>
          </div>
          
          {/* Phase Stepper */}
          <div className="mb-6">
            <div className="flex items-center justify-between">
              {PHASES.slice(0, -1).map((phase, idx) => {
                const currentIdx = getCurrentPhaseIndex()
                const isActive = idx === currentIdx
                const isComplete = idx < currentIdx
                const Icon = phase.icon
                
                return (
                  <div key={phase.key} className="flex-1 flex items-center">
                    <div className={`flex flex-col items-center ${idx > 0 ? 'flex-1' : ''}`}>
                      {idx > 0 && (
                        <div className={`h-0.5 w-full mb-2 ${isComplete || isActive ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'}`} />
                      )}
                      <div className={`flex items-center justify-center w-8 h-8 rounded-full ${
                        isActive ? 'bg-primary text-white ring-4 ring-primary/20' :
                        isComplete ? 'bg-green-500 text-white' :
                        'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-400'
                      }`}>
                        {isActive && ingestionStatus.status === 'running' ? (
                          <ArrowPathIcon className="h-4 w-4 animate-spin" />
                        ) : isComplete ? (
                          <CheckCircleIcon className="h-4 w-4" />
                        ) : (
                          <Icon className="h-4 w-4" />
                        )}
                      </div>
                      <span className={`text-xs mt-1 ${
                        isActive ? 'text-primary font-medium' : 
                        isComplete ? 'text-green-600 dark:text-green-400' :
                        'text-gray-500 dark:text-gray-400'
                      }`}>
                        {phase.label}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
            {/* Phase message */}
            <div className="text-center mt-2">
              <span className="text-sm text-secondary dark:text-gray-400">
                {streamEvent?.phase_message || ingestionStatus.phase_message || 'Processing...'}
              </span>
            </div>
          </div>
          
          {/* Discovery Progress (shown during discovering/filtering phases) */}
          {(streamEvent?.phase === 'discovering' || streamEvent?.phase === 'filtering' || 
            ingestionStatus.phase === 'discovering' || ingestionStatus.phase === 'filtering') && (
            <div className="mb-4 p-3 bg-primary-50 dark:bg-primary-900/20 rounded-xl">
              <div className="flex items-center gap-3">
                <MagnifyingGlassIcon className="h-5 w-5 text-primary animate-pulse" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-primary-900 dark:text-gray-200">
                    {streamEvent?.discovery_progress?.current_folder?.split(/[\\/]/).pop() || 
                     ingestionStatus.discovery_progress?.current_folder?.split(/[\\/]/).pop() || 'Scanning...'}
                  </p>
                  <p className="text-xs text-secondary dark:text-gray-400">
                    Found {streamEvent?.discovery_progress?.files_found || ingestionStatus.discovery_progress?.files_found || 0} files
                    {(streamEvent?.discovery_progress?.files_skipped || ingestionStatus.discovery_progress?.files_skipped) ? 
                      ` • ${streamEvent?.discovery_progress?.files_skipped || ingestionStatus.discovery_progress?.files_skipped} already processed` : ''}
                  </p>
                </div>
              </div>
            </div>
          )}
          
          {/* Progress Bar */}
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className={`px-3 py-1 rounded-lg font-medium ${
                ingestionStatus.status === 'running' ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300' : 'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400'
              }`}>
                {ingestionStatus.status === 'running' && <ArrowPathIcon className="h-4 w-4 animate-spin inline mr-1" />}
                {ingestionStatus.status.charAt(0).toUpperCase() + ingestionStatus.status.slice(1)}
              </span>
              <span
                className="text-secondary dark:text-gray-400"
                title={etaBreakdownText(streamEvent?.metrics?.eta || ingestionStatus.eta) || undefined}
              >
                Elapsed: {formatDuration(streamEvent?.metrics?.elapsed_seconds || ingestionStatus.elapsed_seconds)}
                {(streamEvent?.metrics?.estimated_remaining_seconds || ingestionStatus.estimated_remaining_seconds) &&
                  ` • ETA: ${formatDuration(streamEvent?.metrics?.estimated_remaining_seconds || ingestionStatus.estimated_remaining_seconds || 0)}`}
                {ingestionStatus.runner === 'worker' && ' • worker'}
              </span>
            </div>
            {etaRows(streamEvent?.metrics?.eta || ingestionStatus.eta).length > 0 && (
              <p className="text-xs text-secondary dark:text-gray-500" data-testid="eta-breakdown">
                {t('ingestion.etaByExtension')}:{' '}
                {etaRows(streamEvent?.metrics?.eta || ingestionStatus.eta)
                  .map(row => `${row.extension || '(none)'} ${row.remaining.toLocaleString()} left` +
                    (row.seconds != null ? ` ≈ ${formatDuration(row.seconds)}` : ' (no pace yet)'))
                  .join(' · ')}
              </p>
            )}
            
            <div className="w-full h-3 bg-surface-variant dark:bg-gray-700 rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full transition-all duration-300" style={{ width: `${Math.max(ingestionStatus.progress_percent, 2)}%` }} />
            </div>
            
            {/* Enhanced Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
              <div>
                <p className="text-secondary dark:text-gray-400">{t('ingestion.progress')}</p>
                <p className="font-medium text-primary-900 dark:text-gray-200">
                  {streamEvent?.progress?.processed_files || ingestionStatus.processed_files} / {streamEvent?.progress?.total_files || ingestionStatus.total_files}
                  {' '}({formatPercent(streamEvent?.progress?.progress_percent || ingestionStatus.progress_percent)})
                </p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">{t('ingestion.chunksCreated')}</p>
                <p className="font-medium text-primary-900 dark:text-gray-200">
                  {streamEvent?.progress?.chunks_created || ingestionStatus.chunks_created}
                </p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">{t('ingestion.failed')}</p>
                <p className="font-medium text-red-500">
                  {streamEvent?.progress?.failed_files || ingestionStatus.failed_files}
                </p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">Rate</p>
                <p className="font-medium text-primary-900 dark:text-gray-200">
                  {formatRate(streamEvent?.metrics?.processing_rate || ingestionStatus.processing_rate || 0)}
                </p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">{t('ingestion.currentFile')}</p>
                <p className="font-medium text-primary-900 dark:text-gray-200 truncate" title={ingestionStatus.current_file || ''}>
                  {(streamEvent?.progress?.current_file || ingestionStatus.current_file)?.split(/[\\/]/).pop() || 'N/A'}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* File Registry Stats */}
      {registryStats && registryStats.total_files > 0 && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-4">
            <ChartBarIcon className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">
              {t('ingestion.filters.title', 'File Registry')} ({registryStats.total_files} files)
            </h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {/* Normal */}
            <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-900/20">
              <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
                {registryStats.by_classification?.normal || 0}
              </div>
              <div className="text-xs text-emerald-700 dark:text-emerald-300">Normal</div>
            </div>
            {/* Image-Only PDFs */}
            <div className="p-3 rounded-xl bg-amber-50 dark:bg-amber-900/20">
              <div className="text-2xl font-bold text-amber-600 dark:text-amber-400">
                {registryStats.by_classification?.image_only_pdf || 0}
              </div>
              <div className="text-xs text-amber-700 dark:text-amber-300">Image PDFs</div>
            </div>
            {/* Timeouts */}
            <div className="p-3 rounded-xl bg-orange-50 dark:bg-orange-900/20">
              <div className="text-2xl font-bold text-orange-600 dark:text-orange-400">
                {registryStats.by_classification?.timeout || 0}
              </div>
              <div className="text-xs text-orange-700 dark:text-orange-300">Timeouts</div>
            </div>
            {/* No Chunks */}
            <div className="p-3 rounded-xl bg-purple-50 dark:bg-purple-900/20">
              <div className="text-2xl font-bold text-purple-600 dark:text-purple-400">
                {registryStats.by_classification?.no_chunks || 0}
              </div>
              <div className="text-xs text-purple-700 dark:text-purple-300">No Chunks</div>
            </div>
            {/* Errors */}
            <div className="p-3 rounded-xl bg-red-50 dark:bg-red-900/20">
              <div className="text-2xl font-bold text-red-600 dark:text-red-400">
                {registryStats.by_classification?.error || 0}
              </div>
              <div className="text-xs text-red-700 dark:text-red-300">Errors</div>
            </div>
            {/* Pending */}
            <div className="p-3 rounded-xl bg-gray-50 dark:bg-gray-700">
              <div className="text-2xl font-bold text-gray-600 dark:text-gray-400">
                {registryStats.by_classification?.pending || 0}
              </div>
              <div className="text-xs text-gray-700 dark:text-gray-300">Pending</div>
            </div>
          </div>
        </div>
      )}

      {/* Queue */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <QueueListIcon className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">
              {t('ingestion.queue')} ({queueStatus?.total_queued || 0})
            </h3>
          </div>
          <button
            onClick={() => setShowAddToQueue(true)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-4 w-4" /> {t('ingestion.addToQueue')}
          </button>
        </div>

        {/* Add to Queue Form */}
        {showAddToQueue && (
          <div className="mb-4 p-4 rounded-xl bg-surface-variant dark:bg-gray-700">
            <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-3">{t('ingestion.addProfilesToQueue')}</h4>
            <div className="space-y-3">
              <div>
                <label className="block text-sm text-secondary dark:text-gray-400 mb-1">{t('ingestion.selectProfiles')}</label>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(profiles).map(([key, profile]) => (
                    <button
                      key={key}
                      onClick={() => setSelectedProfiles(prev => prev.includes(key) ? prev.filter(p => p !== key) : [...prev, key])}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                        selectedProfiles.includes(key)
                          ? 'bg-primary text-white'
                          : 'bg-white dark:bg-gray-600 text-primary-900 dark:text-gray-200'
                      }`}
                    >
                      {profile.name}
                    </button>
                  ))}
                </div>
              </div>
              
              <div>
                <label className="block text-sm text-secondary dark:text-gray-400 mb-1">{t('ingestion.fileTypes')}</label>
                <div className="flex flex-wrap gap-2">
                  {FILE_TYPE_OPTIONS.map(opt => (
                    <button
                      key={opt.value}
                      onClick={() => {
                        if (opt.value === 'all') {
                          setSelectedFileTypes(['all'])
                        } else {
                          setSelectedFileTypes(prev => {
                            const without = prev.filter(t => t !== 'all' && t !== opt.value)
                            return prev.includes(opt.value) ? without : [...without, opt.value]
                          })
                        }
                      }}
                      className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                        selectedFileTypes.includes(opt.value)
                          ? 'bg-primary text-white'
                          : 'bg-white dark:bg-gray-600 text-primary-900 dark:text-gray-200'
                      }`}
                    >
                      <opt.icon className="h-4 w-4" />
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
              
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={incremental} onChange={e => setIncremental(e.target.checked)} className="rounded-sm" />
                <span className="text-sm text-primary-900 dark:text-gray-200">{t('ingestion.incremental')}</span>
              </label>
              
              {/* Selective Ingestion Filters */}
              <div className="border-t border-gray-200 dark:border-gray-600 pt-3 mt-3">
                <div className="flex items-center gap-2 mb-2">
                  <FunnelIcon className="h-4 w-4 text-secondary" />
                  <span className="text-sm font-medium text-primary-900 dark:text-gray-200">{t('ingestion.filters.title', 'Selective Filters')}</span>
                </div>
                
                {/* Info message when retry filters are active without incremental */}
                {!incremental && (retryImageOnlyPdfs || retryTimeouts || retryErrors || retryNoChunks) && (
                  <div className="mb-2 p-2 rounded-lg bg-blue-50 dark:bg-blue-900/30 text-xs text-blue-700 dark:text-blue-300 flex items-start gap-2">
                    <InformationCircleIcon className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>{t('ingestion.filters.retryModeInfo', 'Retry mode: Only files matching the selected retry filters will be processed.')}</span>
                  </div>
                )}
                
                {/* Retry Filters */}
                <div className="mb-2">
                  <span className="text-xs text-secondary dark:text-gray-400 mb-1 block">{t('ingestion.filters.retryGroup', 'Retry Specific File Types')}</span>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="flex items-center gap-2">
                      <input 
                        type="checkbox" 
                        checked={retryImageOnlyPdfs} 
                        onChange={e => setRetryImageOnlyPdfs(e.target.checked)} 
                        className="rounded-sm text-amber-500" 
                      />
                      <span className="text-xs text-primary-900 dark:text-gray-200">{t('ingestion.filters.retryImagePdfs', 'Retry Image-Only PDFs')}</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input 
                        type="checkbox" 
                        checked={retryTimeouts} 
                        onChange={e => setRetryTimeouts(e.target.checked)} 
                        className="rounded-sm text-amber-500" 
                      />
                      <span className="text-xs text-primary-900 dark:text-gray-200">{t('ingestion.filters.retryTimeouts', 'Retry Timeouts')}</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input 
                        type="checkbox" 
                        checked={retryErrors} 
                        onChange={e => setRetryErrors(e.target.checked)} 
                        className="rounded-sm text-amber-500" 
                      />
                      <span className="text-xs text-primary-900 dark:text-gray-200">{t('ingestion.filters.retryErrors', 'Retry Errors')}</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input 
                        type="checkbox" 
                        checked={retryNoChunks} 
                        onChange={e => setRetryNoChunks(e.target.checked)} 
                        className="rounded-sm text-amber-500" 
                      />
                      <span className="text-xs text-primary-900 dark:text-gray-200">{t('ingestion.filters.retryNoChunks', 'Retry No-Chunks')}</span>
                    </label>
                  </div>
                </div>
                
                {/* Skip Filters */}
                <div>
                  <span className="text-xs text-secondary dark:text-gray-400 mb-1 block">{t('ingestion.filters.skipGroup', 'Skip File Types')}</span>
                  <label className="flex items-center gap-2">
                    <input 
                      type="checkbox" 
                      checked={skipImageOnlyPdfs} 
                      onChange={e => setSkipImageOnlyPdfs(e.target.checked)} 
                      className="rounded-sm text-red-500" 
                    />
                    <span className="text-xs text-primary-900 dark:text-gray-200">{t('ingestion.filters.skipImagePdfs', 'Skip Image-Only PDFs')}</span>
                  </label>
                </div>
              </div>
              
              <IngestEstimateLine profileKeys={selectedProfiles} incremental={incremental} />

              <div className="flex gap-2">
                <button
                  onClick={handleAddToQueue}
                  disabled={selectedProfiles.length === 0 || isOperationPending || actionCooldown}
                  className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {(isOperationPending || actionCooldown) && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
                  {actionCooldown ? 'Adding...' : t('ingestion.addProfile', { count: selectedProfiles.length })}
                </button>
                <button onClick={() => { setShowAddToQueue(false); setSelectedProfiles([]) }} className="px-4 py-2 rounded-xl bg-gray-200 dark:bg-gray-600 text-primary-900 dark:text-gray-200 text-sm font-medium">
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Queue List */}
        {queueStatus && queueStatus.queue.length > 0 ? (
          <div className="space-y-2">
            {queueStatus.queue.map((job, i) => {
              // Collect active filters for display
              const activeFilters: string[] = []
              if (job.retry_image_only_pdfs) activeFilters.push(t('ingestion.filters.retryImagePdfs'))
              if (job.retry_timeouts) activeFilters.push(t('ingestion.filters.retryTimeouts'))
              if (job.retry_errors) activeFilters.push(t('ingestion.filters.retryErrors'))
              if (job.retry_no_chunks) activeFilters.push(t('ingestion.filters.retryNoChunks'))
              if (job.skip_image_only_pdfs) activeFilters.push(t('ingestion.filters.skipImagePdfs'))
              
              return (
                <div key={job.id} className="flex items-center justify-between p-3 rounded-xl bg-surface-variant dark:bg-gray-700">
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-secondary dark:text-gray-400">#{i + 1}</span>
                    <div>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{job.profile_name}</p>
                      <p className="text-xs text-secondary dark:text-gray-500">
                        {job.file_types.join(', ')} • {job.incremental ? t('ingestion.incremental') : t('ingestion.fullMode')}
                      </p>
                      {activeFilters.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {activeFilters.map((filter, idx) => (
                            <span key={idx} className="px-1.5 py-0.5 rounded-sm text-[10px] bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
                              {filter}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <button 
                    onClick={() => handleRemoveFromQueue(job.id)} 
                    disabled={isOperationPending}
                    className="p-2 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              )
            })}
          </div>
        ) : !showAddToQueue && (
          <div className="text-center py-8">
            <DocumentPlusIcon className="h-12 w-12 text-gray-400 mx-auto mb-3" />
            <h4 className="font-medium text-primary-900 dark:text-gray-200">{t('ingestion.noJobsInQueue')}</h4>
            <p className="text-sm text-secondary dark:text-gray-400 mt-1 max-w-md mx-auto">
              Add documents to your knowledge base by selecting a profile and starting an ingestion job.
            </p>
            <button 
              onClick={() => setShowAddToQueue(true)} 
              className="mt-4 flex items-center gap-2 mx-auto rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
            >
              <PlusIcon className="h-4 w-4" /> Add First Job
            </button>
          </div>
        )}
      </div>

      {/* Scheduled Jobs */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <CalendarIcon className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">
              {t('ingestion.scheduledJobs')} ({schedules.length})
            </h3>
          </div>
          <button
            onClick={() => setShowAddSchedule(true)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-4 w-4" /> {t('ingestion.newSchedule')}
          </button>
        </div>

        {/* Add Schedule Form */}
        {showAddSchedule && (
          <div className="mb-4 p-4 rounded-xl bg-surface-variant dark:bg-gray-700">
            <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-3">{t('ingestion.createSchedule')}</h4>
            <div className="space-y-3">
              <div>
                <label className="block text-sm text-secondary dark:text-gray-400 mb-1">{t('ingestion.selectProfiles')}</label>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(profiles).map(([key, profile]) => (
                    <button
                      key={key}
                      onClick={() => setSelectedProfiles(prev => prev.includes(key) ? prev.filter(p => p !== key) : [...prev, key])}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium ${selectedProfiles.includes(key) ? 'bg-primary text-white' : 'bg-white dark:bg-gray-600 text-primary-900 dark:text-gray-200'}`}
                    >
                      {profile.name}
                    </button>
                  ))}
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-secondary dark:text-gray-400 mb-1">{t('ingestion.frequency')}</label>
                  <select value={scheduleFrequency} onChange={e => setScheduleFrequency(e.target.value)} className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm">
                    {FREQUENCY_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm text-secondary dark:text-gray-400 mb-1">{t('ingestion.hour')}</label>
                  <input type="number" min={0} max={23} value={scheduleHour} onChange={e => setScheduleHour(parseInt(e.target.value))} className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm" />
                </div>
              </div>
              
              <div className="flex gap-2">
                <button onClick={handleCreateSchedule} disabled={selectedProfiles.length === 0} className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium disabled:opacity-50">
                  {t('ingestion.createSchedule')}
                </button>
                <button onClick={() => { setShowAddSchedule(false); setSelectedProfiles([]) }} className="px-4 py-2 rounded-xl bg-gray-200 dark:bg-gray-600 text-primary-900 dark:text-gray-200 text-sm font-medium">
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Schedules List */}
        {schedules.length > 0 ? (
          <div className="space-y-2">
            {schedules.map(schedule => (
              <div key={schedule.id} className="flex items-center justify-between p-3 rounded-xl bg-surface-variant dark:bg-gray-700">
                <div className="flex items-center gap-3">
                  <div className={`w-2 h-2 rounded-full ${schedule.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                  <div>
                    <p className="font-medium text-primary-900 dark:text-gray-200">{schedule.profile_name}</p>
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {schedule.frequency} at {schedule.hour}:00 • {schedule.file_types.join(', ')}
                    </p>
                    {schedule.next_run && (
                      <p className="text-xs text-secondary dark:text-gray-500">
                        {t('ingestion.nextRun')}: {new Date(schedule.next_run).toLocaleString()}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button 
                    onClick={() => handleRunScheduleNow(schedule.id)} 
                    disabled={isOperationPending || actionCooldown}
                    className="p-2 text-primary hover:bg-primary-100 dark:hover:bg-primary-900/30 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed" 
                    title={t('ingestion.runNow')}
                  >
                    <PlayIcon className="h-4 w-4" />
                  </button>
                  <button 
                    onClick={() => handleToggleSchedule(schedule.id)} 
                    disabled={isOperationPending}
                    className="p-2 text-amber-500 hover:bg-amber-100 dark:hover:bg-amber-900/30 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed" 
                    title={t('ingestion.toggle')}
                  >
                    {schedule.enabled ? <PauseIcon className="h-4 w-4" /> : <PlayIcon className="h-4 w-4" />}
                  </button>
                  <button 
                    onClick={() => handleDeleteSchedule(schedule.id)} 
                    disabled={isOperationPending}
                    className="p-2 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed" 
                    title={t('common.delete')}
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-secondary dark:text-gray-400 text-sm">{t('ingestion.noScheduledJobs')}</p>
        )}
      </div>

      {/* Logs */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">{t('ingestion.logs')} ({logs.length})</h3>
          <div className="flex gap-2">
            {logs.length > 0 && (
              <button 
                onClick={copyAllLogs} 
                className="flex items-center gap-1 rounded-xl bg-surface-variant dark:bg-gray-700 px-3 py-1.5 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-600"
                title="Copy all logs"
              >
                <ClipboardIcon className="h-4 w-4" /> Copy All
              </button>
            )}
            {!isStreaming ? (
              <button onClick={startLogStreaming} className="flex items-center gap-1 rounded-xl bg-green-100 dark:bg-green-900/30 px-3 py-1.5 text-sm font-medium text-green-700 dark:text-green-400">
                <SignalIcon className="h-4 w-4" /> {t('ingestion.streamLogs')}
              </button>
            ) : (
              <button onClick={stopLogStreaming} className="flex items-center gap-1 rounded-xl bg-red-100 dark:bg-red-900/30 px-3 py-1.5 text-sm font-medium text-red-600 dark:text-red-400">
                <StopIcon className="h-4 w-4" /> {t('ingestion.stopStream')}
              </button>
            )}
            <button onClick={() => setShowLogs(!showLogs)} className="rounded-xl bg-surface-variant dark:bg-gray-700 px-3 py-1.5 text-sm font-medium">
              {showLogs ? t('ingestion.collapse') : t('ingestion.expand')}
            </button>
          </div>
        </div>
        {showLogs && (
          <div className="bg-gray-900 rounded-xl p-4 font-mono text-xs max-h-64 overflow-auto">
            {logs.length === 0 ? (
              <p className="text-gray-500">{t('ingestion.noLogs')}</p>
            ) : (
              logs.slice(-100).map((log, i) => (
                <div key={i} className="group flex gap-2 hover:bg-gray-800 px-1 rounded-sm">
                  <span className="text-gray-500 shrink-0">{new Date(log.timestamp).toLocaleTimeString()}</span>
                  <span className={`shrink-0 ${log.level === 'ERROR' ? 'text-red-400' : log.level === 'WARNING' ? 'text-yellow-400' : 'text-blue-400'}`}>
                    [{log.level}]
                  </span>
                  <span className="text-gray-300 flex-1">{log.message}</span>
                  <button 
                    onClick={() => copyToClipboard(log.message)}
                    className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-400 hover:text-white rounded-sm shrink-0"
                    title="Copy log entry"
                  >
                    <ClipboardIcon className="h-3 w-3" />
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Confirmation Modal */}
      {confirmModal.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-md w-full mx-4 shadow-xl">
            <div className="flex items-start gap-4">
              {confirmModal.destructive ? (
                <div className="shrink-0 w-10 h-10 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
                  <ExclamationCircleIcon className="h-6 w-6 text-red-600 dark:text-red-400" />
                </div>
              ) : (
                <div className="shrink-0 w-10 h-10 rounded-full bg-primary-100 dark:bg-primary-900/30 flex items-center justify-center">
                  <InformationCircleIcon className="h-6 w-6 text-primary dark:text-primary-400" />
                </div>
              )}
              <div className="flex-1">
                <h3 className="text-lg font-semibold text-primary-900 dark:text-gray-200">{confirmModal.title}</h3>
                <p className="mt-2 text-sm text-secondary dark:text-gray-400">{confirmModal.message}</p>
              </div>
              <button 
                onClick={() => setConfirmModal(prev => ({ ...prev, open: false }))}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              >
                <XMarkIcon className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setConfirmModal(prev => ({ ...prev, open: false }))}
                className="px-4 py-2 rounded-xl bg-gray-100 dark:bg-gray-700 text-sm font-medium text-primary-900 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600"
              >
                Cancel
              </button>
              <button
                onClick={confirmModal.onConfirm}
                className={`px-4 py-2 rounded-xl text-sm font-medium text-white ${
                  confirmModal.destructive 
                    ? 'bg-red-600 hover:bg-red-700' 
                    : 'bg-primary hover:bg-primary-700'
                }`}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
