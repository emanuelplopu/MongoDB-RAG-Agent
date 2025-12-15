import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CpuChipIcon,
  DocumentTextIcon,
  ArrowPathIcon,
  ClockIcon,
  FolderIcon,
  CheckCircleIcon,
} from '@heroicons/react/24/outline'
import { statusApi, StatusDashboard } from '../api/client'
import { useAuth } from '../contexts/AuthContext'

export default function StatusPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  
  const [dashboard, setDashboard] = useState<StatusDashboard | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await statusApi.getDashboard()
      setDashboard(data)
    } catch (err) {
      console.error('Error fetching status:', err)
      setError('Failed to load status dashboard')
    } finally {
      setIsLoading(false)
    }
  }, [])
  
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/')
    }
  }, [user, authLoading, navigate])
  
  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      fetchData()
    }
  }, [authLoading, user, fetchData])
  
  if (authLoading || isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  
  if (!user?.is_admin) return null

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  const formatUptime = (seconds: number) => {
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    if (days > 0) return `${days}d ${hours}h`
    if (hours > 0) return `${hours}h ${mins}m`
    return `${mins}m`
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">System Status</h2>
          <p className="text-sm text-secondary dark:text-gray-400">KPIs and metrics overview</p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}

      {dashboard && (
        <>
          {/* Overview Cards */}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-primary-100 dark:bg-primary-900/50 p-2">
                  <FolderIcon className="h-5 w-5 text-primary" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Profiles</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.total_profiles}
              </p>
              <p className="text-xs text-secondary dark:text-gray-500 mt-1">
                Active: {dashboard.active_profile}
              </p>
            </div>

            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-blue-100 dark:bg-blue-900/50 p-2">
                  <DocumentTextIcon className="h-5 w-5 text-blue-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Documents</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.total_documents.toLocaleString()}
              </p>
            </div>

            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-green-100 dark:bg-green-900/50 p-2">
                  <CpuChipIcon className="h-5 w-5 text-green-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Chunks</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.total_chunks.toLocaleString()}
              </p>
            </div>

            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-purple-100 dark:bg-purple-900/50 p-2">
                  <ClockIcon className="h-5 w-5 text-purple-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">API Uptime</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {formatUptime(dashboard.api_uptime_seconds)}
              </p>
            </div>
          </div>

          {/* System Metrics */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">System Resources</h3>
            <div className="grid gap-4 md:grid-cols-3">
              <ResourceMeter
                label="CPU Usage"
                value={dashboard.system_metrics.cpu_percent}
                color="blue"
              />
              <ResourceMeter
                label="Memory Usage"
                value={dashboard.system_metrics.memory_percent}
                color="green"
                detail={`${dashboard.system_metrics.memory_used_gb.toFixed(1)} / ${dashboard.system_metrics.memory_total_gb.toFixed(1)} GB`}
              />
              <ResourceMeter
                label="Disk Usage"
                value={dashboard.system_metrics.disk_percent}
                color="purple"
                detail={`${dashboard.system_metrics.disk_used_gb.toFixed(1)} / ${dashboard.system_metrics.disk_total_gb.toFixed(1)} GB`}
              />
            </div>
          </div>

          {/* Active Configuration */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Active Configuration</h3>
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <p className="text-sm text-secondary dark:text-gray-400">LLM Provider</p>
                <p className="font-medium text-primary-900 dark:text-gray-200">{dashboard.llm_provider}</p>
              </div>
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <p className="text-sm text-secondary dark:text-gray-400">LLM Model</p>
                <p className="font-medium text-primary-900 dark:text-gray-200 truncate">{dashboard.llm_model}</p>
              </div>
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <p className="text-sm text-secondary dark:text-gray-400">Embedding Model</p>
                <p className="font-medium text-primary-900 dark:text-gray-200 truncate">{dashboard.embedding_model}</p>
              </div>
            </div>
          </div>

          {/* Profile Stats */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Profile Statistics</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-700">
                    <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Profile</th>
                    <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Database</th>
                    <th className="text-right py-3 px-4 text-secondary dark:text-gray-400 font-medium">Documents</th>
                    <th className="text-right py-3 px-4 text-secondary dark:text-gray-400 font-medium">Chunks</th>
                    <th className="text-right py-3 px-4 text-secondary dark:text-gray-400 font-medium">Storage</th>
                    <th className="text-right py-3 px-4 text-secondary dark:text-gray-400 font-medium">Jobs</th>
                    <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Last Ingestion</th>
                  </tr>
                </thead>
                <tbody>
                  {dashboard.profiles.map((profile) => (
                    <tr key={profile.profile_key} className={`border-b border-gray-100 dark:border-gray-800 ${profile.profile_key === dashboard.active_profile ? 'bg-primary-50 dark:bg-primary-900/20' : ''}`}>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          {profile.profile_key === dashboard.active_profile && (
                            <CheckCircleIcon className="h-4 w-4 text-green-500" />
                          )}
                          <span className="font-medium text-primary-900 dark:text-gray-200">{profile.profile_name}</span>
                        </div>
                        <span className="text-xs text-secondary dark:text-gray-500">{profile.profile_key}</span>
                      </td>
                      <td className="py-3 px-4 text-secondary dark:text-gray-400">{profile.database}</td>
                      <td className="py-3 px-4 text-right text-primary-900 dark:text-gray-200">{profile.documents_count.toLocaleString()}</td>
                      <td className="py-3 px-4 text-right text-primary-900 dark:text-gray-200">{profile.chunks_count.toLocaleString()}</td>
                      <td className="py-3 px-4 text-right text-secondary dark:text-gray-400">{formatBytes(profile.storage_size_bytes)}</td>
                      <td className="py-3 px-4 text-right text-secondary dark:text-gray-400">{profile.ingestion_jobs_count}</td>
                      <td className="py-3 px-4 text-secondary dark:text-gray-400">
                        {profile.last_ingestion ? new Date(profile.last_ingestion).toLocaleDateString() : 'Never'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function ResourceMeter({ label, value, color, detail }: { label: string; value: number; color: string; detail?: string }) {
  const colorClasses = {
    blue: 'bg-blue-500',
    green: 'bg-green-500',
    purple: 'bg-purple-500',
    red: 'bg-red-500',
  }
  
  const bgColorClasses = {
    blue: 'bg-blue-100 dark:bg-blue-900/30',
    green: 'bg-green-100 dark:bg-green-900/30',
    purple: 'bg-purple-100 dark:bg-purple-900/30',
    red: 'bg-red-100 dark:bg-red-900/30',
  }
  
  const isHigh = value > 80
  const barColor = isHigh ? 'bg-red-500' : colorClasses[color as keyof typeof colorClasses]
  const bgColor = isHigh ? 'bg-red-100 dark:bg-red-900/30' : bgColorClasses[color as keyof typeof bgColorClasses]
  
  return (
    <div className={`rounded-xl ${bgColor} p-4`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-primary-900 dark:text-gray-200">{label}</span>
        <span className={`text-sm font-semibold ${isHigh ? 'text-red-600' : 'text-primary-900 dark:text-gray-200'}`}>
          {value.toFixed(1)}%
        </span>
      </div>
      <div className="w-full h-2 bg-white dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full ${barColor} rounded-full transition-all`} style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
      {detail && <p className="text-xs text-secondary dark:text-gray-500 mt-1">{detail}</p>}
    </div>
  )
}
