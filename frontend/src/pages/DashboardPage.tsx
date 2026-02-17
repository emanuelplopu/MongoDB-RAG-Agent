import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChatBubbleLeftRightIcon,
  DocumentTextIcon,
  ArrowPathIcon,
  ClockIcon,
  FolderIcon,
  MagnifyingGlassIcon,
  SparklesIcon,
  PlusIcon,
  ArrowTrendingUpIcon,
  DocumentArrowUpIcon,
  CheckCircleIcon,
  ExclamationCircleIcon,
  ChevronRightIcon,
  CpuChipIcon,
} from '@heroicons/react/24/outline'
import {
  documentsApi,
  ingestionApi,
  profilesApi,
  Document,
  IngestionRunSummary,
  ProfileListResponse,
  IngestionStatus,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { useChatSidebar } from '../contexts/ChatSidebarContext'
import { LocalizedLink } from '../components/LocalizedLink'

// Format relative time
const formatRelativeTime = (dateStr: string): string => {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

// Format ingestion status badge
const getStatusBadge = (status: string) => {
  const statusConfig: Record<string, { color: string; bg: string; icon: typeof CheckCircleIcon }> = {
    completed: { color: 'text-green-600', bg: 'bg-green-100 dark:bg-green-900/30', icon: CheckCircleIcon },
    running: { color: 'text-blue-600', bg: 'bg-blue-100 dark:bg-blue-900/30', icon: ArrowPathIcon },
    failed: { color: 'text-red-600', bg: 'bg-red-100 dark:bg-red-900/30', icon: ExclamationCircleIcon },
    idle: { color: 'text-gray-600', bg: 'bg-gray-100 dark:bg-gray-700', icon: ClockIcon },
  }
  const config = statusConfig[status] || statusConfig.idle
  const Icon = config.icon
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${config.bg} ${config.color}`}>
      <Icon className={`h-3 w-3 ${status === 'running' ? 'animate-spin' : ''}`} />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

export default function DashboardPage() {
  const { user, isLoading: authLoading } = useAuth()
  const { sessions, handleSelectSession, handleNewChat } = useChatSidebar()
  const { t } = useTranslation()

  const [isLoading, setIsLoading] = useState(true)
  const [recentDocs, setRecentDocs] = useState<Document[]>([])
  const [ingestionRuns, setIngestionRuns] = useState<IngestionRunSummary[]>([])
  const [currentIngestion, setCurrentIngestion] = useState<IngestionStatus | null>(null)
  const [profiles, setProfiles] = useState<ProfileListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Fetch dashboard data
  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [docsRes, runsRes, statusRes, profilesRes] = await Promise.all([
        documentsApi.list(1, 5),
        ingestionApi.getRuns(1, 10), // Fetch more to filter
        ingestionApi.getStatus().catch(() => null),
        profilesApi.list(),
      ])
      
      // Get accessible profile keys from the profiles response
      // (backend already filters profiles by user access)
      const accessibleKeys = new Set(Object.keys(profilesRes.profiles))
      
      // Filter ingestion runs to only show those from accessible profiles
      const filteredRuns = runsRes.runs.filter(run => {
        // If no profile specified, it's from the default profile - check if active profile is accessible
        if (!run.profile) {
          return accessibleKeys.has(profilesRes.active_profile)
        }
        return accessibleKeys.has(run.profile)
      }).slice(0, 5)
      
      // Filter current ingestion status - only show if user has access to the profile
      let filteredStatus = statusRes
      if (statusRes && statusRes.status !== 'idle' && statusRes.status !== 'completed') {
        // Check if the current ingestion's profile is accessible
        const ingestionProfile = (statusRes as any).profile || profilesRes.active_profile
        if (!accessibleKeys.has(ingestionProfile)) {
          filteredStatus = null
        }
      }
      
      // Only show documents if user has access to the active profile
      // (documents are fetched from the active profile's database)
      const hasActiveProfileAccess = accessibleKeys.has(profilesRes.active_profile)
      const filteredDocs = hasActiveProfileAccess ? docsRes.documents : []
      
      setRecentDocs(filteredDocs)
      setIngestionRuns(filteredRuns)
      setCurrentIngestion(filteredStatus)
      setProfiles(profilesRes)
    } catch (err) {
      console.error('Error fetching dashboard data:', err)
      setError(t('dashboard.failedToLoad'))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!authLoading && user) {
      fetchData()
    }
  }, [authLoading, user, fetchData])

  // Get recent chats (last 5)
  const recentChats = sessions.slice(0, 5)

  // Get time-based greeting
  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return t('dashboard.greeting.morning')
    if (hour < 17) return t('dashboard.greeting.afternoon')
    return t('dashboard.greeting.evening')
  }

  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Welcome Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-primary-900 dark:text-gray-100">
            {getGreeting()}, {user?.name?.split(' ')[0] || 'there'}!
          </h1>
          <p className="text-secondary dark:text-gray-400 mt-1">
            {t('dashboard.overview')}
          </p>
        </div>
        <button
          onClick={fetchData}
          disabled={isLoading}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600 disabled:opacity-50"
        >
          <ArrowPathIcon className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          {t('common.refresh')}
        </button>
      </div>

      {error && (
        <div className="rounded-2xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 text-amber-700 dark:text-amber-400 text-sm">
          {error}
        </div>
      )}

      {/* Quick Actions */}
      <div className="grid gap-4 md:grid-cols-4">
        <button
          onClick={() => handleNewChat()}
          className="flex items-center gap-3 p-4 rounded-2xl bg-primary text-white hover:bg-primary-700 transition-all shadow-lg hover:shadow-xl"
        >
          <div className="rounded-xl bg-white/20 p-2">
            <PlusIcon className="h-6 w-6" />
          </div>
          <div className="text-left">
            <p className="font-semibold">{t('dashboard.quickActions.newChat')}</p>
            <p className="text-sm text-white/80">{t('dashboard.quickActions.startConversation')}</p>
          </div>
        </button>

        <LocalizedLink
          to="/search"
          className="flex items-center gap-3 p-4 rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 hover:shadow-elevation-2 transition-all group"
        >
          <div className="rounded-xl bg-blue-100 dark:bg-blue-900/50 p-2 group-hover:bg-blue-200 dark:group-hover:bg-blue-800/50 transition-colors">
            <MagnifyingGlassIcon className="h-6 w-6 text-blue-600" />
          </div>
          <div>
            <p className="font-semibold text-primary-900 dark:text-gray-100">{t('dashboard.quickActions.search')}</p>
            <p className="text-sm text-secondary dark:text-gray-400">{t('dashboard.quickActions.findDocuments')}</p>
          </div>
        </LocalizedLink>

        <LocalizedLink
          to="/documents"
          className="flex items-center gap-3 p-4 rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 hover:shadow-elevation-2 transition-all group"
        >
          <div className="rounded-xl bg-green-100 dark:bg-green-900/50 p-2 group-hover:bg-green-200 dark:group-hover:bg-green-800/50 transition-colors">
            <DocumentTextIcon className="h-6 w-6 text-green-600" />
          </div>
          <div>
            <p className="font-semibold text-primary-900 dark:text-gray-100">{t('dashboard.quickActions.documents')}</p>
            <p className="text-sm text-secondary dark:text-gray-400">{t('dashboard.quickActions.browseLibrary')}</p>
          </div>
        </LocalizedLink>

        {user?.is_admin && (
          <LocalizedLink
            to="/system/ingestion"
            className="flex items-center gap-3 p-4 rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 hover:shadow-elevation-2 transition-all group"
          >
            <div className="rounded-xl bg-purple-100 dark:bg-purple-900/50 p-2 group-hover:bg-purple-200 dark:group-hover:bg-purple-800/50 transition-colors">
              <DocumentArrowUpIcon className="h-6 w-6 text-purple-600" />
            </div>
            <div>
              <p className="font-semibold text-primary-900 dark:text-gray-100">{t('dashboard.quickActions.ingestion')}</p>
              <p className="text-sm text-secondary dark:text-gray-400">{t('dashboard.quickActions.manageFiles')}</p>
            </div>
          </LocalizedLink>
        )}
      </div>

      {/* Main Dashboard Grid */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Recent Chats */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100 flex items-center gap-2">
              <ChatBubbleLeftRightIcon className="h-5 w-5 text-primary" />
              {t('dashboard.recentChats.title')}
            </h2>
            <button
              onClick={() => handleNewChat()}
              className="text-sm text-primary hover:text-primary-700 dark:hover:text-primary-300 font-medium"
            >
              {t('dashboard.recentChats.newChat')}
            </button>
          </div>
          
          {recentChats.length > 0 ? (
            <div className="space-y-2">
              {recentChats.map((chat) => (
                <button
                  key={chat.id}
                  onClick={() => handleSelectSession(chat.id)}
                  className="w-full flex items-center gap-3 p-3 rounded-xl hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-left group"
                >
                  <div className="rounded-lg bg-primary-100 dark:bg-primary-900/50 p-2 group-hover:bg-primary-200 dark:group-hover:bg-primary-800/50 transition-colors">
                    <SparklesIcon className="h-4 w-4 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-primary-900 dark:text-gray-100 truncate">
                      {chat.title || t('chat.newChat')}
                    </p>
                    <p className="text-xs text-secondary dark:text-gray-400">
                      {chat.stats?.total_messages || 0} messages • {formatRelativeTime(chat.updated_at)}
                    </p>
                  </div>
                  <ChevronRightIcon className="h-4 w-4 text-secondary group-hover:text-primary transition-colors" />
                </button>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-secondary dark:text-gray-400">
              <ChatBubbleLeftRightIcon className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">{t('dashboard.recentChats.noChats')}</p>
              <button
                onClick={() => handleNewChat()}
                className="mt-3 text-sm text-primary hover:text-primary-700 font-medium"
              >
                {t('dashboard.recentChats.startFirst')}
              </button>
            </div>
          )}
        </div>

        {/* Recent Documents */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100 flex items-center gap-2">
              <DocumentTextIcon className="h-5 w-5 text-green-600" />
              {t('dashboard.recentDocuments.title')}
            </h2>
            <LocalizedLink
              to="/documents"
              className="text-sm text-primary hover:text-primary-700 dark:hover:text-primary-300 font-medium"
            >
              {t('common.viewAll')}
            </LocalizedLink>
          </div>
          
          {isLoading ? (
            <div className="flex justify-center py-8">
              <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : recentDocs.length > 0 ? (
            <div className="space-y-2">
              {recentDocs.map((doc) => (
                <LocalizedLink
                  key={doc.id}
                  to={`/documents/${doc.id}`}
                  className="flex items-center gap-3 p-3 rounded-xl hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors group"
                >
                  <div className="rounded-lg bg-green-100 dark:bg-green-900/50 p-2 group-hover:bg-green-200 dark:group-hover:bg-green-800/50 transition-colors">
                    <DocumentTextIcon className="h-4 w-4 text-green-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-primary-900 dark:text-gray-100 truncate">
                      {doc.title}
                    </p>
                    <p className="text-xs text-secondary dark:text-gray-400">
                      {doc.chunks_count} chunks • {doc.created_at ? formatRelativeTime(doc.created_at) : 'Unknown'}
                    </p>
                  </div>
                  <ChevronRightIcon className="h-4 w-4 text-secondary group-hover:text-primary transition-colors" />
                </LocalizedLink>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-secondary dark:text-gray-400">
              <DocumentTextIcon className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">{t('dashboard.recentDocuments.noDocuments')}</p>
              {user?.is_admin && (
                <LocalizedLink
                  to="/system/ingestion"
                  className="mt-3 inline-block text-sm text-primary hover:text-primary-700 font-medium"
                >
                  {t('dashboard.recentDocuments.startIngestion')}
                </LocalizedLink>
              )}
            </div>
          )}
        </div>

        {/* Ingestion Status */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100 flex items-center gap-2">
              <ArrowPathIcon className="h-5 w-5 text-blue-600" />
              {t('dashboard.ingestionActivity.title')}
            </h2>
            {user?.is_admin && (
              <LocalizedLink
                to="/system/ingestion"
                className="text-sm text-primary hover:text-primary-700 dark:hover:text-primary-300 font-medium"
              >
                {t('common.manage')}
              </LocalizedLink>
            )}
          </div>

          {/* Current Ingestion Status */}
          {currentIngestion && currentIngestion.status !== 'idle' && (
            <div className="mb-4 p-4 rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-blue-700 dark:text-blue-300">{t('dashboard.ingestionActivity.running')}</span>
                {getStatusBadge(currentIngestion.status)}
              </div>
              <div className="mb-2">
                <div className="flex justify-between text-xs text-blue-600 dark:text-blue-400 mb-1">
                  <span>{t('dashboard.ingestionActivity.progress')}</span>
                  <span>{currentIngestion.progress_percent}%</span>
                </div>
                <div className="w-full h-2 bg-blue-200 dark:bg-blue-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-600 rounded-full transition-all"
                    style={{ width: `${currentIngestion.progress_percent}%` }}
                  />
                </div>
              </div>
              <p className="text-xs text-blue-600 dark:text-blue-400">
                {currentIngestion.processed_files} / {currentIngestion.total_files} files processed
              </p>
            </div>
          )}

          {isLoading ? (
            <div className="flex justify-center py-8">
              <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : ingestionRuns.length > 0 ? (
            <div className="space-y-2">
              {ingestionRuns.map((run) => (
                <div
                  key={run.job_id}
                  className="flex items-center gap-3 p-3 rounded-xl bg-surface-variant dark:bg-gray-700"
                >
                  <div className="rounded-lg bg-blue-100 dark:bg-blue-900/50 p-2">
                    <CpuChipIcon className="h-4 w-4 text-blue-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-primary-900 dark:text-gray-100 truncate">
                        {run.profile || t('dashboard.ingestionActivity.defaultProfile')}
                      </p>
                      {getStatusBadge(run.status)}
                    </div>
                    <p className="text-xs text-secondary dark:text-gray-400">
                      {run.document_count} docs, {run.chunks_created} chunks • {run.started_at ? formatRelativeTime(run.started_at) : 'Unknown'}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-secondary dark:text-gray-400">
              <ArrowPathIcon className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">{t('dashboard.ingestionActivity.noActivity')}</p>
            </div>
          )}
        </div>

        {/* Profile Overview */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100 flex items-center gap-2">
              <FolderIcon className="h-5 w-5 text-purple-600" />
              {t('dashboard.profiles.title')}
            </h2>
            {user?.is_admin && (
              <LocalizedLink
                to="/profiles"
                className="text-sm text-primary hover:text-primary-700 dark:hover:text-primary-300 font-medium"
              >
                {t('common.manage')}
              </LocalizedLink>
            )}
          </div>
          
          {isLoading ? (
            <div className="flex justify-center py-8">
              <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : profiles && Object.keys(profiles.profiles).length > 0 ? (
            <div className="space-y-2">
              {Object.entries(profiles.profiles).slice(0, 4).map(([key, profile]) => (
                <div
                  key={key}
                  className={`flex items-center gap-3 p-3 rounded-xl transition-colors ${
                    key === profiles.active_profile
                      ? 'bg-primary-100 dark:bg-primary-900/30 border border-primary-200 dark:border-primary-800'
                      : 'bg-surface-variant dark:bg-gray-700'
                  }`}
                >
                  <div className={`rounded-lg p-2 ${
                    key === profiles.active_profile
                      ? 'bg-primary-200 dark:bg-primary-800'
                      : 'bg-purple-100 dark:bg-purple-900/50'
                  }`}>
                    <FolderIcon className={`h-4 w-4 ${
                      key === profiles.active_profile ? 'text-primary-700' : 'text-purple-600'
                    }`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-primary-900 dark:text-gray-100 truncate">
                        {profile.name}
                      </p>
                      {key === profiles.active_profile && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-primary text-white font-medium">
                          {t('dashboard.profiles.active')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-secondary dark:text-gray-400 truncate">
                      {profile.description || t('dashboard.profiles.noDescription')}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-secondary dark:text-gray-400">
              <FolderIcon className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">{t('dashboard.profiles.noProfiles')}</p>
            </div>
          )}
        </div>
      </div>

      {/* Tips & Productivity Section */}
      <div className="rounded-2xl bg-gradient-to-br from-primary-50 to-blue-50 dark:from-gray-800 dark:to-gray-800 p-6 border border-primary-100 dark:border-gray-700">
        <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100 mb-4 flex items-center gap-2">
          <ArrowTrendingUpIcon className="h-5 w-5 text-primary" />
          {t('dashboard.tips.title')}
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="bg-white/50 dark:bg-gray-700/50 rounded-xl p-4">
            <div className="rounded-lg bg-primary-100 dark:bg-primary-900/50 w-10 h-10 flex items-center justify-center mb-3">
              <SparklesIcon className="h-5 w-5 text-primary" />
            </div>
            <h3 className="font-medium text-primary-900 dark:text-gray-100 mb-1">{t('dashboard.tips.beSpecific.title')}</h3>
            <p className="text-sm text-secondary dark:text-gray-400">
              {t('dashboard.tips.beSpecific.description')}
            </p>
          </div>
          <div className="bg-white/50 dark:bg-gray-700/50 rounded-xl p-4">
            <div className="rounded-lg bg-green-100 dark:bg-green-900/50 w-10 h-10 flex items-center justify-center mb-3">
              <DocumentTextIcon className="h-5 w-5 text-green-600" />
            </div>
            <h3 className="font-medium text-primary-900 dark:text-gray-100 mb-1">{t('dashboard.tips.checkSources.title')}</h3>
            <p className="text-sm text-secondary dark:text-gray-400">
              {t('dashboard.tips.checkSources.description')}
            </p>
          </div>
          <div className="bg-white/50 dark:bg-gray-700/50 rounded-xl p-4">
            <div className="rounded-lg bg-blue-100 dark:bg-blue-900/50 w-10 h-10 flex items-center justify-center mb-3">
              <MagnifyingGlassIcon className="h-5 w-5 text-blue-600" />
            </div>
            <h3 className="font-medium text-primary-900 dark:text-gray-100 mb-1">{t('dashboard.tips.useSearch.title')}</h3>
            <p className="text-sm text-secondary dark:text-gray-400">
              {t('dashboard.tips.useSearch.description')}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
