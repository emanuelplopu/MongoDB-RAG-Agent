import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CloudIcon,
  PlusIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  ClockIcon,
  DocumentIcon,
  CogIcon,
  LinkIcon,
} from '@heroicons/react/24/outline'
import {
  cloudSourcesApi,
  CloudSourcesDashboard,
  CloudProvider,
  CloudProviderType,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'

// Provider icons mapping
const PROVIDER_ICONS: Record<CloudProviderType, string> = {
  google_drive: '🔵',
  onedrive: '☁️',
  sharepoint: '📊',
  dropbox: '📦',
  owncloud: '☁️',
  nextcloud: '🟢',
  confluence: '📝',
  jira: '🔷',
  email_imap: '✉️',
  email_gmail: '📧',
  email_outlook: '📨',
}

function formatRelativeTime(dateString?: string): string {
  if (!dateString) return 'Never'
  const date = new Date(dateString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 7) return `${days}d ago`
  return date.toLocaleDateString()
}

export default function CloudSourcesPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  
  const [dashboard, setDashboard] = useState<CloudSourcesDashboard | null>(null)
  const [providers, setProviders] = useState<CloudProvider[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddSource, setShowAddSource] = useState(false)
  
  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const [dashboardRes, providersRes] = await Promise.all([
        cloudSourcesApi.getDashboard(),
        cloudSourcesApi.getProviders(),
      ])
      setDashboard(dashboardRes)
      setProviders(providersRes.providers)
    } catch (err: any) {
      console.error('Error fetching cloud sources data:', err)
      setError(err.message || 'Failed to load cloud sources')
    } finally {
      setIsLoading(false)
    }
  }, [])
  
  useEffect(() => {
    if (!authLoading && user) {
      fetchData()
    }
  }, [authLoading, user, fetchData])
  
  const handleConnectProvider = async (provider: CloudProvider) => {
    if (provider.supported_auth_types.includes('oauth2')) {
      // For OAuth providers, initiate OAuth flow
      try {
        const displayName = prompt(`Enter a name for this ${provider.display_name} connection:`)
        if (!displayName) return
        
        const { authorization_url } = await cloudSourcesApi.initiateOAuth(
          provider.provider_type,
          displayName
        )
        // Redirect to OAuth provider
        window.location.href = authorization_url
      } catch (err: any) {
        setError(err.message || 'Failed to start OAuth flow')
      }
    } else {
      // For non-OAuth providers, navigate to connection form
      navigate(`/cloud-sources/connect/${provider.provider_type}`)
    }
  }
  
  if (authLoading || isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  
  if (!user) {
    navigate('/login')
    return null
  }
  
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-primary-900 dark:text-gray-100">Cloud Sources</h1>
          <p className="text-secondary dark:text-gray-400 mt-1">
            Connect and sync documents from cloud storage and collaboration platforms
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchData}
            disabled={isLoading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <ArrowPathIcon className={`h-5 w-5 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={() => setShowAddSource(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-5 w-5" />
            Add Source
          </button>
        </div>
      </div>
      
      {/* Error Alert */}
      {error && (
        <div className="rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-4">
          <div className="flex items-center gap-3">
            <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
            <p className="text-red-700 dark:text-red-400">{error}</p>
          </div>
        </div>
      )}
      
      {/* Stats Overview */}
      {dashboard && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary-100 dark:bg-primary-900/50">
                <LinkIcon className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold text-primary-900 dark:text-gray-100">
                  {dashboard.total_connections}
                </p>
                <p className="text-sm text-secondary dark:text-gray-400">Connections</p>
              </div>
            </div>
          </div>
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-green-100 dark:bg-green-900/50">
                <CogIcon className="h-5 w-5 text-green-600 dark:text-green-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-primary-900 dark:text-gray-100">
                  {dashboard.total_sync_configs}
                </p>
                <p className="text-sm text-secondary dark:text-gray-400">Sync Configs</p>
              </div>
            </div>
          </div>
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-blue-100 dark:bg-blue-900/50">
                <DocumentIcon className="h-5 w-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-primary-900 dark:text-gray-100">
                  {dashboard.total_files_indexed.toLocaleString()}
                </p>
                <p className="text-sm text-secondary dark:text-gray-400">Files Indexed</p>
              </div>
            </div>
          </div>
          
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-amber-100 dark:bg-amber-900/50">
                <ClockIcon className="h-5 w-5 text-amber-600 dark:text-amber-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-primary-900 dark:text-gray-100">
                  {dashboard.active_jobs}
                </p>
                <p className="text-sm text-secondary dark:text-gray-400">Active Syncs</p>
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Connected Sources */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <CloudIcon className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100">
              Connected Sources
            </h2>
          </div>
          <button
            onClick={() => navigate('/cloud-sources/connections')}
            className="text-sm text-primary hover:text-primary-700"
          >
            Manage All →
          </button>
        </div>
        
        {dashboard && dashboard.sources.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {dashboard.sources.map((source) => (
              <div
                key={source.connection_id}
                className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 hover:border-primary dark:hover:border-primary transition-colors cursor-pointer"
                onClick={() => navigate(`/cloud-sources/connections/${source.connection_id}`)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{PROVIDER_ICONS[source.provider]}</span>
                    <div>
                      <h3 className="font-medium text-primary-900 dark:text-gray-100">
                        {source.display_name}
                      </h3>
                      <p className="text-xs text-secondary dark:text-gray-400 capitalize">
                        {source.provider.replace('_', ' ')}
                      </p>
                    </div>
                  </div>
                  {source.status === 'active' ? (
                    <CheckCircleIcon className="h-5 w-5 text-green-500" />
                  ) : source.has_errors ? (
                    <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
                  ) : (
                    <ClockIcon className="h-5 w-5 text-amber-500" />
                  )}
                </div>
                
                <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-secondary dark:text-gray-400">Files</p>
                      <p className="font-medium text-primary-900 dark:text-gray-100">
                        {source.total_files_indexed.toLocaleString()}
                      </p>
                    </div>
                    <div>
                      <p className="text-secondary dark:text-gray-400">Syncs</p>
                      <p className="font-medium text-primary-900 dark:text-gray-100">
                        {source.sync_configs_count}
                      </p>
                    </div>
                  </div>
                  
                  <div className="mt-2 flex items-center justify-between text-xs text-secondary dark:text-gray-400">
                    <span>Last sync: {formatRelativeTime(source.last_sync_at)}</span>
                    {source.next_sync_at && (
                      <span>Next: {formatRelativeTime(source.next_sync_at)}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8">
            <CloudIcon className="h-12 w-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
            <p className="text-secondary dark:text-gray-400 mb-4">
              No cloud sources connected yet
            </p>
            <button
              onClick={() => setShowAddSource(true)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700"
            >
              <PlusIcon className="h-5 w-5" />
              Connect Your First Source
            </button>
          </div>
        )}
      </div>
      
      {/* Recent Errors */}
      {dashboard && dashboard.recent_errors.length > 0 && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center gap-3 mb-4">
            <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100">
              Recent Errors
            </h2>
          </div>
          
          <div className="space-y-2">
            {dashboard.recent_errors.map((error, i) => (
              <div
                key={i}
                className="flex items-start gap-3 p-3 rounded-xl bg-red-50 dark:bg-red-900/20"
              >
                <ExclamationTriangleIcon className="h-5 w-5 text-red-500 flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-red-700 dark:text-red-400">{error.message}</p>
                  <p className="text-xs text-red-500 dark:text-red-500 mt-1">
                    {error.file_path} • {formatRelativeTime(error.timestamp)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      
      {/* Add Source Modal */}
      {showAddSource && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-2xl w-full max-h-[80vh] overflow-auto">
            <div className="p-6 border-b border-gray-200 dark:border-gray-700">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-100">
                  Add Cloud Source
                </h2>
                <button
                  onClick={() => setShowAddSource(false)}
                  className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  ✕
                </button>
              </div>
              <p className="text-secondary dark:text-gray-400 mt-1">
                Connect a cloud storage or collaboration platform to index documents
              </p>
            </div>
            
            <div className="p-6">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {providers.map((provider) => (
                  <button
                    key={provider.provider_type}
                    onClick={() => {
                      setShowAddSource(false)
                      handleConnectProvider(provider)
                    }}
                    className="flex items-start gap-4 p-4 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-primary dark:hover:border-primary hover:bg-primary-50 dark:hover:bg-primary-900/20 transition-colors text-left"
                  >
                    <span className="text-3xl">{PROVIDER_ICONS[provider.provider_type]}</span>
                    <div className="flex-1">
                      <h3 className="font-medium text-primary-900 dark:text-gray-100">
                        {provider.display_name}
                      </h3>
                      <p className="text-xs text-secondary dark:text-gray-400 mt-1 line-clamp-2">
                        {provider.description}
                      </p>
                      <div className="flex items-center gap-2 mt-2">
                        {provider.supports_delta_sync && (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-400">
                            Delta Sync
                          </span>
                        )}
                        {provider.supported_auth_types.includes('oauth2') && (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-400">
                            OAuth
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
