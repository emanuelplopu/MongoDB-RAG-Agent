import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import {
  ClockIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationTriangleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  TrashIcon,
  ArrowPathIcon,
  ClipboardDocumentIcon,
  FolderIcon,
  ChartBarIcon
} from '@heroicons/react/24/outline'
import { Link } from 'react-router-dom'
import { CogIcon } from '@heroicons/react/24/outline'

interface JobSummary {
  job_id: string
  profile: string | null
  status: string
  phase: string | null
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  total_files: number
  processed_files: number
  failed_files: number
  chunks_created: number
  duplicates_skipped: number
  stats_count: number
  failed_count: number
  logs_count: number
  errors: string[]
}

interface JobDetails {
  job: Record<string, unknown>
  stats_summary: {
    total_files: number
    successful: number
    failed: number
    total_processing_time_ms: number
    total_chunks: number
    total_size_bytes: number
    avg_processing_time_ms: number
  } | null
  failed_by_type: Record<string, number>
}

interface LogEntry {
  timestamp: string
  level: string
  message: string
  logger: string
}

interface FileStat {
  _id: string
  file_path: string
  file_name: string
  file_size_bytes: number
  processing_time_ms: number
  chunks_created: number
  success: boolean
  error_type: string | null
  error_message: string | null
}

interface FailedDoc {
  _id: string
  file_path: string
  file_name: string
  error_type: string
  error_message: string
  failed_at: string
}

interface JobsSummary {
  total_jobs: number
  by_status: Record<string, number>
  overall_stats: {
    total_files_processed: number
    successful_files: number
    failed_files: number
    total_chunks: number
    total_size_bytes: number
    avg_processing_time_ms: number
  } | null
}

const API_BASE = '/api/v1/ingestion'

export default function JobHistoryPage() {
  const { user, isLoading: authLoading } = useAuth()
  const navigate = useNavigate()
  const { t } = useTranslation()
  
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [summary, setSummary] = useState<JobsSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedJob, setExpandedJob] = useState<string | null>(null)
  const [jobDetails, setJobDetails] = useState<Record<string, JobDetails>>({})
  const [jobLogs, setJobLogs] = useState<Record<string, LogEntry[]>>({})
  const [jobStats, setJobStats] = useState<Record<string, FileStat[]>>({})
  const [jobFailed, setJobFailed] = useState<Record<string, FailedDoc[]>>({})
  const [activeTab, setActiveTab] = useState<Record<string, 'overview' | 'stats' | 'logs' | 'failed'>>({})
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const limit = 10

  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/dashboard')
    }
  }, [user, authLoading, navigate])

  const fetchJobs = useCallback(async () => {
    try {
      setLoading(true)
      const [jobsRes, summaryRes] = await Promise.all([
        fetch(`${API_BASE}/jobs?skip=${page * limit}&limit=${limit}`),
        fetch(`${API_BASE}/jobs/stats/summary`)
      ])
      
      if (jobsRes.ok) {
        const data = await jobsRes.json()
        setJobs(data.jobs)
        setTotal(data.total)
      }
      
      if (summaryRes.ok) {
        setSummary(await summaryRes.json())
      }
    } catch (err) {
      console.error('Error fetching jobs:', err)
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      fetchJobs()
    }
  }, [authLoading, user, fetchJobs])

  const toggleJob = async (jobId: string) => {
    if (expandedJob === jobId) {
      setExpandedJob(null)
      return
    }
    
    setExpandedJob(jobId)
    setActiveTab(prev => ({ ...prev, [jobId]: 'overview' }))
    
    // Load job details if not already loaded
    if (!jobDetails[jobId]) {
      try {
        const res = await fetch(`${API_BASE}/jobs/${jobId}`)
        if (res.ok) {
          const data = await res.json()
          setJobDetails(prev => ({ ...prev, [jobId]: data }))
        }
      } catch (err) {
        console.error('Error loading job details:', err)
      }
    }
  }

  const loadJobLogs = async (jobId: string) => {
    if (jobLogs[jobId]) return
    try {
      const res = await fetch(`${API_BASE}/jobs/${jobId}/logs`)
      if (res.ok) {
        const data = await res.json()
        setJobLogs(prev => ({ ...prev, [jobId]: data.logs }))
      }
    } catch (err) {
      console.error('Error loading logs:', err)
    }
  }

  const loadJobStats = async (jobId: string) => {
    if (jobStats[jobId]) return
    try {
      const res = await fetch(`${API_BASE}/jobs/${jobId}/stats?limit=50`)
      if (res.ok) {
        const data = await res.json()
        setJobStats(prev => ({ ...prev, [jobId]: data.stats }))
      }
    } catch (err) {
      console.error('Error loading stats:', err)
    }
  }

  const loadJobFailed = async (jobId: string) => {
    if (jobFailed[jobId]) return
    try {
      const res = await fetch(`${API_BASE}/jobs/${jobId}/failed?limit=50`)
      if (res.ok) {
        const data = await res.json()
        setJobFailed(prev => ({ ...prev, [jobId]: data.failed_documents }))
      }
    } catch (err) {
      console.error('Error loading failed docs:', err)
    }
  }

  const handleTabChange = async (jobId: string, tab: 'overview' | 'stats' | 'logs' | 'failed') => {
    setActiveTab(prev => ({ ...prev, [jobId]: tab }))
    
    if (tab === 'logs') await loadJobLogs(jobId)
    if (tab === 'stats') await loadJobStats(jobId)
    if (tab === 'failed') await loadJobFailed(jobId)
  }

  const deleteJob = async (jobId: string) => {
    if (!confirm(`Delete job ${jobId} and all related data?`)) return
    
    try {
      const res = await fetch(`${API_BASE}/jobs/${jobId}`, { method: 'DELETE' })
      if (res.ok) {
        setJobs(prev => prev.filter(j => j.job_id !== jobId))
        if (expandedJob === jobId) setExpandedJob(null)
        fetchJobs() // Refresh summary
      }
    } catch (err) {
      console.error('Error deleting job:', err)
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
  }

  const formatDuration = (seconds: number | null): string => {
    if (seconds === null) return '-'
    if (seconds < 60) return `${seconds.toFixed(0)}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
    const hours = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    return `${hours}h ${mins}m`
  }

  const formatBytes = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
  }

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '-'
    try {
      return new Date(dateStr).toLocaleString()
    } catch {
      return dateStr
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status.toLowerCase()) {
      case 'completed':
        return <CheckCircleIcon className="h-5 w-5 text-green-500" />
      case 'failed':
        return <XCircleIcon className="h-5 w-5 text-red-500" />
      case 'running':
        return <ArrowPathIcon className="h-5 w-5 text-blue-500 animate-spin" />
      case 'interrupted':
        return <ExclamationTriangleIcon className="h-5 w-5 text-yellow-500" />
      default:
        return <ClockIcon className="h-5 w-5 text-gray-400" />
    }
  }

  const getStatusBadge = (status: string) => {
    const base = "px-2 py-0.5 rounded-full text-xs font-medium"
    switch (status.toLowerCase()) {
      case 'completed':
        return `${base} bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200`
      case 'failed':
        return `${base} bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200`
      case 'running':
        return `${base} bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200`
      case 'interrupted':
        return `${base} bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200`
      default:
        return `${base} bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200`
    }
  }

  if (authLoading) {
    return <div className="flex justify-center items-center h-64">Loading...</div>
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
          onClick={fetchJobs}
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
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg text-secondary hover:bg-gray-100 dark:hover:bg-gray-700"
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
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg bg-primary text-white"
        >
          <ClockIcon className="h-4 w-4" />
          History
        </Link>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-blue-100 dark:bg-blue-900/50 p-2">
                <ClockIcon className="h-5 w-5 text-blue-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Jobs</span>
            </div>
            <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">{summary.total_jobs}</p>
            <p className="text-xs text-secondary dark:text-gray-400 mt-1">
              {summary.by_status.completed || 0} completed, {summary.by_status.failed || 0} failed
            </p>
          </div>
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-green-100 dark:bg-green-900/50 p-2">
                <CheckCircleIcon className="h-5 w-5 text-green-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Files Processed</span>
            </div>
            <p className="text-2xl font-semibold text-green-600">
              {summary.overall_stats?.total_files_processed.toLocaleString() || 0}
            </p>
            <p className="text-xs text-secondary dark:text-gray-400 mt-1">
              {summary.overall_stats?.successful_files || 0} successful
            </p>
          </div>
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-purple-100 dark:bg-purple-900/50 p-2">
                <ChartBarIcon className="h-5 w-5 text-purple-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Chunks</span>
            </div>
            <p className="text-2xl font-semibold text-purple-600">
              {summary.overall_stats?.total_chunks.toLocaleString() || 0}
            </p>
          </div>
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-orange-100 dark:bg-orange-900/50 p-2">
                <FolderIcon className="h-5 w-5 text-orange-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Size</span>
            </div>
            <p className="text-2xl font-semibold text-orange-600">
              {formatBytes(summary.overall_stats?.total_size_bytes || 0)}
            </p>
          </div>
        </div>
      )}

      {/* Jobs List */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 overflow-hidden">
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Jobs</h3>
          <button
            onClick={fetchJobs}
            className="p-2 text-secondary hover:text-primary-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
            title="Refresh"
          >
            <ArrowPathIcon className="h-5 w-5" />
          </button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-secondary">
            <ArrowPathIcon className="h-8 w-8 animate-spin text-primary mx-auto mb-2" />
            Loading jobs...
          </div>
        ) : jobs.length === 0 ? (
          <div className="p-8 text-center text-secondary">
            <FolderIcon className="h-12 w-12 mx-auto mb-4 text-gray-300" />
            <p>No jobs found</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {jobs.map(job => (
              <div key={job.job_id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                {/* Job Header Row */}
                <div
                  className="p-4 flex items-center gap-4 cursor-pointer"
                  onClick={() => toggleJob(job.job_id)}
                >
                  <div className="flex-shrink-0">
                    {expandedJob === job.job_id ? (
                      <ChevronDownIcon className="h-5 w-5 text-gray-400" />
                    ) : (
                      <ChevronRightIcon className="h-5 w-5 text-gray-400" />
                    )}
                  </div>
                  
                  <div className="flex-shrink-0">
                    {getStatusIcon(job.status)}
                  </div>
                  
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-gray-600 dark:text-gray-400">
                        {job.job_id.substring(0, 8)}...
                      </span>
                      <span className={getStatusBadge(job.status)}>
                        {job.status}
                      </span>
                      {job.profile && (
                        <span className="text-sm text-indigo-600 dark:text-indigo-400">
                          {job.profile}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                      {formatDate(job.started_at)}
                      {job.duration_seconds !== null && (
                        <span className="ml-2">({formatDuration(job.duration_seconds)})</span>
                      )}
                    </div>
                  </div>
                  
                  <div className="flex items-center gap-6 text-sm text-gray-500">
                    <div className="text-center" title="Files">
                      <div className="font-medium text-gray-900 dark:text-white">{job.processed_files}</div>
                      <div className="text-xs">files</div>
                    </div>
                    <div className="text-center" title="Chunks">
                      <div className="font-medium text-gray-900 dark:text-white">{job.chunks_created}</div>
                      <div className="text-xs">chunks</div>
                    </div>
                    {job.failed_files > 0 && (
                      <div className="text-center text-red-500" title="Failed">
                        <div className="font-medium">{job.failed_files}</div>
                        <div className="text-xs">failed</div>
                      </div>
                    )}
                  </div>
                  
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteJob(job.job_id) }}
                    className="p-1.5 text-gray-400 hover:text-red-500"
                    title="Delete job"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
                
                {/* Expanded Job Details */}
                {expandedJob === job.job_id && (
                  <div className="px-4 pb-4 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-850">
                    {/* Tabs */}
                    <div className="flex gap-1 mt-3 mb-4 border-b border-gray-200 dark:border-gray-600">
                      {['overview', 'stats', 'logs', 'failed'].map(tab => (
                        <button
                          key={tab}
                          onClick={() => handleTabChange(job.job_id, tab as 'overview' | 'stats' | 'logs' | 'failed')}
                          className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ${
                            activeTab[job.job_id] === tab
                              ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                              : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                          }`}
                        >
                          {tab.charAt(0).toUpperCase() + tab.slice(1)}
                          {tab === 'stats' && job.stats_count > 0 && (
                            <span className="ml-1 text-xs bg-gray-200 dark:bg-gray-600 px-1.5 py-0.5 rounded-full">
                              {job.stats_count}
                            </span>
                          )}
                          {tab === 'logs' && job.logs_count > 0 && (
                            <span className="ml-1 text-xs bg-gray-200 dark:bg-gray-600 px-1.5 py-0.5 rounded-full">
                              {job.logs_count}
                            </span>
                          )}
                          {tab === 'failed' && job.failed_count > 0 && (
                            <span className="ml-1 text-xs bg-red-200 dark:bg-red-800 text-red-700 dark:text-red-200 px-1.5 py-0.5 rounded-full">
                              {job.failed_count}
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                    
                    {/* Tab Content */}
                    <div className="min-h-[200px]">
                      {/* Overview Tab */}
                      {activeTab[job.job_id] === 'overview' && jobDetails[job.job_id] && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                          <div className="bg-white dark:bg-gray-800 rounded p-3">
                            <div className="text-xs text-gray-500">Total Files</div>
                            <div className="text-lg font-semibold">{jobDetails[job.job_id].stats_summary?.total_files || 0}</div>
                          </div>
                          <div className="bg-white dark:bg-gray-800 rounded p-3">
                            <div className="text-xs text-gray-500">Successful</div>
                            <div className="text-lg font-semibold text-green-600">{jobDetails[job.job_id].stats_summary?.successful || 0}</div>
                          </div>
                          <div className="bg-white dark:bg-gray-800 rounded p-3">
                            <div className="text-xs text-gray-500">Failed</div>
                            <div className="text-lg font-semibold text-red-600">{jobDetails[job.job_id].stats_summary?.failed || 0}</div>
                          </div>
                          <div className="bg-white dark:bg-gray-800 rounded p-3">
                            <div className="text-xs text-gray-500">Avg Time</div>
                            <div className="text-lg font-semibold">
                              {((jobDetails[job.job_id].stats_summary?.avg_processing_time_ms || 0) / 1000).toFixed(1)}s
                            </div>
                          </div>
                          <div className="bg-white dark:bg-gray-800 rounded p-3">
                            <div className="text-xs text-gray-500">Total Chunks</div>
                            <div className="text-lg font-semibold">{jobDetails[job.job_id].stats_summary?.total_chunks || 0}</div>
                          </div>
                          <div className="bg-white dark:bg-gray-800 rounded p-3">
                            <div className="text-xs text-gray-500">Total Size</div>
                            <div className="text-lg font-semibold">{formatBytes(jobDetails[job.job_id].stats_summary?.total_size_bytes || 0)}</div>
                          </div>
                          <div className="bg-white dark:bg-gray-800 rounded p-3 col-span-2">
                            <div className="text-xs text-gray-500 mb-1">Failed by Type</div>
                            <div className="flex gap-2 flex-wrap">
                              {Object.entries(jobDetails[job.job_id].failed_by_type || {}).map(([type, count]) => (
                                <span key={type} className="text-xs bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 px-2 py-1 rounded">
                                  {type}: {count}
                                </span>
                              ))}
                              {Object.keys(jobDetails[job.job_id].failed_by_type || {}).length === 0 && (
                                <span className="text-xs text-gray-400">None</span>
                              )}
                            </div>
                          </div>
                        </div>
                      )}
                      
                      {/* Stats Tab */}
                      {activeTab[job.job_id] === 'stats' && (
                        <div className="space-y-2 max-h-80 overflow-y-auto">
                          {!jobStats[job.job_id] ? (
                            <div className="text-gray-500 text-sm">Loading stats...</div>
                          ) : jobStats[job.job_id].length === 0 ? (
                            <div className="text-gray-500 text-sm">No stats available</div>
                          ) : (
                            jobStats[job.job_id].map(stat => (
                              <div key={stat._id} className="bg-white dark:bg-gray-800 rounded p-3 flex items-center gap-3">
                                <div className={`w-2 h-2 rounded-full ${stat.success ? 'bg-green-500' : 'bg-red-500'}`} />
                                <div className="flex-1 min-w-0">
                                  <div className="text-sm font-medium truncate">{stat.file_name}</div>
                                  <div className="text-xs text-gray-500">
                                    {formatBytes(stat.file_size_bytes)} • {(stat.processing_time_ms / 1000).toFixed(1)}s • {stat.chunks_created} chunks
                                  </div>
                                </div>
                                {stat.error_type && (
                                  <span className="text-xs bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 px-2 py-0.5 rounded">
                                    {stat.error_type}
                                  </span>
                                )}
                              </div>
                            ))
                          )}
                        </div>
                      )}
                      
                      {/* Logs Tab */}
                      {activeTab[job.job_id] === 'logs' && (
                        <div className="space-y-1 max-h-80 overflow-y-auto font-mono text-xs">
                          <div className="flex justify-end mb-2">
                            <button
                              onClick={() => copyToClipboard(JSON.stringify(jobLogs[job.job_id], null, 2))}
                              className="text-gray-500 hover:text-gray-700 flex items-center gap-1"
                            >
                              <ClipboardDocumentIcon className="h-4 w-4" /> Copy All
                            </button>
                          </div>
                          {!jobLogs[job.job_id] ? (
                            <div className="text-gray-500">Loading logs...</div>
                          ) : jobLogs[job.job_id].length === 0 ? (
                            <div className="text-gray-500">No logs available</div>
                          ) : (
                            jobLogs[job.job_id].map((log, i) => (
                              <div key={i} className={`p-2 rounded ${
                                log.level === 'ERROR' ? 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300' :
                                log.level === 'WARNING' ? 'bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300' :
                                'bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-300'
                              }`}>
                                <span className="text-gray-400">{log.timestamp.split('T')[1]?.substring(0, 8)}</span>
                                {' '}
                                <span className={`font-bold ${
                                  log.level === 'ERROR' ? 'text-red-600' :
                                  log.level === 'WARNING' ? 'text-yellow-600' :
                                  'text-blue-600'
                                }`}>[{log.level}]</span>
                                {' '}
                                {log.message}
                              </div>
                            ))
                          )}
                        </div>
                      )}
                      
                      {/* Failed Tab */}
                      {activeTab[job.job_id] === 'failed' && (
                        <div className="space-y-2 max-h-80 overflow-y-auto">
                          {!jobFailed[job.job_id] ? (
                            <div className="text-gray-500 text-sm">Loading failed documents...</div>
                          ) : jobFailed[job.job_id].length === 0 ? (
                            <div className="text-gray-500 text-sm flex items-center gap-2">
                              <CheckCircleIcon className="h-5 w-5 text-green-500" />
                              No failed documents
                            </div>
                          ) : (
                            jobFailed[job.job_id].map(doc => (
                              <div key={doc._id} className="bg-white dark:bg-gray-800 rounded p-3">
                                <div className="flex items-start gap-2">
                                  <XCircleIcon className="h-5 w-5 text-red-500 flex-shrink-0 mt-0.5" />
                                  <div className="flex-1 min-w-0">
                                    <div className="font-medium text-sm truncate">{doc.file_name}</div>
                                    <div className="text-xs text-gray-500 truncate">{doc.file_path}</div>
                                    <div className="mt-1 text-xs">
                                      <span className="bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 px-2 py-0.5 rounded mr-2">
                                        {doc.error_type}
                                      </span>
                                      <span className="text-gray-500">{formatDate(doc.failed_at)}</span>
                                    </div>
                                    {doc.error_message && (
                                      <div className="mt-1 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 p-2 rounded">
                                        {doc.error_message}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Pagination */}
        {total > limit && (
          <div className="flex items-center justify-between border-t border-gray-100 dark:border-gray-700 px-4 py-3">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-lg px-3 py-1 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-sm text-secondary">
              Page {page + 1} of {Math.ceil(total / limit)}
            </span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={(page + 1) * limit >= total}
              className="rounded-lg px-3 py-1 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
