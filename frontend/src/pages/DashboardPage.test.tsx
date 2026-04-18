import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { AttachmentInfo, ChatSession } from '../api/client'
import DashboardPage from './DashboardPage'
import { mockUser } from '../test/test-utils'

const createSessionMock = vi.fn()
const localizedNavigateMock = vi.fn()
const setSessionsMock = vi.fn()
const setCurrentSessionMock = vi.fn()
const setPendingMessageMock = vi.fn()

let authState: {
  user: typeof mockUser | null
  isLoading: boolean
}

let preferencesState: {
  preferences: {
    defaultModel: string | null
  }
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'dashboard.greeting.morning': 'Good morning',
        'dashboard.greeting.afternoon': 'Good afternoon',
        'dashboard.greeting.evening': 'Good evening',
        'dashboard.whereToBegin': 'Where would you like to begin?',
        'dashboard.askAnything': 'Ask anything',
        'dashboard.suggestions.summarize': 'Summarize this document',
        'dashboard.suggestions.findDocument': 'Find the policy document',
        'dashboard.suggestions.compare': 'Compare two reports',
        'dashboard.suggestions.explain': 'Explain the latest results',
        'common.send': 'Send',
        'common.newLine': 'New line',
      }
      return translations[key] ?? key
    },
  }),
}))

vi.mock('../api/client', () => ({
  sessionsApi: {
    create: (...args: unknown[]) => createSessionMock(...args),
  },
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../contexts/ChatSidebarContext', () => ({
  useChatSidebar: () => ({
    setSessions: setSessionsMock,
    setCurrentSession: setCurrentSessionMock,
    setPendingMessage: setPendingMessageMock,
  }),
}))

vi.mock('../contexts/UserPreferencesContext', () => ({
  useUserPreferences: () => preferencesState,
}))

vi.mock('../components/LocalizedLink', () => ({
  useLocalizedNavigate: () => localizedNavigateMock,
}))

vi.mock('../hooks/useLocalStorage', async () => {
  const ReactModule = await vi.importActual<typeof import('react')>('react')
  const actual = await vi.importActual<typeof import('../hooks/useLocalStorage')>('../hooks/useLocalStorage')

  return {
    ...actual,
    useLocalStorage: <T,>(_key: string, initialValue: T) => {
      const [value, setValue] = ReactModule.useState<T>(initialValue)
      return [value, setValue, vi.fn()] as const
    },
  }
})

describe('DashboardPage', () => {
  let fileReaderResult: string | null
  let getHoursSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    authState = {
      user: mockUser,
      isLoading: false,
    }
    preferencesState = {
      preferences: {
        defaultModel: 'gpt-5.4-mini',
      },
    }

    createSessionMock.mockReset()
    localizedNavigateMock.mockReset()
    setSessionsMock.mockReset()
    setCurrentSessionMock.mockReset()
    setPendingMessageMock.mockReset()
    fileReaderResult = 'data:image/png;base64,preview'
    getHoursSpy = vi.spyOn(Date.prototype, 'getHours').mockReturnValue(9)

    class MockFileReader {
      result: string | null = null
      onload: null | (() => void) = null
      onerror: null | (() => void) = null

      readAsDataURL() {
        if (fileReaderResult === null) {
          this.onerror?.()
          return
        }

        this.result = fileReaderResult
        this.onload?.()
      }
    }

    vi.stubGlobal('FileReader', MockFileReader)
  })

  afterEach(() => {
    getHoursSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('shows a loading spinner while auth state is loading', () => {
    authState = {
      user: mockUser,
      isLoading: true,
    }

    const { container } = render(<DashboardPage />)

    expect(container.querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByText(/Where would you like to begin/i)).not.toBeInTheDocument()
  })

  it('renders the greeting and suggestion chips, and fills the composer from a suggestion', async () => {
    const user = userEvent.setup()

    render(<DashboardPage />)

    expect(screen.getByRole('heading', { name: 'Good morning, Test' })).toBeInTheDocument()
    expect(screen.getByText('Where would you like to begin?')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Summarize this document' }))

    const input = screen.getByPlaceholderText('Ask anything')
    expect(input).toHaveValue('Summarize this document')
    expect(input).toHaveFocus()
  })

  it('supports attaching files, computing token estimates, and removing attachments', async () => {
    const user = userEvent.setup()

    render(<DashboardPage />)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(fileInput).not.toBeNull()

    const imageFile = new File(['image-bytes'], 'preview.png', { type: 'image/png' })
    const textFile = new File(['a'.repeat(4000)], 'notes.txt', { type: 'text/plain' })
    const hugeFile = new File(['x'], 'too-big.pdf', { type: 'application/pdf' })
    Object.defineProperty(hugeFile, 'size', { value: 25 * 1024 * 1024 })

    await user.upload(fileInput, [imageFile, textFile, hugeFile])

    expect(await screen.findByText('preview.png')).toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    expect(screen.queryByText('too-big.pdf')).not.toBeInTheDocument()
    expect(screen.getByText(/~765 tokens/)).toBeInTheDocument()
    expect(screen.getByText(/~1,000 tokens/)).toBeInTheDocument()
    expect(screen.getByText('Total: ~1,765 tokens')).toBeInTheDocument()
    expect(screen.getByAltText('preview.png')).toHaveAttribute('src', 'data:image/png;base64,preview')

    const notesChip = screen.getByText('notes.txt').closest('div.relative')
    expect(notesChip).not.toBeNull()
    const removeButton = (notesChip as HTMLElement).querySelector('button')
    expect(removeButton).not.toBeNull()
    await user.click(removeButton as HTMLButtonElement)

    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument()
    expect(screen.getByText('Total: ~765 tokens')).toBeInTheDocument()
  })

  it('supports switching agent mode and closes the selector when clicking outside', async () => {
    const user = userEvent.setup()

    render(<DashboardPage />)

    await user.click(screen.getByRole('button', { name: /Auto/i }))
    expect(screen.getByText('Full orchestrator-worker pipeline for complex questions')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Thinking/i }))
    expect(screen.queryByText('Full orchestrator-worker pipeline for complex questions')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Thinking/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Thinking/i }))
    expect(screen.getByText('Direct search without orchestration for quick answers')).toBeInTheDocument()

    await user.click(document.body)
    expect(screen.queryByText('Direct search without orchestration for quick answers')).not.toBeInTheDocument()
  })

  it('creates a session, stores the pending message, and navigates to chat on submit', async () => {
    const user = userEvent.setup()
    const session: ChatSession = {
      id: 'session-1',
      title: 'New Session',
      model: 'gpt-5.4-mini',
      created_at: '2026-04-18T09:00:00Z',
      updated_at: '2026-04-18T09:00:00Z',
      messages: [],
      stats: {
        total_messages: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        total_tokens: 0,
        total_cost_usd: 0,
        avg_tokens_per_second: 0,
        avg_latency_ms: 0,
      },
      is_pinned: false,
      is_archived: false,
    }
    createSessionMock.mockResolvedValue(session)

    render(<DashboardPage />)

    const input = screen.getByPlaceholderText('Ask anything')
    await user.type(input, '  Please summarize this PDF  ')
    await user.keyboard('{Enter}')

    await waitFor(() => {
      expect(createSessionMock).toHaveBeenCalledWith({ model: 'gpt-5.4-mini' })
    })

    const sessionUpdater = setSessionsMock.mock.calls[0][0] as (sessions: ChatSession[]) => ChatSession[]
    expect(sessionUpdater([{
      ...session,
      id: 'existing',
      title: 'Existing',
    }])).toEqual([
      session,
      {
        ...session,
        id: 'existing',
        title: 'Existing',
      },
    ])
    expect(setCurrentSessionMock).toHaveBeenCalledWith(session)
    expect(setPendingMessageMock).toHaveBeenCalledWith('Please summarize this PDF', null)
    expect(localizedNavigateMock).toHaveBeenCalledWith('/chat')
  })

  it('keeps attachments with the pending message when creating a new session', async () => {
    const user = userEvent.setup()

    render(<DashboardPage />)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const imageFile = new File(['image-bytes'], 'preview.png', { type: 'image/png' })
    await user.upload(fileInput, imageFile)

    const textarea = screen.getByPlaceholderText('Ask anything')
    await user.type(textarea, 'Investigate this screenshot')

    const createdSession: ChatSession = {
      id: 'session-2',
      title: 'With attachment',
      model: 'gpt-5.4-mini',
      created_at: '2026-04-18T09:10:00Z',
      updated_at: '2026-04-18T09:10:00Z',
      messages: [],
      stats: {
        total_messages: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        total_tokens: 0,
        total_cost_usd: 0,
        avg_tokens_per_second: 0,
        avg_latency_ms: 0,
      },
      is_pinned: false,
      is_archived: false,
    }

    createSessionMock.mockResolvedValueOnce(createdSession)
    const submitButton = document.querySelector('button[type="submit"]') as HTMLButtonElement
    expect(submitButton).not.toBeNull()
    await user.click(submitButton)

    await waitFor(() => {
      expect(setPendingMessageMock).toHaveBeenCalledTimes(1)
    })

    const attachmentsArg = setPendingMessageMock.mock.calls[0][1] as AttachmentInfo[]
    expect(attachmentsArg).toHaveLength(1)
    expect(attachmentsArg[0]).toMatchObject({
      filename: 'preview.png',
      content_type: 'image/png',
      token_estimate: 765,
      data_url: 'data:image/png;base64,preview',
    })
  })

  it('recovers from failed session creation and re-enables submit', async () => {
    const user = userEvent.setup()
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    createSessionMock.mockRejectedValueOnce(new Error('failed'))

    render(<DashboardPage />)

    const textarea = screen.getByPlaceholderText('Ask anything')
    await user.type(textarea, 'Retry later')
    const submitButton = document.querySelector('button[type="submit"]') as HTMLButtonElement
    expect(submitButton).not.toBeNull()
    await user.click(submitButton)

    await waitFor(() => {
      expect(consoleErrorSpy).toHaveBeenCalled()
    })
    expect(localizedNavigateMock).not.toHaveBeenCalled()
    expect(submitButton).toBeEnabled()

    consoleErrorSpy.mockRestore()
  })
})
