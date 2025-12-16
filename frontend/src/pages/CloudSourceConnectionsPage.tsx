import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  CloudIcon,
  PlusIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  ClockIcon,
  TrashIcon,
  PlayIcon,
  ChevronRightIcon,
  ArrowLeftIcon,
  FolderIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import {
  cloudSourcesApi,
  CloudConnection,
  CloudProvider,
  CloudProviderType,
  SyncConfig,
  RemoteFolder,
  SourcePath,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import FolderPicker from '../components/FolderPicker'

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

const STATUS_STYLES: Record<string, { bg: string; text: string; icon: typeof CheckCircleIcon }> = {
  active: {
    bg: 'bg-green-100 dark:bg-green-900/50',
    text: 'text-green-700 dark:text-green-400',
    icon: CheckCircleIcon,
  },
  expired: {
    bg: 'bg-amber-100 dark:bg-amber-900/50',
    text: 'text-amber-700 dark:text-amber-400',
    icon: ClockIcon,
  },
  error: {
    bg: 'bg-red-100 dark:bg-red-900/50',
    text: 'text-red-700 dark:text-red-400',
    icon: ExclamationTriangleIcon,
  },
  pending: {
    bg: 'bg-gray-100 dark:bg-gray-900/50',
    text: 'text-gray-700 dark:text-gray-400',
    icon: ClockIcon,
  },
  revoked: {
    bg: 'bg-red-100 dark:bg-red-900/50',
    text: 'text-red-700 dark:text-red-400',
    icon: ExclamationTriangleIcon,
  },
}

function formatDate(dateString?: string): string {
  if (!dateString) return 'Never'
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function CloudSourceConnectionsPage() {
  const navigate = useNavigate()
  const { connectionId } = useParams<{ connectionId?: string }>()
  const { user, isLoading: authLoading } = useAuth()

  const [connections, setConnections] = useState<CloudConnection[]>([])
  const [selectedConnection, setSelectedConnection] = useState<CloudConnection | null>(null)
  const [syncConfigs, setSyncConfigs] = useState<SyncConfig[]>([])
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_providers, setProviders] = useState<CloudProvider[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{
    success: boolean
    message: string
  } | null>(null)
  const [isTesting, setIsTesting] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showCreateSync, setShowCreateSync] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  // Load all connections
  const fetchConnections = useCallback(async () => {
    try {
      setError(null)
      const [connRes, provRes] = await Promise.all([
        cloudSourcesApi.getConnections(),
        cloudSourcesApi.getProviders(),
      ])
      setConnections(connRes.connections)
      setProviders(provRes.providers)
    } catch (err: any) {
      setError(err.message || 'Failed to load connections')
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Load specific connection details
  const fetchConnectionDetails = useCallback(async (id: string) => {
    try {
      setError(null)
      const [conn, configs] = await Promise.all([
        cloudSourcesApi.getConnection(id),
        cloudSourcesApi.getSyncConfigs({ connection_id: id }),
      ])
      setSelectedConnection(conn)
      setSyncConfigs(configs.configs)
    } catch (err: any) {
      setError(err.message || 'Failed to load connection details')
    }
  }, [])

  useEffect(() => {
    if (!authLoading && user) {
      fetchConnections()
    }
  }, [authLoading, user, fetchConnections])

  useEffect(() => {
    if (connectionId) {
      fetchConnectionDetails(connectionId)
    } else {
      setSelectedConnection(null)
      setSyncConfigs([])
    }
  }, [connectionId, fetchConnectionDetails])

  const handleTestConnection = async () => {
    if (!selectedConnection) return
    setIsTesting(true)
    setTestResult(null)
    try {
      const result = await cloudSourcesApi.testConnection(selectedConnection.id)
      setTestResult(result)
    } catch (err: any) {
      setTestResult({
        success: false,
        message: err.message || 'Connection test failed',
      })
    } finally {
      setIsTesting(false)
    }
  }

  const handleDeleteConnection = async () => {
    if (!selectedConnection) return
    setIsDeleting(true)
    try {
      await cloudSourcesApi.deleteConnection(selectedConnection.id)
      setShowDeleteConfirm(false)
      navigate('/cloud-sources/connections')
      fetchConnections()
    } catch (err: any) {
      setError(err.message || 'Failed to delete connection')
    } finally {
      setIsDeleting(false)
    }
  }

  const handleRefreshTokens = async () => {
    if (!selectedConnection) return
    try {
      const updated = await cloudSourcesApi.refreshOAuthTokens(selectedConnection.id)
      setSelectedConnection(updated)
    } catch (err: any) {
      setError(err.message || 'Failed to refresh tokens')
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

  // Connection detail view
  if (connectionId && selectedConnection) {
    const statusStyle = STATUS_STYLES[selectedConnection.status] || STATUS_STYLES.pending
    const StatusIcon = statusStyle.icon

    return (
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/cloud-sources/connections')}
            className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <ArrowLeftIcon className="h-5 w-5 text-primary-900 dark:text-gray-200" />
          </button>
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <span className="text-3xl">{PROVIDER_ICONS[selectedConnection.provider]}</span>
              <div>
                <h1 className="text-2xl font-bold text-primary-900 dark:text-gray-100">
                  {selectedConnection.display_name}
                </h1>
                <p className="text-secondary dark:text-gray-400 capitalize">
                  {selectedConnection.provider.replace('_', ' ')}
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestConnection}
              disabled={isTesting}
              className="flex items-center gap-2 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              {isTesting ? (
                <ArrowPathIcon className="h-5 w-5 animate-spin" />
              ) : (
                <PlayIcon className="h-5 w-5" />
              )}
              Test
            </button>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="flex items-center gap-2 px-4 py-2 rounded-xl border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
            >
              <TrashIcon className="h-5 w-5" />
              Delete
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

        {/* Test Result */}
        {testResult && (
          <div
            className={`rounded-xl p-4 border ${
              testResult.success
                ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
            }`}
          >
            <div className="flex items-center gap-3">
              {testResult.success ? (
                <CheckCircleIcon className="h-5 w-5 text-green-500" />
              ) : (
                <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
              )}
              <p
                className={
                  testResult.success
                    ? 'text-green-700 dark:text-green-400'
                    : 'text-red-700 dark:text-red-400'
                }
              >
                {testResult.message}
              </p>
            </div>
          </div>
        )}

        {/* Connection Details */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100 mb-4">
              Connection Details
            </h2>
            <dl className="space-y-4">
              <div>
                <dt className="text-sm text-secondary dark:text-gray-400">Status</dt>
                <dd className="mt-1">
                  <span
                    className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm ${statusStyle.bg} ${statusStyle.text}`}
                  >
                    <StatusIcon className="h-4 w-4" />
                    {selectedConnection.status.charAt(0).toUpperCase() +
                      selectedConnection.status.slice(1)}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-sm text-secondary dark:text-gray-400">Authentication Type</dt>
                <dd className="mt-1 text-primary-900 dark:text-gray-100 capitalize">
                  {selectedConnection.auth_type.replace('_', ' ')}
                </dd>
              </div>
              {selectedConnection.server_url && (
                <div>
                  <dt className="text-sm text-secondary dark:text-gray-400">Server URL</dt>
                  <dd className="mt-1 text-primary-900 dark:text-gray-100 font-mono text-sm">
                    {selectedConnection.server_url}
                  </dd>
                </div>
              )}
              {selectedConnection.oauth_email && (
                <div>
                  <dt className="text-sm text-secondary dark:text-gray-400">Connected Account</dt>
                  <dd className="mt-1 text-primary-900 dark:text-gray-100">
                    {selectedConnection.oauth_email}
                  </dd>
                </div>
              )}
              {selectedConnection.oauth_expires_at && (
                <div className="flex items-center justify-between">
                  <div>
                    <dt className="text-sm text-secondary dark:text-gray-400">Token Expires</dt>
                    <dd className="mt-1 text-primary-900 dark:text-gray-100">
                      {formatDate(selectedConnection.oauth_expires_at)}
                    </dd>
                  </div>
                  <button
                    onClick={handleRefreshTokens}
                    className="text-sm text-primary hover:text-primary-700"
                  >
                    Refresh Now
                  </button>
                </div>
              )}
              {selectedConnection.error_message && (
                <div>
                  <dt className="text-sm text-secondary dark:text-gray-400">Error</dt>
                  <dd className="mt-1 text-red-600 dark:text-red-400 text-sm">
                    {selectedConnection.error_message}
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-sm text-secondary dark:text-gray-400">Created</dt>
                <dd className="mt-1 text-primary-900 dark:text-gray-100">
                  {formatDate(selectedConnection.created_at)}
                </dd>
              </div>
              <div>
                <dt className="text-sm text-secondary dark:text-gray-400">Last Validated</dt>
                <dd className="mt-1 text-primary-900 dark:text-gray-100">
                  {formatDate(selectedConnection.last_validated_at)}
                </dd>
              </div>
            </dl>
          </div>

          {/* Sync Configurations */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100">
                Sync Configurations
              </h2>
              <button
                onClick={() => setShowCreateSync(true)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-primary text-white text-sm hover:bg-primary-700"
              >
                <PlusIcon className="h-4 w-4" />
                Add Config
              </button>
            </div>

            {syncConfigs.length > 0 ? (
              <div className="space-y-3">
                {syncConfigs.map((config) => (
                  <div
                    key={config.id}
                    className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 hover:border-primary dark:hover:border-primary transition-colors cursor-pointer"
                    onClick={() => navigate(`/cloud-sources/sync-configs/${config.id}`)}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-medium text-primary-900 dark:text-gray-100">
                          {config.name}
                        </h3>
                        <p className="text-sm text-secondary dark:text-gray-400">
                          {config.source_paths.length} folder(s) •{' '}
                          {config.stats.total_files.toLocaleString()} files
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {config.schedule.enabled && (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-400">
                            Scheduled
                          </span>
                        )}
                        <ChevronRightIcon className="h-5 w-5 text-gray-400" />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-8">
                <FolderIcon className="h-12 w-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
                <p className="text-secondary dark:text-gray-400 mb-4">
                  No sync configurations yet
                </p>
                <button
                  onClick={() => setShowCreateSync(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700"
                >
                  <PlusIcon className="h-5 w-5" />
                  Create First Config
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Delete Confirmation Modal */}
        {showDeleteConfirm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-md w-full p-6">
              <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-100 mb-2">
                Delete Connection
              </h2>
              <p className="text-secondary dark:text-gray-400 mb-6">
                Are you sure you want to delete "{selectedConnection.display_name}"? This will
                also delete all sync configurations and indexed documents from this source.
              </p>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
                >
                  Cancel
                </button>
                <button
                  onClick={handleDeleteConnection}
                  disabled={isDeleting}
                  className="px-4 py-2 rounded-xl bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {isDeleting ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Create Sync Config Modal */}
        {showCreateSync && (
          <CreateSyncConfigModal
            connection={selectedConnection}
            onClose={() => setShowCreateSync(false)}
            onCreated={() => {
              setShowCreateSync(false)
              fetchConnectionDetails(selectedConnection.id)
            }}
          />
        )}
      </div>
    )
  }

  // Connections list view
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            to="/cloud-sources"
            className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <ArrowLeftIcon className="h-5 w-5 text-primary-900 dark:text-gray-200" />
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-primary-900 dark:text-gray-100">
              Cloud Connections
            </h1>
            <p className="text-secondary dark:text-gray-400 mt-1">
              Manage your connected cloud storage accounts
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchConnections}
            disabled={isLoading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <ArrowPathIcon className={`h-5 w-5 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={() => navigate('/cloud-sources')}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-5 w-5" />
            Add Connection
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

      {/* Connections Grid */}
      {connections.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {connections.map((conn) => {
            const statusStyle = STATUS_STYLES[conn.status] || STATUS_STYLES.pending
            const StatusIcon = statusStyle.icon

            return (
              <div
                key={conn.id}
                onClick={() => navigate(`/cloud-sources/connections/${conn.id}`)}
                className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1 hover:shadow-elevation-2 transition-shadow cursor-pointer"
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-3xl">{PROVIDER_ICONS[conn.provider]}</span>
                    <div>
                      <h3 className="font-semibold text-primary-900 dark:text-gray-100">
                        {conn.display_name}
                      </h3>
                      <p className="text-sm text-secondary dark:text-gray-400 capitalize">
                        {conn.provider.replace('_', ' ')}
                      </p>
                    </div>
                  </div>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs ${statusStyle.bg} ${statusStyle.text}`}
                  >
                    <StatusIcon className="h-3 w-3" />
                    {conn.status}
                  </span>
                </div>

                <div className="mt-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                  {conn.oauth_email && (
                    <p className="text-sm text-secondary dark:text-gray-400 truncate">
                      {conn.oauth_email}
                    </p>
                  )}
                  {conn.server_url && (
                    <p className="text-sm text-secondary dark:text-gray-400 truncate font-mono">
                      {conn.server_url}
                    </p>
                  )}
                  <p className="text-xs text-secondary dark:text-gray-500 mt-2">
                    Created {formatDate(conn.created_at)}
                  </p>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-12 shadow-elevation-1 text-center">
          <CloudIcon className="h-16 w-16 text-gray-300 dark:text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-primary-900 dark:text-gray-100 mb-2">
            No Connections Yet
          </h3>
          <p className="text-secondary dark:text-gray-400 mb-6 max-w-md mx-auto">
            Connect your cloud storage accounts to start syncing documents into your RAG pipeline.
          </p>
          <button
            onClick={() => navigate('/cloud-sources')}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary text-white hover:bg-primary-700"
          >
            <PlusIcon className="h-5 w-5" />
            Add Your First Connection
          </button>
        </div>
      )}
    </div>
  )
}

// Create Sync Config Modal Component
interface CreateSyncConfigModalProps {
  connection: CloudConnection
  onClose: () => void
  onCreated: () => void
}

function CreateSyncConfigModal({ connection, onClose, onCreated }: CreateSyncConfigModalProps) {
  const [name, setName] = useState('')
  const [profileKey, setProfileKey] = useState('default')
  const [selectedPaths, setSelectedPaths] = useState<SourcePath[]>([])
  const [showFolderPicker, setShowFolderPicker] = useState(false)
  const [scheduleEnabled, setScheduleEnabled] = useState(false)
  const [scheduleFrequency, setScheduleFrequency] = useState<'hourly' | 'daily' | 'weekly'>('daily')
  const [scheduleHour, setScheduleHour] = useState(2)
  const [deleteRemoved, setDeleteRemoved] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // File type filters
  const [fileTypes, setFileTypes] = useState<string[]>([
    'pdf', 'docx', 'doc', 'txt', 'md', 'xlsx', 'xls', 'pptx', 'ppt'
  ])
  const [maxFileSizeMb, setMaxFileSizeMb] = useState(100)

  const handleFolderSelected = (folder: RemoteFolder) => {
    const newPath: SourcePath = {
      path: folder.path,
      remote_id: folder.id,
      include_subfolders: true,
      display_name: folder.name,
    }
    if (!selectedPaths.find((p) => p.remote_id === folder.id)) {
      setSelectedPaths([...selectedPaths, newPath])
    }
    setShowFolderPicker(false)
  }

  const handleRemovePath = (remoteId: string) => {
    setSelectedPaths(selectedPaths.filter((p) => p.remote_id !== remoteId))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || selectedPaths.length === 0) {
      setError('Please provide a name and select at least one folder')
      return
    }

    setIsSubmitting(true)
    setError(null)

    try {
      await cloudSourcesApi.createSyncConfig({
        connection_id: connection.id,
        profile_key: profileKey,
        name: name.trim(),
        source_paths: selectedPaths,
        filters: {
          file_types: fileTypes,
          exclude_patterns: [],
          max_file_size_mb: maxFileSizeMb,
        },
        schedule: {
          enabled: scheduleEnabled,
          frequency: scheduleFrequency,
          hour: scheduleHour,
        },
        delete_removed: deleteRemoved,
      })
      onCreated()
    } catch (err: any) {
      setError(err.message || 'Failed to create sync configuration')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-auto">
        <form onSubmit={handleSubmit}>
          <div className="p-6 border-b border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-100">
                Create Sync Configuration
              </h2>
              <button
                type="button"
                onClick={onClose}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              >
                <XMarkIcon className="h-6 w-6" />
              </button>
            </div>
            <p className="text-secondary dark:text-gray-400 mt-1">
              Configure which folders to sync from {connection.display_name}
            </p>
          </div>

          <div className="p-6 space-y-6">
            {/* Error */}
            {error && (
              <div className="rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-3">
                <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
              </div>
            )}

            {/* Name */}
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
                Configuration Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Project Documents"
                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:ring-2 focus:ring-primary focus:border-transparent"
              />
            </div>

            {/* Profile */}
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
                Target Profile
              </label>
              <input
                type="text"
                value={profileKey}
                onChange={(e) => setProfileKey(e.target.value)}
                placeholder="default"
                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              <p className="text-xs text-secondary dark:text-gray-400 mt-1">
                Documents will be indexed to this profile
              </p>
            </div>

            {/* Selected Folders */}
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
                Source Folders
              </label>
              {selectedPaths.length > 0 ? (
                <div className="space-y-2 mb-3">
                  {selectedPaths.map((path) => (
                    <div
                      key={path.remote_id}
                      className="flex items-center justify-between px-4 py-2 rounded-xl bg-gray-50 dark:bg-gray-700"
                    >
                      <div className="flex items-center gap-2">
                        <FolderIcon className="h-5 w-5 text-primary" />
                        <span className="text-primary-900 dark:text-gray-100">
                          {path.display_name || path.path}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRemovePath(path.remote_id)}
                        className="text-gray-400 hover:text-red-500"
                      >
                        <XMarkIcon className="h-5 w-5" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-secondary dark:text-gray-400 text-sm mb-3">
                  No folders selected
                </p>
              )}
              <button
                type="button"
                onClick={() => setShowFolderPicker(true)}
                className="flex items-center gap-2 px-4 py-2 rounded-xl border border-dashed border-gray-300 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 w-full justify-center"
              >
                <FolderIcon className="h-5 w-5" />
                Browse Folders
              </button>
            </div>

            {/* File Types */}
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
                File Types
              </label>
              <input
                type="text"
                value={fileTypes.join(', ')}
                onChange={(e) =>
                  setFileTypes(
                    e.target.value
                      .split(',')
                      .map((t) => t.trim().toLowerCase())
                      .filter(Boolean)
                  )
                }
                placeholder="pdf, docx, txt, md"
                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              <p className="text-xs text-secondary dark:text-gray-400 mt-1">
                Comma-separated list of file extensions to sync
              </p>
            </div>

            {/* Max File Size */}
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
                Max File Size (MB)
              </label>
              <input
                type="number"
                value={maxFileSizeMb}
                onChange={(e) => setMaxFileSizeMb(parseInt(e.target.value) || 100)}
                min={1}
                max={1000}
                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:ring-2 focus:ring-primary focus:border-transparent"
              />
            </div>

            {/* Schedule */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <label className="text-sm font-medium text-primary-900 dark:text-gray-100">
                  Automatic Sync
                </label>
                <button
                  type="button"
                  onClick={() => setScheduleEnabled(!scheduleEnabled)}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    scheduleEnabled ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      scheduleEnabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>

              {scheduleEnabled && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">
                      Frequency
                    </label>
                    <select
                      value={scheduleFrequency}
                      onChange={(e) =>
                        setScheduleFrequency(e.target.value as 'hourly' | 'daily' | 'weekly')
                      }
                      className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100"
                    >
                      <option value="hourly">Hourly</option>
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">
                      Hour (UTC)
                    </label>
                    <select
                      value={scheduleHour}
                      onChange={(e) => setScheduleHour(parseInt(e.target.value))}
                      className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100"
                    >
                      {Array.from({ length: 24 }, (_, i) => (
                        <option key={i} value={i}>
                          {i.toString().padStart(2, '0')}:00
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              )}
            </div>

            {/* Delete Removed */}
            <div className="flex items-center justify-between">
              <div>
                <label className="text-sm font-medium text-primary-900 dark:text-gray-100">
                  Delete Removed Files
                </label>
                <p className="text-xs text-secondary dark:text-gray-400">
                  Remove indexed documents when source files are deleted
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDeleteRemoved(!deleteRemoved)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  deleteRemoved ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    deleteRemoved ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          </div>

          <div className="p-6 border-t border-gray-200 dark:border-gray-700 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !name.trim() || selectedPaths.length === 0}
              className="px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {isSubmitting ? 'Creating...' : 'Create Configuration'}
            </button>
          </div>
        </form>

        {/* Folder Picker Modal */}
        {showFolderPicker && (
          <FolderPicker
            connectionId={connection.id}
            onSelect={handleFolderSelected}
            onClose={() => setShowFolderPicker(false)}
          />
        )}
      </div>
    </div>
  )
}
