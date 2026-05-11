import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
const translations: Record<string, string> = {
  'archivedChatsPage.title': 'Archived Chats',
  'archivedChatsPage.subtitle': 'View and manage your archived conversations',
  'archivedChatsPage.loadFailed': 'Failed to load archived chats',
  'archivedChatsPage.restoreFailed': 'Failed to restore sessions',
  'archivedChatsPage.deleteFailed': 'Failed to delete sessions',
  'archivedChatsPage.exportFailed': 'Failed to export session',
  'archivedChatsPage.selectAll': 'Select All',
  'archivedChatsPage.noChats': 'No archived chats',
  'archivedChatsPage.noChatsDesc': 'Chats you archive will appear here',
  'archivedChatsPage.untitledChat': 'Untitled Chat',
  'archivedChatsPage.download': 'Download',
  'archivedChatsPage.restore': 'Restore',
  'archivedChatsPage.deleteForever': 'Delete Forever',
  'archivedChatsPage.aboutTitle': 'About Archived Chats',
  'archivedChatsPage.aboutBullet1': 'Archived chats are hidden from your main chat list',
  'archivedChatsPage.aboutBullet2': 'You can restore them at any time',
  'archivedChatsPage.aboutBullet3': 'Permanently deleted chats cannot be recovered',
  'archivedChatsPage.aboutBullet4': 'Export chats before deleting to keep a backup',
  'archivedChatsPage.dateUnknown': 'Unknown',
}
const stableT = (key: string, params?: Record<string, unknown>) => {
  if (key === 'archivedChatsPage.messagesCount') return `${params?.count ?? 0} messages`
  if (key === 'archivedChatsPage.clearCount') return `Clear (${params?.count ?? 0})`
  if (key === 'archivedChatsPage.restoreCount') return `Restore (${params?.count ?? 0})`
  if (key === 'archivedChatsPage.deleteForeverCount') return `Delete Forever (${params?.count ?? 0})`
  if (key === 'archivedChatsPage.confirmDelete') return `Permanently delete ${params?.count ?? 0} chat(s)?`
  if (key === 'archivedChatsPage.archivedAt') return `Archived: ${params?.date ?? ''}`
  if (key === 'archivedChatsPage.createdAt') return `Created: ${params?.date ?? ''}`
  return translations[key] ?? key
}
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: stableT, i18n: { language: 'en', changeLanguage: async () => {} } }),
  Trans: ({ children }: { children?: unknown }) => children,
  initReactI18next: { type: '3rdParty', init: () => {} },
}))
import type { ChatSession } from '../api/client'
import ArchivedChatsPage from './ArchivedChatsPage'

const listArchivedMock = vi.fn()
const restoreSessionsMock = vi.fn()
const deletePermanentlyMock = vi.fn()
const exportSessionMock = vi.fn()

vi.mock('../api/client', () => ({
  sessionsApi: {
    listArchived: (...args: unknown[]) => listArchivedMock(...args),
    restoreSessions: (...args: unknown[]) => restoreSessionsMock(...args),
    deletePermanently: (...args: unknown[]) => deletePermanentlyMock(...args),
    exportSession: (...args: unknown[]) => exportSessionMock(...args),
  },
}))

const makeSession = (overrides: Partial<ChatSession> = {}): ChatSession => ({
  id: overrides.id ?? 'session-1',
  title: overrides.title ?? 'Important Chat',
  model: overrides.model ?? 'gpt-5.4-mini',
  created_at: overrides.created_at ?? '2026-04-15T10:00:00Z',
  updated_at: overrides.updated_at ?? '2026-04-15T10:05:00Z',
  messages: overrides.messages ?? [],
  stats: overrides.stats ?? {
    total_messages: 3,
    total_input_tokens: 10,
    total_output_tokens: 20,
    total_tokens: 30,
    total_cost_usd: 0.01,
    avg_tokens_per_second: 1,
    avg_latency_ms: 150,
  },
  is_pinned: overrides.is_pinned ?? false,
  is_archived: overrides.is_archived ?? true,
  archived_at: overrides.archived_at ?? '2026-04-16T12:30:00Z',
  profile: overrides.profile,
  folder_id: overrides.folder_id,
})

describe('ArchivedChatsPage', () => {
  const createObjectURLMock = vi.fn(() => 'blob:export-url')
  const revokeObjectURLMock = vi.fn()
  let confirmMock: ReturnType<typeof vi.fn>
  let clickSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    listArchivedMock.mockReset()
    restoreSessionsMock.mockReset()
    deletePermanentlyMock.mockReset()
    exportSessionMock.mockReset()
    createObjectURLMock.mockClear()
    revokeObjectURLMock.mockClear()
    confirmMock = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmMock)
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    })
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  afterEach(() => {
    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('loads archived sessions and restores a bulk selection', async () => {
    const sessions = [
      makeSession(),
      makeSession({
        id: 'session-2',
        title: 'Second Chat',
        stats: {
          total_messages: 8,
          total_input_tokens: 30,
          total_output_tokens: 40,
          total_tokens: 70,
          total_cost_usd: 0.02,
          avg_tokens_per_second: 2,
          avg_latency_ms: 100,
        },
      }),
    ]
    listArchivedMock.mockResolvedValue({ sessions })
    restoreSessionsMock.mockResolvedValue({ success: true, restored_count: 2 })
    const user = userEvent.setup()

    render(<ArchivedChatsPage />)

    expect(await screen.findByText('Important Chat')).toBeInTheDocument()
    expect(screen.getByText('Second Chat')).toBeInTheDocument()
    expect(screen.getByText('3 messages')).toBeInTheDocument()
    expect(screen.getByText('8 messages')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Select All' }))
    expect(screen.getByRole('button', { name: 'Clear (2)' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Restore (2)' }))

    await waitFor(() => {
      expect(restoreSessionsMock).toHaveBeenCalledWith(['session-1', 'session-2'])
    })
    expect(screen.queryByText('Important Chat')).not.toBeInTheDocument()
    expect(screen.queryByText('Second Chat')).not.toBeInTheDocument()
  })

  it('exports a session and supports cancelling permanent deletion', async () => {
    const session = makeSession()
    listArchivedMock.mockResolvedValue({ sessions: [session] })
    exportSessionMock.mockResolvedValue({
      session: {
        title: 'Roadmap / Planning',
      },
      messages: [],
    })
    const user = userEvent.setup()

    render(<ArchivedChatsPage />)

    expect(await screen.findByText('Important Chat')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Download' }))

    await waitFor(() => {
      expect(exportSessionMock).toHaveBeenCalledWith('session-1')
    })
    expect(createObjectURLMock).toHaveBeenCalledTimes(1)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:export-url')

    confirmMock.mockReturnValueOnce(false)
    await user.click(screen.getByRole('button', { name: 'Delete Forever' }))
    expect(deletePermanentlyMock).not.toHaveBeenCalled()
  })

  it('surfaces loading failures and clears the error banner', async () => {
    listArchivedMock.mockRejectedValue(new Error('nope'))
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()

    render(<ArchivedChatsPage />)

    expect(await screen.findByText('Failed to load archived chats')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '×' }))
    expect(screen.queryByText('Failed to load archived chats')).not.toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })
})
