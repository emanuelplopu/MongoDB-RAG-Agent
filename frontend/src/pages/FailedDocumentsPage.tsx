import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import {
  ExclamationTriangleIcon,
  ArrowPathIcon,
  TrashIcon,
  CheckCircleIcon,
  ClockIcon,
  DocumentTextIcon,
  FunnelIcon,
  XMarkIcon,
  CogIcon,
  ChartBarIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'

interface FailedDocument {
  _id: string
  file_path: string
  file_name: string
  file_size_bytes: number
  error_type: 'timeout' | 'error' | string
  error_message: string
  timeout_seconds: number
  processing_time_ms: number
  failed_at: string
  profile_key: string
  resolved: boolean
  retry_count: number
}

interface FailedDocsSummary {
  by_error_type: Record<string, { count: number; total_size_bytes: number }>
  total_unresolved: number
  total_resolved: number
  total: number
}

export default function FailedDocumentsPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  
  const [documents, setDocuments] = useState<FailedDocument[]>([])
  const [summary, setSummary] = useState<FailedDocsSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filterType, setFilterType] = useState<string>('')
  const [filterResolved, setFilterResolved] = useState<boolean | null>(false)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const pageSize = 25
  
  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      // Build query params
      const params = new URLSearchParams()
      params.set('skip', String(page * pageSize))
      params.set('limit', String(pageSize))
      if (filterType) params.set('error_type', filterType)
      if (filterResolved !== null) params.set('resolved', String(filterResolved))
      
      const [docsRes, summaryRes] = await Promise.all([
        fetch(`/api/v1/ingestion/failed-documents?${params}`),
        fetch('/api/v1/ingestion/failed-documents/summary')
      ])
      
      if (!docsRes.ok) throw new Error('Failed to fetch documents')
      if (!summaryRes.ok) throw new Error('Failed to fetch summary')
      
      const docsData = await docsRes.json()
      const summaryData = await summaryRes.json()
      
      setDocuments(docsData.documents)
      setTotal(docsData.total)
      setSummary(summaryData)
    } catch (err) {
      console.error('Error fetching failed documents:', err)
      setError('Failed to load failed documents')
    } finally {
      setIsLoading(false)
    }
  }, [page, filterType, filterResolved])
  
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
  
  const handleResolve = async (docId: string) => {
    try {
      const res = await fetch(`/api/v1/ingestion/failed-documents/${docId}/resolve`, {
        method: 'POST'
      })
      if (!res.ok) throw new Error('Failed to resolve')
      fetchData()
    } catch {
      setError('Failed to mark as resolved')
    }
  }
  
  const handleDelete = async (docId: string) => {
    try {
      const res = await fetch(`/api/v1/ingestion/failed-documents/${docId}`, {
        method: 'DELETE'
      })
      if (!res.ok) throw new Error('Failed to delete')
      fetchData()
    } catch {
      setError('Failed to delete record')
    }
  }
  
  const handleClearResolved = async () => {
    if (!confirm('Clear all resolved records?')) return
    try {
      const res = await fetch('/api/v1/ingestion/failed-documents?resolved_only=true', {
        method: 'DELETE'
      })
      if (!res.ok) throw new Error('Failed to clear')
      fetchData()
    } catch {
      setError('Failed to clear resolved records')
    }
  }
  
  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }
  
  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms.toFixed(0)}ms`
    if (ms < 60000) return `${(ms/1000).toFixed(1)}s`
    return `${(ms/60000).toFixed(1)}m`
  }
  
  if (authLoading || isLoading) {
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
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">Ingestion Management</h2>
          <p className="text-sm text-secondary dark:text-gray-400">Queue, scheduling, and progress</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchData}
            className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
          >
            <ArrowPathIcon className="h-4 w-4" />
            Refresh
          </button>
          {summary && summary.total_resolved > 0 && (
            <button
              onClick={handleClearResolved}
              className="flex items-center gap-2 rounded-xl bg-red-100 dark:bg-red-900/30 px-4 py-2 text-sm font-medium text-red-700 dark:text-red-400 transition-all hover:bg-red-200 dark:hover:bg-red-900/50"
            >
              <TrashIcon className="h-4 w-4" />
              Clear Resolved ({summary.total_resolved})
            </button>
          )}
        </div>
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
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg bg-primary text-white"
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

      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}

      {/* Summary Cards */}
      {summary && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-red-100 dark:bg-red-900/50 p-2">
                <ExclamationTriangleIcon className="h-5 w-5 text-red-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Unresolved</span>
            </div>
            <p className="text-2xl font-semibold text-red-600">{summary.total_unresolved}</p>
          </div>
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-green-100 dark:bg-green-900/50 p-2">
                <CheckCircleIcon className="h-5 w-5 text-green-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Resolved</span>
            </div>
            <p className="text-2xl font-semibold text-green-600">{summary.total_resolved}</p>
          </div>
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-orange-100 dark:bg-orange-900/50 p-2">
                <ClockIcon className="h-5 w-5 text-orange-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Timeouts</span>
            </div>
            <p className="text-2xl font-semibold text-orange-600">
              {summary.by_error_type['timeout']?.count || 0}
            </p>
          </div>
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="rounded-xl bg-purple-100 dark:bg-purple-900/50 p-2">
                <XMarkIcon className="h-5 w-5 text-purple-600" />
              </div>
              <span className="text-sm font-medium text-secondary dark:text-gray-400">Errors</span>
            </div>
            <p className="text-2xl font-semibold text-purple-600">
              {summary.by_error_type['error']?.count || 0}
            </p>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-4 rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
        <FunnelIcon className="h-5 w-5 text-secondary" />
        <select
          value={filterType}
          onChange={(e) => { setFilterType(e.target.value); setPage(0) }}
          className="rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm"
        >
          <option value="">All Types</option>
          <option value="timeout">Timeouts</option>
          <option value="error">Errors</option>
        </select>
        <select
          value={filterResolved === null ? 'all' : String(filterResolved)}
          onChange={(e) => { 
            setFilterResolved(e.target.value === 'all' ? null : e.target.value === 'true')
            setPage(0)
          }}
          className="rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm"
        >
          <option value="all">All Status</option>
          <option value="false">Unresolved</option>
          <option value="true">Resolved</option>
        </select>
        <span className="text-sm text-secondary ml-auto">
          Showing {documents.length} of {total}
        </span>
      </div>

      {/* Documents List */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 overflow-hidden">
        {documents.length === 0 ? (
          <div className="p-8 text-center text-secondary">
            <DocumentTextIcon className="h-12 w-12 mx-auto mb-4 text-gray-300" />
            <p>No failed documents found</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700">
            {documents.map((doc) => (
              <div key={doc._id} className={`p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50 ${doc.resolved ? 'opacity-60' : ''}`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        doc.error_type === 'timeout' 
                          ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/50 dark:text-orange-400'
                          : 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400'
                      }`}>
                        {doc.error_type === 'timeout' ? <ClockIcon className="h-3 w-3 mr-1" /> : <XMarkIcon className="h-3 w-3 mr-1" />}
                        {doc.error_type}
                      </span>
                      {doc.resolved && (
                        <span className="inline-flex items-center rounded-full bg-green-100 dark:bg-green-900/50 px-2 py-0.5 text-xs font-medium text-green-700 dark:text-green-400">
                          <CheckCircleIcon className="h-3 w-3 mr-1" />
                          Resolved
                        </span>
                      )}
                      <span className="text-xs text-secondary">{formatBytes(doc.file_size_bytes)}</span>
                      <span className="text-xs text-secondary">Timeout: {doc.timeout_seconds}s</span>
                      <span className="text-xs text-secondary">Took: {formatDuration(doc.processing_time_ms)}</span>
                    </div>
                    <h4 className="font-medium text-primary-900 dark:text-gray-200 truncate" title={doc.file_name}>
                      {doc.file_name}
                    </h4>
                    <p className="text-sm text-secondary truncate mt-1" title={doc.file_path}>
                      {doc.file_path}
                    </p>
                    <p className="text-sm text-red-600 dark:text-red-400 mt-2 line-clamp-2" title={doc.error_message}>
                      {doc.error_message}
                    </p>
                    <p className="text-xs text-secondary mt-1">
                      Failed: {new Date(doc.failed_at).toLocaleString()} | Profile: {doc.profile_key}
                    </p>
                  </div>
                  <div className="flex flex-col gap-2">
                    {!doc.resolved && (
                      <button
                        onClick={() => handleResolve(doc._id)}
                        className="flex items-center gap-1 rounded-lg bg-green-100 dark:bg-green-900/30 px-3 py-1.5 text-xs font-medium text-green-700 dark:text-green-400 hover:bg-green-200 dark:hover:bg-green-900/50"
                      >
                        <CheckCircleIcon className="h-4 w-4" />
                        Resolve
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(doc._id)}
                      className="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-gray-700 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600"
                    >
                      <TrashIcon className="h-4 w-4" />
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
        
        {/* Pagination */}
        {total > pageSize && (
          <div className="flex items-center justify-between border-t border-gray-100 dark:border-gray-700 px-4 py-3">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-lg px-3 py-1 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-sm text-secondary">
              Page {page + 1} of {Math.ceil(total / pageSize)}
            </span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={(page + 1) * pageSize >= total}
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
