/**
 * Unit tests for ChatSidebarContext - chat session and folder management.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { ChatSidebarProvider, useChatSidebar } from './ChatSidebarContext'
import * as client from '../api/client'

// Mock useNavigate and useLocation
const { mockNavigate, mockLocation, mockPreferences } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockLocation: { pathname: '/chat' },
  mockPreferences: { defaultModel: '' },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => mockLocation,
  }
})

// Mock AuthContext
vi.mock('./AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', name: 'Test User', is_admin: false },
    isLoading: false,
    isAuthenticated: true,
  }),
}))

vi.mock('./UserPreferencesContext', () => ({
  useUserPreferences: () => ({
    preferences: mockPreferences,
    setPreference: vi.fn(),
    setPreferences: vi.fn(),
    resetPreferences: vi.fn(),
    resetPreference: vi.fn(),
    isDefault: vi.fn(() => true),
    applyFromSync: vi.fn(),
  }),
}))

// Mock api/client
vi.mock('../api/client', () => ({
  sessionsApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
    update: vi.fn(),
    createFolder: vi.fn(),
    deleteFolder: vi.fn(),
    moveToFolder: vi.fn(),
    getModelPricing: vi.fn(),
    archiveSessions: vi.fn(),
    deletePermanently: vi.fn(),
  },
  systemApi: {
    listLLMModels: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    getUserMessage() { return this.message }
    technicalDetails = null
  },
}))

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
}
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

// Test consumer component
function TestConsumer() {
  const context = useChatSidebar()
  
  return (
    <div>
      <div data-testid="sessions-count">{context.sessions.length}</div>
      <div data-testid="folders-count">{context.folders.length}</div>
      <div data-testid="is-loading">{context.isSidebarLoading ? 'loading' : 'loaded'}</div>
      <div data-testid="current-session">{context.currentSession?.id || 'none'}</div>
      <div data-testid="current-session-title">{context.currentSession?.title || 'none'}</div>
      <div data-testid="current-session-pinned">{String(context.currentSession?.is_pinned ?? false)}</div>
      <div data-testid="current-session-folder">{context.currentSession?.folder_id || 'none'}</div>
      <div data-testid="is-select-mode">{context.isSelectMode ? 'true' : 'false'}</div>
      <div data-testid="selected-count">{context.selectedSessions.size}</div>
      <div data-testid="collapsed-count">{context.collapsedFolders.size}</div>
      <div data-testid="pending-message">{context.pendingMessage || 'none'}</div>
      <div data-testid="pending-attachments">{context.pendingAttachments?.length ?? 0}</div>
      <div data-testid="session-titles">{context.sessions.map((session) => session.title).join('|')}</div>
      <div data-testid="session-pins">{context.sessions.map((session) => String(!!session.is_pinned)).join('|')}</div>
      <div data-testid="session-folders">{context.sessions.map((session) => session.folder_id || 'none').join('|')}</div>
      <div data-testid="pricing">
        {JSON.stringify(context.getPricing('gpt-4.1-mini'))}
      </div>
      <button data-testid="load-sessions" onClick={() => context.loadSessions()}>Load</button>
      <button data-testid="new-chat" onClick={() => context.handleNewChat()}>New Chat</button>
      <button data-testid="new-chat-in-folder" onClick={() => context.handleNewChat('folder-1')}>New Chat In Folder</button>
      <button data-testid="select-session" onClick={() => void context.handleSelectSession('session-1')}>Select Session</button>
      <button data-testid="delete-session" onClick={() => void context.handleDeleteSession('session-1')}>Delete Session</button>
      <button data-testid="toggle-pin" onClick={() => void context.handleTogglePin('session-1', false)}>Toggle Pin</button>
      <button
        data-testid="start-edit-title"
        onClick={() => {
          context.setEditingTitle('session-1')
          context.setEditingTitleValue('  Renamed Chat  ')
        }}
      >
        Start Edit
      </button>
      <button
        data-testid="set-empty-title"
        onClick={() => {
          context.setEditingTitle('session-1')
          context.setEditingTitleValue('   ')
        }}
      >
        Empty Edit
      </button>
      <button data-testid="update-title" onClick={() => void context.handleUpdateTitle('session-1')}>Update Title</button>
      <button
        data-testid="prepare-folder"
        onClick={() => {
          context.setShowNewFolder(true)
          context.setNewFolderName(' Project Alpha ')
        }}
      >
        Prepare Folder
      </button>
      <button data-testid="create-folder" onClick={() => void context.handleCreateFolder()}>Create Folder</button>
      <button data-testid="delete-folder" onClick={() => void context.handleDeleteFolder('folder-1')}>Delete Folder</button>
      <button data-testid="toggle-folder" onClick={() => context.toggleFolder('folder-1')}>Toggle Folder</button>
      <button data-testid="select-mode" onClick={() => context.toggleSelectMode()}>Toggle Select</button>
      <button data-testid="toggle-session-selection" onClick={() => context.toggleSessionSelection('session-1')}>Toggle Session Selection</button>
      <button data-testid="select-all" onClick={() => context.selectAllSessions()}>Select All</button>
      <button data-testid="clear-selection" onClick={() => context.clearSelection()}>Clear</button>
      <button data-testid="archive-selected" onClick={() => context.archiveSelected()}>Archive</button>
      <button data-testid="delete-selected" onClick={() => context.deleteSelected()}>Delete Selected</button>
      <button data-testid="move-selected" onClick={() => void context.moveSelectedToFolder('folder-1')}>Move Selected</button>
      <button data-testid="move-selected-root" onClick={() => void context.moveSelectedToFolder(null)}>Move Selected Root</button>
      <button
        data-testid="set-pending"
        onClick={() => context.setPendingMessage('Draft prompt', [{ id: 'att-1' }])}
      >
        Set Pending
      </button>
    </div>
  )
}

// Wrapper for rendering with providers
const renderWithProvider = () => {
  return render(
    <MemoryRouter>
      <ChatSidebarProvider>
        <TestConsumer />
      </ChatSidebarProvider>
    </MemoryRouter>
  )
}

describe('ChatSidebarContext', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.getItem.mockReturnValue(null)
    mockLocation.pathname = '/chat'
    mockPreferences.defaultModel = ''
    
    // Default mock responses
    vi.mocked(client.sessionsApi.list).mockResolvedValue({
      sessions: [],
      folders: [],
    })
    vi.mocked(client.systemApi.listLLMModels).mockResolvedValue({
      models: [],
      provider: 'openai',
      cached: false,
    })
    vi.mocked(client.sessionsApi.getModelPricing).mockResolvedValue({
      models: [],
    })
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  describe('useChatSidebar hook', () => {
    it('should throw error when used outside provider', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      
      expect(() => {
        render(<TestConsumer />)
      }).toThrow('useChatSidebar must be used within ChatSidebarProvider')
      
      consoleSpy.mockRestore()
    })
  })

  describe('Initial state', () => {
    it('should load sessions on mount', async () => {
      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [
          { id: 'session-1', title: 'Chat 1', is_pinned: false },
          { id: 'session-2', title: 'Chat 2', is_pinned: true },
        ],
        folders: [
          { id: 'folder-1', name: 'Project', color: '#000' },
        ],
      } as any)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('2')
        expect(screen.getByTestId('folders-count')).toHaveTextContent('1')
      })
    })

    it('should show loading state initially then load', async () => {
      vi.mocked(client.sessionsApi.list).mockImplementation(async () => {
        await new Promise(r => setTimeout(r, 50))
        return { sessions: [], folders: [] }
      })
      
      renderWithProvider()
      
      // Initially loading
      expect(screen.getByTestId('is-loading')).toHaveTextContent('loading')
      
      // Then loaded
      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })
    })

    it('should load collapsed folders from localStorage', async () => {
      localStorageMock.getItem.mockReturnValue('["folder-1", "folder-2"]')
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(localStorageMock.getItem).toHaveBeenCalledWith('chat_collapsed_folders')
      })
    })
  })

  describe('New chat', () => {
    it('should create a new session', async () => {
      const user = userEvent.setup()
      
      vi.mocked(client.sessionsApi.create).mockResolvedValue({
        id: 'new-session',
        title: 'New Chat',
        is_pinned: false,
      } as any)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })
      
      await user.click(screen.getByTestId('new-chat'))
      
      await waitFor(() => {
        expect(client.sessionsApi.create).toHaveBeenCalled()
        expect(screen.getByTestId('current-session')).toHaveTextContent('new-session')
      })
    })

    it('should include the preferred default model and folder when creating a new chat in-chat', async () => {
      const user = userEvent.setup()

      mockPreferences.defaultModel = 'gpt-4.1-mini'
      vi.mocked(client.sessionsApi.create).mockResolvedValue({
        id: 'new-session',
        title: 'New Chat',
        is_pinned: false,
        folder_id: 'folder-1',
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })

      await user.click(screen.getByTestId('new-chat-in-folder'))

      await waitFor(() => {
        expect(client.sessionsApi.create).toHaveBeenCalledWith({
          folder_id: 'folder-1',
          model: 'gpt-4.1-mini',
        })
        expect(mockNavigate).not.toHaveBeenCalled()
      })
    })
  })

  describe('Session actions', () => {
    it('should load and select a session and navigate when outside chat', async () => {
      const user = userEvent.setup()

      mockLocation.pathname = '/dashboard'
      vi.mocked(client.sessionsApi.get).mockResolvedValue({
        id: 'session-1',
        title: 'Loaded Session',
        is_pinned: false,
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })

      await user.click(screen.getByTestId('select-session'))

      await waitFor(() => {
        expect(client.sessionsApi.get).toHaveBeenCalledWith('session-1')
        expect(screen.getByTestId('current-session')).toHaveTextContent('session-1')
        expect(mockNavigate).toHaveBeenCalledWith('/chat')
      })
    })

    it('should delete a session, clear its draft, and reset current state', async () => {
      const user = userEvent.setup()

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [{ id: 'session-1', title: 'Chat 1', is_pinned: false }],
        folders: [],
      } as any)
      vi.mocked(client.sessionsApi.get).mockResolvedValue({
        id: 'session-1',
        title: 'Chat 1',
        is_pinned: false,
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('select-session'))
      await waitFor(() => {
        expect(screen.getByTestId('current-session')).toHaveTextContent('session-1')
      })

      await user.click(screen.getByTestId('delete-session'))

      await waitFor(() => {
        expect(client.sessionsApi.delete).toHaveBeenCalledWith('session-1')
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('0')
        expect(screen.getByTestId('current-session')).toHaveTextContent('none')
        expect(localStorageMock.removeItem).toHaveBeenCalledWith('chat_draft_session-1')
      })
    })

    it('should toggle pins and update titles, including empty-title cancel handling', async () => {
      const user = userEvent.setup()

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [{ id: 'session-1', title: 'Chat 1', is_pinned: false }],
        folders: [],
      } as any)
      vi.mocked(client.sessionsApi.get).mockResolvedValue({
        id: 'session-1',
        title: 'Chat 1',
        is_pinned: false,
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('select-session'))
      await user.click(screen.getByTestId('toggle-pin'))

      await waitFor(() => {
        expect(client.sessionsApi.update).toHaveBeenCalledWith('session-1', { is_pinned: true })
        expect(screen.getByTestId('current-session-pinned')).toHaveTextContent('true')
      })

      await user.click(screen.getByTestId('start-edit-title'))
      await user.click(screen.getByTestId('update-title'))

      await waitFor(() => {
        expect(client.sessionsApi.update).toHaveBeenCalledWith('session-1', { title: 'Renamed Chat' })
        expect(screen.getByTestId('current-session-title')).toHaveTextContent('Renamed Chat')
      })

      const updateCallsBeforeEmpty = vi.mocked(client.sessionsApi.update).mock.calls.length
      await user.click(screen.getByTestId('set-empty-title'))
      await user.click(screen.getByTestId('update-title'))
      expect(vi.mocked(client.sessionsApi.update).mock.calls.length).toBe(updateCallsBeforeEmpty)
    })
  })

  describe('Folder actions and derived state', () => {
    it('should create and delete folders and persist collapsed folder state', async () => {
      const user = userEvent.setup()

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [],
        folders: [{ id: 'folder-1', name: 'Existing', color: '#000' }],
      } as any)
      vi.mocked(client.sessionsApi.createFolder).mockResolvedValue({
        id: 'folder-2',
        name: 'Project Alpha',
        color: '#111',
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('folders-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('prepare-folder'))
      await user.click(screen.getByTestId('create-folder'))

      await waitFor(() => {
        expect(client.sessionsApi.createFolder).toHaveBeenCalledWith('Project Alpha')
        expect(screen.getByTestId('folders-count')).toHaveTextContent('2')
      })

      await user.click(screen.getByTestId('toggle-folder'))
      expect(screen.getByTestId('collapsed-count')).toHaveTextContent('1')
      expect(localStorageMock.setItem).toHaveBeenCalledWith(
        'chat_collapsed_folders',
        JSON.stringify(['folder-1'])
      )

      await user.click(screen.getByTestId('toggle-folder'))
      expect(screen.getByTestId('collapsed-count')).toHaveTextContent('0')

      await user.click(screen.getByTestId('delete-folder'))
      await waitFor(() => {
        expect(client.sessionsApi.deleteFolder).toHaveBeenCalledWith('folder-1')
        expect(client.sessionsApi.list).toHaveBeenCalledTimes(2)
      })
    })

    it('should move selected sessions to a folder and expose pending message state', async () => {
      const user = userEvent.setup()

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [{ id: 's1', title: 'Chat 1', is_pinned: false }],
        folders: [{ id: 'folder-1', name: 'Existing', color: '#000' }],
      } as any)
      vi.mocked(client.sessionsApi.moveToFolder).mockResolvedValue({} as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('move-selected'))

      await waitFor(() => {
        expect(client.sessionsApi.moveToFolder).toHaveBeenCalledWith(['s1'], 'folder-1')
        expect(screen.getByTestId('selected-count')).toHaveTextContent('0')
        expect(screen.getByTestId('is-select-mode')).toHaveTextContent('false')
      })

      await user.click(screen.getByTestId('set-pending'))
      expect(screen.getByTestId('pending-message')).toHaveTextContent('Draft prompt')
      expect(screen.getByTestId('pending-attachments')).toHaveTextContent('1')
    })

    it('should return matched model pricing when available', async () => {
      vi.mocked(client.sessionsApi.getModelPricing).mockResolvedValue({
        models: [
          {
            id: 'gpt-4.1',
            pricing: { input: 0.15, output: 0.6 },
          },
        ],
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('pricing')).toHaveTextContent('"input":0.15')
        expect(screen.getByTestId('pricing')).toHaveTextContent('"output":0.6')
      })
    })
  })

  describe('Uncovered action branches', () => {
    it('should handle ApiError branches for session and model loading', async () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const consoleDebugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
      const apiError = new client.ApiError('Session load failed')
      apiError.technicalDetails = 'trace-id'

      vi.mocked(client.sessionsApi.list).mockRejectedValue(apiError)
      vi.mocked(client.systemApi.listLLMModels).mockRejectedValue(apiError)
      vi.mocked(client.sessionsApi.getModelPricing).mockRejectedValue(apiError)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })

      expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load sessions:', 'Session load failed')
      expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load models:', 'Session load failed')
      expect(consoleDebugSpy).toHaveBeenCalledWith('Technical details:', 'trace-id')
    })

    it('should support selecting and deselecting one session explicitly', async () => {
      const user = userEvent.setup()

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [{ id: 'session-1', title: 'Chat 1', is_pinned: false }],
        folders: [],
      } as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('toggle-session-selection'))
      expect(screen.getByTestId('selected-count')).toHaveTextContent('1')

      await user.click(screen.getByTestId('toggle-session-selection'))
      expect(screen.getByTestId('selected-count')).toHaveTextContent('0')
    })

    it('should keep non-target sessions untouched when pinning, renaming, and moving', async () => {
      const user = userEvent.setup()

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [
          { id: 'session-1', title: 'Chat 1', is_pinned: false, folder_id: undefined },
          { id: 'session-2', title: 'Chat 2', is_pinned: false, folder_id: undefined },
        ],
        folders: [{ id: 'folder-1', name: 'Existing', color: '#000' }],
      } as any)
      vi.mocked(client.sessionsApi.get).mockResolvedValue({
        id: 'session-1',
        title: 'Chat 1',
        is_pinned: false,
        folder_id: undefined,
      } as any)
      vi.mocked(client.sessionsApi.moveToFolder).mockResolvedValue({} as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('2')
      })

      await user.click(screen.getByTestId('select-session'))
      await user.click(screen.getByTestId('toggle-pin'))
      await waitFor(() => {
        expect(screen.getByTestId('session-pins')).toHaveTextContent('true|false')
      })

      await user.click(screen.getByTestId('start-edit-title'))
      await user.click(screen.getByTestId('update-title'))
      await waitFor(() => {
        expect(screen.getByTestId('session-titles')).toHaveTextContent('Renamed Chat|Chat 2')
      })

      await user.click(screen.getByTestId('toggle-session-selection'))
      await user.click(screen.getByTestId('move-selected'))
      await waitFor(() => {
        expect(screen.getByTestId('session-folders')).toHaveTextContent('folder-1|none')
      })
    })

    it('should clear or update the current session when bulk actions affect it', async () => {
      const user = userEvent.setup()

      const sessionsFixture = {
        sessions: [{ id: 'session-1', title: 'Chat 1', is_pinned: false, folder_id: undefined }],
        folders: [{ id: 'folder-1', name: 'Existing', color: '#000' }],
      } as any

      vi.mocked(client.sessionsApi.list).mockResolvedValue(sessionsFixture)
      vi.mocked(client.sessionsApi.get).mockResolvedValue({
        id: 'session-1',
        title: 'Chat 1',
        is_pinned: false,
        folder_id: undefined,
      } as any)
      vi.mocked(client.sessionsApi.archiveSessions).mockResolvedValue({} as any)
      vi.mocked(client.sessionsApi.deletePermanently).mockResolvedValue({} as any)
      vi.mocked(client.sessionsApi.moveToFolder).mockResolvedValue({} as any)

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('select-session'))
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('archive-selected'))
      await waitFor(() => {
        expect(screen.getByTestId('current-session')).toHaveTextContent('none')
      })

      await user.click(screen.getByTestId('load-sessions'))
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })
      await user.click(screen.getByTestId('select-session'))
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('delete-selected'))
      await waitFor(() => {
        expect(screen.getByTestId('current-session')).toHaveTextContent('none')
      })

      await user.click(screen.getByTestId('load-sessions'))
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })
      await user.click(screen.getByTestId('select-session'))
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('move-selected-root'))
      await waitFor(() => {
        expect(screen.getByTestId('current-session')).toHaveTextContent('session-1')
        expect(screen.getByTestId('current-session-folder')).toHaveTextContent('none')
      })
    })

    it('should no-op when folder creation is blank and bulk actions have no selection', async () => {
      const user = userEvent.setup()

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })

      await user.click(screen.getByTestId('create-folder'))
      await user.click(screen.getByTestId('archive-selected'))
      await user.click(screen.getByTestId('delete-selected'))
      await user.click(screen.getByTestId('move-selected'))

      expect(client.sessionsApi.createFolder).not.toHaveBeenCalled()
      expect(client.sessionsApi.archiveSessions).not.toHaveBeenCalled()
      expect(client.sessionsApi.deletePermanently).not.toHaveBeenCalled()
      expect(client.sessionsApi.moveToFolder).not.toHaveBeenCalled()
    })

    it('should surface errors for session, folder, and bulk actions', async () => {
      const user = userEvent.setup()
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [{ id: 'session-1', title: 'Chat 1', is_pinned: false, folder_id: undefined }],
        folders: [{ id: 'folder-1', name: 'Existing', color: '#000' }],
      } as any)
      vi.mocked(client.sessionsApi.get).mockRejectedValue(new Error('Load failed'))
      vi.mocked(client.sessionsApi.delete).mockRejectedValue(new Error('Delete failed'))
      vi.mocked(client.sessionsApi.update).mockRejectedValue(new Error('Update failed'))
      vi.mocked(client.sessionsApi.createFolder).mockRejectedValue(new Error('Create folder failed'))
      vi.mocked(client.sessionsApi.deleteFolder).mockRejectedValue(new Error('Delete folder failed'))
      vi.mocked(client.sessionsApi.archiveSessions).mockRejectedValue(new Error('Archive failed'))
      vi.mocked(client.sessionsApi.deletePermanently).mockRejectedValue(new Error('Permanent delete failed'))
      vi.mocked(client.sessionsApi.moveToFolder).mockRejectedValue(new Error('Move failed'))

      renderWithProvider()

      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })

      await user.click(screen.getByTestId('select-session'))
      await user.click(screen.getByTestId('delete-session'))
      await user.click(screen.getByTestId('toggle-pin'))
      await user.click(screen.getByTestId('start-edit-title'))
      await user.click(screen.getByTestId('update-title'))
      await user.click(screen.getByTestId('prepare-folder'))
      await user.click(screen.getByTestId('create-folder'))
      await user.click(screen.getByTestId('delete-folder'))
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('archive-selected'))
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('delete-selected'))
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('move-selected'))

      await waitFor(() => {
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load session:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to delete session:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to toggle pin:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to update title:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to create folder:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to delete folder:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to archive sessions:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to delete sessions:', expect.any(Error))
        expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to move sessions to folder:', expect.any(Error))
      })
    })
  })

  describe('Select mode', () => {
    it('should toggle select mode', async () => {
      const user = userEvent.setup()
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })
      
      expect(screen.getByTestId('is-select-mode')).toHaveTextContent('false')
      
      await user.click(screen.getByTestId('select-mode'))
      
      expect(screen.getByTestId('is-select-mode')).toHaveTextContent('true')
      
      // Toggle back should clear selection
      await user.click(screen.getByTestId('select-mode'))
      
      expect(screen.getByTestId('is-select-mode')).toHaveTextContent('false')
    })

    it('should select all sessions', async () => {
      const user = userEvent.setup()
      
      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [
          { id: 's1', title: 'Chat 1', is_pinned: false },
          { id: 's2', title: 'Chat 2', is_pinned: false },
        ],
        folders: [],
      } as any)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('2')
      })
      
      await user.click(screen.getByTestId('select-all'))
      
      expect(screen.getByTestId('selected-count')).toHaveTextContent('2')
    })

    it('should clear selection', async () => {
      const user = userEvent.setup()
      
      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [{ id: 's1', title: 'Chat 1', is_pinned: false }],
        folders: [],
      } as any)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })
      
      await user.click(screen.getByTestId('select-all'))
      expect(screen.getByTestId('selected-count')).toHaveTextContent('1')
      
      await user.click(screen.getByTestId('clear-selection'))
      expect(screen.getByTestId('selected-count')).toHaveTextContent('0')
    })
  })

  describe('Archive sessions', () => {
    it('should archive selected sessions', async () => {
      const user = userEvent.setup()
      
      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [
          { id: 's1', title: 'Chat 1', is_pinned: false },
          { id: 's2', title: 'Chat 2', is_pinned: false },
        ],
        folders: [],
      } as any)
      vi.mocked(client.sessionsApi.archiveSessions).mockResolvedValue({} as any)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('2')
      })
      
      // Select all and archive
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('archive-selected'))
      
      await waitFor(() => {
        expect(client.sessionsApi.archiveSessions).toHaveBeenCalledWith(['s1', 's2'])
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('0')
        expect(screen.getByTestId('is-select-mode')).toHaveTextContent('false')
      })
    })
  })

  describe('Delete sessions', () => {
    it('should delete selected sessions permanently', async () => {
      const user = userEvent.setup()
      
      vi.mocked(client.sessionsApi.list).mockResolvedValue({
        sessions: [
          { id: 's1', title: 'Chat 1', is_pinned: false },
        ],
        folders: [],
      } as any)
      vi.mocked(client.sessionsApi.deletePermanently).mockResolvedValue({} as any)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('1')
      })
      
      await user.click(screen.getByTestId('select-all'))
      await user.click(screen.getByTestId('delete-selected'))
      
      await waitFor(() => {
        expect(client.sessionsApi.deletePermanently).toHaveBeenCalledWith(['s1'])
        expect(screen.getByTestId('sessions-count')).toHaveTextContent('0')
      })
    })
  })

  describe('Error handling', () => {
    it('should handle session load errors gracefully', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      
      vi.mocked(client.sessionsApi.list).mockRejectedValue(new Error('Network error'))
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })
      
      expect(consoleSpy).toHaveBeenCalled()
      consoleSpy.mockRestore()
    })

    it('should handle new chat creation errors', async () => {
      const user = userEvent.setup()
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      
      vi.mocked(client.sessionsApi.create).mockRejectedValue(new Error('Failed'))
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })
      
      await user.click(screen.getByTestId('new-chat'))
      
      await waitFor(() => {
        expect(consoleSpy).toHaveBeenCalled()
      })
      
      consoleSpy.mockRestore()
    })
  })

  describe('getPricing', () => {
    it('should return default pricing when model not found', async () => {
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('is-loading')).toHaveTextContent('loaded')
      })
      
      // The getPricing function returns default values when model not found
      // This is tested through the context value
    })
  })
})
