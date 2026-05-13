import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SearchIndexesPage from './SearchIndexesPage'

const navigateMock = vi.fn()
const getDashboardMock = vi.fn()
const createIndexesMock = vi.fn()
const tMock = (key: string) => key

let authState: {
  user: { id: string; is_admin: boolean } | null
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
    t: tMock,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  indexesApi: {
    getDashboard: (...args: unknown[]) => getDashboardMock(...args),
    createIndexes: (...args: unknown[]) => createIndexesMock(...args),
  },
}))

const dashboardFixture = {
  performance: {
    avg_response_time_ms: 123.4,
    p95_response_time_ms: 456.7,
    searches_last_24h: 321,
    total_searches: 8765,
  },
  indexes: [
    {
      name: 'vector_idx',
      type: 'vector',
      documents_indexed: 1200,
      size_bytes: 2048,
      status: 'READY',
    },
  ],
  resource_allocation: {
    cpu: { cores: 8, usage_percent: 65.2 },
    memory: { total_gb: 32, available_gb: 20.5 },
    mongodb: { connection_pool_size: 12, recommended_pool_size: 50 },
  },
  suggestions: [
    {
      severity: 'high',
      title: 'Increase candidates',
      description: 'Boost recall by raising numCandidates.',
      action: 'Raise numCandidates',
      estimated_impact: '+15% recall',
    },
  ],
}

describe('SearchIndexesPage', () => {
  beforeEach(() => {
    vi.useRealTimers()
    authState = {
      user: { id: 'admin-1', is_admin: true },
      isLoading: false,
    }
    navigateMock.mockReset()
    getDashboardMock.mockReset()
    createIndexesMock.mockReset()
    getDashboardMock.mockResolvedValue(dashboardFixture)
    createIndexesMock.mockResolvedValue({ success: true })
  })

  it('renders dashboard data for admins and refreshes after successful index creation', async () => {
    const user = userEvent.setup()

    render(<SearchIndexesPage />)

    expect(await screen.findByText('indexes.title')).toBeInTheDocument()
    expect(screen.getByText('123 ms')).toBeInTheDocument()
    expect(screen.getByText('457 ms')).toBeInTheDocument()
    expect(screen.getByText(/8[,.]765/)).toBeInTheDocument()
    expect(screen.getByText('vector_idx')).toBeInTheDocument()
    expect(screen.getByText('Increase candidates')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'indexes.createIndexes' }))
    expect(createIndexesMock).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Indexes created successfully. They may take time to become READY.')).toBeInTheDocument()
  })

  it('shows no-index and error states when creation fails', async () => {
    getDashboardMock.mockResolvedValueOnce({
      ...dashboardFixture,
      indexes: [],
      suggestions: [],
    })
    createIndexesMock.mockResolvedValueOnce({ success: false, errors: ['permission denied'] })
    const user = userEvent.setup()

    render(<SearchIndexesPage />)

    expect(await screen.findByText('indexes.noIndexesFound')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'indexes.createIndexes' }))
    expect(await screen.findByText('permission denied')).toBeInTheDocument()
  })

  it('redirects non-admin users away from the page', async () => {
    authState = {
      user: { id: 'user-1', is_admin: false },
      isLoading: false,
    }

    render(<SearchIndexesPage />)

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/dashboard'))
    expect(getDashboardMock).not.toHaveBeenCalled()
  })
})
