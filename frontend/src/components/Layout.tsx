import { useState, useRef, useEffect, useCallback } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ChatBubbleLeftRightIcon,
  MagnifyingGlassIcon,
  DocumentTextIcon,
  UserCircleIcon,
  Cog6ToothIcon,
  Bars3Icon,
  XMarkIcon,
  PlusIcon,
  FolderIcon,
  FolderPlusIcon,
  TrashIcon,
  PencilIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  StarIcon,
  CheckIcon,
  ArrowRightOnRectangleIcon,
  EllipsisHorizontalIcon,
  ChartBarIcon,
  MagnifyingGlassCircleIcon,
  ArrowPathIcon,
  WrenchScrewdriverIcon,
  UsersIcon,
  HomeIcon,
  CloudIcon,
  EnvelopeIcon,
  CommandLineIcon,
  CodeBracketIcon,
  KeyIcon,
  ArchiveBoxIcon,
  BeakerIcon,
  CloudArrowUpIcon,
  PencilSquareIcon,
  FolderArrowDownIcon,
  ArrowUpTrayIcon,
  ChevronDoubleLeftIcon,
  BugAntIcon,
} from '@heroicons/react/24/outline'
import { StarIcon as StarIconSolid } from '@heroicons/react/24/solid'
import ThemeSwitcher from './ThemeSwitcher'
import LanguageSwitcher from './LanguageSwitcher'
import ConnectionStatus from './ConnectionStatus'
import CommandPalette, { useCommandPalette } from './CommandPalette'
import { LocalizedLink, useLocalizedNavigate } from './LocalizedLink'
import { useChatSidebar } from '../contexts/ChatSidebarContext'
import { useAuth } from '../contexts/AuthContext'
import { useTenant } from '../contexts/TenantContext'
import { ChatSession, indexesApi, profilesApi, ProfileListResponse } from '../api/client'
import SidebarWarningToast, { SidebarWarning } from './SidebarWarningToast'
import SupportRequestButton from './SupportRequestButton'

// User menu items (shown in dropdown like OpenAI's user menu)
const baseMenuItems = [
  { nameKey: 'nav.dashboard', href: '/dashboard', icon: HomeIcon, adminOnly: false, exact: true },
  { nameKey: 'nav.search', href: '/search', icon: MagnifyingGlassIcon, adminOnly: false },
  { nameKey: 'nav.documents', href: '/documents', icon: DocumentTextIcon, adminOnly: false },
  { nameKey: 'nav.archivedChats', href: '/archived-chats', icon: ArchiveBoxIcon, adminOnly: false },
  { nameKey: 'nav.cloudSources', href: '/cloud-sources', icon: CloudIcon, adminOnly: false },
  { nameKey: 'nav.emailCloudConfig', href: '/email-cloud-config', icon: EnvelopeIcon, adminOnly: false },
  { nameKey: 'nav.apiDocs', href: '/api-docs', icon: CodeBracketIcon, adminOnly: false, external: true },
  { nameKey: 'nav.profiles', href: '/profiles', icon: UserCircleIcon, adminOnly: true },
]

// System sub-menu items (admin only)
const systemMenuItems = [
  { nameKey: 'nav.status', href: '/system/status', icon: ChartBarIcon },
  { nameKey: 'nav.searchIndexes', href: '/system/indexes', icon: MagnifyingGlassCircleIcon },
  { nameKey: 'nav.ingestion', href: '/system/ingestion', icon: ArrowPathIcon },
  { nameKey: 'nav.configuration', href: '/system/config', icon: WrenchScrewdriverIcon },
  { nameKey: 'nav.users', href: '/system/users', icon: UsersIcon },
  { nameKey: 'nav.prompts', href: '/system/prompts', icon: CommandLineIcon },
  { nameKey: 'nav.apiKeys', href: '/system/api-keys', icon: KeyIcon },
  { nameKey: 'nav.strategies', href: '/system/strategies', icon: BeakerIcon },
  { nameKey: 'nav.backups', href: '/system/backups', icon: CloudArrowUpIcon },
  { nameKey: 'nav.liveDebug', href: '/system/debug', icon: BugAntIcon },
]

// Tenant Icon component - uses tenant iconUrl with fallback to Q letter
function TenantIcon({ className = 'h-8 w-8' }: { className?: string }) {
  const { tenant } = useTenant()
  const [imgFailed, setImgFailed] = useState(false)

  if (tenant.branding.iconUrl && !imgFailed) {
    return (
      <img
        src={tenant.branding.iconUrl}
        alt={tenant.branding.appName}
        className={`${className} object-contain rounded-lg`}
        onError={() => setImgFailed(true)}
      />
    )
  }

  return (
    <svg className={className} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <text x="16" y="22" textAnchor="middle" className="fill-white" style={{ fontSize: '18px', fontWeight: 700, fontFamily: 'system-ui, sans-serif' }}>
        {tenant.branding.appName.charAt(0)}
      </text>
    </svg>
  )
}

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('recallhub_sidebar_collapsed') === 'true'
    } catch { return false }
  })
  const [sidebarHovered, setSidebarHovered] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [showMoveMenu, setShowMoveMenu] = useState(false)
  const [sidebarWarnings, setSidebarWarnings] = useState<SidebarWarning[]>([])
  const [dismissedWarnings, setDismissedWarnings] = useState<Set<string>>(new Set())
  const [chatSearchQuery, setChatSearchQuery] = useState('')
  const [showChatSearch, setShowChatSearch] = useState(false)
  // Knowledge profiles state
  const [profilesDropdownOpen, setProfilesDropdownOpen] = useState(false)
  const [profilesData, setProfilesData] = useState<ProfileListResponse | null>(null)
  const profilesDropdownRef = useRef<HTMLDivElement>(null)
  const userMenuRef = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const navigate = useLocalizedNavigate()
  const { t } = useTranslation()
  const { user, isAuthenticated, isLoading: isAuthLoading, logout } = useAuth()
  const { tenant } = useTenant()
  const commandPalette = useCommandPalette()
  const {
    sessions,
    folders,
    currentSession,
    isSidebarLoading,
    collapsedFolders,
    editingTitle,
    editingTitleValue,
    showNewFolder,
    newFolderName,
    contextMenu,
    isSelectMode,
    selectedSessions,
    handleNewChat,
    handleSelectSession,
    handleDeleteSession,
    handleTogglePin,
    handleUpdateTitle,
    handleCreateFolder,
    handleDeleteFolder,
    toggleFolder,
    setEditingTitle,
    setEditingTitleValue,
    setShowNewFolder,
    setNewFolderName,
    setContextMenu,
    toggleSelectMode,
    toggleSessionSelection,
    selectAllSessions,
    clearSelection,
    archiveSelected,
    deleteSelected,
    moveSelectedToFolder,
  } = useChatSidebar()

  // Persist sidebar collapsed state
  useEffect(() => {
    localStorage.setItem('recallhub_sidebar_collapsed', String(desktopSidebarCollapsed))
  }, [desktopSidebarCollapsed])

  // Listen for sidebar control events dispatched from ChatPageNew
  useEffect(() => {
    const handleOpenMobileSidebar = () => setSidebarOpen(true)
    const handleToggleDesktopSidebar = () => setDesktopSidebarCollapsed(prev => !prev)

    window.addEventListener('open-mobile-sidebar', handleOpenMobileSidebar)
    window.addEventListener('toggle-desktop-sidebar', handleToggleDesktopSidebar)
    return () => {
      window.removeEventListener('open-mobile-sidebar', handleOpenMobileSidebar)
      window.removeEventListener('toggle-desktop-sidebar', handleToggleDesktopSidebar)
    }
  }, [])

  // Fetch profiles for header dropdown
  const fetchProfiles = useCallback(async () => {
    try {
      const res = await profilesApi.list()
      setProfilesData(res)
    } catch (err) {
      console.error('Error fetching profiles for header:', err)
    }
  }, [])

  useEffect(() => {
    if (isAuthenticated && !isAuthLoading) {
      fetchProfiles()
    }
  }, [isAuthenticated, isAuthLoading, fetchProfiles])

  // Close profiles dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (profilesDropdownRef.current && !profilesDropdownRef.current.contains(event.target as Node)) {
        setProfilesDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSwitchProfile = async (profileKey: string) => {
    try {
      await profilesApi.switch(profileKey)
      setProfilesData(prev => prev ? { ...prev, active_profile: profileKey } : null)
      setProfilesDropdownOpen(false)
      // Reload the page to refresh data for new profile
      window.location.reload()
    } catch (err) {
      console.error('Error switching profile:', err)
    }
  }

  // Group sessions by folder
  const sessionsByFolder = new Map<string | null, ChatSession[]>()
  const pinnedSessions: ChatSession[] = []
  
  sessions.forEach(session => {
    if (session.is_pinned) {
      pinnedSessions.push(session)
    } else {
      const key = session.folder_id || null
      if (!sessionsByFolder.has(key)) {
        sessionsByFolder.set(key, [])
      }
      sessionsByFolder.get(key)!.push(session)
    }
  })

  // Filter sessions by chat search
  const filterSessions = (sessionList: ChatSession[]) => {
    if (!chatSearchQuery.trim()) return sessionList
    const q = chatSearchQuery.toLowerCase()
    return sessionList.filter(s => (s.title || '').toLowerCase().includes(q))
  }

  // Check if we're on the chat page (handles language prefix: /en/chat, /de/chat, /chat)
  const isOnChatPage = /\/chat(\/|$)/.test(location.pathname)
  
  // Check if we're on the dashboard page
  const isOnDashboardPage = /\/dashboard(\/|$)/.test(location.pathname)

  // Get page title for non-chat pages
  const getPageTitle = () => {
    if (isOnDashboardPage) return tenant.branding.appName
    if (isOnChatPage) return t('nav.chat')
    const item = baseMenuItems.find(n => !n.exact && location.pathname.startsWith(n.href))
    return item ? t(item.nameKey) : t('nav.chat')
  }

  // Filter menu items based on user admin status and tenant features
  const userMenuItems = baseMenuItems.filter(item => {
    if (item.adminOnly && !user?.is_admin) return false
    if (item.href === '/cloud-sources' && !tenant.features.showCloudSources) return false
    if (item.href === '/email-cloud-config' && !tenant.features.showEmailConfig) return false
    if (item.href === '/profiles' && !tenant.features.showProfiles) return false
    if (item.nameKey === 'nav.apiDocs' && !tenant.features.showApiDocs) return false
    return true
  })

  // Filter system menu items based on tenant features
  const filteredSystemItems = systemMenuItems.filter(item => {
    if (item.href === '/system/strategies' && !tenant.features.showStrategies) return false
    if (item.href === '/system/benchmark' && !tenant.features.showEmbeddingBenchmark) return false
    if (item.href === '/system/backups' && !tenant.features.showBackups) return false
    return true
  })
  
  // State for system submenu expansion
  const [systemMenuOpen, setSystemMenuOpen] = useState(location.pathname.startsWith('/system'))

  // Close user menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Check indexes on load and generate warnings
  const checkIndexes = useCallback(async () => {
    if (!isAuthenticated || !user?.is_admin) return
    
    try {
      const dashboard = await indexesApi.getDashboard()
      const warnings: SidebarWarning[] = []
      
      const vectorIndex = dashboard.indexes?.find(idx => idx.type === 'vector' || idx.type === 'vectorSearch')
      const textIndex = dashboard.indexes?.find(idx => idx.type === 'search')
      
      const vectorMissing = !vectorIndex || vectorIndex.status !== 'READY'
      const textMissing = !textIndex || textIndex.status !== 'READY'
      
      if (vectorMissing && textMissing) {
        warnings.push({
          id: 'missing-both-indexes',
          level: 'critical',
          message: t('warnings.missingBothIndexes'),
          path: '/system/indexes',
          actionLabel: t('warnings.createIndexes'),
        })
      } else if (vectorMissing) {
        warnings.push({
          id: 'missing-vector-index',
          level: 'warning',
          message: t('warnings.missingVectorIndex'),
          path: '/system/indexes',
          actionLabel: t('warnings.createIndexes'),
        })
      } else if (textMissing) {
        warnings.push({
          id: 'missing-text-index',
          level: 'warning',
          message: t('warnings.missingTextIndex'),
          path: '/system/indexes',
          actionLabel: t('warnings.createIndexes'),
        })
      }
      
      // Filter out dismissed warnings
      const filteredWarnings = warnings.filter(w => !dismissedWarnings.has(w.id))
      setSidebarWarnings(filteredWarnings)
    } catch (err) {
      console.error('Error checking indexes:', err)
    }
  }, [isAuthenticated, user?.is_admin, dismissedWarnings, t])

  useEffect(() => {
    checkIndexes()
  }, [checkIndexes])

  const handleDismissWarning = (id: string) => {
    setDismissedWarnings(prev => new Set(prev).add(id))
    setSidebarWarnings(prev => prev.filter(w => w.id !== id))
  }

  const handleLogout = async () => {
    await logout()
    setUserMenuOpen(false)
    navigate('/login')
  }

  const SidebarContent = () => (
    <div className="flex flex-col h-full min-h-0">
      {/* Top Section - ChatGPT-style header */}
      <div className="p-3 flex-shrink-0 space-y-1">
        {isSelectMode ? (
          /* Select Mode Actions - unchanged */
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-primary-900 dark:text-gray-200">
                {selectedSessions.size} {t('common.selected')}
              </span>
              <button
                onClick={toggleSelectMode}
                className="text-sm text-primary-600 dark:text-primary-400 hover:underline"
              >
                {t('common.cancel')}
              </button>
            </div>
            <div className="flex gap-2">
              <button
                onClick={selectedSessions.size === sessions.length ? clearSelection : selectAllSessions}
                className="flex-1 px-2 py-1.5 text-xs bg-surface-variant dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
              >
                {selectedSessions.size === sessions.length ? t('sidebar.deselectAll') : t('sidebar.selectAll')}
              </button>
            </div>
            {selectedSessions.size > 0 && (
              <div className="flex flex-col gap-1.5">
                <div className="relative">
                  <button
                    onClick={() => setShowMoveMenu(!showMoveMenu)}
                    className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 rounded-lg hover:bg-primary-200 dark:hover:bg-primary-900/50 transition-colors"
                  >
                    <FolderArrowDownIcon className="h-3.5 w-3.5" />
                    {t('sidebar.moveToProject')} ({selectedSessions.size})
                  </button>
                  {showMoveMenu && (
                    <>
                      <div className="fixed inset-0 z-30" onClick={() => setShowMoveMenu(false)} />
                      <div className="absolute left-0 right-0 top-full mt-1 z-40 bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-600 py-1 max-h-48 overflow-y-auto">
                        <button
                          onClick={() => { moveSelectedToFolder(null); setShowMoveMenu(false) }}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-surface-variant dark:hover:bg-gray-700 text-secondary dark:text-gray-400"
                        >
                          <XMarkIcon className="h-3.5 w-3.5" />
                          {t('sidebar.unfiled')}
                        </button>
                        {folders.map(folder => (
                          <button
                            key={folder.id}
                            onClick={() => { moveSelectedToFolder(folder.id); setShowMoveMenu(false) }}
                            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-surface-variant dark:hover:bg-gray-700 dark:text-gray-200"
                          >
                            <FolderIcon className="h-3.5 w-3.5" style={{ color: folder.color }} />
                            {folder.name}
                          </button>
                        ))}
                        {folders.length === 0 && (
                          <div className="px-3 py-2 text-xs text-secondary dark:text-gray-500 italic">
                            {t('sidebar.noProjects')}
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
                <div className="flex gap-1.5">
                  <button
                    onClick={archiveSelected}
                    className="flex-1 flex items-center justify-center gap-1 px-2 py-1.5 text-xs bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 rounded-lg hover:bg-primary-200 dark:hover:bg-primary-900/50 transition-colors"
                  >
                    <ArchiveBoxIcon className="h-3.5 w-3.5" />
                    {t('sidebar.archive')} ({selectedSessions.size})
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(t('confirm.deleteMultiple', { count: selectedSessions.size }))) {
                        deleteSelected()
                      }
                    }}
                    className="flex-1 flex items-center justify-center gap-1 px-2 py-1.5 text-xs bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                  >
                    <TrashIcon className="h-3.5 w-3.5" />
                    {t('common.delete')} ({selectedSessions.size})
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <>
            {/* Row 1: Q Logo | + New Chat | Edit | Collapse */}
            <div className="flex items-center gap-1">
              <LocalizedLink
                to="/dashboard"
                className="flex items-center justify-center w-9 h-9 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors flex-shrink-0"
                title={t('sidebar.home')}
              >
                <TenantIcon className="h-7 w-7" />
              </LocalizedLink>
              <button
                onClick={() => handleNewChat()}
                className="flex-1 flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm font-medium text-primary-900 dark:text-gray-200"
              >
                <PencilSquareIcon className="h-5 w-5" />
                {t('sidebar.newChat')}
              </button>
              <button
                onClick={toggleSelectMode}
                className="flex items-center justify-center w-9 h-9 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400 flex-shrink-0"
                title={t('sidebar.editChats')}
              >
                <PencilIcon className="h-4 w-4" />
              </button>
              <button
                onClick={() => setDesktopSidebarCollapsed(true)}
                className="hidden lg:flex items-center justify-center w-9 h-9 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400 flex-shrink-0"
                title={t('sidebar.collapse')}
              >
                <ChevronDoubleLeftIcon className="h-4 w-4" />
              </button>
            </div>

            {/* Row 2: Search / Find Documents */}
            <LocalizedLink
              to="/search"
              className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm text-primary-900 dark:text-gray-300"
            >
              <MagnifyingGlassIcon className="h-5 w-5 text-secondary dark:text-gray-400" />
              {t('sidebar.searchDocuments')}
            </LocalizedLink>

            {/* Row 3: Search Chats */}
            <button
              onClick={() => setShowChatSearch(!showChatSearch)}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm text-primary-900 dark:text-gray-300"
            >
              <MagnifyingGlassCircleIcon className="h-5 w-5 text-secondary dark:text-gray-400" />
              {t('sidebar.searchChats')}
            </button>

            {/* Chat Search Input (conditionally shown) */}
            {showChatSearch && (
              <div className="relative px-1">
                <input
                  type="text"
                  value={chatSearchQuery}
                  onChange={(e) => setChatSearchQuery(e.target.value)}
                  placeholder={t('sidebar.searchChats')}
                  className="w-full text-sm bg-surface-variant dark:bg-gray-700 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500"
                  autoFocus
                />
                {chatSearchQuery && (
                  <button
                    onClick={() => { setChatSearchQuery(''); setShowChatSearch(false) }}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-secondary hover:text-primary"
                  >
                    <XMarkIcon className="h-4 w-4" />
                  </button>
                )}
              </div>
            )}

            {/* Row 4: Documents + Datenimport */}
            <div className="flex items-center gap-1">
              <LocalizedLink
                to="/documents"
                className="flex-1 flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm text-primary-900 dark:text-gray-300"
              >
                <DocumentTextIcon className="h-5 w-5 text-secondary dark:text-gray-400" />
                {t('nav.documents')}
              </LocalizedLink>
              {user?.is_admin && (
                <LocalizedLink
                  to="/system/ingestion"
                  className="flex items-center justify-center w-9 h-9 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400 flex-shrink-0"
                  title={t('sidebar.datenimport')}
                >
                  <ArrowUpTrayIcon className="h-5 w-5" />
                </LocalizedLink>
              )}
            </div>
          </>
        )}
      </div>

      {/* Sidebar Warnings */}
      <SidebarWarningToast warnings={sidebarWarnings} onDismiss={handleDismissWarning} />

      {/* Scrollable middle: folders + conversations (single scroll area so bottom user menu stays pinned) */}
      <div className="flex-1 min-h-0 overflow-y-auto">

      {/* Folders Section (Projects) */}
      <div className="px-3">
        <div className="text-xs font-medium text-primary-600 dark:text-primary-400 uppercase tracking-wider px-2 py-2">
          {t('sidebar.projects')}
        </div>
        
        {/* New Folder Input */}
        {showNewFolder ? (
          <div className="flex items-center gap-1 px-2 py-1 mb-1">
            <FolderPlusIcon className="h-4 w-4 text-primary" />
            <input
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateFolder()
                if (e.key === 'Escape') setShowNewFolder(false)
              }}
              placeholder={t('sidebar.folderName')}
              className="flex-1 text-sm bg-transparent border-b border-primary focus:outline-none dark:text-gray-200"
              autoFocus
            />
            <button onClick={handleCreateFolder} className="p-1">
              <CheckIcon className="h-4 w-4 text-secondary" />
            </button>
            <button onClick={() => setShowNewFolder(false)} className="p-1">
              <XMarkIcon className="h-4 w-4 text-red-500" />
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowNewFolder(true)}
            className="flex items-center gap-2 px-2 py-2 text-sm text-secondary hover:text-primary dark:text-gray-400 dark:hover:text-primary-300 w-full rounded-lg hover:bg-surface-variant dark:hover:bg-gray-800"
          >
            <FolderPlusIcon className="h-4 w-4" />
            {t('sidebar.newProject')}
          </button>
        )}

        {/* Folder List */}
        <div className="mt-1 space-y-1">
          {folders.map(folder => (
            <div key={folder.id}>
              <div
                className="flex items-center gap-1 px-2 py-1.5 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-800 cursor-pointer group"
                onClick={() => toggleFolder(folder.id)}
              >
                {collapsedFolders.has(folder.id) ? (
                  <ChevronRightIcon className="h-4 w-4 text-secondary" />
                ) : (
                  <ChevronDownIcon className="h-4 w-4 text-secondary" />
                )}
                <FolderIcon className="h-4 w-4" style={{ color: folder.color }} />
                <span className="flex-1 text-sm truncate dark:text-gray-200">{folder.name}</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    handleNewChat(folder.id)
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 hover:bg-surface dark:hover:bg-gray-700 rounded"
                >
                  <PlusIcon className="h-3 w-3 text-secondary" />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    if (confirm(t('confirm.deleteFolder', { name: folder.name }))) {
                      handleDeleteFolder(folder.id)
                    }
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 hover:bg-surface dark:hover:bg-gray-700 rounded"
                >
                  <TrashIcon className="h-3 w-3 text-red-500" />
                </button>
              </div>
              {!collapsedFolders.has(folder.id) && (
                <div className="ml-4 mt-0.5 space-y-0.5">
                  {filterSessions(sessionsByFolder.get(folder.id) || []).map(session => (
                    <SessionItem
                      key={session.id}
                      session={session}
                      isActive={currentSession?.id === session.id && isOnChatPage}
                      isEditing={editingTitle === session.id}
                      editValue={editingTitleValue}
                      isSelectMode={isSelectMode}
                      isSelected={selectedSessions.has(session.id)}
                      onSelect={() => handleSelectSession(session.id)}
                      onToggleSelect={() => toggleSessionSelection(session.id)}
                      onEditChange={setEditingTitleValue}
                      onEditSave={() => handleUpdateTitle(session.id)}
                      onEditCancel={() => setEditingTitle(null)}
                      onContextMenu={(e) => {
                        e.preventDefault()
                        setContextMenu({ sessionId: session.id, x: e.clientX, y: e.clientY })
                      }}
                    />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Divider */}
      <div className="mx-3 my-2 premium-divider" />

      {/* Conversations List */}
      <div className="px-3">
        {isSidebarLoading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin h-6 w-6 border-2 border-primary border-t-transparent rounded-full" />
          </div>
        ) : (
          <div className="space-y-0.5">
            {/* Pinned Sessions */}
            {filterSessions(pinnedSessions).length > 0 && (
              <div className="mb-2">
                <div className="px-2 py-1 text-xs font-medium text-primary-600 dark:text-primary-400 uppercase tracking-wider">
                  {t('sidebar.pinned')}
                </div>
                {filterSessions(pinnedSessions).map(session => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    isActive={currentSession?.id === session.id && isOnChatPage}
                    isEditing={editingTitle === session.id}
                    editValue={editingTitleValue}
                    isSelectMode={isSelectMode}
                    isSelected={selectedSessions.has(session.id)}
                    onSelect={() => handleSelectSession(session.id)}
                    onToggleSelect={() => toggleSessionSelection(session.id)}
                    onEditChange={setEditingTitleValue}
                    onEditSave={() => handleUpdateTitle(session.id)}
                    onEditCancel={() => setEditingTitle(null)}
                    onContextMenu={(e) => {
                      e.preventDefault()
                      setContextMenu({ sessionId: session.id, x: e.clientX, y: e.clientY })
                    }}
                  />
                ))}
              </div>
            )}

            {/* Your Chats */}
            {filterSessions(sessionsByFolder.get(null) || []).length > 0 && (
              <div>
                <div className="px-2 py-1 text-xs font-medium text-primary-600 dark:text-primary-400 uppercase tracking-wider">
                  {t('sidebar.yourChats')}
                </div>
                {filterSessions(sessionsByFolder.get(null) || []).map(session => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    isActive={currentSession?.id === session.id && isOnChatPage}
                    isEditing={editingTitle === session.id}
                    editValue={editingTitleValue}
                    isSelectMode={isSelectMode}
                    isSelected={selectedSessions.has(session.id)}
                    onSelect={() => handleSelectSession(session.id)}
                    onToggleSelect={() => toggleSessionSelection(session.id)}
                    onEditChange={setEditingTitleValue}
                    onEditSave={() => handleUpdateTitle(session.id)}
                    onEditCancel={() => setEditingTitle(null)}
                    onContextMenu={(e) => {
                      e.preventDefault()
                      setContextMenu({ sessionId: session.id, x: e.clientX, y: e.clientY })
                    }}
                  />
                ))}
              </div>
            )}

            {sessions.length === 0 && !isSidebarLoading && (
              <div className="text-center py-8 text-secondary dark:text-gray-500 text-sm">
                {t('sidebar.noChats')}
              </div>
            )}
          </div>
        )}
      </div>
      {/* end scrollable middle */}
      </div>

      {/* Bottom Sticky Section - User Menu */}
      <div className="flex-shrink-0 border-t border-surface-variant dark:border-gray-700 p-3" ref={userMenuRef}>
        {/* User Menu Dropdown (appears above the button) */}
        {userMenuOpen && (
          <div
            className="absolute bottom-20 left-3 right-3 bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-600 py-2 z-50 max-h-[70vh] overflow-y-auto"
            onMouseDown={(e) => e.stopPropagation()}
            onTouchStart={(e) => e.stopPropagation()}
          >
            {/* User Info */}
            {isAuthenticated && user && (
              <div className="px-4 py-2 border-b border-surface-variant dark:border-gray-700">
                <div className="font-medium text-sm text-primary-900 dark:text-gray-200">
                  {user.title_prefix ? `${user.title_prefix} ` : ''}{user.name}
                </div>
                {user.title_suffix && (
                  <div className="text-xs text-primary-600/70 dark:text-gray-400 font-medium">{user.title_suffix}</div>
                )}
                <div className="text-xs text-secondary dark:text-gray-400">{user.email}</div>
              </div>
            )}
            
            {/* Menu Items */}
            <div className="py-1">
              {userMenuItems.map((item) => {
                const isActive = item.exact 
                  ? location.pathname === item.href 
                  : location.pathname.startsWith(item.href)
                return (
                  <LocalizedLink
                    key={item.nameKey}
                    to={item.href}
                    onClick={() => setUserMenuOpen(false)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                      isActive
                        ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                        : 'text-secondary dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
                    }`}
                  >
                    <item.icon className="h-5 w-5" />
                    {t(item.nameKey)}
                  </LocalizedLink>
                )
              })}
              
              {/* System Menu with Submenu (admin only) */}
              {user?.is_admin && (
                <>
                  <button
                    onClick={() => setSystemMenuOpen(!systemMenuOpen)}
                    className={`w-full flex items-center justify-between px-4 py-2 text-sm transition-colors ${
                      location.pathname.startsWith('/system')
                        ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                        : 'text-secondary dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <Cog6ToothIcon className="h-5 w-5" />
                      {t('nav.system')}
                    </div>
                    {systemMenuOpen ? (
                      <ChevronDownIcon className="h-4 w-4" />
                    ) : (
                      <ChevronRightIcon className="h-4 w-4" />
                    )}
                  </button>
                  {systemMenuOpen && (
                    <div className="ml-4 border-l border-surface-variant dark:border-gray-600">
                      {filteredSystemItems.map((subItem) => {
                        const isSubActive = location.pathname === subItem.href
                        return (
                          <LocalizedLink
                            key={subItem.nameKey}
                            to={subItem.href}
                            onClick={() => setUserMenuOpen(false)}
                            className={`w-full flex items-center gap-3 px-4 py-1.5 text-sm transition-colors ${
                              isSubActive
                                ? 'text-primary-700 dark:text-primary-300 bg-primary-50 dark:bg-primary-900/30'
                                : 'text-secondary dark:text-gray-400 hover:text-primary-700 dark:hover:text-primary-300'
                            }`}
                          >
                            <subItem.icon className="h-4 w-4" />
                            {t(subItem.nameKey)}
                          </LocalizedLink>
                        )
                      })}
                    </div>
                  )}
                </>
              )}
            </div>
            
            <div className="px-4 py-2 border-t border-surface-variant dark:border-gray-700 flex items-center justify-between">
              <span className="text-sm text-secondary dark:text-gray-400">{t('nav.theme')}</span>
              <ThemeSwitcher compact={false} />
            </div>
            
            {/* Language Switcher */}
            <div className="px-4 py-2 border-t border-surface-variant dark:border-gray-700 flex items-center justify-between">
              <span className="text-sm text-secondary dark:text-gray-400">{t('language.select')}</span>
              <LanguageSwitcher compact={false} />
            </div>
            
            {/* Connection Status */}
            <div className="px-4 py-2 border-t border-surface-variant dark:border-gray-700 flex items-center justify-between">
              <span className="text-sm text-secondary dark:text-gray-400">API Status</span>
              <ConnectionStatus healthCheckUrl="/api/health" showText size="sm" />
            </div>
            
            {/* Auth Actions */}
            <div className="border-t border-surface-variant dark:border-gray-700 py-1">
              {isAuthenticated ? (
                <button
                  onClick={handleLogout}
                  className="w-full flex items-center gap-3 px-4 py-2 text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20"
                >
                  <ArrowRightOnRectangleIcon className="h-5 w-5" />
                  {t('nav.signOut')}
                </button>
              ) : (
                <button
                  onClick={() => { navigate('/login'); setUserMenuOpen(false) }}
                  className="w-full flex items-center gap-3 px-4 py-2 text-sm text-primary-600 dark:text-primary-400 hover:bg-primary-50 dark:hover:bg-primary-900/20"
                >
                  <ArrowRightOnRectangleIcon className="h-5 w-5" />
                  {t('nav.signIn')}
                </button>
              )}
            </div>
          </div>
        )}
        
        {/* User Button */}
        <button
          onClick={() => setUserMenuOpen(!userMenuOpen)}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors"
        >
          <div className="w-8 h-8 rounded-full bg-gradient-brand flex items-center justify-center text-white text-sm font-medium">
            {isAuthenticated && user ? (
              user.name.charAt(0).toUpperCase()
            ) : (
              <TenantIcon className="h-5 w-5" />
            )}
          </div>
          <div className="flex-1 text-left min-w-0">
            <div className="text-sm font-medium text-primary-900 dark:text-gray-200 truncate">
              {isAuthenticated && user ? (
                <>{user.title_prefix ? `${user.title_prefix} ` : ''}{user.name}</>
              ) : tenant.branding.appName}
            </div>
            {isAuthenticated && user && user.title_suffix && (
              <div className="text-[11px] text-primary-600/60 dark:text-gray-400 truncate leading-tight">{user.title_suffix}</div>
            )}
            {isAuthenticated && user && (
              <div className="text-xs text-secondary dark:text-gray-500 truncate">{user.email}</div>
            )}
          </div>
          <EllipsisHorizontalIcon className="h-5 w-5 text-secondary dark:text-gray-400" />
        </button>
      </div>
    </div>
  )

  // Determine if desktop sidebar is effectively visible
  const desktopSidebarVisible = !desktopSidebarCollapsed || sidebarHovered

  return (
    <div className="min-h-screen bg-background dark:bg-gray-900 transition-colors duration-200">
      {/* Auth Loading Overlay */}
      {isAuthLoading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background dark:bg-gray-900">
          <div className="text-center">
            <div className="animate-spin h-10 w-10 border-4 border-primary border-t-transparent rounded-full mx-auto mb-4" />
            <p className="text-secondary dark:text-gray-400">{t('common.loading')}</p>
          </div>
        </div>
      )}

      {/* Command Palette - Ctrl+K */}
      <CommandPalette isOpen={commandPalette.isOpen} onClose={commandPalette.close} />

      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Mobile sidebar */}
      <div
        className={`fixed inset-y-0 left-0 z-50 w-72 transform bg-surface dark:bg-gray-800 shadow-elevation-3 transition-transform duration-300 ease-in-out lg:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <SidebarContent />
      </div>

      {/* Desktop sidebar - collapsible */}
      <div
        className={`hidden lg:fixed lg:inset-y-0 lg:flex lg:flex-col transition-all duration-300 ease-in-out z-40 ${
          desktopSidebarVisible ? 'lg:w-72' : 'lg:w-14'
        }`}
        onMouseEnter={() => { if (desktopSidebarCollapsed) setSidebarHovered(true) }}
        onMouseLeave={() => { if (desktopSidebarCollapsed) setSidebarHovered(false) }}
      >
        {/* Expanded sidebar content */}
        <div className={`flex grow flex-col bg-surface dark:bg-gray-800 shadow-elevation-1 transition-all duration-300 min-h-0 ${
          desktopSidebarVisible ? 'w-72 opacity-100' : 'w-0 opacity-0 overflow-hidden pointer-events-none absolute'
        }`}>
          <SidebarContent />
        </div>

        {/* Collapsed icon column */}
        {desktopSidebarCollapsed && !sidebarHovered && (
          <div className="flex flex-col h-full w-14 bg-surface dark:bg-gray-800 shadow-elevation-1 items-center py-3 gap-1">
            {/* Tenant Icon */}
            <LocalizedLink
              to="/dashboard"
              className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors mb-1"
              title={tenant.branding.appName}
            >
              <TenantIcon className="h-7 w-7" />
            </LocalizedLink>

            {/* New Chat */}
            <button
              onClick={() => handleNewChat()}
              className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-primary-900 dark:text-gray-200"
              title={t('sidebar.newChat')}
            >
              <PencilSquareIcon className="h-5 w-5" />
            </button>

            {/* Search Documents */}
            <LocalizedLink
              to="/search"
              className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400"
              title={t('sidebar.searchDocuments')}
            >
              <MagnifyingGlassIcon className="h-5 w-5" />
            </LocalizedLink>

            {/* Documents */}
            <LocalizedLink
              to="/documents"
              className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400"
              title={t('nav.documents')}
            >
              <DocumentTextIcon className="h-5 w-5" />
            </LocalizedLink>

            {/* Chat */}
            <LocalizedLink
              to="/chat"
              className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400"
              title={t('nav.chat')}
            >
              <ChatBubbleLeftRightIcon className="h-5 w-5" />
            </LocalizedLink>

            {/* Spacer */}
            <div className="flex-1" />

            {/* Expand button */}
            <button
              onClick={() => setDesktopSidebarCollapsed(false)}
              className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-secondary dark:text-gray-400"
              title={t('sidebar.expand')}
            >
              <Bars3Icon className="h-5 w-5" />
            </button>
          </div>
        )}
      </div>

      {/* Main content */}
      <div className={`transition-all duration-300 ${desktopSidebarVisible ? 'lg:pl-72' : 'lg:pl-14'}`}>
        {/* Desktop Header Bar - hidden on chat page (merged into ChatPageNew header) */}
        {!isOnChatPage && (
        <div className="sticky top-0 z-30 flex h-14 items-center gap-x-4 bg-surface/95 dark:bg-gray-800/95 px-4 shadow-sm backdrop-blur">
          {/* Mobile menu button */}
          <button
            type="button"
            className="-m-2.5 p-2.5 text-secondary dark:text-gray-400 lg:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            <Bars3Icon className="h-6 w-6" />
          </button>

          {/* Desktop: collapsed sidebar expand button */}
          {desktopSidebarCollapsed && !sidebarHovered && (
            <button
              type="button"
              className="hidden lg:flex -m-2.5 p-2.5 text-secondary dark:text-gray-400 hover:text-primary transition-colors"
              onClick={() => setDesktopSidebarCollapsed(false)}
              title={t('sidebar.expand')}
            >
              <ChevronRightIcon className="h-5 w-5" />
            </button>
          )}

          <div className="flex flex-1 items-center justify-between">
            <h1 className="text-lg font-semibold text-primary-900 dark:text-primary-200">
              {getPageTitle()}
            </h1>
            
            {/* Right side: Knowledge Profiles dropdown */}
            <div className="flex items-center gap-3">
              {/* Knowledge Profiles Dropdown */}
              {profilesData && Object.keys(profilesData.profiles).length > 0 && (
                <div className="relative" ref={profilesDropdownRef}>
                  <button
                    onClick={() => setProfilesDropdownOpen(!profilesDropdownOpen)}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm"
                  >
                    <FolderIcon className="h-4 w-4 text-primary" />
                    <span className="text-primary-900 dark:text-gray-200 hidden sm:inline">
                      {profilesData.profiles[profilesData.active_profile]?.name || profilesData.active_profile}
                    </span>
                    <ChevronDownIcon className="h-3 w-3 text-secondary" />
                  </button>
                  {profilesDropdownOpen && (
                    <div className="absolute right-0 mt-1 w-64 bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-600 py-2 z-50">
                      <div className="px-3 py-1.5 text-xs font-medium text-secondary dark:text-gray-500 uppercase">
                        {t('nav.knowledgeProfiles')}
                      </div>
                      {Object.entries(profilesData.profiles).map(([key, profile]) => (
                        <button
                          key={key}
                          onClick={() => handleSwitchProfile(key)}
                          className={`w-full flex items-center gap-3 px-3 py-2 text-sm transition-colors ${
                            key === profilesData.active_profile
                              ? 'bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300'
                              : 'text-primary-900 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
                          }`}
                        >
                          <FolderIcon className={`h-4 w-4 ${key === profilesData.active_profile ? 'text-primary' : 'text-secondary'}`} />
                          <div className="flex-1 text-left min-w-0">
                            <div className="truncate font-medium">{profile.name}</div>
                            {profile.description && (
                              <div className="text-xs text-secondary dark:text-gray-500 truncate">{profile.description}</div>
                            )}
                          </div>
                          {key === profilesData.active_profile && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary text-white font-medium">
                              {t('dashboard.profiles.active')}
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        )}

        {/* Page content */}
        <main className={isOnChatPage ? 'h-screen' : isOnDashboardPage ? 'h-[calc(100vh-3.5rem)]' : 'py-6 px-4 sm:px-6 lg:px-8'}>
          <Outlet />
        </main>

        {/* Support request button for non-admin users */}
        <SupportRequestButton sessionId={currentSession?.id} />
      </div>

      {/* Context Menu */}
      {contextMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setContextMenu(null)}
          />
          <div
            className="fixed z-50 bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-600 py-1 min-w-[160px]"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            {(() => {
              const session = sessions.find(s => s.id === contextMenu.sessionId)
              if (!session) return null
              return (
                <>
                  <button
                    onClick={() => handleTogglePin(session.id, session.is_pinned)}
                    className="w-full flex items-center gap-2 px-4 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-gray-200"
                  >
                    {session.is_pinned ? (
                      <>
                        <StarIcon className="h-4 w-4" />
                        {t('sidebar.unpin')}
                      </>
                    ) : (
                      <>
                        <StarIconSolid className="h-4 w-4 text-primary" />
                        {t('sidebar.pin')}
                      </>
                    )}
                  </button>
                  <button
                    onClick={() => {
                      setEditingTitle(session.id)
                      setEditingTitleValue(session.title)
                      setContextMenu(null)
                    }}
                    className="w-full flex items-center gap-2 px-4 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-gray-200"
                  >
                    <PencilIcon className="h-4 w-4" />
                    {t('sidebar.rename')}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(t('confirm.deleteChat', { title: session.title }))) {
                        handleDeleteSession(session.id)
                      } else {
                        setContextMenu(null)
                      }
                    }}
                    className="w-full flex items-center gap-2 px-4 py-2 text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20"
                  >
                    <TrashIcon className="h-4 w-4" />
                    {t('common.delete')}
                  </button>
                </>
              )
            })()}
          </div>
        </>
      )}
    </div>
  )
}

// Session Item Component
function SessionItem({
  session,
  isActive,
  isEditing,
  editValue,
  isSelectMode,
  isSelected,
  onSelect,
  onToggleSelect,
  onEditChange,
  onEditSave,
  onEditCancel,
  onContextMenu,
}: {
  session: ChatSession
  isActive: boolean
  isEditing: boolean
  editValue: string
  isSelectMode: boolean
  isSelected: boolean
  onSelect: () => void
  onToggleSelect: () => void
  onEditChange: (value: string) => void
  onEditSave: () => void
  onEditCancel: () => void
  onContextMenu: (e: React.MouseEvent) => void
}) {
  const { t } = useTranslation()
  return (
    <div
      onClick={isSelectMode ? onToggleSelect : onSelect}
      onContextMenu={onContextMenu}
      className={`flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer group transition-colors ${
        isSelected
          ? 'bg-primary-200 dark:bg-primary-800/50 text-primary-900 dark:text-primary-100'
          : isActive
            ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-900 dark:text-primary-100'
            : 'hover:bg-surface-variant dark:hover:bg-gray-800 text-primary-900 dark:text-gray-300'
      }`}
    >
      {isSelectMode ? (
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggleSelect}
          onClick={(e) => e.stopPropagation()}
          className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
        />
      ) : (
        <ChatBubbleLeftRightIcon className="h-4 w-4 flex-shrink-0 text-secondary" />
      )}
      {isEditing ? (
        <input
          type="text"
          value={editValue}
          onChange={(e) => onEditChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onEditSave()
            if (e.key === 'Escape') onEditCancel()
          }}
          onBlur={onEditSave}
          className="flex-1 text-sm bg-transparent border-b border-primary focus:outline-none"
          autoFocus
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 text-sm truncate">{session.title || t('chat.newChat')}</span>
      )}
      {session.is_pinned && (
        <StarIconSolid className="h-3 w-3 text-primary flex-shrink-0" />
      )}
    </div>
  )
}

// Named export for testing
export { Layout }
