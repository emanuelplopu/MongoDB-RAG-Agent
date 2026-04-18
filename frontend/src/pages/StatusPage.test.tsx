import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { StatusDashboard } from '../api/client'
import StatusPage from './StatusPage'
import { mockNonAdminUser, mockUser } from '../test/test-utils'

const navigateMock = vi.fn()
const getDashboardMock = vi.fn()

let authState: {
  user: typeof mockUser | typeof mockNonAdminUser | null
  isLoading: boolean
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  statusApi: {
    getDashboard: (...args: unknown[]) => getDashboardMock(...args),
  },
}))

const dashboardFixture: StatusDashboard = {
  profiles: [
    {
      profile_key: 'main',
      profile_name: 'Main Profile',
      database: 'recallhub',
      documents_count: 1234,
      chunks_count: 5678,
      total_tokens: 1000,
      avg_chunk_size: 250,
      storage_size_bytes: 5 * 1024 * 1024,
      last_ingestion: '2026-04-17T10:15:00Z',
      ingestion_jobs_count: 4,
    },
    {
      profile_key: 'archive',
      profile_name: 'Archive',
      database: 'archive_db',
      documents_count: 5,
      chunks_count: 10,
      total_tokens: 500,
      avg_chunk_size: 100,
      storage_size_bytes: 0,
      ingestion_jobs_count: 0,
    },
  ],
  active_profile: 'main',
  system_metrics: {
    cpu_percent: 91.2,
    memory_percent: 62.5,
    memory_used_gb: 10.4,
    memory_total_gb: 16,
    disk_percent: 48.1,
    disk_used_gb: 120.2,
    disk_total_gb: 250,
  },
  total_documents: 1239,
  total_chunks: 5688,
  total_profiles: 2,
  api_uptime_seconds: 90061,
  llm_provider: 'OpenAI',
  llm_model: 'gpt-5.4-mini',
  embedding_model: 'text-embedding-3-small',
}

describe('StatusPage', () => {
  beforeEach(() => {
    authState = {
      user: mockUser,
      isLoading: false,
    }
    navigateMock.mockReset()
    getDashboardMock.mockReset()
  })

  it('renders dashboard data and refreshes on demand for admins', async () => {
    getDashboardMock.mockResolvedValue(dashboardFixture)
    const user = userEvent.setup()

    render(<StatusPage />)

    expect(await screen.findByText('System Resources')).toBeInTheDocument()
    expect(screen.getByText('1,239')).toBeInTheDocument()
    expect(screen.getByText('5,688')).toBeInTheDocument()
    expect(screen.getByText('1d 1h')).toBeInTheDocument()
    expect(screen.getByText('10.4 / 16.0 GB')).toBeInTheDocument()
    expect(screen.getByText('120.2 / 250.0 GB')).toBeInTheDocument()
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
    expect(screen.getByText('gpt-5.4-mini')).toBeInTheDocument()
    expect(screen.getByText('text-embedding-3-small')).toBeInTheDocument()
    expect(screen.getByText('5 MB')).toBeInTheDocument()
    expect(screen.getByText('91.2%')).toBeInTheDocument()
    expect(screen.getByText(new Date('2026-04-17T10:15:00Z').toLocaleDateString())).toBeInTheDocument()
    expect(getDashboardMock).toHaveBeenCalledTimes(1)

    getDashboardMock.mockResolvedValueOnce(dashboardFixture)
    await user.click(screen.getByRole('button', { name: 'common.refresh' }))

    await waitFor(() => {
      expect(getDashboardMock).toHaveBeenCalledTimes(2)
    })
  })

  it('shows a translated error when dashboard loading fails', async () => {
    getDashboardMock.mockRejectedValue(new Error('boom'))
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(<StatusPage />)

    expect(await screen.findByText('status.failedToLoad')).toBeInTheDocument()
    expect(screen.queryByText('System Resources')).not.toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })

  it('redirects non-admin users without requesting dashboard data', async () => {
    authState = {
      user: mockNonAdminUser,
      isLoading: false,
    }

    render(<StatusPage />)

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/dashboard')
    })
    expect(getDashboardMock).not.toHaveBeenCalled()
  })
})
