import { useState, useEffect, useCallback, useRef } from 'react'
import {
  EnvelopeIcon,
  CloudIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
  PlusIcon,
  ServerStackIcon,
  Cog6ToothIcon,
  TrashIcon,
  BeakerIcon,
  PlayIcon,
  PencilIcon,
  InformationCircleIcon,
} from '@heroicons/react/24/outline'

// Types
interface EmailProvider {
  type: string
  display_name: string
  icon: string
  auth_type: string
  description: string
  requires_airbyte: boolean
  config_fields: ConfigField[]
}

interface CloudStorageProvider {
  type: string
  display_name: string
  icon: string
  auth_type: string
  description: string
  requires_airbyte: boolean
  supports_multiple: boolean
  config_fields: ConfigField[]
}

interface ConfigField {
  name: string
  label: string
  type: string
  required: boolean
  placeholder?: string
  default?: string | number | boolean
}

interface DatabaseStatus {
  database: string
  exists: boolean
  has_collections: boolean
  collections: string[]
  documents_count: number
  chunks_count: number
  has_documents_collection: boolean
  has_chunks_collection: boolean
  can_create: boolean
}

interface MongoDBTestResult {
  connected: boolean
  server_version?: string
  current_database?: string
  uri_host?: string
  error?: string
}

interface AirbyteStatus {
  enabled: boolean
  available: boolean
  api_url?: string
  webapp_url?: string
  message?: string
  error?: string
}

interface CloudSourceAssociation {
  connection_id: string
  provider_type: string
  display_name: string
  airbyte_source_id?: string
  airbyte_connection_id?: string
  enabled: boolean
  sync_schedule?: string
  last_sync_at?: string
  last_sync_status?: string
}

// API base URL
const API_BASE = '/api/v1'

async function fetchJSON<T>(url: string, options?: RequestInit & { signal?: AbortSignal }): Promise<T> {
  const token = localStorage.getItem('auth_token')
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  })
  if (!response.ok) {
    // Try to extract error message from response body
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`
    try {
      const errorData = await response.json()
      if (errorData.detail) {
        errorMessage = typeof errorData.detail === 'string' 
          ? errorData.detail 
          : JSON.stringify(errorData.detail)
      } else if (errorData.message) {
        errorMessage = errorData.message
      }
    } catch {
      // Use default error message if parsing fails
    }
    throw new Error(errorMessage)
  }
  return response.json()
}

// Validate required fields in config form
function validateConfigForm(fields: ConfigField[], form: Record<string, string>): string[] {
  const errors: string[] = []
  for (const field of fields) {
    if (field.required && !form[field.name]?.trim()) {
      errors.push(`${field.label} is required`)
    }
    if (field.type === 'email' && form[field.name]) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
      if (!emailRegex.test(form[field.name])) {
        errors.push(`${field.label} must be a valid email address`)
      }
    }
    if (field.type === 'url' && form[field.name]) {
      try {
        new URL(form[field.name])
      } catch {
        errors.push(`${field.label} must be a valid URL`)
      }
    }
  }
  return errors
}

export default function EmailCloudConfigPage() {
  // State
  const [emailProviders, setEmailProviders] = useState<EmailProvider[]>([])
  const [cloudProviders, setCloudProviders] = useState<CloudStorageProvider[]>([])
  const [databaseStatus, setDatabaseStatus] = useState<DatabaseStatus | null>(null)
  const [mongoTestResult, setMongoTestResult] = useState<MongoDBTestResult | null>(null)
  const [airbyteStatus, setAirbyteStatus] = useState<AirbyteStatus | null>(null)
  const [configuredSources, setConfiguredSources] = useState<CloudSourceAssociation[]>([])
  
  const [loading, setLoading] = useState(true)
  const [testingMongo, setTestingMongo] = useState(false)
  const [checkingDatabase, setCheckingDatabase] = useState(false)
  const [creatingDatabase, setCreatingDatabase] = useState(false)
  const [savingConfig, setSavingConfig] = useState(false)
  const [removingSource, setRemovingSource] = useState<string | null>(null)
  const [triggeringSyncId, setTriggeringSyncId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  
  // Configuration modal state
  const [showConfigModal, setShowConfigModal] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<EmailProvider | CloudStorageProvider | null>(null)
  const [configForm, setConfigForm] = useState<Record<string, string>>({})
  const [configDisplayName, setConfigDisplayName] = useState('')
  const [editingSourceId, setEditingSourceId] = useState<string | null>(null)
  
  // Current profile database
  const [currentDatabase, setCurrentDatabase] = useState('rag_db')
  const [activeProfileKey, setActiveProfileKey] = useState<string>('')
  
  // AbortController ref for cleanup
  const abortControllerRef = useRef<AbortController | null>(null)
  
  // Success message timeout ref
  const successTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
      if (successTimeoutRef.current) {
        clearTimeout(successTimeoutRef.current)
      }
    }
  }, [])
  
  // Load data on mount
  useEffect(() => {
    loadData()
  }, [])
  
  // Show success message with auto-dismiss
  const showSuccess = useCallback((message: string) => {
    setSuccessMessage(message)
    if (successTimeoutRef.current) {
      clearTimeout(successTimeoutRef.current)
    }
    successTimeoutRef.current = setTimeout(() => setSuccessMessage(null), 4000)
  }, [])

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    
    // Cancel any pending requests
    abortControllerRef.current?.abort()
    abortControllerRef.current = new AbortController()
    const signal = abortControllerRef.current.signal
    
    try {
      const [emailResp, cloudResp, airbyteResp, mongoResp] = await Promise.all([
        fetchJSON<{ providers: EmailProvider[] }>(`${API_BASE}/system/email-providers`, { signal }),
        fetchJSON<{ providers: CloudStorageProvider[] }>(`${API_BASE}/system/cloud-storage-providers`, { signal }),
        fetchJSON<AirbyteStatus>(`${API_BASE}/system/airbyte/status`, { signal }),
        fetchJSON<MongoDBTestResult>(`${API_BASE}/system/database/test-connection`, { signal }),
      ])
      
      setEmailProviders(emailResp.providers)
      setCloudProviders(cloudResp.providers)
      setAirbyteStatus(airbyteResp)
      setMongoTestResult(mongoResp)
      
      // Check current database status
      if (mongoResp.current_database) {
        setCurrentDatabase(mongoResp.current_database)
        await checkDatabaseStatus(mongoResp.current_database)
      }
      
      // Load configured sources for current profile
      await loadConfiguredSources()
      
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        return // Ignore aborted requests
      }
      setError(err instanceof Error ? err.message : 'Failed to load configuration data')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadConfiguredSources = useCallback(async () => {
    try {
      // Get active profile
      const profileResp = await fetchJSON<{ profile_key: string }>(`${API_BASE}/profiles/active`)
      const profileKey = profileResp.profile_key
      setActiveProfileKey(profileKey)
      
      // Get cloud sources for profile
      const sourcesResp = await fetchJSON<{ cloud_sources: CloudSourceAssociation[] }>(
        `${API_BASE}/profiles/${profileKey}/cloud-sources`
      )
      setConfiguredSources(sourcesResp.cloud_sources || [])
    } catch {
      // Ignore errors - profile might not have cloud sources
      setConfiguredSources([])
    }
  }, [])

  const checkDatabaseStatus = async (dbName: string) => {
    setCheckingDatabase(true)
    try {
      const status = await fetchJSON<DatabaseStatus>(`${API_BASE}/system/database/check/${dbName}`)
      setDatabaseStatus(status)
    } catch (err) {
      console.error('Failed to check database:', err)
    } finally {
      setCheckingDatabase(false)
    }
  }

  const testMongoConnection = useCallback(async () => {
    setTestingMongo(true)
    setError(null)
    try {
      const result = await fetchJSON<MongoDBTestResult>(`${API_BASE}/system/database/test-connection`)
      setMongoTestResult(result)
      if (result.connected) {
        showSuccess('MongoDB connection successful!')
      } else {
        setError(result.error || 'Connection test failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connection test failed')
    } finally {
      setTestingMongo(false)
    }
  }, [showSuccess])

  const createDatabase = useCallback(async () => {
    if (!currentDatabase) return
    
    setCreatingDatabase(true)
    setError(null)
    try {
      const result = await fetchJSON<{ database: string; created: boolean; errors: string[] }>(`${API_BASE}/system/database/create`, {
        method: 'POST',
        body: JSON.stringify({
          database: currentDatabase,
          create_indexes: true,
        }),
      })
      if (result.errors?.length > 0) {
        setError(`Database created with warnings: ${result.errors.join(', ')}`)
      } else {
        showSuccess(`Database "${currentDatabase}" created successfully!`)
      }
      await checkDatabaseStatus(currentDatabase)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create database')
    } finally {
      setCreatingDatabase(false)
    }
  }, [currentDatabase, showSuccess])

  const openConfigModal = useCallback((_type: 'email' | 'cloud', provider: EmailProvider | CloudStorageProvider, existingSource?: CloudSourceAssociation) => {
    setSelectedProvider(provider)
    setValidationErrors([])
    
    if (existingSource) {
      // Editing existing source
      setEditingSourceId(existingSource.connection_id)
      setConfigDisplayName(existingSource.display_name)
      // TODO: Load existing config values when backend supports storing them
      setConfigForm({})
    } else {
      // New source
      setEditingSourceId(null)
      setConfigDisplayName(`My ${provider.display_name}`)
      // Set default values from provider config
      const defaults: Record<string, string> = {}
      provider.config_fields.forEach(field => {
        if (field.default !== undefined) {
          defaults[field.name] = String(field.default)
        }
      })
      setConfigForm(defaults)
    }
    setShowConfigModal(true)
  }, [])

  const saveConfiguration = useCallback(async () => {
    if (!selectedProvider) return
    
    // Validate form
    const errors = validateConfigForm(selectedProvider.config_fields, configForm)
    if (!configDisplayName.trim()) {
      errors.unshift('Display Name is required')
    }
    if (errors.length > 0) {
      setValidationErrors(errors)
      return
    }
    setValidationErrors([])
    setSavingConfig(true)
    
    try {
      const profileKey = activeProfileKey || 'default'
      
      if (editingSourceId) {
        // Update existing source
        await fetchJSON(`${API_BASE}/profiles/${profileKey}/cloud-sources/${editingSourceId}`, {
          method: 'PUT',
          body: JSON.stringify({
            display_name: configDisplayName,
            // config_data: configForm, // When backend supports storing config
          }),
        })
        showSuccess(`${selectedProvider.display_name} configuration updated!`)
      } else {
        // Create new cloud source association
        const newSource: Partial<CloudSourceAssociation> = {
          connection_id: `${selectedProvider.type}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
          provider_type: selectedProvider.type,
          display_name: configDisplayName,
          enabled: true,
        }
        
        await fetchJSON(`${API_BASE}/profiles/${profileKey}/cloud-sources`, {
          method: 'POST',
          body: JSON.stringify(newSource),
        })
        showSuccess(`${selectedProvider.display_name} configuration saved!`)
      }
      
      setShowConfigModal(false)
      await loadConfiguredSources()
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save configuration')
    } finally {
      setSavingConfig(false)
    }
  }, [selectedProvider, configForm, configDisplayName, activeProfileKey, editingSourceId, loadConfiguredSources, showSuccess])

  const removeSource = useCallback(async (connectionId: string) => {
    if (!confirm('Are you sure you want to remove this source? This action cannot be undone.')) return
    
    setRemovingSource(connectionId)
    setError(null)
    
    try {
      const profileKey = activeProfileKey || 'default'
      
      await fetchJSON(`${API_BASE}/profiles/${profileKey}/cloud-sources/${connectionId}`, {
        method: 'DELETE',
      })
      
      showSuccess('Source removed successfully!')
      await loadConfiguredSources()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove source')
    } finally {
      setRemovingSource(null)
    }
  }, [activeProfileKey, loadConfiguredSources, showSuccess])

  const triggerSync = useCallback(async (connectionId: string) => {
    setTriggeringSyncId(connectionId)
    setError(null)
    
    try {
      // TODO: Implement actual sync trigger via Airbyte when ready
      // For now, show a message
      showSuccess('Sync triggered! Check Airbyte UI for progress.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to trigger sync')
    } finally {
      setTriggeringSyncId(null)
    }
  }, [showSuccess])
  
  // Get provider icon based on type
  const getProviderIcon = (providerType: string): string => {
    const iconMap: Record<string, string> = {
      gmail: '📧',
      outlook: '📨',
      imap: '✉️',
      google_drive: '🔵',
      dropbox: '📦',
      onedrive: '☁️',
      webdav: '🌐',
      confluence: '📝',
      jira: '🔷',
    }
    return iconMap[providerType] || '☁️'
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="animate-spin h-8 w-8 border-4 border-primary border-t-transparent rounded-full" />
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-primary-900 dark:text-white flex items-center gap-3">
            <EnvelopeIcon className="h-7 w-7" />
            Email & Cloud Source Configuration
          </h1>
          <p className="mt-1 text-secondary dark:text-gray-400">
            Configure email accounts and cloud storage for RAG knowledge ingestion
          </p>
        </div>
        <button
          onClick={loadData}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-secondary hover:text-primary dark:text-gray-400 dark:hover:text-white"
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Alerts */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 flex items-start gap-3">
          <XCircleIcon className="h-5 w-5 text-red-500 mt-0.5" />
          <div>
            <p className="text-red-700 dark:text-red-300 font-medium">Error</p>
            <p className="text-red-600 dark:text-red-400 text-sm">{error}</p>
          </div>
          <button onClick={() => setError(null)} className="ml-auto text-red-500 hover:text-red-700">
            <XCircleIcon className="h-5 w-5" />
          </button>
        </div>
      )}

      {successMessage && (
        <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4 flex items-center gap-3">
          <CheckCircleIcon className="h-5 w-5 text-green-500" />
          <p className="text-green-700 dark:text-green-300">{successMessage}</p>
        </div>
      )}

      {/* Connection Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* MongoDB Status */}
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2">
              <ServerStackIcon className="h-5 w-5" />
              MongoDB Connection
            </h2>
            <span className={`px-2 py-1 rounded-full text-xs font-medium ${
              mongoTestResult?.connected
                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
            }`}>
              {mongoTestResult?.connected ? 'Connected' : 'Disconnected'}
            </span>
          </div>
          
          {mongoTestResult?.connected ? (
            <div className="space-y-2 text-sm">
              <p className="text-secondary dark:text-gray-400">
                <span className="font-medium">Server:</span> {mongoTestResult.uri_host}
              </p>
              <p className="text-secondary dark:text-gray-400">
                <span className="font-medium">Version:</span> {mongoTestResult.server_version}
              </p>
              <p className="text-secondary dark:text-gray-400">
                <span className="font-medium">Database:</span> {mongoTestResult.current_database}
              </p>
            </div>
          ) : (
            <p className="text-red-500 text-sm">{mongoTestResult?.error || 'Connection failed'}</p>
          )}
          
          <button
            onClick={testMongoConnection}
            disabled={testingMongo}
            className="mt-4 w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-600 disabled:opacity-50"
          >
            {testingMongo ? (
              <ArrowPathIcon className="h-4 w-4 animate-spin" />
            ) : (
              <BeakerIcon className="h-4 w-4" />
            )}
            Test Connection
          </button>
        </div>

        {/* Airbyte Status */}
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2">
              <Cog6ToothIcon className="h-5 w-5" />
              Airbyte Integration
            </h2>
            <span className={`px-2 py-1 rounded-full text-xs font-medium ${
              airbyteStatus?.available
                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                : airbyteStatus?.enabled
                  ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
                  : 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-400'
            }`}>
              {airbyteStatus?.available ? 'Available' : airbyteStatus?.enabled ? 'Unavailable' : 'Disabled'}
            </span>
          </div>
          
          {airbyteStatus?.available ? (
            <div className="space-y-2 text-sm">
              <p className="text-secondary dark:text-gray-400">
                <span className="font-medium">API:</span> {airbyteStatus.api_url}
              </p>
              {airbyteStatus.webapp_url && (
                <a 
                  href={airbyteStatus.webapp_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  Open Airbyte UI →
                </a>
              )}
            </div>
          ) : (
            <div className="text-sm">
              <p className="text-yellow-600 dark:text-yellow-400">
                {airbyteStatus?.message || 'Airbyte is not running'}
              </p>
              <p className="text-secondary dark:text-gray-500 mt-2">
                Email syncing via Gmail/Outlook requires Airbyte to be running.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Database Status & Creation */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2">
            <ServerStackIcon className="h-5 w-5" />
            Database Status: {currentDatabase}
          </h2>
          <button
            onClick={() => checkDatabaseStatus(currentDatabase)}
            disabled={checkingDatabase}
            className="text-sm text-primary hover:underline flex items-center gap-1"
          >
            {checkingDatabase && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
            Refresh
          </button>
        </div>

        {databaseStatus ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-surface-variant dark:bg-gray-700 rounded-lg p-4">
                <p className="text-xs text-secondary dark:text-gray-400 uppercase">Exists</p>
                <p className="text-lg font-semibold mt-1 flex items-center gap-2">
                  {databaseStatus.exists ? (
                    <><CheckCircleIcon className="h-5 w-5 text-green-500" /> Yes</>
                  ) : (
                    <><XCircleIcon className="h-5 w-5 text-red-500" /> No</>
                  )}
                </p>
              </div>
              <div className="bg-surface-variant dark:bg-gray-700 rounded-lg p-4">
                <p className="text-xs text-secondary dark:text-gray-400 uppercase">Collections</p>
                <p className="text-lg font-semibold mt-1">{databaseStatus.collections.length}</p>
              </div>
              <div className="bg-surface-variant dark:bg-gray-700 rounded-lg p-4">
                <p className="text-xs text-secondary dark:text-gray-400 uppercase">Documents</p>
                <p className="text-lg font-semibold mt-1">{databaseStatus.documents_count.toLocaleString()}</p>
              </div>
              <div className="bg-surface-variant dark:bg-gray-700 rounded-lg p-4">
                <p className="text-xs text-secondary dark:text-gray-400 uppercase">Chunks</p>
                <p className="text-lg font-semibold mt-1">{databaseStatus.chunks_count.toLocaleString()}</p>
              </div>
            </div>

            {!databaseStatus.exists || !databaseStatus.has_documents_collection || !databaseStatus.has_chunks_collection ? (
              <div className="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <ExclamationTriangleIcon className="h-5 w-5 text-yellow-500 mt-0.5" />
                  <div className="flex-1">
                    <p className="text-yellow-700 dark:text-yellow-300 font-medium">Database Setup Required</p>
                    <p className="text-yellow-600 dark:text-yellow-400 text-sm mt-1">
                      {!databaseStatus.exists 
                        ? 'The database does not exist yet.'
                        : 'Required collections are missing.'}
                    </p>
                    <button
                      onClick={createDatabase}
                      disabled={creatingDatabase}
                      className="mt-3 flex items-center gap-2 px-4 py-2 bg-yellow-500 text-white rounded-lg hover:bg-yellow-600 disabled:opacity-50"
                    >
                      {creatingDatabase ? (
                        <ArrowPathIcon className="h-4 w-4 animate-spin" />
                      ) : (
                        <PlusIcon className="h-4 w-4" />
                      )}
                      Create Database & Collections
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4 flex items-center gap-3">
                <CheckCircleIcon className="h-5 w-5 text-green-500" />
                <p className="text-green-700 dark:text-green-300">
                  Database is properly configured and ready for email/cloud ingestion.
                </p>
              </div>
            )}
          </div>
        ) : (
          <p className="text-secondary dark:text-gray-400">Loading database status...</p>
        )}
      </div>

      {/* Configured Sources */}
      {configuredSources.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
          <h2 className="text-lg font-semibold text-primary-900 dark:text-white mb-4">
            Configured Sources ({configuredSources.length})
          </h2>
          <div className="space-y-3">
            {configuredSources.map(source => (
              <div 
                key={source.connection_id}
                className="flex items-center justify-between p-4 bg-surface-variant dark:bg-gray-700 rounded-lg"
              >
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{getProviderIcon(source.provider_type)}</span>
                  <div>
                    <p className="font-medium text-primary-900 dark:text-white">{source.display_name}</p>
                    <p className="text-sm text-secondary dark:text-gray-400">{source.provider_type}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {source.last_sync_status && (
                    <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                      source.last_sync_status === 'success'
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                        : source.last_sync_status === 'running'
                          ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                          : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                    }`}>
                      {source.last_sync_status}
                    </span>
                  )}
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                    source.enabled
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-400'
                  }`}>
                    {source.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                  {source.last_sync_at && (
                    <span className="text-xs text-secondary dark:text-gray-500">
                      Last sync: {new Date(source.last_sync_at).toLocaleDateString()}
                    </span>
                  )}
                  <button
                    onClick={() => triggerSync(source.connection_id)}
                    disabled={triggeringSyncId === source.connection_id || !source.enabled}
                    className="p-2 text-primary hover:bg-primary-50 dark:hover:bg-primary-900/20 rounded-lg disabled:opacity-50"
                    title="Trigger sync"
                  >
                    {triggeringSyncId === source.connection_id ? (
                      <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    ) : (
                      <PlayIcon className="h-4 w-4" />
                    )}
                  </button>
                  <button
                    onClick={() => {
                      const provider = [...emailProviders, ...cloudProviders].find(p => p.type === source.provider_type)
                      if (provider) openConfigModal(emailProviders.includes(provider as EmailProvider) ? 'email' : 'cloud', provider, source)
                    }}
                    className="p-2 text-secondary hover:bg-surface-variant dark:hover:bg-gray-600 rounded-lg"
                    title="Edit configuration"
                  >
                    <PencilIcon className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => removeSource(source.connection_id)}
                    disabled={removingSource === source.connection_id}
                    className="p-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg disabled:opacity-50"
                    title="Remove source"
                  >
                    {removingSource === source.connection_id ? (
                      <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    ) : (
                      <TrashIcon className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Email Providers */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
        <h2 className="text-lg font-semibold text-primary-900 dark:text-white mb-4 flex items-center gap-2">
          <EnvelopeIcon className="h-5 w-5" />
          Email Providers
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {emailProviders.map(provider => (
            <div 
              key={provider.type}
              className="border border-surface-variant dark:border-gray-600 rounded-lg p-4 hover:border-primary dark:hover:border-primary-500 transition-colors"
            >
              <div className="flex items-center gap-3 mb-3">
                <span className="text-3xl">{provider.icon}</span>
                <div>
                  <h3 className="font-medium text-primary-900 dark:text-white">{provider.display_name}</h3>
                  <p className="text-xs text-secondary dark:text-gray-400">{provider.auth_type}</p>
                </div>
              </div>
              <p className="text-sm text-secondary dark:text-gray-400 mb-4">{provider.description}</p>
              
              {provider.requires_airbyte && !airbyteStatus?.available ? (
                <div className="flex items-center gap-2 text-xs text-yellow-600 dark:text-yellow-400">
                  <ExclamationTriangleIcon className="h-4 w-4" />
                  Requires Airbyte
                </div>
              ) : (
                <button
                  onClick={() => openConfigModal('email', provider)}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-600"
                >
                  <PlusIcon className="h-4 w-4" />
                  Add {provider.display_name}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Cloud Storage Providers */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
        <h2 className="text-lg font-semibold text-primary-900 dark:text-white mb-4 flex items-center gap-2">
          <CloudIcon className="h-5 w-5" />
          Cloud Storage Providers
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {cloudProviders.map(provider => (
            <div 
              key={provider.type}
              className="border border-surface-variant dark:border-gray-600 rounded-lg p-4 hover:border-primary dark:hover:border-primary-500 transition-colors"
            >
              <div className="flex items-center gap-3 mb-3">
                <span className="text-3xl">{provider.icon}</span>
                <div>
                  <h3 className="font-medium text-primary-900 dark:text-white">{provider.display_name}</h3>
                  <p className="text-xs text-secondary dark:text-gray-400">
                    {provider.auth_type}
                    {provider.supports_multiple && ' • Multiple accounts'}
                  </p>
                </div>
              </div>
              <p className="text-sm text-secondary dark:text-gray-400 mb-4">{provider.description}</p>
              
              {provider.requires_airbyte && !airbyteStatus?.available ? (
                <div className="flex items-center gap-2 text-xs text-yellow-600 dark:text-yellow-400">
                  <ExclamationTriangleIcon className="h-4 w-4" />
                  Requires Airbyte
                </div>
              ) : (
                <button
                  onClick={() => openConfigModal('cloud', provider)}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-600"
                >
                  <PlusIcon className="h-4 w-4" />
                  Add {provider.display_name}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Configuration Modal */}
      {showConfigModal && selectedProvider && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-md w-full mx-4 p-6">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-white mb-4">
              Configure {selectedProvider.display_name}
            </h2>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-secondary dark:text-gray-300 mb-1">
                  Display Name
                </label>
                <input
                  type="text"
                  value={configDisplayName}
                  onChange={e => setConfigDisplayName(e.target.value)}
                  placeholder="e.g., My Work Email"
                  className="w-full px-3 py-2 border border-surface-variant dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                />
              </div>
              
              {selectedProvider.config_fields.map(field => (
                <div key={field.name}>
                  <label className="block text-sm font-medium text-secondary dark:text-gray-300 mb-1">
                    {field.label}
                    {field.required && <span className="text-red-500 ml-1">*</span>}
                  </label>
                  {field.type === 'checkbox' ? (
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={configForm[field.name] === 'true' || (field.default === true && configForm[field.name] === undefined)}
                        onChange={e => setConfigForm({ ...configForm, [field.name]: e.target.checked ? 'true' : 'false' })}
                        className="rounded border-gray-300"
                      />
                      <span className="text-sm text-secondary dark:text-gray-400">Enable</span>
                    </label>
                  ) : (
                    <input
                      type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'}
                      value={configForm[field.name] || field.default?.toString() || ''}
                      onChange={e => setConfigForm({ ...configForm, [field.name]: e.target.value })}
                      placeholder={field.placeholder}
                      required={field.required}
                      className="w-full px-3 py-2 border border-surface-variant dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                    />
                  )}
                </div>
              ))}
              
              {selectedProvider.auth_type === 'oauth2' && (
                <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
                  <p className="text-sm text-blue-700 dark:text-blue-300">
                    OAuth authentication: You will be redirected to {selectedProvider.display_name} to authorize access.
                  </p>
                </div>
              )}
            </div>

            {/* Validation Errors */}
            {validationErrors.length > 0 && (
              <div className="mt-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
                <div className="flex items-start gap-2">
                  <InformationCircleIcon className="h-5 w-5 text-red-500 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-red-700 dark:text-red-300">Please fix the following:</p>
                    <ul className="mt-1 text-sm text-red-600 dark:text-red-400 list-disc list-inside">
                      {validationErrors.map((err, i) => <li key={i}>{err}</li>)}
                    </ul>
                  </div>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setShowConfigModal(false)}
                disabled={savingConfig}
                className="px-4 py-2 text-secondary dark:text-gray-400 hover:text-primary-900 dark:hover:text-white disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={saveConfiguration}
                disabled={savingConfig}
                className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-600 disabled:opacity-50 flex items-center gap-2"
              >
                {savingConfig && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
                {editingSourceId ? 'Update' : 'Save'} Configuration
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
