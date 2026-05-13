import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CloudSourcesPage from './CloudSourcesPage'

const navigateMock = vi.fn()
const getDashboardMock = vi.fn()
const getProvidersMock = vi.fn()
const initiateOAuthMock = vi.fn()

let authState: {
  user: { id: string; is_admin?: boolean } | null
  isLoading: boolean
}

const translations: Record<string, string> = {
  'cloudSourcesPage.title': 'Cloud Sources',
  'cloudSourcesPage.subtitle': 'Manage cloud content sources',
  'cloudSourcesPage.addSource': 'Add source',
  'cloudSourcesPage.connections': 'Connections',
  'cloudSourcesPage.syncConfigs': 'Sync configs',
  'cloudSourcesPage.filesIndexed': 'Files indexed',
  'cloudSourcesPage.activeSyncs': 'Active syncs',
  'cloudSourcesPage.connectedSources': 'Connected sources',
  'cloudSourcesPage.manageAll': 'Manage all',
  'cloudSourcesPage.files': 'Files',
  'cloudSourcesPage.syncs': 'Syncs',
  'cloudSourcesPage.noSources': 'No sources connected',
  'cloudSourcesPage.connectFirst': 'Connect first source',
  'cloudSourcesPage.recentErrors': 'Recent errors',
  'cloudSourcesPage.addCloudSource': 'Add cloud source',
  'cloudSourcesPage.addCloudSourceDesc': 'Choose a provider to connect',
  'cloudSourcesPage.deltaSyncBadge': 'Delta sync',
  'cloudSourcesPage.oauthBadge': 'OAuth',
  'cloudSourcesPage.timeNever': 'Never',
  'cloudSourcesPage.timeJustNow': 'Just now',
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
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'cloudSourcesPage.lastSync') return `Last sync ${params?.time ?? ''}`
      if (key === 'cloudSourcesPage.nextSync') return `Next sync ${params?.time ?? ''}`
      if (key === 'cloudSourcesPage.timeMinutesAgo') return `${params?.count ?? 0} minutes ago`
      if (key === 'cloudSourcesPage.timeHoursAgo') return `${params?.count ?? 0} hours ago`
      if (key === 'cloudSourcesPage.timeDaysAgo') return `${params?.count ?? 0} days ago`
      return translations[key] ?? key
    },
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  cloudSourcesApi: {
    getDashboard: (...args: unknown[]) => getDashboardMock(...args),
    getProviders: (...args: unknown[]) => getProvidersMock(...args),
    initiateOAuth: (...args: unknown[]) => initiateOAuthMock(...args),
  },
}))

const dashboardFixture = {
  total_connections: 2,
  total_sync_configs: 3,
  total_files_indexed: 1550,
  active_jobs: 1,
  sources: [
    {
      connection_id: 'conn-1',
      provider: 'dropbox',
      display_name: 'Finance Dropbox',
      status: 'active',
      has_errors: false,
      total_files_indexed: 1450,
      sync_configs_count: 2,
      last_sync_at: new Date().toISOString(),
      next_sync_at: undefined,
    },
  ],
  recent_errors: [
    {
      message: 'Sync failed',
      file_path: '/folder/report.pdf',
      timestamp: new Date().toISOString(),
    },
  ],
}

const providersFixture = {
  providers: [
    {
      provider_type: 'dropbox',
      display_name: 'Dropbox',
      description: 'Dropbox files',
      supports_delta_sync: true,
      supported_auth_types: ['api_key'],
    },
    {
      provider_type: 'google_drive',
      display_name: 'Google Drive',
      description: 'Drive documents',
      supports_delta_sync: true,
      supported_auth_types: ['oauth2'],
    },
  ],
}

describe('CloudSourcesPage', () => {
  beforeEach(() => {
    authState = {
      user: { id: 'user-1' },
      isLoading: false,
    }
    navigateMock.mockReset()
    getDashboardMock.mockReset()
    getProvidersMock.mockReset()
    initiateOAuthMock.mockReset()
    getDashboardMock.mockResolvedValue(dashboardFixture)
    getProvidersMock.mockResolvedValue(providersFixture)
    initiateOAuthMock.mockResolvedValue({ authorization_url: 'https://example.com/oauth' })
    vi.stubGlobal('prompt', vi.fn(() => null))
  })

  it('renders dashboard data and navigates to connected sources and manual provider forms', async () => {
    const user = userEvent.setup()

    render(<CloudSourcesPage />)

    expect(await screen.findByText('Cloud Sources')).toBeInTheDocument()
    expect(screen.getByText(/1[,.]550/)).toBeInTheDocument()
    expect(screen.getByText('Finance Dropbox')).toBeInTheDocument()
    expect(screen.getByText('Recent errors')).toBeInTheDocument()

    await user.click(screen.getByText('Manage all'))
    expect(navigateMock).toHaveBeenCalledWith('/cloud-sources/connections')

    await user.click(screen.getByText('Add source'))
    expect(await screen.findByText('Add cloud source')).toBeInTheDocument()
    await user.click(screen.getByText('Dropbox'))
    expect(navigateMock).toHaveBeenCalledWith('/cloud-sources/connect/dropbox')
  })

  it('shows empty and error states and avoids OAuth initiation when setup is cancelled', async () => {
    const user = userEvent.setup()
    getDashboardMock.mockRejectedValueOnce(new Error('Failed to load cloud sources'))

    const firstRender = render(<CloudSourcesPage />)
    expect(await screen.findByText('Failed to load cloud sources')).toBeInTheDocument()

    firstRender.unmount()
    getDashboardMock.mockResolvedValueOnce({
      ...dashboardFixture,
      sources: [],
      recent_errors: [],
    })
    render(<CloudSourcesPage />)

    expect(await screen.findByText('No sources connected')).toBeInTheDocument()
    await user.click(screen.getByText('Connect first source'))
    await user.click(await screen.findByText('Google Drive'))
    expect(initiateOAuthMock).not.toHaveBeenCalled()
  })

  it('shows a loading spinner while auth is still resolving', () => {
    authState = {
      user: null,
      isLoading: true,
    }

    const { container } = render(<CloudSourcesPage />)
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })
})
