import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { ReactNode } from 'react'
import { act, cleanup, render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { Layout } from './Layout'

const mockFns = vi.hoisted(() => ({
  logout: vi.fn(),
  navigate: vi.fn(),
  handleNewChat: vi.fn(),
  handleSelectSession: vi.fn(),
  handleDeleteSession: vi.fn(),
  handleTogglePin: vi.fn(),
  handleUpdateTitle: vi.fn(),
  handleCreateFolder: vi.fn(),
  handleDeleteFolder: vi.fn(),
  toggleFolder: vi.fn(),
  setEditingTitle: vi.fn(),
  setEditingTitleValue: vi.fn(),
  setShowNewFolder: vi.fn(),
  setNewFolderName: vi.fn(),
  setContextMenu: vi.fn(),
  toggleSelectMode: vi.fn(),
  toggleSessionSelection: vi.fn(),
  selectAllSessions: vi.fn(),
  clearSelection: vi.fn(),
  archiveSelected: vi.fn(),
  deleteSelected: vi.fn(),
  moveSelectedToFolder: vi.fn(),
  getDashboard: vi.fn(),
  listProfiles: vi.fn(),
  switchProfile: vi.fn(),
}))

const mockStore = vi.hoisted(() => ({
  state: {
    sessions: [] as Array<Record<string, unknown>>,
    folders: [] as Array<Record<string, unknown>>,
    isSelectMode: false,
    selectedSessions: new Set<string>(),
    isSidebarLoading: false,
    showNewFolder: false,
    newFolderName: '',
    collapsedFolders: new Set<string>(),
    currentSession: null as Record<string, unknown> | null,
    contextMenu: null as { sessionId: string; x: number; y: number } | null,
    editingTitle: null as string | null,
    editingTitleValue: '',
    isAuthLoading: false,
    isAuthenticated: false,
    user: {
      id: 'user-1',
      name: 'Test User',
      email: 'test@example.com',
      is_admin: false,
      title_prefix: 'Dr.',
      title_suffix: 'PhD',
    } as Record<string, unknown> | null,
    tenant: {
      branding: {
        appName: 'RecallHub',
        iconUrl: null as string | null,
      },
      features: {
        showCloudSources: true,
        showEmailConfig: true,
        showProfiles: true,
        showApiDocs: true,
        showStrategies: true,
        showEmbeddingBenchmark: true,
        showBackups: true,
      },
    },
    profilesResponse: {
      profiles: {},
      active_profile: null,
    } as {
      profiles: Record<string, { name: string; description?: string }>,
      active_profile: string | null,
    },
    dashboardResponse: { indexes: [] as Array<{ type: string; status: string }> },
  },
}))

const translations: Record<string, string> = {
  'sidebar.newChat': 'New chat',
  'sidebar.projects': 'Projects',
  'sidebar.newProject': 'New project',
  'sidebar.noChats': 'No chats yet',
  'sidebar.pinned': 'Pinned',
  'sidebar.yourChats': 'Your chats',
  'sidebar.selectAll': 'Select All',
  'sidebar.deselectAll': 'Deselect All',
  'sidebar.searchDocuments': 'Search documents',
  'sidebar.searchChats': 'Search chats',
  'sidebar.folderName': 'Folder name',
  'sidebar.moveToProject': 'Move to project',
  'sidebar.unfiled': 'Unfiled',
  'sidebar.noProjects': 'No projects',
  'sidebar.home': 'Home',
  'sidebar.editChats': 'Edit chats',
  'sidebar.collapse': 'Collapse',
  'sidebar.expand': 'Expand',
  'sidebar.datenimport': 'Import data',
  'sidebar.archive': 'Archive',
  'sidebar.rename': 'Rename',
  'sidebar.pin': 'Pin',
  'sidebar.unpin': 'Unpin',
  'common.selected': 'selected',
  'common.cancel': 'Clear',
  'common.delete': 'Delete',
  'common.loading': 'Loading...',
  'nav.documents': 'Documents',
  'nav.chat': 'Chat',
  'nav.dashboard': 'Home',
  'nav.search': 'Search',
  'nav.archivedChats': 'Archived chats',
  'nav.cloudSources': 'Cloud sources',
  'nav.emailCloudConfig': 'Email configuration',
  'nav.apiDocs': 'API docs',
  'nav.profiles': 'Profiles',
  'nav.status': 'Status',
  'nav.searchIndexes': 'Search indexes',
  'nav.ingestion': 'Ingestion',
  'nav.configuration': 'Configuration',
  'nav.users': 'Users',
  'nav.prompts': 'Prompts',
  'nav.apiKeys': 'API keys',
  'nav.strategies': 'Strategies',
  'nav.backups': 'Backups',
  'nav.system': 'System',
  'nav.theme': 'Theme',
  'nav.knowledgeProfiles': 'Knowledge profiles',
  'nav.signOut': 'Sign out',
  'nav.signIn': 'Sign in',
  'language.select': 'Language',
  'warnings.missingBothIndexes': 'Both indexes are missing',
  'warnings.missingVectorIndex': 'Vector index is missing',
  'warnings.missingTextIndex': 'Text index is missing',
  'warnings.createIndexes': 'Create indexes',
  'dashboard.profiles.active': 'Active',
  'confirm.deleteMultiple': 'Delete selected chats?',
  'confirm.deleteFolder': 'Delete folder?',
  'confirm.deleteChat': 'Delete chat?',
  'chat.newChat': 'New chat',
}

const mockSessions = [
  {
    id: 'session-1',
    title: 'General Chat',
    is_pinned: false,
    folder_id: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
  {
    id: 'session-2',
    title: 'Pinned Chat',
    is_pinned: true,
    folder_id: null,
    created_at: '2025-01-02T00:00:00Z',
    updated_at: '2025-01-02T00:00:00Z',
  },
  {
    id: 'session-3',
    title: 'Folder Chat',
    is_pinned: false,
    folder_id: 'folder-1',
    created_at: '2025-01-03T00:00:00Z',
    updated_at: '2025-01-03T00:00:00Z',
  },
]

const mockFolders = [
  { id: 'folder-1', name: 'Project A', color: '#3b82f6', created_at: '2025-01-01T00:00:00Z' },
]

const stableT = (key: string, _params?: Record<string, unknown>) => translations[key] ?? key
const stableI18n = { language: 'en', changeLanguage: async () => {} }
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: stableT, i18n: stableI18n }),
  Trans: ({ children }: { children?: unknown }) => children,
  initReactI18next: { type: '3rdParty', init: () => {} },
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockStore.state.user,
    isLoading: mockStore.state.isAuthLoading,
    isAuthenticated: mockStore.state.isAuthenticated,
    sessionExpired: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: mockFns.logout,
    refreshUser: vi.fn(),
    dismissSessionExpired: vi.fn(),
  }),
}))

vi.mock('../contexts/TenantContext', () => ({
  useTenant: () => ({
    tenant: mockStore.state.tenant,
  }),
}))

vi.mock('../contexts/ChatSidebarContext', () => ({
  useChatSidebar: () => ({
    sessions: mockStore.state.sessions,
    folders: mockStore.state.folders,
    currentSession: mockStore.state.currentSession,
    isSidebarLoading: mockStore.state.isSidebarLoading,
    collapsedFolders: mockStore.state.collapsedFolders,
    editingTitle: mockStore.state.editingTitle,
    editingTitleValue: mockStore.state.editingTitleValue,
    showNewFolder: mockStore.state.showNewFolder,
    newFolderName: mockStore.state.newFolderName,
    contextMenu: mockStore.state.contextMenu,
    isSelectMode: mockStore.state.isSelectMode,
    selectedSessions: mockStore.state.selectedSessions,
    handleNewChat: mockFns.handleNewChat,
    handleSelectSession: mockFns.handleSelectSession,
    handleDeleteSession: mockFns.handleDeleteSession,
    handleTogglePin: mockFns.handleTogglePin,
    handleUpdateTitle: mockFns.handleUpdateTitle,
    handleCreateFolder: mockFns.handleCreateFolder,
    handleDeleteFolder: mockFns.handleDeleteFolder,
    toggleFolder: mockFns.toggleFolder,
    setEditingTitle: mockFns.setEditingTitle,
    setEditingTitleValue: mockFns.setEditingTitleValue,
    setShowNewFolder: mockFns.setShowNewFolder,
    setNewFolderName: mockFns.setNewFolderName,
    setContextMenu: mockFns.setContextMenu,
    toggleSelectMode: mockFns.toggleSelectMode,
    toggleSessionSelection: mockFns.toggleSessionSelection,
    selectAllSessions: mockFns.selectAllSessions,
    clearSelection: mockFns.clearSelection,
    archiveSelected: mockFns.archiveSelected,
    deleteSelected: mockFns.deleteSelected,
    moveSelectedToFolder: mockFns.moveSelectedToFolder,
  }),
}))

vi.mock('./ThemeSwitcher', () => ({
  default: () => <div>Theme Switcher</div>,
}))

vi.mock('./LanguageSwitcher', () => ({
  default: () => <div>Language Switcher</div>,
}))

vi.mock('./ConnectionStatus', () => ({
  default: () => <div>Connected</div>,
}))

vi.mock('./SupportRequestButton', () => ({
  default: () => null,
}))

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    addToast: vi.fn(),
    removeToast: vi.fn(),
    clearToasts: vi.fn(),
    toasts: [],
  }),
  ToastProvider: ({ children }: { children: React.ReactNode }) => children,
}))

vi.mock('./CommandPalette', () => ({
  default: ({ isOpen }: { isOpen: boolean }) => isOpen ? <div>Palette open</div> : null,
  useCommandPalette: () => ({
    isOpen: false,
    open: vi.fn(),
    close: vi.fn(),
    toggle: vi.fn(),
  }),
}))

vi.mock('./SidebarWarningToast', () => ({
  default: ({
    warnings,
    onDismiss,
  }: {
    warnings: Array<{ id: string; message: string; actionLabel: string }>;
    onDismiss: (id: string) => void;
  }) => (
    <div>
      {warnings.map((warning) => (
        <div key={warning.id}>
          <span>{warning.message}</span>
          <span>{warning.actionLabel}</span>
          <button onClick={() => onDismiss(warning.id)}>Dismiss {warning.id}</button>
        </div>
      ))}
    </div>
  ),
}))

vi.mock('./LocalizedLink', () => ({
  LocalizedLink: ({
    children,
    to,
    onClick,
    title,
  }: {
      children: ReactNode;
    to: string;
    onClick?: () => void;
    title?: string;
  }) => (
    <a href={to} onClick={onClick} title={title}>
      {children}
    </a>
  ),
  useLocalizedNavigate: () => mockFns.navigate,
}))

vi.mock('../api/client', () => ({
  indexesApi: {
    getDashboard: mockFns.getDashboard,
  },
  profilesApi: {
    list: mockFns.listProfiles,
    switch: mockFns.switchProfile,
  },
}))

function renderWithRouter(initialPath = '/chat') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<div>Root Content</div>} />
          <Route path="/chat" element={<div>Chat Content</div>} />
          <Route path="/documents" element={<div>Documents Content</div>} />
          <Route path="/dashboard" element={<div>Dashboard Content</div>} />
          <Route path="/search" element={<div>Search Content</div>} />
          <Route path="/system/status" element={<div>Status Content</div>} />
          <Route path="/login" element={<div>Login Content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

function resetStore() {
  mockStore.state.sessions = []
  mockStore.state.folders = []
  mockStore.state.isSelectMode = false
  mockStore.state.selectedSessions = new Set<string>()
  mockStore.state.isSidebarLoading = false
  mockStore.state.showNewFolder = false
  mockStore.state.newFolderName = ''
  mockStore.state.collapsedFolders = new Set<string>()
  mockStore.state.currentSession = null
  mockStore.state.contextMenu = null
  mockStore.state.editingTitle = null
  mockStore.state.editingTitleValue = ''
  mockStore.state.isAuthLoading = false
  mockStore.state.isAuthenticated = false
  mockStore.state.user = {
    id: 'user-1',
    name: 'Test User',
    email: 'test@example.com',
    is_admin: false,
    title_prefix: 'Dr.',
    title_suffix: 'PhD',
  } as Record<string, unknown> | null
  mockStore.state.tenant = {
    branding: {
      appName: 'RecallHub',
      iconUrl: null,
    },
    features: {
      showCloudSources: true,
      showEmailConfig: true,
      showProfiles: true,
      showApiDocs: true,
      showStrategies: true,
      showEmbeddingBenchmark: true,
      showBackups: true,
    },
  }
  mockStore.state.profilesResponse = {
    profiles: {},
    active_profile: null,
  }
  mockStore.state.dashboardResponse = { indexes: [] }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetStore()
  localStorage.clear()
  mockFns.logout.mockResolvedValue(undefined)
  mockFns.getDashboard.mockImplementation(async () => mockStore.state.dashboardResponse)
  mockFns.listProfiles.mockImplementation(async () => mockStore.state.profilesResponse)
  mockFns.switchProfile.mockResolvedValue(undefined)
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Layout', () => {
  it('renders the sidebar shell, outlet, and authenticated user info', async () => {
    mockStore.state.isAuthenticated = true
    const firstRender = renderWithRouter('/chat')

    await waitFor(() => expect(mockFns.listProfiles).toHaveBeenCalled())
    expect(screen.getAllByText('New chat').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Projects').length).toBeGreaterThan(0)
    expect(screen.getByText('Chat Content')).toBeInTheDocument()
    expect(screen.getAllByText('Dr. Test User').length).toBeGreaterThan(0)
    expect(screen.getAllByText('PhD').length).toBeGreaterThan(0)
    expect(screen.getAllByText('test@example.com').length).toBeGreaterThan(0)
  })

  it('shows loading and empty states', async () => {
    mockStore.state.isAuthLoading = true

    renderWithRouter('/chat')

    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.getAllByText('No chats yet').length).toBeGreaterThan(0)
  })

  it('renders the search chats control when sessions are present', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.folders = mockFolders

    renderWithRouter('/chat')

    expect(screen.getAllByRole('button', { name: 'Search chats' }).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Pinned Chat').length).toBeGreaterThan(0)
  })

  it('renders pinned, foldered, loading, and editable sessions', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.folders = mockFolders
    mockStore.state.isSidebarLoading = true
    mockStore.state.editingTitle = 'session-3'
    mockStore.state.editingTitleValue = 'Folder Draft'
    mockStore.state.currentSession = mockSessions[0]

    const firstRender = renderWithRouter('/chat')

    expect(screen.getAllByDisplayValue('Folder Draft').length).toBeGreaterThan(0)

    firstRender.unmount()
    mockStore.state.isSidebarLoading = false
    renderWithRouter('/chat')

    expect(screen.getAllByText('Pinned').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Project A').length).toBeGreaterThan(0)
    const editableInput = screen.getAllByDisplayValue('Folder Draft')[0]
    fireEvent.change(editableInput, { target: { value: 'Folder Draft!' } })
    expect(mockFns.setEditingTitleValue).toHaveBeenCalled()
    fireEvent.keyDown(editableInput, { key: 'Enter' })
    expect(mockFns.handleUpdateTitle).toHaveBeenCalledWith('session-3')
  })

  it('supports select mode actions and bulk delete confirmation', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.folders = mockFolders
    mockStore.state.isSelectMode = true
    mockStore.state.selectedSessions = new Set(['session-1'])

    renderWithRouter('/chat')

    expect(screen.getAllByText(/1 selected/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Select All').length).toBeGreaterThan(0)

    fireEvent.click(screen.getAllByText('Archive (1)')[0])
    expect(mockFns.archiveSelected).toHaveBeenCalled()

    fireEvent.click(screen.getAllByText('Delete (1)')[0])
    expect(window.confirm).toHaveBeenCalled()
    expect(mockFns.deleteSelected).toHaveBeenCalled()
  })

  it('uses deselect all when every session is selected', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.isSelectMode = true
    mockStore.state.selectedSessions = new Set(mockSessions.map((session) => session.id))

    renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('Deselect All')[0])
    expect(mockFns.clearSelection).toHaveBeenCalled()
  })

  it('creates and cancels new folders from the inline input', () => {
    mockStore.state.showNewFolder = true
    mockStore.state.newFolderName = 'Quarterly'

    renderWithRouter('/chat')

    const input = screen.getAllByPlaceholderText('Folder name')[0]
    fireEvent.change(input, { target: { value: 'Quarterly Plan' } })
    expect(mockFns.setNewFolderName).toHaveBeenCalled()

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockFns.handleCreateFolder).toHaveBeenCalled()

    fireEvent.keyDown(input, { key: 'Escape' })
    expect(mockFns.setShowNewFolder).toHaveBeenCalledWith(false)
  })

  it('renders folder sessions and lets you collapse folders', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.folders = mockFolders

    renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('Project A')[0])
    expect(mockFns.toggleFolder).toHaveBeenCalledWith('folder-1')
    expect(screen.getAllByText('Folder Chat').length).toBeGreaterThan(0)
  })

  it('opens the context menu and handles pin, rename, and delete branches', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.contextMenu = { sessionId: 'session-1', x: 40, y: 80 }

    renderWithRouter('/chat')

    fireEvent.click(screen.getByText('Pin'))
    expect(mockFns.handleTogglePin).toHaveBeenCalledWith('session-1', false)

    fireEvent.click(screen.getByText('Rename'))
    expect(mockFns.setEditingTitle).toHaveBeenCalledWith('session-1')
    expect(mockFns.setEditingTitleValue).toHaveBeenCalledWith('General Chat')
    expect(mockFns.setContextMenu).toHaveBeenCalledWith(null)

    ;(window.confirm as ReturnType<typeof vi.fn>).mockReturnValueOnce(false)
    fireEvent.click(screen.getByText('Delete'))
    expect(mockFns.handleDeleteSession).not.toHaveBeenCalled()
    expect(mockFns.setContextMenu).toHaveBeenCalledWith(null)
  })

  it('renders the unpin action for pinned sessions and skips unknown context menu sessions', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.contextMenu = { sessionId: 'session-2', x: 10, y: 20 }

    const firstRender = renderWithRouter('/chat')

    fireEvent.click(screen.getByText('Unpin'))
    expect(mockFns.handleTogglePin).toHaveBeenCalledWith('session-2', true)

    firstRender.unmount()
    mockStore.state.contextMenu = { sessionId: 'missing-session', x: 1, y: 1 }
    renderWithRouter('/chat')

    expect(screen.queryByText('Rename')).not.toBeInTheDocument()
  })

  it('shows sidebar warnings for missing indexes', async () => {
    mockStore.state.isAuthenticated = true
    mockStore.state.user = { ...mockStore.state.user, is_admin: true }
    mockStore.state.dashboardResponse = { indexes: [] }

    renderWithRouter('/chat')

    await waitFor(() => expect(screen.getAllByText('Both indexes are missing').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Create indexes').length).toBeGreaterThan(0)
  })

  it('covers vector-only and text-only index warning branches and the error fallback', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockStore.state.isAuthenticated = true
    mockStore.state.user = { ...mockStore.state.user, is_admin: true }
    mockStore.state.dashboardResponse = {
      indexes: [{ type: 'search', status: 'READY' }],
    }

    let currentRender = renderWithRouter('/chat')
    await waitFor(() => expect(screen.getAllByText('Vector index is missing').length).toBeGreaterThan(0))

    currentRender.unmount()
    mockFns.getDashboard.mockResolvedValueOnce({
      indexes: [{ type: 'vector', status: 'READY' }],
    })
    currentRender = renderWithRouter('/chat')
    await waitFor(() => expect(screen.getAllByText('Text index is missing').length).toBeGreaterThan(0))

    currentRender.unmount()
    mockFns.getDashboard.mockRejectedValueOnce(new Error('index lookup failed'))
    renderWithRouter('/chat')
    await waitFor(() => expect(consoleError).toHaveBeenCalledWith('Error checking indexes:', expect.any(Error)))
  })

  it('skips index checks for non-admin users', async () => {
    mockStore.state.isAuthenticated = true
    mockStore.state.user = {
      ...mockStore.state.user,
      is_admin: false,
    }

    renderWithRouter('/chat')

    await waitFor(() => expect(mockFns.listProfiles).toHaveBeenCalled())
    expect(mockFns.getDashboard).not.toHaveBeenCalled()
    expect(screen.queryByTitle('Import data')).not.toBeInTheDocument()
  })

  it('opens the user menu, filters feature-gated items, and logs out', async () => {
    const user = userEvent.setup()
    mockStore.state.isAuthenticated = true
    mockStore.state.tenant.features.showCloudSources = false
    mockStore.state.tenant.features.showEmailConfig = false
    mockStore.state.tenant.features.showProfiles = false
    mockStore.state.tenant.features.showApiDocs = false

    renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('test@example.com')[0].closest('button')!)
    await waitFor(() => expect(screen.getAllByText('Theme Switcher').length).toBeGreaterThan(0))

    expect(screen.getAllByText('Documents').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('Cloud sources')).toHaveLength(0)
    expect(screen.queryAllByText('Email configuration')).toHaveLength(0)
    expect(screen.queryAllByText('Profiles')).toHaveLength(0)
    expect(screen.queryAllByText('API docs')).toHaveLength(0)

    await user.click(screen.getAllByText('Sign out')[0])
    await waitFor(() => expect(mockFns.logout).toHaveBeenCalled())
    expect(mockFns.navigate).toHaveBeenCalledWith('/login')
  })

  it('supports unauthenticated sign-in flow and tenant branding fallback', async () => {
    const user = userEvent.setup()
    mockStore.state.isAuthenticated = false
    mockStore.state.user = null

    renderWithRouter('/documents')

    fireEvent.click(screen.getAllByText('RecallHub')[0].closest('button')!)
    await waitFor(() => expect(screen.getAllByText('Theme Switcher').length).toBeGreaterThan(0))
    await user.click(screen.getAllByText('Sign in')[0])

    expect(mockFns.navigate).toHaveBeenCalledWith('/login')
    expect(screen.getAllByText('Documents').length).toBeGreaterThan(0)
  })

  it('opens the system submenu and shows filtered admin entries', async () => {
    mockStore.state.isAuthenticated = true
    mockStore.state.user = { ...mockStore.state.user, is_admin: true }
    mockStore.state.tenant.features.showStrategies = false
    mockStore.state.tenant.features.showBackups = false

    renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('test@example.com')[0].closest('button')!)
    await waitFor(() => expect(screen.getAllByText('Theme Switcher').length).toBeGreaterThan(0))
    expect(screen.queryAllByText('Search indexes')).toHaveLength(0)
    expect(screen.queryAllByText('Strategies')).toHaveLength(0)
    expect(screen.queryAllByText('Backups')).toHaveLength(0)

    fireEvent.click(screen.getAllByText('System')[0])
    await waitFor(() => expect(screen.getAllByText('Search indexes').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Status').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('Strategies')).toHaveLength(0)
    expect(screen.queryAllByText('Backups')).toHaveLength(0)
  })

  it('renders the active knowledge profile when profiles are loaded', async () => {
    mockStore.state.isAuthenticated = true
    mockStore.state.profilesResponse = {
      profiles: {
        research: { name: 'Research', description: 'Research profile' },
        support: { name: 'Support', description: 'Support profile' },
      },
      active_profile: 'research',
    }

    renderWithRouter('/documents')

    await waitFor(() => expect(screen.getAllByText('Research').length).toBeGreaterThan(0))
    expect(screen.queryAllByText('Support')).toHaveLength(0)
  })

  it('logs profile loading errors', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockStore.state.isAuthenticated = true
    mockFns.listProfiles.mockRejectedValueOnce(new Error('profiles failed'))

    renderWithRouter('/chat')
    await waitFor(() => expect(consoleError).toHaveBeenCalledWith('Error fetching profiles for header:', expect.any(Error)))
  })

  it('opens move menus, handles folder moves, empty projects, and dismisses warnings', async () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.folders = mockFolders
    mockStore.state.isSelectMode = true
    mockStore.state.selectedSessions = new Set(['session-1'])
    mockStore.state.isAuthenticated = true
    mockStore.state.user = { ...mockStore.state.user, is_admin: true }
    mockStore.state.dashboardResponse = { indexes: [] }

    const firstRender = renderWithRouter('/chat')

    await waitFor(() => expect(screen.getAllByText('Both indexes are missing').length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByText('Move to project (1)')[0])
    fireEvent.click(screen.getAllByText('Project A')[0])
    expect(mockFns.moveSelectedToFolder).toHaveBeenCalledWith('folder-1')

    fireEvent.click(screen.getAllByText('Dismiss missing-both-indexes')[0])
    await waitFor(() => expect(screen.queryAllByText('Both indexes are missing')).toHaveLength(0))

    firstRender.unmount()
    mockStore.state.folders = []
    renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('Move to project (1)')[0])
    expect(screen.getAllByText('No projects').length).toBeGreaterThan(0)
    fireEvent.click(screen.getAllByText('Unfiled')[0])
    expect(mockFns.moveSelectedToFolder).toHaveBeenCalledWith(null)
  })

  it('handles session row selection, context menus, select mode toggles, and edit cancel', () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.folders = mockFolders

    const firstRender = renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('General Chat')[0])
    fireEvent.click(screen.getAllByText('Folder Chat')[0])
    fireEvent.click(screen.getAllByText('Pinned Chat')[0])
    expect(mockFns.handleSelectSession).toHaveBeenCalledWith('session-1')
    expect(mockFns.handleSelectSession).toHaveBeenCalledWith('session-3')
    expect(mockFns.handleSelectSession).toHaveBeenCalledWith('session-2')

    fireEvent.contextMenu(screen.getAllByText('General Chat')[0], { clientX: 10, clientY: 20 })
    fireEvent.contextMenu(screen.getAllByText('Folder Chat')[0], { clientX: 15, clientY: 25 })
    fireEvent.contextMenu(screen.getAllByText('Pinned Chat')[0], { clientX: 20, clientY: 30 })
    expect(mockFns.setContextMenu).toHaveBeenCalled()

    firstRender.unmount()
    mockStore.state.isSelectMode = true
    renderWithRouter('/chat')

    fireEvent.click(screen.getAllByText('General Chat')[0])
    fireEvent.click(screen.getAllByText('Folder Chat')[0])
    fireEvent.click(screen.getAllByText('Pinned Chat')[0])
    expect(mockFns.toggleSessionSelection).toHaveBeenCalledWith('session-1')
    expect(mockFns.toggleSessionSelection).toHaveBeenCalledWith('session-3')
    expect(mockFns.toggleSessionSelection).toHaveBeenCalledWith('session-2')

    cleanup()
    resetStore()
    mockStore.state.sessions = mockSessions
    mockStore.state.editingTitle = 'session-1'
    mockStore.state.editingTitleValue = 'General Draft'
    renderWithRouter('/chat')

    const editInput = screen.getAllByDisplayValue('General Draft')[0]
    fireEvent.click(editInput)
    fireEvent.keyDown(editInput, { key: 'Escape' })
    expect(mockFns.setEditingTitle).toHaveBeenCalledWith(null)
  })

  it('handles sidebar quick actions, search controls, and sidebar events', async () => {
    mockStore.state.sessions = mockSessions
    mockStore.state.user = { ...mockStore.state.user, is_admin: true }

    renderWithRouter('/documents')

    fireEvent.click(screen.getAllByText('New chat')[0])
    expect(mockFns.handleNewChat).toHaveBeenCalledWith()

    fireEvent.click(screen.getAllByTitle('Edit chats')[0])
    expect(mockFns.toggleSelectMode).toHaveBeenCalled()

    fireEvent.click(screen.getAllByTitle('Collapse')[0])
    expect(screen.getAllByTitle('Expand').length).toBeGreaterThan(0)

    await act(async () => {
      window.dispatchEvent(new Event('toggle-desktop-sidebar'))
    })
    await waitFor(() => expect(screen.queryAllByTitle('Expand')).toHaveLength(0))

    fireEvent.click(screen.getAllByRole('button', { name: 'Search chats' })[0])
    const searchInput = screen.getAllByPlaceholderText('Search chats')[0]
    fireEvent.change(searchInput, { target: { value: 'General' } })
    await waitFor(() => expect(document.querySelector('button.absolute.right-3')).not.toBeNull())
    const clearButton = document.querySelector('button.absolute.right-3')
    fireEvent.click(clearButton!)
    await waitFor(() => expect(screen.queryAllByPlaceholderText('Search chats')).toHaveLength(0))

    await act(async () => {
      window.dispatchEvent(new Event('open-mobile-sidebar'))
    })
    await waitFor(() => expect(document.querySelector('div.fixed.inset-0.z-40.bg-black\\/50')).not.toBeNull())
    const backdrop = document.querySelector('div.fixed.inset-0.z-40.bg-black\\/50')
    expect(backdrop).not.toBeNull()
    fireEvent.click(backdrop!)
    await waitFor(() => expect(document.querySelector('div.fixed.inset-0.z-40.bg-black\\/50')).toBeNull())
  })

  it('opens the profile dropdown, closes it on outside click, and switches profiles', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockStore.state.isAuthenticated = true
    mockStore.state.profilesResponse = {
      profiles: {
        research: { name: 'Research', description: 'Research profile' },
        support: { name: 'Support', description: 'Support profile' },
      },
      active_profile: 'research',
    }

    renderWithRouter('/documents')

    await waitFor(() => expect(screen.getAllByText('Research').length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByText('Research')[0].closest('button')!)
    expect(screen.getAllByText('Support').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Active').length).toBeGreaterThan(0)

    fireEvent.mouseDown(document.body)
    await waitFor(() => expect(screen.queryAllByText('Support')).toHaveLength(0))

    fireEvent.click(screen.getAllByText('Research')[0].closest('button')!)
    fireEvent.click(screen.getAllByText('Support')[0])
    await waitFor(() => expect(mockFns.switchProfile).toHaveBeenCalledWith('support'))
  })

  it('restores the collapsed desktop sidebar and expands it again', async () => {
    const user = userEvent.setup()
    localStorage.setItem('recallhub_sidebar_collapsed', 'true')

    renderWithRouter('/chat')

    const expandButtons = screen.getAllByTitle('Expand')
    expect(expandButtons.length).toBeGreaterThan(0)
    await user.click(expandButtons[0])
    expect(screen.queryByTitle('Expand')).not.toBeInTheDocument()
  })

  it('renders the dashboard title in the header', () => {
    renderWithRouter('/dashboard')

    expect(screen.getByRole('heading', { name: 'RecallHub' })).toBeInTheDocument()
  })

  it('renders tenant icon images and falls back when the image fails', () => {
    mockStore.state.tenant.branding.iconUrl = 'https://example.com/icon.png'

    renderWithRouter('/chat')

    const image = screen.getAllByRole('img', { name: 'RecallHub' })[0]
    expect(image).toHaveAttribute('src', 'https://example.com/icon.png')
    fireEvent.error(image)
    expect(screen.getAllByText('R').length).toBeGreaterThan(0)
  })
})
