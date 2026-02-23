import { useState, useEffect, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArchiveBoxIcon,
  ArrowPathIcon,
  CloudArrowUpIcon,
  CloudArrowDownIcon,
  TrashIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  XCircleIcon,
  ClockIcon,
  ServerIcon,
  Cog6ToothIcon,
  DocumentDuplicateIcon,
  FolderIcon,
  InformationCircleIcon,
  ChevronDownIcon,
  ChevronUpIcon,
} from '@heroicons/react/24/outline'
import {
  backupsApi,
  profilesApi,
  BackupMetadata,
  BackupConfig,
  StorageStats,
  BackupType,
  BackupStatus,
  RestoreMode,
  BackupProgress,
  ProfileListResponse,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'

// Format bytes to human readable
function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

// Format date
function formatDate(dateStr: string | undefined): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString()
}

// Format relative time
function formatRelativeTime(dateStr: string | undefined): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)
  
  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return formatDate(dateStr)
}

// Get status color
function getStatusColor(status: BackupStatus): string {
  switch (status) {
    case 'completed':
      return 'text-green-600 dark:text-green-400'
    case 'in_progress':
      return 'text-blue-600 dark:text-blue-400'
    case 'pending':
      return 'text-yellow-600 dark:text-yellow-400'
    case 'failed':
      return 'text-red-600 dark:text-red-400'
    default:
      return 'text-gray-600 dark:text-gray-400'
  }
}

// Get status icon
function getStatusIcon(status: BackupStatus) {
  switch (status) {
    case 'completed':
      return <CheckCircleIcon className="h-5 w-5 text-green-500" />
    case 'in_progress':
      return <ArrowPathIcon className="h-5 w-5 text-blue-500 animate-spin" />
    case 'pending':
      return <ClockIcon className="h-5 w-5 text-yellow-500" />
    case 'failed':
      return <XCircleIcon className="h-5 w-5 text-red-500" />
    default:
      return null
  }
}

// Get backup type label
function getBackupTypeLabel(type: BackupType): string {
  switch (type) {
    case 'full':
      return 'Full'
    case 'incremental':
      return 'Incremental'
    case 'checkpoint':
      return 'Checkpoint'
    case 'post_ingestion':
      return 'Post-Ingestion'
    default:
      return type
  }
}

// Get backup type color
function getBackupTypeColor(type: BackupType): string {
  switch (type) {
    case 'full':
      return 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300'
    case 'incremental':
      return 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300'
    case 'checkpoint':
      return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300'
    case 'post_ingestion':
      return 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300'
    default:
      return 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'
  }
}

export default function BackupManagementPage() {
  const { t } = useTranslation()
  const { user } = useAuth()
  
  // State
  const [backups, setBackups] = useState<BackupMetadata[]>([])
  const [config, setConfig] = useState<BackupConfig | null>(null)
  const [storageStats, setStorageStats] = useState<StorageStats | null>(null)
  const [profiles, setProfiles] = useState<ProfileListResponse | null>(null)
  const [progress, setProgress] = useState<BackupProgress | null>(null)
  
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [restoring, setRestoring] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [savingConfig, setSavingConfig] = useState(false)
  
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  
  // Form state
  const [selectedBackupType, setSelectedBackupType] = useState<BackupType>('full')
  const [selectedProfile, setSelectedProfile] = useState<string>('')
  const [backupName, setBackupName] = useState('')
  const [includeEmbeddings, setIncludeEmbeddings] = useState(true)
  const [includeSystemCollections, setIncludeSystemCollections] = useState(true)
  
  // Restore form state
  const [showRestoreModal, setShowRestoreModal] = useState(false)
  const [selectedBackupForRestore, setSelectedBackupForRestore] = useState<BackupMetadata | null>(null)
  const [restoreMode, setRestoreMode] = useState<RestoreMode>('full')
  const [skipUsers, setSkipUsers] = useState(false)
  const [skipSessions, setSkipSessions] = useState(false)
  
  // Filter state
  const [filterType, setFilterType] = useState<BackupType | ''>('')
  const [filterProfile, setFilterProfile] = useState<string>('')
  
  // Expanded rows
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())
  
  // Settings panel
  const [showSettings, setShowSettings] = useState(false)
  const [configForm, setConfigForm] = useState<Partial<BackupConfig>>({})
  
  // Load data
  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    
    try {
      const [backupsRes, configRes, storageRes, profilesRes] = await Promise.all([
        backupsApi.list(filterProfile || undefined, filterType || undefined),
        backupsApi.getConfig(),
        backupsApi.getStorageStats(),
        profilesApi.list(),
      ])
      
      setBackups(backupsRes.backups)
      setConfig(configRes)
      setConfigForm(configRes)
      setStorageStats(storageRes)
      setProfiles(profilesRes)
      
      // Check for in-progress backup
      const progressRes = await backupsApi.getStatus()
      setProgress(progressRes)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load backup data')
    } finally {
      setLoading(false)
    }
  }, [filterType, filterProfile])
  
  useEffect(() => {
    loadData()
  }, [loadData])
  
  // Poll progress while backup is in progress
  useEffect(() => {
    if (!progress || progress.status !== 'in_progress') return
    
    const interval = setInterval(async () => {
      try {
        const progressRes = await backupsApi.getStatus()
        setProgress(progressRes)
        
        if (!progressRes || progressRes.status !== 'in_progress') {
          clearInterval(interval)
          loadData()
        }
      } catch {
        clearInterval(interval)
      }
    }, 2000)
    
    return () => clearInterval(interval)
  }, [progress, loadData])
  
  // Show success message with auto-dismiss
  const showSuccess = useCallback((message: string) => {
    setSuccessMessage(message)
    setTimeout(() => setSuccessMessage(null), 5000)
  }, [])
  
  // Create backup
  const handleCreateBackup = useCallback(async () => {
    setCreating(true)
    setError(null)
    
    try {
      const result = await backupsApi.create({
        backup_type: selectedBackupType,
        profile_key: selectedProfile || undefined,
        name: backupName || undefined,
        include_embeddings: includeEmbeddings,
        include_system_collections: includeSystemCollections,
      })
      
      showSuccess(`Backup ${result.backup_id} created successfully`)
      setBackupName('')
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create backup')
    } finally {
      setCreating(false)
    }
  }, [selectedBackupType, selectedProfile, backupName, includeEmbeddings, includeSystemCollections, showSuccess, loadData])
  
  // Restore from backup
  const handleRestore = useCallback(async () => {
    if (!selectedBackupForRestore) return
    
    setRestoring(selectedBackupForRestore.backup_id)
    setError(null)
    
    try {
      const result = await backupsApi.restore(selectedBackupForRestore.backup_id, {
        backup_id: selectedBackupForRestore.backup_id,
        restore_mode: restoreMode,
        skip_users: skipUsers,
        skip_sessions: skipSessions,
      })
      
      if (result.success) {
        showSuccess(`Restore completed: ${result.collections_restored.length} collections, ${Object.values(result.documents_restored).reduce((a, b) => a + b, 0)} documents`)
      } else {
        setError(result.error_message || 'Restore failed')
      }
      
      setShowRestoreModal(false)
      setSelectedBackupForRestore(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to restore backup')
    } finally {
      setRestoring(null)
    }
  }, [selectedBackupForRestore, restoreMode, skipUsers, skipSessions, showSuccess])
  
  // Delete backup
  const handleDelete = useCallback(async (backupId: string) => {
    if (!confirm('Are you sure you want to delete this backup? This cannot be undone.')) return
    
    setDeleting(backupId)
    setError(null)
    
    try {
      await backupsApi.delete(backupId)
      showSuccess('Backup deleted successfully')
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete backup')
    } finally {
      setDeleting(null)
    }
  }, [showSuccess, loadData])
  
  // Save config
  const handleSaveConfig = useCallback(async () => {
    setSavingConfig(true)
    setError(null)
    
    try {
      const result = await backupsApi.updateConfig(configForm)
      setConfig(result)
      showSuccess('Configuration saved successfully')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save configuration')
    } finally {
      setSavingConfig(false)
    }
  }, [configForm, showSuccess])
  
  // Toggle row expansion
  const toggleRow = useCallback((backupId: string) => {
    setExpandedRows(prev => {
      const next = new Set(prev)
      if (next.has(backupId)) {
        next.delete(backupId)
      } else {
        next.add(backupId)
      }
      return next
    })
  }, [])
  
  // Open restore modal
  const openRestoreModal = useCallback((backup: BackupMetadata) => {
    setSelectedBackupForRestore(backup)
    setRestoreMode('full')
    setSkipUsers(false)
    setSkipSessions(false)
    setShowRestoreModal(true)
  }, [])
  
  // Filtered backups
  const filteredBackups = useMemo(() => {
    return backups.filter(backup => {
      if (filterType && backup.backup_type !== filterType) return false
      if (filterProfile && backup.profile_key !== filterProfile) return false
      return true
    })
  }, [backups, filterType, filterProfile])
  
  // Storage usage percentage
  const storageUsagePercent = useMemo(() => {
    if (!storageStats || !storageStats.available_space_bytes) return null
    const total = storageStats.total_size_bytes + storageStats.available_space_bytes
    return (storageStats.total_size_bytes / total) * 100
  }, [storageStats])

  return (
    <div className="container mx-auto px-4 py-8 max-w-7xl">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-primary-900 dark:text-white flex items-center gap-3">
          <ArchiveBoxIcon className="h-8 w-8" />
          {t('backup.title', 'Backup & Restore')}
        </h1>
        <p className="text-secondary dark:text-gray-400 mt-1">
          {t('backup.subtitle', 'Manage database backups and recovery')}
        </p>
      </div>
      
      {/* Messages */}
      {error && (
        <div className="mb-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-center gap-2 text-red-700 dark:text-red-400">
          <ExclamationTriangleIcon className="h-5 w-5 flex-shrink-0" />
          {error}
        </div>
      )}
      
      {successMessage && (
        <div className="mb-4 p-4 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg flex items-center gap-2 text-green-700 dark:text-green-400">
          <CheckCircleIcon className="h-5 w-5 flex-shrink-0" />
          {successMessage}
        </div>
      )}
      
      {/* Progress indicator */}
      {progress && progress.status === 'in_progress' && (
        <div className="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
          <div className="flex items-center gap-3 mb-2">
            <ArrowPathIcon className="h-5 w-5 text-blue-500 animate-spin" />
            <span className="font-medium text-blue-700 dark:text-blue-300">
              Backup in progress: {progress.backup_id}
            </span>
          </div>
          <div className="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-2 mb-2">
            <div
              className="bg-blue-500 h-2 rounded-full transition-all duration-300"
              style={{ width: `${progress.progress_percent}%` }}
            />
          </div>
          <div className="text-sm text-blue-600 dark:text-blue-400">
            {progress.message || `${progress.collections_completed}/${progress.total_collections} collections - ${Math.round(progress.progress_percent)}%`}
          </div>
        </div>
      )}
      
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column - Create backup & Stats */}
        <div className="lg:col-span-1 space-y-6">
          {/* Storage Stats Card */}
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2 mb-4">
              <ServerIcon className="h-5 w-5" />
              {t('backup.storage.title', 'Storage')}
            </h2>
            
            {storageStats ? (
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-secondary dark:text-gray-400">Used</span>
                    <span className="font-medium">{formatBytes(storageStats.total_size_bytes)}</span>
                  </div>
                  {storageUsagePercent !== null && (
                    <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${storageUsagePercent > 80 ? 'bg-red-500' : storageUsagePercent > 60 ? 'bg-yellow-500' : 'bg-green-500'}`}
                        style={{ width: `${Math.min(storageUsagePercent, 100)}%` }}
                      />
                    </div>
                  )}
                </div>
                
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="text-secondary dark:text-gray-400">Total Backups</div>
                    <div className="font-semibold text-lg">{storageStats.total_backups}</div>
                  </div>
                  {storageStats.available_space_bytes && (
                    <div>
                      <div className="text-secondary dark:text-gray-400">Available</div>
                      <div className="font-semibold text-lg">{formatBytes(storageStats.available_space_bytes)}</div>
                    </div>
                  )}
                </div>
                
                {Object.keys(storageStats.backups_by_type).length > 0 && (
                  <div className="pt-3 border-t border-gray-200 dark:border-gray-700">
                    <div className="text-sm text-secondary dark:text-gray-400 mb-2">By Type</div>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(storageStats.backups_by_type).map(([type, count]) => (
                        <span
                          key={type}
                          className={`px-2 py-1 rounded text-xs font-medium ${getBackupTypeColor(type as BackupType)}`}
                        >
                          {getBackupTypeLabel(type as BackupType)}: {count}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="animate-pulse space-y-3">
                <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded" />
                <div className="h-8 bg-gray-200 dark:bg-gray-700 rounded" />
              </div>
            )}
          </div>
          
          {/* Create Backup Card */}
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md p-6 border border-surface-variant dark:border-gray-700">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2 mb-4">
              <CloudArrowUpIcon className="h-5 w-5" />
              {t('backup.createBackup', 'Create Backup')}
            </h2>
            
            <div className="space-y-4">
              {/* Backup Type */}
              <div>
                <label className="block text-sm font-medium text-secondary dark:text-gray-300 mb-1">
                  Backup Type
                </label>
                <select
                  value={selectedBackupType}
                  onChange={(e) => setSelectedBackupType(e.target.value as BackupType)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                >
                  <option value="full">Full Backup</option>
                  <option value="incremental">Incremental</option>
                  <option value="checkpoint">Checkpoint</option>
                </select>
              </div>
              
              {/* Profile */}
              <div>
                <label className="block text-sm font-medium text-secondary dark:text-gray-300 mb-1">
                  Profile
                </label>
                <select
                  value={selectedProfile}
                  onChange={(e) => setSelectedProfile(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                >
                  <option value="">Active Profile</option>
                  {profiles && Object.entries(profiles.profiles).map(([key, profile]) => (
                    <option key={key} value={key}>
                      {profile.name} ({key})
                    </option>
                  ))}
                </select>
              </div>
              
              {/* Name (for checkpoints) */}
              {selectedBackupType === 'checkpoint' && (
                <div>
                  <label className="block text-sm font-medium text-secondary dark:text-gray-300 mb-1">
                    Checkpoint Name
                  </label>
                  <input
                    type="text"
                    value={backupName}
                    onChange={(e) => setBackupName(e.target.value)}
                    placeholder="e.g., Before update"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                  />
                </div>
              )}
              
              {/* Options */}
              {selectedBackupType === 'full' && (
                <div className="space-y-2">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={includeEmbeddings}
                      onChange={(e) => setIncludeEmbeddings(e.target.checked)}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                    <span className="text-sm text-secondary dark:text-gray-300">Include embeddings</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={includeSystemCollections}
                      onChange={(e) => setIncludeSystemCollections(e.target.checked)}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                    <span className="text-sm text-secondary dark:text-gray-300">Include system data (users, sessions)</span>
                  </label>
                </div>
              )}
              
              <button
                onClick={handleCreateBackup}
                disabled={creating || (progress?.status === 'in_progress')}
                className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {creating ? (
                  <>
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                    Creating...
                  </>
                ) : (
                  <>
                    <CloudArrowUpIcon className="h-5 w-5" />
                    {t('backup.createBackup', 'Create Backup')}
                  </>
                )}
              </button>
            </div>
          </div>
          
          {/* Settings Card */}
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md border border-surface-variant dark:border-gray-700">
            <button
              onClick={() => setShowSettings(!showSettings)}
              className="w-full p-4 flex items-center justify-between text-left"
            >
              <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2">
                <Cog6ToothIcon className="h-5 w-5" />
                {t('backup.settings.title', 'Settings')}
              </h2>
              {showSettings ? (
                <ChevronUpIcon className="h-5 w-5 text-gray-500" />
              ) : (
                <ChevronDownIcon className="h-5 w-5 text-gray-500" />
              )}
            </button>
            
            {showSettings && config && (
              <div className="px-6 pb-6 space-y-4 border-t border-gray-200 dark:border-gray-700 pt-4">
                <label className="flex items-center justify-between cursor-pointer">
                  <span className="text-sm text-secondary dark:text-gray-300">
                    {t('backup.settings.autoBackup', 'Auto-backup after ingestion')}
                  </span>
                  <input
                    type="checkbox"
                    checked={configForm.auto_backup_after_ingestion ?? config.auto_backup_after_ingestion}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, auto_backup_after_ingestion: e.target.checked }))}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                </label>
                
                <div>
                  <label className="block text-sm text-secondary dark:text-gray-300 mb-1">
                    {t('backup.settings.retention', 'Retention (days)')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={configForm.retention_days ?? config.retention_days}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, retention_days: parseInt(e.target.value) }))}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                  />
                </div>
                
                <div>
                  <label className="block text-sm text-secondary dark:text-gray-300 mb-1">
                    {t('backup.settings.maxBackups', 'Max backups per profile')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={configForm.max_backups_per_profile ?? config.max_backups_per_profile}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, max_backups_per_profile: parseInt(e.target.value) }))}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                  />
                </div>
                
                <label className="flex items-center justify-between cursor-pointer">
                  <span className="text-sm text-secondary dark:text-gray-300">
                    {t('backup.settings.compression', 'Enable compression')}
                  </span>
                  <input
                    type="checkbox"
                    checked={configForm.compression_enabled ?? config.compression_enabled}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, compression_enabled: e.target.checked }))}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                </label>
                
                <label className="flex items-center justify-between cursor-pointer">
                  <span className="text-sm text-secondary dark:text-gray-300">
                    {t('backup.settings.includeEmbeddings', 'Include embeddings by default')}
                  </span>
                  <input
                    type="checkbox"
                    checked={configForm.include_embeddings ?? config.include_embeddings}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, include_embeddings: e.target.checked }))}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                </label>
                
                <button
                  onClick={handleSaveConfig}
                  disabled={savingConfig}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-600 disabled:opacity-50"
                >
                  {savingConfig ? (
                    <ArrowPathIcon className="h-4 w-4 animate-spin" />
                  ) : (
                    <CheckCircleIcon className="h-4 w-4" />
                  )}
                  Save Settings
                </button>
              </div>
            )}
          </div>
        </div>
        
        {/* Right column - Backup list */}
        <div className="lg:col-span-2">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-md border border-surface-variant dark:border-gray-700">
            {/* Header with filters */}
            <div className="p-4 border-b border-gray-200 dark:border-gray-700">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <h2 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2">
                  <DocumentDuplicateIcon className="h-5 w-5" />
                  Backups ({filteredBackups.length})
                </h2>
                
                <div className="flex gap-2">
                  <select
                    value={filterType}
                    onChange={(e) => setFilterType(e.target.value as BackupType | '')}
                    className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                  >
                    <option value="">All Types</option>
                    <option value="full">Full</option>
                    <option value="incremental">Incremental</option>
                    <option value="checkpoint">Checkpoint</option>
                    <option value="post_ingestion">Post-Ingestion</option>
                  </select>
                  
                  <select
                    value={filterProfile}
                    onChange={(e) => setFilterProfile(e.target.value)}
                    className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-primary-900 dark:text-white"
                  >
                    <option value="">All Profiles</option>
                    {profiles && Object.entries(profiles.profiles).map(([key, profile]) => (
                      <option key={key} value={key}>{profile.name}</option>
                    ))}
                  </select>
                  
                  <button
                    onClick={loadData}
                    disabled={loading}
                    className="p-1.5 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                    title="Refresh"
                  >
                    <ArrowPathIcon className={`h-5 w-5 ${loading ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>
            </div>
            
            {/* Backup list */}
            <div className="divide-y divide-gray-200 dark:divide-gray-700">
              {loading && !backups.length ? (
                <div className="p-8 text-center text-gray-500">
                  <ArrowPathIcon className="h-8 w-8 animate-spin mx-auto mb-2" />
                  Loading backups...
                </div>
              ) : filteredBackups.length === 0 ? (
                <div className="p-8 text-center text-gray-500">
                  <ArchiveBoxIcon className="h-12 w-12 mx-auto mb-2 opacity-50" />
                  <p>No backups found</p>
                  <p className="text-sm">Create your first backup to get started</p>
                </div>
              ) : (
                filteredBackups.map((backup) => (
                  <div key={backup.backup_id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    {/* Main row */}
                    <div className="p-4 flex items-center gap-4">
                      <button
                        onClick={() => toggleRow(backup.backup_id)}
                        className="flex-shrink-0 p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded"
                      >
                        {expandedRows.has(backup.backup_id) ? (
                          <ChevronUpIcon className="h-5 w-5 text-gray-500" />
                        ) : (
                          <ChevronDownIcon className="h-5 w-5 text-gray-500" />
                        )}
                      </button>
                      
                      <div className="flex-shrink-0">
                        {getStatusIcon(backup.status)}
                      </div>
                      
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`px-2 py-0.5 rounded text-xs font-medium ${getBackupTypeColor(backup.backup_type)}`}>
                            {getBackupTypeLabel(backup.backup_type)}
                          </span>
                          <span className="text-sm text-secondary dark:text-gray-400 truncate">
                            {backup.backup_id}
                          </span>
                        </div>
                        <div className="flex items-center gap-4 text-sm">
                          <span className="flex items-center gap-1 text-secondary dark:text-gray-400">
                            <FolderIcon className="h-4 w-4" />
                            {backup.profile_key}
                          </span>
                          <span className="text-secondary dark:text-gray-400">
                            {formatBytes(backup.size_bytes)}
                          </span>
                          <span className="text-secondary dark:text-gray-400">
                            {formatRelativeTime(backup.created_at)}
                          </span>
                        </div>
                      </div>
                      
                      <div className="flex items-center gap-2">
                        {backup.status === 'completed' && (
                          <button
                            onClick={() => openRestoreModal(backup)}
                            disabled={restoring === backup.backup_id}
                            className="p-2 text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/20 rounded-lg"
                            title="Restore"
                          >
                            <CloudArrowDownIcon className="h-5 w-5" />
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(backup.backup_id)}
                          disabled={deleting === backup.backup_id}
                          className="p-2 text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20 rounded-lg"
                          title="Delete"
                        >
                          {deleting === backup.backup_id ? (
                            <ArrowPathIcon className="h-5 w-5 animate-spin" />
                          ) : (
                            <TrashIcon className="h-5 w-5" />
                          )}
                        </button>
                      </div>
                    </div>
                    
                    {/* Expanded details */}
                    {expandedRows.has(backup.backup_id) && (
                      <div className="px-4 pb-4 ml-12 text-sm">
                        <div className="bg-gray-50 dark:bg-gray-700/50 rounded-lg p-4 space-y-2">
                          {backup.name && (
                            <div className="flex">
                              <span className="w-32 text-secondary dark:text-gray-400">Name:</span>
                              <span className="font-medium">{backup.name}</span>
                            </div>
                          )}
                          <div className="flex">
                            <span className="w-32 text-secondary dark:text-gray-400">Database:</span>
                            <span>{backup.database_name}</span>
                          </div>
                          <div className="flex">
                            <span className="w-32 text-secondary dark:text-gray-400">Created:</span>
                            <span>{formatDate(backup.created_at)}</span>
                          </div>
                          {backup.completed_at && (
                            <div className="flex">
                              <span className="w-32 text-secondary dark:text-gray-400">Completed:</span>
                              <span>{formatDate(backup.completed_at)}</span>
                            </div>
                          )}
                          <div className="flex">
                            <span className="w-32 text-secondary dark:text-gray-400">Collections:</span>
                            <span>{backup.collections_included.join(', ') || '-'}</span>
                          </div>
                          {Object.keys(backup.document_counts).length > 0 && (
                            <div className="flex">
                              <span className="w-32 text-secondary dark:text-gray-400">Documents:</span>
                              <span>
                                {Object.entries(backup.document_counts).map(([col, count]) => (
                                  <span key={col} className="mr-3">{col}: {count}</span>
                                ))}
                              </span>
                            </div>
                          )}
                          {backup.parent_backup_id && (
                            <div className="flex">
                              <span className="w-32 text-secondary dark:text-gray-400">Parent:</span>
                              <span className="font-mono text-xs">{backup.parent_backup_id}</span>
                            </div>
                          )}
                          {backup.ingestion_job_id && (
                            <div className="flex">
                              <span className="w-32 text-secondary dark:text-gray-400">Ingestion Job:</span>
                              <span className="font-mono text-xs">{backup.ingestion_job_id}</span>
                            </div>
                          )}
                          {backup.error_message && (
                            <div className="flex">
                              <span className="w-32 text-secondary dark:text-gray-400">Error:</span>
                              <span className="text-red-600 dark:text-red-400">{backup.error_message}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
      
      {/* Restore Modal */}
      {showRestoreModal && selectedBackupForRestore && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-md w-full">
            <div className="p-6">
              <h3 className="text-lg font-semibold text-primary-900 dark:text-white flex items-center gap-2 mb-4">
                <CloudArrowDownIcon className="h-6 w-6" />
                Restore Backup
              </h3>
              
              <div className="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
                <div className="flex items-start gap-2 text-yellow-700 dark:text-yellow-400">
                  <ExclamationTriangleIcon className="h-5 w-5 flex-shrink-0 mt-0.5" />
                  <div className="text-sm">
                    <p className="font-medium mb-1">{t('backup.restore.warning', 'Warning')}</p>
                    <p>This will restore data from backup. Existing data may be affected depending on restore mode.</p>
                  </div>
                </div>
              </div>
              
              <div className="space-y-4">
                <div className="text-sm">
                  <span className="text-secondary dark:text-gray-400">Backup: </span>
                  <span className="font-medium">{selectedBackupForRestore.backup_id}</span>
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-secondary dark:text-gray-300 mb-1">
                    {t('backup.restore.mode', 'Restore Mode')}
                  </label>
                  <select
                    value={restoreMode}
                    onChange={(e) => setRestoreMode(e.target.value as RestoreMode)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
                  >
                    <option value="full">{t('backup.restore.fullRestore', 'Full Restore')} - Replace all data</option>
                    <option value="merge">{t('backup.restore.mergeRestore', 'Merge')} - Add missing documents</option>
                    <option value="selective">{t('backup.restore.selectiveRestore', 'Selective')} - Choose collections</option>
                  </select>
                </div>
                
                <div className="space-y-2">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={skipUsers}
                      onChange={(e) => setSkipUsers(e.target.checked)}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                    <span className="text-sm text-secondary dark:text-gray-300">Skip users collection</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={skipSessions}
                      onChange={(e) => setSkipSessions(e.target.checked)}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                    <span className="text-sm text-secondary dark:text-gray-300">Skip chat sessions</span>
                  </label>
                </div>
              </div>
            </div>
            
            <div className="px-6 py-4 bg-gray-50 dark:bg-gray-700/50 rounded-b-xl flex justify-end gap-3">
              <button
                onClick={() => setShowRestoreModal(false)}
                className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={handleRestore}
                disabled={restoring !== null}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
              >
                {restoring ? (
                  <>
                    <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    Restoring...
                  </>
                ) : (
                  <>
                    <CloudArrowDownIcon className="h-4 w-4" />
                    Restore
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
