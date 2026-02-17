import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
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
} from '@heroicons/react/24/outline'
import { 
  ingestionApi, ingestionQueueApi, profilesApi,
  QueueStatus, ScheduledIngestionJob, IngestionStatus, LogEntry,
  Profile
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'

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
  
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null)
  const [schedules, setSchedules] = useState<ScheduledIngestionJob[]>([])
  const [ingestionStatus, setIngestionStatus] = useState<IngestionStatus | null>(null)
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [showAddToQueue, setShowAddToQueue] = useState(false)
  const [showAddSchedule, setShowAddSchedule] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  
  const eventSourceRef = useRef<EventSource | null>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  
  // Form states
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([])
  const [selectedFileTypes, setSelectedFileTypes] = useState<string[]>(['all'])
  const [incremental, setIncremental] = useState(true)
  const [scheduleFrequency, setScheduleFrequency] = useState('daily')
  const [scheduleHour, setScheduleHour] = useState(0)
  
  const fetchData = useCallback(async () => {
    try {
      const [queueRes, schedulesRes, ingestionRes, profilesRes] = await Promise.all([
        ingestionQueueApi.getQueue(),
        ingestionQueueApi.getSchedules(),
        ingestionApi.getStatus(),
        profilesApi.list()
      ])
      setQueueStatus(queueRes)
      setSchedules(schedulesRes.schedules)
      setIngestionStatus(ingestionRes)
      setProfiles(profilesRes.profiles)
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
    }
  }, [])
  
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

  const handleAddToQueue = async () => {
    if (selectedProfiles.length === 0) return
    
    try {
      const jobs = selectedProfiles.map(profile_key => ({
        profile_key,
        file_types: selectedFileTypes,
        incremental,
        priority: 0
      }))
      await ingestionQueueApi.addMultipleToQueue(jobs)
      setShowAddToQueue(false)
      setSelectedProfiles([])
      fetchData()
    } catch (err) {
      console.error('Error adding to queue:', err)
    }
  }

  const handleRemoveFromQueue = async (jobId: string) => {
    try {
      await ingestionQueueApi.removeFromQueue(jobId)
      fetchData()
    } catch (err) {
      console.error('Error removing from queue:', err)
    }
  }

  const handleCreateSchedule = async () => {
    if (selectedProfiles.length === 0) return
    
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
    }
  }

  const handleDeleteSchedule = async (scheduleId: string) => {
    if (!confirm('Delete this schedule?')) return
    try {
      await ingestionQueueApi.deleteSchedule(scheduleId)
      fetchData()
    } catch (err) {
      console.error('Error deleting schedule:', err)
    }
  }

  const handleToggleSchedule = async (scheduleId: string) => {
    try {
      await ingestionQueueApi.toggleSchedule(scheduleId)
      fetchData()
    } catch (err) {
      console.error('Error toggling schedule:', err)
    }
  }

  const handleRunScheduleNow = async (scheduleId: string) => {
    try {
      await ingestionQueueApi.runScheduleNow(scheduleId)
      fetchData()
    } catch (err) {
      console.error('Error running schedule:', err)
    }
  }

  const handlePause = async () => {
    await ingestionApi.pause()
    fetchData()
  }

  const handleResume = async () => {
    await ingestionApi.resume()
    fetchData()
  }

  const handleStop = async () => {
    if (!confirm('Stop ingestion?')) return
    await ingestionApi.stop()
    fetchData()
  }

  const formatTime = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`
    const mins = Math.floor(seconds / 60)
    const secs = Math.round(seconds % 60)
    return `${mins}m ${secs}s`
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">Ingestion Management</h2>
          <p className="text-sm text-secondary dark:text-gray-400">Queue, scheduling, and progress</p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-600"
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Current Ingestion Status */}
      {ingestionStatus && (ingestionStatus.status === 'running' || ingestionStatus.status === 'paused') && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Current Ingestion</h3>
            <div className="flex gap-2">
              {ingestionStatus.status === 'paused' ? (
                <button onClick={handleResume} className="flex items-center gap-2 rounded-xl bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700">
                  <PlayIcon className="h-4 w-4" /> Resume
                </button>
              ) : (
                <button onClick={handlePause} className="flex items-center gap-2 rounded-xl bg-amber-500 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600">
                  <PauseIcon className="h-4 w-4" /> Pause
                </button>
              )}
              <button onClick={handleStop} className="flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
                <StopIcon className="h-4 w-4" /> Stop
              </button>
            </div>
          </div>
          
          {/* Progress */}
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className={`px-3 py-1 rounded-lg font-medium ${
                ingestionStatus.status === 'running' ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300' : 'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400'
              }`}>
                {ingestionStatus.status === 'running' && <ArrowPathIcon className="h-4 w-4 animate-spin inline mr-1" />}
                {ingestionStatus.status.charAt(0).toUpperCase() + ingestionStatus.status.slice(1)}
              </span>
              <span className="text-secondary dark:text-gray-400">
                Elapsed: {formatTime(ingestionStatus.elapsed_seconds)}
                {ingestionStatus.estimated_remaining_seconds && ` • ETA: ${formatTime(ingestionStatus.estimated_remaining_seconds)}`}
              </span>
            </div>
            
            <div className="w-full h-3 bg-surface-variant dark:bg-gray-700 rounded-full">
              <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${Math.max(ingestionStatus.progress_percent, 2)}%` }} />
            </div>
            
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <p className="text-secondary dark:text-gray-400">Progress</p>
                <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.processed_files} / {ingestionStatus.total_files}</p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">Chunks Created</p>
                <p className="font-medium text-primary-900 dark:text-gray-200">{ingestionStatus.chunks_created}</p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">Failed</p>
                <p className="font-medium text-red-500">{ingestionStatus.failed_files}</p>
              </div>
              <div>
                <p className="text-secondary dark:text-gray-400">Current File</p>
                <p className="font-medium text-primary-900 dark:text-gray-200 truncate">{ingestionStatus.current_file?.split(/[\\/]/).pop() || 'N/A'}</p>
              </div>
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
              Ingestion Queue ({queueStatus?.total_queued || 0})
            </h3>
          </div>
          <button
            onClick={() => setShowAddToQueue(true)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-4 w-4" /> Add to Queue
          </button>
        </div>

        {/* Add to Queue Form */}
        {showAddToQueue && (
          <div className="mb-4 p-4 rounded-xl bg-surface-variant dark:bg-gray-700">
            <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-3">Add Profiles to Queue</h4>
            <div className="space-y-3">
              <div>
                <label className="block text-sm text-secondary dark:text-gray-400 mb-1">Select Profiles</label>
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
                <label className="block text-sm text-secondary dark:text-gray-400 mb-1">File Types</label>
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
                <input type="checkbox" checked={incremental} onChange={e => setIncremental(e.target.checked)} className="rounded" />
                <span className="text-sm text-primary-900 dark:text-gray-200">Incremental (skip existing)</span>
              </label>
              
              <div className="flex gap-2">
                <button onClick={handleAddToQueue} disabled={selectedProfiles.length === 0} className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium disabled:opacity-50">
                  Add {selectedProfiles.length} Profile(s)
                </button>
                <button onClick={() => { setShowAddToQueue(false); setSelectedProfiles([]) }} className="px-4 py-2 rounded-xl bg-gray-200 dark:bg-gray-600 text-primary-900 dark:text-gray-200 text-sm font-medium">
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Queue List */}
        {queueStatus && queueStatus.queue.length > 0 ? (
          <div className="space-y-2">
            {queueStatus.queue.map((job, i) => (
              <div key={job.id} className="flex items-center justify-between p-3 rounded-xl bg-surface-variant dark:bg-gray-700">
                <div className="flex items-center gap-3">
                  <span className="text-sm text-secondary dark:text-gray-400">#{i + 1}</span>
                  <div>
                    <p className="font-medium text-primary-900 dark:text-gray-200">{job.profile_name}</p>
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {job.file_types.join(', ')} • {job.incremental ? 'Incremental' : 'Full'}
                    </p>
                  </div>
                </div>
                <button onClick={() => handleRemoveFromQueue(job.id)} className="p-2 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg">
                  <TrashIcon className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-secondary dark:text-gray-400 text-sm">No jobs in queue</p>
        )}
      </div>

      {/* Scheduled Jobs */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <CalendarIcon className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">
              Scheduled Jobs ({schedules.length})
            </h3>
          </div>
          <button
            onClick={() => setShowAddSchedule(true)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-4 w-4" /> New Schedule
          </button>
        </div>

        {/* Add Schedule Form */}
        {showAddSchedule && (
          <div className="mb-4 p-4 rounded-xl bg-surface-variant dark:bg-gray-700">
            <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-3">Create Schedule</h4>
            <div className="space-y-3">
              <div>
                <label className="block text-sm text-secondary dark:text-gray-400 mb-1">Select Profiles</label>
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
                  <label className="block text-sm text-secondary dark:text-gray-400 mb-1">Frequency</label>
                  <select value={scheduleFrequency} onChange={e => setScheduleFrequency(e.target.value)} className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm">
                    {FREQUENCY_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm text-secondary dark:text-gray-400 mb-1">Hour (0-23)</label>
                  <input type="number" min={0} max={23} value={scheduleHour} onChange={e => setScheduleHour(parseInt(e.target.value))} className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm" />
                </div>
              </div>
              
              <div className="flex gap-2">
                <button onClick={handleCreateSchedule} disabled={selectedProfiles.length === 0} className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium disabled:opacity-50">
                  Create Schedule
                </button>
                <button onClick={() => { setShowAddSchedule(false); setSelectedProfiles([]) }} className="px-4 py-2 rounded-xl bg-gray-200 dark:bg-gray-600 text-primary-900 dark:text-gray-200 text-sm font-medium">
                  Cancel
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
                        Next: {new Date(schedule.next_run).toLocaleString()}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => handleRunScheduleNow(schedule.id)} className="p-2 text-primary hover:bg-primary-100 dark:hover:bg-primary-900/30 rounded-lg" title="Run Now">
                    <PlayIcon className="h-4 w-4" />
                  </button>
                  <button onClick={() => handleToggleSchedule(schedule.id)} className="p-2 text-amber-500 hover:bg-amber-100 dark:hover:bg-amber-900/30 rounded-lg" title="Toggle">
                    {schedule.enabled ? <PauseIcon className="h-4 w-4" /> : <PlayIcon className="h-4 w-4" />}
                  </button>
                  <button onClick={() => handleDeleteSchedule(schedule.id)} className="p-2 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg" title="Delete">
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-secondary dark:text-gray-400 text-sm">No scheduled jobs</p>
        )}
      </div>

      {/* Logs */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Logs ({logs.length})</h3>
          <div className="flex gap-2">
            {!isStreaming ? (
              <button onClick={startLogStreaming} className="flex items-center gap-1 rounded-xl bg-green-100 dark:bg-green-900/30 px-3 py-1.5 text-sm font-medium text-green-700 dark:text-green-400">
                <SignalIcon className="h-4 w-4" /> Stream
              </button>
            ) : (
              <button onClick={stopLogStreaming} className="flex items-center gap-1 rounded-xl bg-red-100 dark:bg-red-900/30 px-3 py-1.5 text-sm font-medium text-red-600 dark:text-red-400">
                <StopIcon className="h-4 w-4" /> Stop
              </button>
            )}
            <button onClick={() => setShowLogs(!showLogs)} className="rounded-xl bg-surface-variant dark:bg-gray-700 px-3 py-1.5 text-sm font-medium">
              {showLogs ? 'Collapse' : 'Expand'}
            </button>
          </div>
        </div>
        {showLogs && (
          <div className="bg-gray-900 rounded-xl p-4 font-mono text-xs max-h-64 overflow-auto">
            {logs.length === 0 ? (
              <p className="text-gray-500">No logs. Start streaming to see live logs.</p>
            ) : (
              logs.slice(-100).map((log, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-gray-500">{new Date(log.timestamp).toLocaleTimeString()}</span>
                  <span className={log.level === 'ERROR' ? 'text-red-400' : log.level === 'WARNING' ? 'text-yellow-400' : 'text-blue-400'}>
                    [{log.level}]
                  </span>
                  <span className="text-gray-300">{log.message}</span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
