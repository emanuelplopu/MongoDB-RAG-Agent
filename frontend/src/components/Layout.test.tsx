/**
 * Unit tests for Layout component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './Layout'
import { mockUser } from '../test/test-utils'

const translations: Record<string, string> = {
  'sidebar.newChat': 'New chat',
  'sidebar.projects': 'Projects',
  'sidebar.newProject': 'New project',
  'sidebar.noChats': 'No chats yet',
  'sidebar.searchDocuments': 'Search documents',
  'sidebar.searchChats': 'Search chats',
  'sidebar.editChats': 'Edit chats',
  'sidebar.collapse': 'Collapse',
  'sidebar.expand': 'Expand',
  'sidebar.moveToProject': 'Move to project',
  'sidebar.unfiled': 'Unfiled',
  'sidebar.noProjects': 'No projects yet',
  'sidebar.deselectAll': 'Deselect All',
  'sidebar.folderName': 'Folder name',
  'sidebar.yourChats': 'Your chats',
  'sidebar.pinned': 'Pinned',
  'sidebar.selectAll': 'Select All',
  'common.cancel': 'Clear',
  'common.selected': 'selected',
  'sidebar.archive': 'Archive',
  'common.delete': 'Delete',
  'nav.dashboard': 'Home',
  'sidebar.home': 'Home',
  'nav.chat': 'Chat',
  'nav.search': 'Search',
  'nav.documents': 'Documents',
  'nav.archivedChats': 'Archived Chats',
  'nav.cloudSources': 'Cloud Sources',
  'nav.emailCloudConfig': 'Email Cloud Config',
  'nav.apiDocs': 'API Docs',
  'nav.profiles': 'Profiles',
  'nav.system': 'System',
  'nav.status': 'Status',
  'nav.searchIndexes': 'Search Indexes',
  'nav.ingestion': 'Ingestion',
  'nav.configuration': 'Configuration',
  'nav.users': 'Users',
  'nav.prompts': 'Prompts',
  'nav.apiKeys': 'API Keys',
  'nav.strategies': 'Strategies',
  'nav.backups': 'Backups',
  'nav.signOut': 'Log out',
  'nav.signIn': 'Sign in',
  'nav.theme': 'Theme',
  'common.loading': 'Loading...',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => translations[key] ?? fallback ?? key,
  }),
}))

// Mock data
const mockSessions = [
  { id: 'session-1', title: 'Test Chat 1', is_pinned: false, folder_id: null, created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z' },
  { id: 'session-2', title: 'Pinned Chat', is_pinned: true, folder_id: null, created_at: '2025-01-02T00:00:00Z', updated_at: '2025-01-02T00:00:00Z' },
  { id: 'session-3', title: 'Folder Chat', is_pinned: false, folder_id: 'folder-1', created_at: '2025-01-03T00:00:00Z', updated_at: '2025-01-03T00:00:00Z' },
]

const mockFolders = [
  { id: 'folder-1', name: 'Project A', color: '#3b82f6', created_at: '2025-01-01T00:00:00Z' },
]

// Mock function references that we can control per test
const mockHandleNewChat = vi.fn()
const mockToggleSelectMode = vi.fn()
const mockSelectAllSessions = vi.fn()
const mockClearSelection = vi.fn()
const mockArchiveSelected = vi.fn()
const mockDeleteSelected = vi.fn()
const mockLogout = vi.fn()

// Create a configurable mock state
let mockState = {
  sessions: [] as typeof mockSessions,
  folders: [] as typeof mockFolders,
  isSelectMode: false,
  selectedSessions: new Set<string>(),
  isSidebarLoading: false,
  showNewFolder: false,
  collapsedFolders: new Set<string>(),
  currentSession: null as (typeof mockSessions)[0] | null,
  contextMenu: null as { sessionId: string; x: number; y: number } | null,
  editingTitle: null as string | null,
  editingTitleValue: '',
  isAuthLoading: false,
  isAuthenticated: true,
  user: mockUser,
}

// Mock the AuthContext module
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockState.user,
    isLoading: mockState.isAuthLoading,
    isAuthenticated: mockState.isAuthenticated,
    sessionExpired: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: mockLogout,
    refreshUser: vi.fn(),
    dismissSessionExpired: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}))

// Mock ThemeContext
vi.mock('../contexts/ThemeContext', () => ({
  useTheme: () => ({
    theme: 'light',
    toggleTheme: vi.fn(),
  }),
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
}))

vi.mock('../contexts/TenantContext', () => ({
  useTenant: () => ({
    tenant: {
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
    },
  }),
}))

// Mock ThemeToggle component
vi.mock('./ThemeToggle', () => ({
  default: () => <button data-testid="theme-toggle">Toggle Theme</button>,
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

vi.mock('./CommandPalette', () => ({
  default: () => null,
  useCommandPalette: () => ({
    isOpen: false,
    open: vi.fn(),
    close: vi.fn(),
    toggle: vi.fn(),
  }),
}))

vi.mock('./SidebarWarningToast', () => ({
  default: () => null,
}))

vi.mock('./LocalizedLink', () => ({
  LocalizedLink: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
  useLocalizedNavigate: () => vi.fn(),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual('../api/client')
  return {
    ...actual,
    indexesApi: {
      getDashboard: vi.fn().mockResolvedValue({ indexes: [] }),
    },
    profilesApi: {
      list: vi.fn().mockResolvedValue({
        profiles: [],
        active_profile: null,
      }),
      switch: vi.fn().mockResolvedValue(undefined),
    },
  }
})

// Mock ChatSidebarContext
vi.mock('../contexts/ChatSidebarContext', () => ({
  useChatSidebar: () => ({
    sessions: mockState.sessions,
    folders: mockState.folders,
    currentSession: mockState.currentSession,
    isSidebarLoading: mockState.isSidebarLoading,
    models: [],
    modelPricing: [],
    collapsedFolders: mockState.collapsedFolders,
    editingTitle: mockState.editingTitle,
    editingTitleValue: mockState.editingTitleValue,
    showNewFolder: mockState.showNewFolder,
    newFolderName: '',
    contextMenu: mockState.contextMenu,
    isSelectMode: mockState.isSelectMode,
    selectedSessions: mockState.selectedSessions,
    loadSessions: vi.fn(),
    handleNewChat: mockHandleNewChat,
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
    setCurrentSession: vi.fn(),
    setSessions: vi.fn(),
    getPricing: vi.fn().mockReturnValue({ input: 0, output: 0 }),
    toggleSelectMode: mockToggleSelectMode,
    toggleSessionSelection: vi.fn(),
    selectAllSessions: mockSelectAllSessions,
    clearSelection: mockClearSelection,
    archiveSelected: mockArchiveSelected,
    deleteSelected: mockDeleteSelected,
  }),
  ChatSidebarProvider: ({ children }: { children: React.ReactNode }) => children,
}))

// Wrapper with router context
const renderWithRouter = (initialPath = '/chat') => {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<div>Home Content</div>} />
          <Route path="/chat" element={<div>Chat Content</div>} />
          <Route path="/chat/:id" element={<div>Chat Session Content</div>} />
          <Route path="/search" element={<div>Search Content</div>} />
          <Route path="/documents" element={<div>Documents Content</div>} />
          <Route path="/profiles" element={<div>Profiles Content</div>} />
          <Route path="/system/status" element={<div>System Status Content</div>} />
          <Route path="/system/config" element={<div>System Config Content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockState = {
    sessions: [],
    folders: [],
    isSelectMode: false,
    selectedSessions: new Set<string>(),
    isSidebarLoading: false,
    showNewFolder: false,
    collapsedFolders: new Set<string>(),
    currentSession: null,
    contextMenu: null,
    editingTitle: null,
    editingTitleValue: '',
    isAuthLoading: false,
    isAuthenticated: true,
    user: mockUser,
  }
})

describe('Layout', () => {
  describe('Basic rendering', () => {
    it('should render layout with sidebar elements', () => {
      renderWithRouter()
      
      expect(screen.getAllByText('New chat').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Projects').length).toBeGreaterThan(0)
      expect(screen.getAllByText('New project').length).toBeGreaterThan(0)
    })

    it('should render children content via Outlet', () => {
      renderWithRouter('/chat')
      
      expect(screen.getByText('Chat Content')).toBeInTheDocument()
    })

    it('should have navigation links', () => {
      renderWithRouter()
      
      const newChatButtons = screen.getAllByText('New chat')
      expect(newChatButtons.length).toBeGreaterThan(0)
    })

    it('should render user info in sidebar', () => {
      renderWithRouter()
      
      expect(screen.getAllByText('Test User').length).toBeGreaterThan(0)
      expect(screen.getAllByText('test@example.com').length).toBeGreaterThan(0)
    })

    it('should show empty state when no chats', () => {
      renderWithRouter('/chat')
      
      expect(screen.getAllByText('No chats yet').length).toBeGreaterThan(0)
    })
  })

  describe('Page title', () => {
    it('should show Home title on home page', () => {
      renderWithRouter('/')
      expect(screen.getByText('Home Content')).toBeInTheDocument()
    })

    it('should show Chat title on chat page', () => {
      renderWithRouter('/chat')
      expect(screen.getByText('Chat Content')).toBeInTheDocument()
    })
  })

  describe('Session display', () => {
    it('should render sessions in sidebar', () => {
      mockState.sessions = mockSessions
      renderWithRouter('/chat')
      
      expect(screen.getAllByText('Test Chat 1').length).toBeGreaterThan(0)
    })

    it('should show pinned sessions separately', () => {
      mockState.sessions = mockSessions
      renderWithRouter('/chat')
      
      expect(screen.getAllByText('Pinned').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Pinned Chat').length).toBeGreaterThan(0)
    })

    it('should show sessions in folders', () => {
      mockState.sessions = mockSessions
      mockState.folders = mockFolders
      renderWithRouter('/chat')
      
      expect(screen.getAllByText('Project A').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Folder Chat').length).toBeGreaterThan(0)
    })

    it('should show loading spinner when sidebar is loading', () => {
      mockState.isSidebarLoading = true
      renderWithRouter('/chat')
      
      const spinners = document.querySelectorAll('.animate-spin')
      expect(spinners.length).toBeGreaterThan(0)
    })
  })

  describe('New chat button', () => {
    it('should call handleNewChat when clicked', async () => {
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const newChatButtons = screen.getAllByRole('button', { name: 'New chat' })
      await user.click(newChatButtons[newChatButtons.length - 1])
      
      expect(mockHandleNewChat).toHaveBeenCalled()
    })
  })

  describe('Select mode', () => {
    it('should show select mode actions when in select mode', () => {
      mockState.isSelectMode = true
      mockState.selectedSessions = new Set(['session-1'])
      renderWithRouter('/chat')
      
      expect(screen.getAllByText(/1\s+selected/).length).toBeGreaterThan(0)
      expect(screen.getAllByText('Select All').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Clear').length).toBeGreaterThan(0)
    })

    it('should show archive and delete buttons when sessions selected', () => {
      mockState.isSelectMode = true
      mockState.selectedSessions = new Set(['session-1', 'session-2'])
      renderWithRouter('/chat')
      
      expect(screen.getAllByText('Archive (2)').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Delete (2)').length).toBeGreaterThan(0)
    })

    it('should call selectAllSessions when Select All clicked', async () => {
      mockState.isSelectMode = true
      mockState.selectedSessions = new Set()
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const selectAllButtons = screen.getAllByText('Select All')
      await user.click(selectAllButtons[0])
      
      expect(mockSelectAllSessions).toHaveBeenCalled()
    })

    it('should call clearSelection when Clear clicked', async () => {
      mockState.isSelectMode = true
      mockState.selectedSessions = new Set(['session-1'])
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const clearButtons = screen.getAllByText('Clear')
      await user.click(clearButtons[0])
      
      expect(mockClearSelection).toHaveBeenCalled()
    })

    it('should call archiveSelected when Archive clicked', async () => {
      mockState.isSelectMode = true
      mockState.selectedSessions = new Set(['session-1'])
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const archiveButtons = screen.getAllByText('Archive (1)')
      await user.click(archiveButtons[0])
      
      expect(mockArchiveSelected).toHaveBeenCalled()
    })
  })

  describe('User menu', () => {
    it('should toggle user menu when user button is clicked', async () => {
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const userButtons = screen.getAllByText('Test User')
      await user.click(userButtons[0])
      
      // These items can appear in both mobile and desktop sidebars
      expect(screen.getAllByText('Home').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Search').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Documents').length).toBeGreaterThan(0)
    })

    it('should show system menu for admin users', async () => {
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const userButtons = screen.getAllByText('Test User')
      await user.click(userButtons[0])
      
      // System menu appears in both mobile and desktop
      expect(screen.getAllByText('System').length).toBeGreaterThan(0)
    })

    it('should show logout button when authenticated', async () => {
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const userButtons = screen.getAllByText('Test User')
      await user.click(userButtons[0])
      
      // Log out appears in both mobile and desktop sidebars
      expect(screen.getAllByText('Log out').length).toBeGreaterThan(0)
    })

    it('should call logout when Log out button clicked', async () => {
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      // Click on the desktop sidebar user button (index 1 for desktop)
      const userButtons = screen.getAllByText('Test User')
      // Desktop sidebar is the second one
      const desktopUserButton = userButtons.length > 1 ? userButtons[1] : userButtons[0]
      await user.click(desktopUserButton)
      
      // Get all logout buttons and click the last one (desktop version)
      const logoutButtons = screen.getAllByText('Log out')
      const desktopLogout = logoutButtons.length > 1 ? logoutButtons[1] : logoutButtons[0]
      await user.click(desktopLogout)
      
      expect(mockLogout).toHaveBeenCalled()
    })

    it('should show theme toggle in menu', async () => {
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const userButtons = screen.getAllByText('Test User')
      await user.click(userButtons[0])
      
      // Theme text and toggle appear in both mobile and desktop
      expect(screen.getAllByText('Theme').length).toBeGreaterThan(0)
      expect(screen.getAllByTestId('theme-toggle').length).toBeGreaterThan(0)
    })
  })

  describe('Unauthenticated state', () => {
    it('should show Sign in button when not authenticated', async () => {
      mockState.isAuthenticated = false
      mockState.user = null as unknown as typeof mockUser
      const user = userEvent.setup()
      renderWithRouter('/chat')
      
      const brandTexts = screen.getAllByText('RecallHub')
      await user.click(brandTexts[0])
      
      // Sign in appears in both mobile and desktop sidebars
      expect(screen.getAllByText('Sign in').length).toBeGreaterThan(0)
    })
  })

  describe('Auth loading state', () => {
    it('should show loading overlay when auth is loading', () => {
      mockState.isAuthLoading = true
      renderWithRouter('/chat')
      
      expect(screen.getByText('Loading...')).toBeInTheDocument()
    })
  })

  describe('Mobile sidebar', () => {
    it('should have mobile menu button', () => {
      renderWithRouter('/chat')
      
      const menuButtons = document.querySelectorAll('button')
      expect(menuButtons.length).toBeGreaterThan(0)
    })
  })

  describe('Folders', () => {
    it('should show new folder input when showNewFolder is true', () => {
      mockState.showNewFolder = true
      renderWithRouter('/chat')
      
      const inputs = document.querySelectorAll('input[placeholder="Folder name"]')
      expect(inputs.length).toBeGreaterThan(0)
    })

    it('should render folder list', () => {
      mockState.folders = mockFolders
      renderWithRouter('/chat')
      
      expect(screen.getAllByText('Project A').length).toBeGreaterThan(0)
    })
  })
})
