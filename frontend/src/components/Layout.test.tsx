/**
 * Focused tests for Layout component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './Layout'
import { mockUser } from '../test/test-utils'

const translations: Record<string, string> = {
  'sidebar.newChat': 'New chat',
  'sidebar.projects': 'Projects',
  'sidebar.newProject': 'New project',
  'sidebar.noChats': 'No chats yet',
  'sidebar.pinned': 'Pinned',
  'sidebar.selectAll': 'Select All',
  'sidebar.deselectAll': 'Deselect All',
  'sidebar.searchDocuments': 'Search documents',
  'sidebar.folderName': 'Folder name',
  'sidebar.moveToProject': 'Move to project',
  'common.selected': 'selected',
  'common.cancel': 'Clear',
  'common.delete': 'Delete',
  'common.loading': 'Loading...',
  'sidebar.archive': 'Archive',
  'nav.documents': 'Documents',
  'nav.chat': 'Chat',
  'nav.dashboard': 'Home',
}

const mockSessions = [
  { id: 'session-1', title: 'Test Chat 1', is_pinned: false, folder_id: null, created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z' },
  { id: 'session-2', title: 'Pinned Chat', is_pinned: true, folder_id: null, created_at: '2025-01-02T00:00:00Z', updated_at: '2025-01-02T00:00:00Z' },
  { id: 'session-3', title: 'Folder Chat', is_pinned: false, folder_id: 'folder-1', created_at: '2025-01-03T00:00:00Z', updated_at: '2025-01-03T00:00:00Z' },
]

const mockFolders = [
  { id: 'folder-1', name: 'Project A', color: '#3b82f6', created_at: '2025-01-01T00:00:00Z' },
]

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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => translations[key] ?? fallback ?? key,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockState.user,
    isLoading: mockState.isAuthLoading,
    isAuthenticated: mockState.isAuthenticated,
    sessionExpired: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
    dismissSessionExpired: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}))

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
    setCurrentSession: vi.fn(),
    setSessions: vi.fn(),
    getPricing: vi.fn().mockReturnValue({ input: 0, output: 0 }),
    toggleSelectMode: vi.fn(),
    toggleSessionSelection: vi.fn(),
    selectAllSessions: vi.fn(),
    clearSelection: vi.fn(),
    archiveSelected: vi.fn(),
    deleteSelected: vi.fn(),
    moveSelectedToFolder: vi.fn(),
  }),
  ChatSidebarProvider: ({ children }: { children: React.ReactNode }) => children,
}))

vi.mock('./ThemeSwitcher', () => ({
  default: () => <div>Theme Switcher</div>,
}))

vi.mock('./LanguageSwitcher', () => ({
  default: () => <div>Language Switcher</div>,
}))

vi.mock('./ThemeToggle', () => ({
  default: () => <button data-testid="theme-toggle">Toggle Theme</button>,
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

const renderWithRouter = (initialPath = '/chat') => {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<div>Home Content</div>} />
          <Route path="/chat" element={<div>Chat Content</div>} />
          <Route path="/documents" element={<div>Documents Content</div>} />
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
  it('renders the main sidebar actions and outlet content', () => {
    renderWithRouter('/chat')

    expect(screen.getAllByText('New chat').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Projects').length).toBeGreaterThan(0)
    expect(screen.getAllByText('New project').length).toBeGreaterThan(0)
    expect(screen.getByText('Chat Content')).toBeInTheDocument()
  })

  it('renders authenticated user information', () => {
    renderWithRouter('/chat')

    expect(screen.getAllByText('Test User').length).toBeGreaterThan(0)
    expect(screen.getAllByText('test@example.com').length).toBeGreaterThan(0)
  })

  it('renders pinned, foldered, and regular chats', () => {
    mockState.sessions = mockSessions
    mockState.folders = mockFolders

    renderWithRouter('/chat')

    expect(screen.getAllByText('Pinned').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Pinned Chat').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Project A').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Folder Chat').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Test Chat 1').length).toBeGreaterThan(0)
  })

  it('renders the empty-chat state when there are no sessions', () => {
    renderWithRouter('/chat')

    expect(screen.getAllByText('No chats yet').length).toBeGreaterThan(0)
  })

  it('renders select mode controls for selected sessions', () => {
    mockState.isSelectMode = true
    mockState.selectedSessions = new Set(['session-1'])

    renderWithRouter('/chat')

    expect(screen.getAllByText(/1\s+selected/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Select All').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Archive (1)').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Delete (1)').length).toBeGreaterThan(0)
  })

  it('renders deselect-all state when select mode has no selected chats', () => {
    mockState.isSelectMode = true
    mockState.selectedSessions = new Set<string>()

    renderWithRouter('/chat')

    expect(screen.getAllByText('Deselect All').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/0\s+selected/).length).toBeGreaterThan(0)
  })

  it('renders the new folder input when creating a project', () => {
    mockState.showNewFolder = true

    renderWithRouter('/chat')

    expect(screen.getAllByPlaceholderText('Folder name').length).toBeGreaterThan(0)
  })

  it('shows the auth loading state', () => {
    mockState.isAuthLoading = true

    renderWithRouter('/chat')

    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })
})
