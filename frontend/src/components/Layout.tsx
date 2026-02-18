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
} from '@heroicons/react/24/outline'
import { StarIcon as StarIconSolid } from '@heroicons/react/24/solid'
import ThemeSwitcher from './ThemeSwitcher'
import LanguageSwitcher from './LanguageSwitcher'
import { LocalizedLink, useLocalizedNavigate } from './LocalizedLink'
import { useChatSidebar } from '../contexts/ChatSidebarContext'
import { useAuth } from '../contexts/AuthContext'
import { ChatSession, indexesApi } from '../api/client'
import SidebarWarningToast, { SidebarWarning } from './SidebarWarningToast'

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
]

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [sidebarWarnings, setSidebarWarnings] = useState<SidebarWarning[]>([])
  const [dismissedWarnings, setDismissedWarnings] = useState<Set<string>>(new Set())
  const userMenuRef = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const navigate = useLocalizedNavigate()
  const { t } = useTranslation()
  const { user, isAuthenticated, isLoading: isAuthLoading, logout } = useAuth()
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
  } = useChatSidebar()

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

  // Check if we're on the chat page
  const isOnChatPage = location.pathname.startsWith('/chat')
  
  // Check if we're on the dashboard page
  const isOnDashboardPage = location.pathname === '/dashboard'

  // Get page title for non-chat pages
  const getPageTitle = () => {
    if (isOnDashboardPage) return t('nav.dashboard')
    if (isOnChatPage) return t('nav.chat')
    const item = baseMenuItems.find(n => !n.exact && location.pathname.startsWith(n.href))
    return item ? t(item.nameKey) : t('nav.chat')
  }

  // Filter menu items based on user admin status
  const userMenuItems = baseMenuItems.filter(item => !item.adminOnly || user?.is_admin)
  
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
    <div className="flex flex-col h-full">
      {/* Top Section - New Chat Button or Select Mode Actions */}
      <div className="p-3 flex-shrink-0">
        {isSelectMode ? (
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
                onClick={selectAllSessions}
                className="flex-1 px-2 py-1.5 text-xs bg-surface-variant dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
              >
                              {t('sidebar.selectAll')}
              </button>
              <button
                onClick={clearSelection}
                className="flex-1 px-2 py-1.5 text-xs bg-surface-variant dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
              >
                              {t('common.clear')}
              </button>
            </div>
            {selectedSessions.size > 0 && (
              <div className="flex gap-2">
                <button
                  onClick={archiveSelected}
                  className="flex-1 px-2 py-1.5 text-xs bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded-lg hover:bg-amber-200 dark:hover:bg-amber-900/50"
                >
                                    {t('sidebar.archive')} ({selectedSessions.size})
                </button>
                <button
                  onClick={() => {
                    if (confirm(t('confirm.deleteMultiple', { count: selectedSessions.size }))) {
                      deleteSelected()
                    }
                  }}
                  className="flex-1 px-2 py-1.5 text-xs bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50"
                >
                                    {t('common.delete')} ({selectedSessions.size})
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              onClick={() => handleNewChat()}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm font-medium text-primary-900 dark:text-gray-200"
            >
              <PlusIcon className="h-5 w-5" />
              {t('sidebar.newChat')}
            </button>
            <button
              onClick={toggleSelectMode}
              className="px-3 py-3 rounded-xl border border-surface-variant dark:border-gray-600 hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors text-sm text-secondary dark:text-gray-400"
              title={t('sidebar.selectMultiple')}
            >
              <CheckIcon className="h-5 w-5" />
            </button>
          </div>
        )}
      </div>

      {/* Sidebar Warnings */}
      <SidebarWarningToast warnings={sidebarWarnings} onDismiss={handleDismissWarning} />

      {/* Folders Section (Projects) */}
      <div className="px-3 flex-shrink-0">
        <div className="text-xs font-medium text-secondary dark:text-gray-500 uppercase tracking-wider px-2 py-2">
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
              <CheckIcon className="h-4 w-4 text-green-500" />
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
                  {(sessionsByFolder.get(folder.id) || []).map(session => (
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
      <div className="mx-3 my-2 border-t border-surface-variant dark:border-gray-700" />

      {/* Scrollable Chat List */}
      <div className="flex-1 overflow-y-auto px-3">
        {isSidebarLoading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin h-6 w-6 border-2 border-primary border-t-transparent rounded-full" />
          </div>
        ) : (
          <div className="space-y-0.5">
            {/* Pinned Sessions */}
            {pinnedSessions.length > 0 && (
              <div className="mb-2">
                <div className="px-2 py-1 text-xs font-medium text-secondary dark:text-gray-500 uppercase tracking-wider">
                  {t('sidebar.pinned')}
                </div>
                {pinnedSessions.map(session => (
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
            {(sessionsByFolder.get(null) || []).length > 0 && (
              <div>
                <div className="px-2 py-1 text-xs font-medium text-secondary dark:text-gray-500 uppercase tracking-wider">
                  {t('sidebar.yourChats')}
                </div>
                {(sessionsByFolder.get(null) || []).map(session => (
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

      {/* Bottom Sticky Section - User Menu */}
      <div className="flex-shrink-0 border-t border-surface-variant dark:border-gray-700 p-3" ref={userMenuRef}>
        {/* User Menu Dropdown (appears above the button) */}
        {userMenuOpen && (
          <div className="absolute bottom-20 left-3 right-3 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-surface-variant dark:border-gray-600 py-2 z-50">
            {/* User Info */}
            {isAuthenticated && user && (
              <div className="px-4 py-2 border-b border-surface-variant dark:border-gray-700">
                <div className="font-medium text-sm text-primary-900 dark:text-gray-200">{user.name}</div>
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
                        : 'text-secondary dark:text-gray-400 hover:bg-surface-variant dark:hover:bg-gray-700'
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
                        : 'text-secondary dark:text-gray-400 hover:bg-surface-variant dark:hover:bg-gray-700'
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
                      {systemMenuItems.map((subItem) => {
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
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-sm font-medium">
            {isAuthenticated && user ? (
              user.name.charAt(0).toUpperCase()
            ) : (
              <svg className="h-5 w-5" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 10h6c2.76 0 5 2.24 5 5s-2.24 5-5 5h-4" stroke="white" strokeWidth="2.5" strokeLinecap="round" fill="none"/>
                <path d="M14 18l-3 3-3-3" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
                <path d="M11 21v2" stroke="white" strokeWidth="2.5" strokeLinecap="round"/>
              </svg>
            )}
          </div>
          <div className="flex-1 text-left">
            <div className="text-sm font-medium text-primary-900 dark:text-gray-200 truncate">
              {isAuthenticated && user ? user.name : 'RecallHub'}
            </div>
            {isAuthenticated && user && (
              <div className="text-xs text-secondary dark:text-gray-500 truncate">{user.email}</div>
            )}
          </div>
          <EllipsisHorizontalIcon className="h-5 w-5 text-secondary dark:text-gray-400" />
        </button>
      </div>
    </div>
  )

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

      {/* Desktop sidebar */}
      <div className="hidden lg:fixed lg:inset-y-0 lg:flex lg:w-72 lg:flex-col">
        <div className="flex grow flex-col bg-surface dark:bg-gray-800 shadow-elevation-1">
          <SidebarContent />
        </div>
      </div>

      {/* Main content */}
      <div className="lg:pl-72">
        {/* Top bar - only show on non-chat pages or mobile */}
        <div className="sticky top-0 z-30 flex h-14 items-center gap-x-4 bg-surface/95 dark:bg-gray-800/95 px-4 shadow-sm backdrop-blur lg:hidden">
          <button
            type="button"
            className="-m-2.5 p-2.5 text-secondary dark:text-gray-400"
            onClick={() => setSidebarOpen(true)}
          >
            <Bars3Icon className="h-6 w-6" />
          </button>
          <div className="flex flex-1 items-center justify-between">
            <h1 className="text-lg font-semibold text-primary-900 dark:text-primary-200">
              {getPageTitle()}
            </h1>
          </div>
        </div>

        {/* Page content */}
        <main className={isOnChatPage || isOnDashboardPage ? (isOnDashboardPage ? 'py-6 px-4 sm:px-6 lg:px-8' : '') : 'py-6 px-4 sm:px-6 lg:px-8'}>
          <Outlet />
        </main>
      </div>

      {/* Context Menu */}
      {contextMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setContextMenu(null)}
          />
          <div
            className="fixed z-50 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-surface-variant dark:border-gray-600 py-1 min-w-[160px]"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            {(() => {
              const session = sessions.find(s => s.id === contextMenu.sessionId)
              if (!session) return null
              return (
                <>
                  <button
                    onClick={() => handleTogglePin(session.id, session.is_pinned)}
                    className="w-full flex items-center gap-2 px-4 py-2 text-sm hover:bg-surface-variant dark:hover:bg-gray-700 dark:text-gray-200"
                  >
                    {session.is_pinned ? (
                      <>
                        <StarIcon className="h-4 w-4" />
                        {t('sidebar.unpin')}
                      </>
                    ) : (
                      <>
                        <StarIconSolid className="h-4 w-4 text-yellow-500" />
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
                    className="w-full flex items-center gap-2 px-4 py-2 text-sm hover:bg-surface-variant dark:hover:bg-gray-700 dark:text-gray-200"
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
        <StarIconSolid className="h-3 w-3 text-yellow-500 flex-shrink-0" />
      )}
    </div>
  )
}

// Named export for testing
export { Layout }
