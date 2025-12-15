import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CpuChipIcon,
  ArrowPathIcon,
  ChartBarIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  LightBulbIcon,
  ClockIcon,
  ServerIcon,
  BoltIcon,
} from '@heroicons/react/24/outline'
import { indexesApi, IndexDashboard, OptimizationSuggestion } from '../api/client'
import { useAuth } from '../contexts/AuthContext'

export default function SearchIndexesPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  
  const [dashboard, setDashboard] = useState<IndexDashboard | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isCreating, setIsCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  
  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await indexesApi.getDashboard()
      setDashboard(data)
    } catch (err) {
      console.error('Error fetching indexes:', err)
      setError('Failed to load index dashboard')
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
  
  const handleCreateIndexes = async () => {
    setIsCreating(true)
    setMessage(null)
    try {
      const result = await indexesApi.createIndexes()
      if (result.success) {
        setMessage('Indexes created successfully. They may take time to become READY.')
        setTimeout(fetchData, 2000)
      } else {
        setError(result.errors?.join(', ') || 'Failed to create indexes')
      }
    } catch (err) {
      setError('Failed to create indexes')
    } finally {
      setIsCreating(false)
    }
  }
  
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
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">Search Indexes</h2>
          <p className="text-sm text-secondary dark:text-gray-400">Performance metrics and optimization</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchData}
            className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
          >
            <ArrowPathIcon className="h-4 w-4" />
            Refresh
          </button>
          <button
            onClick={handleCreateIndexes}
            disabled={isCreating}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-all hover:bg-primary-700 disabled:opacity-50"
          >
            {isCreating ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <CpuChipIcon className="h-4 w-4" />}
            {isCreating ? 'Creating...' : 'Create Indexes'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}
      
      {message && (
        <div className="rounded-2xl bg-green-50 dark:bg-green-900/30 p-4 text-green-700 dark:text-green-400">{message}</div>
      )}

      {dashboard && (
        <>
          {/* Performance Overview */}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-blue-100 dark:bg-blue-900/50 p-2">
                  <ClockIcon className="h-5 w-5 text-blue-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Avg Response</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.performance.avg_response_time_ms.toFixed(0)} ms
              </p>
            </div>

            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-green-100 dark:bg-green-900/50 p-2">
                  <BoltIcon className="h-5 w-5 text-green-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">P95 Latency</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.performance.p95_response_time_ms.toFixed(0)} ms
              </p>
            </div>

            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-purple-100 dark:bg-purple-900/50 p-2">
                  <ChartBarIcon className="h-5 w-5 text-purple-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Searches (24h)</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.performance.searches_last_24h.toLocaleString()}
              </p>
            </div>

            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="rounded-xl bg-amber-100 dark:bg-amber-900/50 p-2">
                  <ServerIcon className="h-5 w-5 text-amber-600" />
                </div>
                <span className="text-sm font-medium text-secondary dark:text-gray-400">Total Searches</span>
              </div>
              <p className="text-2xl font-semibold text-primary-900 dark:text-gray-200">
                {dashboard.performance.total_searches.toLocaleString()}
              </p>
            </div>
          </div>

          {/* Index Status */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Index Status</h3>
            {dashboard.indexes.length > 0 ? (
              <div className="grid gap-4 md:grid-cols-2">
                {dashboard.indexes.map((index, i) => (
                  <div key={i} className="flex items-center justify-between rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                    <div>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{index.name}</p>
                      <p className="text-sm text-secondary dark:text-gray-400">
                        {index.type} • {index.documents_indexed.toLocaleString()} docs • {formatBytes(index.size_bytes)}
                      </p>
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
              <div className="text-center py-8">
                <CpuChipIcon className="h-12 w-12 text-gray-400 mx-auto mb-3" />
                <p className="text-secondary dark:text-gray-400 mb-2">No indexes found</p>
                <p className="text-sm text-secondary dark:text-gray-500">Click "Create Indexes" to build search indexes.</p>
              </div>
            )}
          </div>

          {/* Resource Allocation */}
          {dashboard.resource_allocation && (
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Resource Allocation</h3>
              <div className="grid gap-4 md:grid-cols-3">
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400 mb-2">CPU</p>
                  <p className="font-medium text-primary-900 dark:text-gray-200">
                    {(dashboard.resource_allocation.cpu as any)?.cores || 0} cores
                  </p>
                  <p className="text-xs text-secondary dark:text-gray-500">
                    Usage: {(dashboard.resource_allocation.cpu as any)?.usage_percent?.toFixed(1) || 0}%
                  </p>
                </div>
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400 mb-2">Memory</p>
                  <p className="font-medium text-primary-900 dark:text-gray-200">
                    {(dashboard.resource_allocation.memory as any)?.total_gb || 0} GB total
                  </p>
                  <p className="text-xs text-secondary dark:text-gray-500">
                    Available: {(dashboard.resource_allocation.memory as any)?.available_gb?.toFixed(1) || 0} GB
                  </p>
                </div>
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400 mb-2">MongoDB</p>
                  <p className="font-medium text-primary-900 dark:text-gray-200">
                    Pool: {(dashboard.resource_allocation.mongodb as any)?.connection_pool_size || 10}
                  </p>
                  <p className="text-xs text-secondary dark:text-gray-500">
                    Recommended: {(dashboard.resource_allocation.mongodb as any)?.recommended_pool_size || 50}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Optimization Suggestions */}
          {dashboard.suggestions.length > 0 && (
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <div className="flex items-center gap-2 mb-4">
                <LightBulbIcon className="h-5 w-5 text-primary" />
                <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Optimization Suggestions</h3>
              </div>
              <div className="space-y-4">
                {dashboard.suggestions.map((suggestion, i) => (
                  <SuggestionCard key={i} suggestion={suggestion} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function SuggestionCard({ suggestion }: { suggestion: OptimizationSuggestion }) {
  const getSeverityStyles = (severity: string) => {
    switch (severity) {
      case 'critical': return { bg: 'bg-red-50 dark:bg-red-900/20', border: 'border-l-red-500', icon: 'text-red-500' }
      case 'high': return { bg: 'bg-orange-50 dark:bg-orange-900/20', border: 'border-l-orange-500', icon: 'text-orange-500' }
      case 'medium': return { bg: 'bg-yellow-50 dark:bg-yellow-900/20', border: 'border-l-yellow-500', icon: 'text-yellow-500' }
      default: return { bg: 'bg-blue-50 dark:bg-blue-900/20', border: 'border-l-blue-500', icon: 'text-blue-500' }
    }
  }
  
  const styles = getSeverityStyles(suggestion.severity)
  
  return (
    <div className={`${styles.bg} ${styles.border} border-l-4 rounded-r-xl p-4`}>
      <div className="flex items-start gap-3">
        <ExclamationTriangleIcon className={`h-5 w-5 ${styles.icon} flex-shrink-0 mt-0.5`} />
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-1">
            <h4 className="font-medium text-primary-900 dark:text-gray-200">{suggestion.title}</h4>
            <span className={`px-2 py-0.5 rounded text-xs font-medium uppercase ${
              suggestion.severity === 'critical' ? 'bg-red-200 text-red-800 dark:bg-red-800 dark:text-red-200' :
              suggestion.severity === 'high' ? 'bg-orange-200 text-orange-800 dark:bg-orange-800 dark:text-orange-200' :
              suggestion.severity === 'medium' ? 'bg-yellow-200 text-yellow-800 dark:bg-yellow-800 dark:text-yellow-200' :
              'bg-blue-200 text-blue-800 dark:bg-blue-800 dark:text-blue-200'
            }`}>
              {suggestion.severity}
            </span>
          </div>
          <p className="text-sm text-secondary dark:text-gray-400 mb-2">{suggestion.description}</p>
          <div className="flex flex-wrap gap-4 text-xs">
            <div>
              <span className="text-secondary dark:text-gray-500">Action: </span>
              <span className="text-primary-900 dark:text-gray-300">{suggestion.action}</span>
            </div>
            <div>
              <span className="text-secondary dark:text-gray-500">Impact: </span>
              <span className="text-green-600 dark:text-green-400">{suggestion.estimated_impact}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
