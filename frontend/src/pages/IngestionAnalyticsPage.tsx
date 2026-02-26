import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ChartBarIcon,
  ArrowPathIcon,
  ClockIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  CubeIcon,
  TrashIcon,
  CogIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'

interface AnalyticsOverview {
  total_files_processed: number
  successful: number
  failed: number
  success_rate: number
  avg_processing_time_ms: number
  min_processing_time_ms: number
  max_processing_time_ms: number
  total_processing_hours: number
  total_chunks_created: number
  total_size_gb: number
  errors_by_type: Record<string, number>
}

interface Outlier {
  _id: string
  file_path: string
  file_name: string
  file_size_bytes: number
  processing_time_ms: number
  chunks_created: number
  started_at: string
}

interface ExtensionStats {
  extension: string
  count: number
  successful: number
  failed: number
  success_rate: number
  avg_processing_time_ms: number
  max_processing_time_ms: number
  total_chunks: number
  avg_size_mb: number
  total_size_mb: number
}

interface TimelineEntry {
  hour: string
  files_processed: number
  successful: number
  failed: number
  chunks_created: number
  avg_processing_time_ms: number
}

export default function IngestionAnalyticsPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  const { t } = useTranslation()
  
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null)
  const [outliers, setOutliers] = useState<Outlier[]>([])
  const [outlierStats, setOutlierStats] = useState<{threshold_ms: number; avg_ms: number; std_dev_ms: number} | null>(null)
  const [extensionStats, setExtensionStats] = useState<ExtensionStats[]>([])
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'overview' | 'outliers' | 'extensions' | 'timeline'>('overview')
  
  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [overviewRes, outliersRes, extensionsRes, timelineRes] = await Promise.all([
        fetch('/api/v1/ingestion/analytics/overview'),
        fetch('/api/v1/ingestion/analytics/outliers?threshold_std=2.0&limit=50'),
        fetch('/api/v1/ingestion/analytics/by-extension'),
        fetch('/api/v1/ingestion/analytics/timeline?hours=48')
      ])
      
      if (!overviewRes.ok || !outliersRes.ok || !extensionsRes.ok || !timelineRes.ok) {
        throw new Error('Failed to fetch analytics')
      }
      
      const [overviewData, outliersData, extensionsData, timelineData] = await Promise.all([
        overviewRes.json(),
        outliersRes.json(),
        extensionsRes.json(),
        timelineRes.json()
      ])
      
      setOverview(overviewData)
      setOutliers(outliersData.outliers)
      setOutlierStats({
        threshold_ms: outliersData.threshold_ms,
        avg_ms: outliersData.avg_ms,
        std_dev_ms: outliersData.std_dev_ms
      })
      setExtensionStats(extensionsData.by_extension)
      setTimeline(timelineData.timeline)
    } catch (err) {
      console.error('Error fetching analytics:', err)
      setError('Failed to load analytics data')
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
  
  const handleClearOldData = async () => {
    if (!confirm('Clear analytics data older than 30 days?')) return
    try {
      const res = await fetch('/api/v1/ingestion/analytics/clear?older_than_days=30', {
        method: 'DELETE'
      })
      if (!res.ok) throw new Error('Failed to clear')
      fetchData()
    } catch {
      setError('Failed to clear old data')
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
    if (ms < 3600000) return `${(ms/60000).toFixed(1)}m`
    return `${(ms/3600000).toFixed(1)}h`
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
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg bg-primary text-white"
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

      {/* Clear Old Data Button + Tabs */}
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-700 pb-2">
        <div className="flex gap-2">
          {(['overview', 'outliers', 'extensions', 'timeline'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
                activeTab === tab
                  ? 'bg-primary text-white'
                  : 'text-secondary hover:bg-gray-100 dark:hover:bg-gray-700'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>
        <button
          onClick={handleClearOldData}
          className="flex items-center gap-2 rounded-xl bg-red-100 dark:bg-red-900/30 px-3 py-1.5 text-sm font-medium text-red-700 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50"
        >
          <TrashIcon className="h-4 w-4" />
          {t('ingestion.clearOldData', 'Clear Old Data')}
        </button>
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && overview && (
        <div className="space-y-6">
          {/* Summary Cards */}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-blue-100 dark:bg-blue-900/50 p-2">
                  <DocumentTextIcon className="h-5 w-5 text-blue-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Processed</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">{overview.total_files_processed.toLocaleString()}</p>
            </div>
            
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-green-100 dark:bg-green-900/50 p-2">
                  <CheckCircleIcon className="h-5 w-5 text-green-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Success Rate</span>
              </div>
              <p className="text-2xl font-semibold text-green-600">{overview.success_rate.toFixed(1)}%</p>
            </div>
            
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-purple-100 dark:bg-purple-900/50 p-2">
                  <CubeIcon className="h-5 w-5 text-purple-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Chunks</span>
              </div>
              <p className="text-2xl font-semibold text-purple-600">{overview.total_chunks_created.toLocaleString()}</p>
            </div>
            
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-orange-100 dark:bg-orange-900/50 p-2">
                  <ClockIcon className="h-5 w-5 text-orange-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Time</span>
              </div>
              <p className="text-2xl font-semibold text-orange-600">{overview.total_processing_hours.toFixed(1)}h</p>
            </div>
          </div>

          {/* Processing Time Stats */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-semibold mb-4 text-primary-900 dark:text-gray-200">Processing Time Statistics</h3>
            <div className="grid gap-4 md:grid-cols-3">
              <div>
                <span className="text-sm text-secondary">Average</span>
                <p className="text-xl font-semibold text-primary-900 dark:text-gray-200">{formatDuration(overview.avg_processing_time_ms)}</p>
              </div>
              <div>
                <span className="text-sm text-secondary">Minimum</span>
                <p className="text-xl font-semibold text-green-600">{formatDuration(overview.min_processing_time_ms)}</p>
              </div>
              <div>
                <span className="text-sm text-secondary">Maximum</span>
                <p className="text-xl font-semibold text-red-600">{formatDuration(overview.max_processing_time_ms)}</p>
              </div>
            </div>
          </div>

          {/* Error Breakdown */}
          {Object.keys(overview.errors_by_type).length > 0 && (
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <h3 className="text-lg font-semibold mb-4 text-primary-900 dark:text-gray-200">Errors by Type</h3>
              <div className="grid gap-4 md:grid-cols-3">
                {Object.entries(overview.errors_by_type).map(([type, count]) => (
                  <div key={type} className="flex items-center justify-between p-3 rounded-xl bg-red-50 dark:bg-red-900/20">
                    <span className="text-sm font-medium text-red-700 dark:text-red-400 capitalize">{type}</span>
                    <span className="text-lg font-semibold text-red-600">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Outliers Tab */}
      {activeTab === 'outliers' && (
        <div className="space-y-4">
          {outlierStats && (
            <div className="rounded-2xl bg-yellow-50 dark:bg-yellow-900/20 p-4">
              <div className="flex items-center gap-2 mb-2">
                <ExclamationTriangleIcon className="h-5 w-5 text-yellow-600" />
                <span className="font-medium text-yellow-700 dark:text-yellow-400">Outlier Detection</span>
              </div>
              <p className="text-sm text-yellow-700 dark:text-yellow-400">
                Files taking longer than {formatDuration(outlierStats.threshold_ms)} (avg: {formatDuration(outlierStats.avg_ms)} + 2x std dev: {formatDuration(outlierStats.std_dev_ms)})
              </p>
            </div>
          )}
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 overflow-hidden">
            {outliers.length === 0 ? (
              <div className="p-8 text-center text-secondary">
                <CheckCircleIcon className="h-12 w-12 mx-auto mb-4 text-green-500" />
                <p>No outliers detected - all files processed within expected time</p>
              </div>
            ) : (
              <table className="w-full">
                <thead className="bg-gray-50 dark:bg-gray-700">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">File</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Size</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Processing Time</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Chunks</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {outliers.map((outlier) => (
                    <tr key={outlier._id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                      <td className="px-4 py-3">
                        <div className="max-w-xs truncate text-sm font-medium text-primary-900 dark:text-gray-200" title={outlier.file_name}>
                          {outlier.file_name}
                        </div>
                        <div className="max-w-xs truncate text-xs text-secondary" title={outlier.file_path}>
                          {outlier.file_path}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-secondary">{formatBytes(outlier.file_size_bytes)}</td>
                      <td className="px-4 py-3">
                        <span className="text-sm font-medium text-red-600">{formatDuration(outlier.processing_time_ms)}</span>
                      </td>
                      <td className="px-4 py-3 text-sm text-secondary">{outlier.chunks_created}</td>
                      <td className="px-4 py-3 text-xs text-secondary">{new Date(outlier.started_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Extensions Tab */}
      {activeTab === 'extensions' && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 overflow-hidden">
          {extensionStats.length === 0 ? (
            <div className="p-8 text-center text-secondary">
              <DocumentTextIcon className="h-12 w-12 mx-auto mb-4 text-gray-300" />
              <p>No extension data available</p>
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-gray-50 dark:bg-gray-700">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Extension</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Files</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Success Rate</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Avg Time</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Max Time</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Chunks</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Avg Size</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {extensionStats.map((ext) => (
                  <tr key={ext.extension} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center rounded-full bg-primary-100 dark:bg-primary-900/50 px-2.5 py-0.5 text-xs font-medium text-primary-700 dark:text-primary-300">
                        .{ext.extension}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-primary-900 dark:text-gray-200">{ext.count.toLocaleString()}</td>
                    <td className="px-4 py-3">
                      <span className={`text-sm font-medium ${ext.success_rate >= 95 ? 'text-green-600' : ext.success_rate >= 80 ? 'text-yellow-600' : 'text-red-600'}`}>
                        {ext.success_rate.toFixed(1)}%
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-secondary">{formatDuration(ext.avg_processing_time_ms)}</td>
                    <td className="px-4 py-3 text-sm text-secondary">{formatDuration(ext.max_processing_time_ms)}</td>
                    <td className="px-4 py-3 text-sm text-secondary">{ext.total_chunks.toLocaleString()}</td>
                    <td className="px-4 py-3 text-sm text-secondary">{ext.avg_size_mb.toFixed(1)} MB</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Timeline Tab */}
      {activeTab === 'timeline' && (
        <div className="space-y-4">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 overflow-hidden">
            {timeline.length === 0 ? (
              <div className="p-8 text-center text-secondary">
                <ChartBarIcon className="h-12 w-12 mx-auto mb-4 text-gray-300" />
                <p>No timeline data available for the last 48 hours</p>
              </div>
            ) : (
              <>
                {/* Simple bar chart visualization */}
                <div className="p-6">
                  <h3 className="text-lg font-semibold mb-4 text-primary-900 dark:text-gray-200">Files Processed (Last 48h)</h3>
                  <div className="flex items-end gap-1 h-40">
                    {timeline.map((entry, idx) => {
                      const maxFiles = Math.max(...timeline.map(t => t.files_processed), 1)
                      const height = (entry.files_processed / maxFiles) * 100
                      const successRate = entry.files_processed > 0 ? (entry.successful / entry.files_processed) * 100 : 100
                      return (
                        <div
                          key={idx}
                          className="flex-1 flex flex-col items-center group relative"
                        >
                          <div
                            className={`w-full rounded-t transition-all ${
                              successRate >= 95 ? 'bg-green-500' : successRate >= 80 ? 'bg-yellow-500' : 'bg-red-500'
                            }`}
                            style={{ height: `${Math.max(height, 2)}%` }}
                          />
                          <div className="absolute bottom-full mb-2 hidden group-hover:block z-10">
                            <div className="bg-gray-900 text-white text-xs rounded px-2 py-1 whitespace-nowrap">
                              {entry.hour.split('T')[1]}:00 - {entry.files_processed} files ({entry.successful} ok)
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
                
                {/* Timeline table */}
                <table className="w-full">
                  <thead className="bg-gray-50 dark:bg-gray-700">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Hour</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Files</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Successful</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Failed</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Chunks</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-secondary uppercase">Avg Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                    {timeline.slice().reverse().map((entry, idx) => (
                      <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                        <td className="px-4 py-3 text-sm text-secondary">{entry.hour.replace('T', ' ')}:00</td>
                        <td className="px-4 py-3 text-sm text-primary-900 dark:text-gray-200">{entry.files_processed}</td>
                        <td className="px-4 py-3 text-sm text-green-600">{entry.successful}</td>
                        <td className="px-4 py-3 text-sm text-red-600">{entry.failed}</td>
                        <td className="px-4 py-3 text-sm text-secondary">{entry.chunks_created.toLocaleString()}</td>
                        <td className="px-4 py-3 text-sm text-secondary">{formatDuration(entry.avg_processing_time_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
