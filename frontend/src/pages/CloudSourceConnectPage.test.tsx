import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CloudSourceConnectPage from './CloudSourceConnectPage'

const navigateMock = vi.fn()
const getProviderMock = vi.fn()
const createConnectionMock = vi.fn()
const testConnectionMock = vi.fn()

let authState: {
  user: { id: string } | null
  isLoading: boolean
}

let routeParams: { providerType?: string } = { providerType: 'owncloud' }

const translations: Record<string, string> = {
  'cloudConnectPage.providerNotFound': 'Provider not found',
  'cloudConnectPage.providerNotFoundDesc': 'The requested provider is unavailable.',
  'cloudConnectPage.backToSources': 'Back to sources',
  'cloudConnectPage.connectionName': 'Connection name',
  'cloudConnectPage.connectionNameHelp': 'Choose a label to identify this connection later.',
  'cloudConnectPage.setupInstructions': 'Setup instructions',
  'cloudConnectPage.viewDocs': 'View docs',
  'cloudConnectPage.securityNote': 'Credentials are stored securely for background syncs.',
  'cloudConnectPage.connecting': 'Connecting...',
  'cloudConnectPage.connect': 'Connect',
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useParams: () => routeParams,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, string>) => {
      if (key === 'cloudConnectPage.connectProvider') {
        return `Connect ${params?.provider ?? ''}`
      }
      return translations[key] ?? key
    },
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  cloudSourcesApi: {
    getProvider: (...args: unknown[]) => getProviderMock(...args),
    createConnection: (...args: unknown[]) => createConnectionMock(...args),
    testConnection: (...args: unknown[]) => testConnectionMock(...args),
  },
}))

const owncloudProvider = {
  provider_type: 'owncloud',
  display_name: 'OwnCloud',
  description: 'Sync files from an OwnCloud server.',
  setup_instructions: 'Create an app password before connecting.',
  documentation_url: 'https://docs.example.com/owncloud',
}

describe('CloudSourceConnectPage', () => {
  beforeEach(() => {
    authState = {
      user: { id: 'user-1' },
      isLoading: false,
    }
    routeParams = { providerType: 'owncloud' }
    navigateMock.mockReset()
    getProviderMock.mockReset()
    createConnectionMock.mockReset()
    testConnectionMock.mockReset()
    getProviderMock.mockResolvedValue(owncloudProvider)
    createConnectionMock.mockResolvedValue({ id: 'connection-123' })
    testConnectionMock.mockResolvedValue({ success: true, message: 'Connection verified' })
  })

  it('shows a loading spinner while auth is still resolving', () => {
    authState = {
      user: null,
      isLoading: true,
    }

    const { container } = render(<CloudSourceConnectPage />)
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })

  it('renders provider-not-found fallback when loading provider details fails', async () => {
    const user = userEvent.setup()
    getProviderMock.mockRejectedValueOnce(new Error('Provider lookup failed'))

    render(<CloudSourceConnectPage />)

    expect(await screen.findByText('Provider not found')).toBeInTheDocument()
    expect(screen.getByText('The requested provider is unavailable.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Back to sources' }))
    expect(navigateMock).toHaveBeenCalledWith('/cloud-sources')
  })

  it('submits manual credentials, toggles password visibility, and navigates after a successful connection test', async () => {
    const user = userEvent.setup()

    render(<CloudSourceConnectPage />)

    expect(await screen.findByText('Connect OwnCloud')).toBeInTheDocument()
    expect(screen.getByText('Create an app password before connecting.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View docs' })).toHaveAttribute(
      'href',
      'https://docs.example.com/owncloud'
    )

    const passwordInput = screen.getByPlaceholderText('Enter password or app token')
    expect(passwordInput).toHaveAttribute('type', 'password')

    const passwordToggle = screen.getAllByRole('button', { name: '' })[1]
    await user.click(passwordToggle)
    expect(passwordInput).toHaveAttribute('type', 'text')

    await user.type(screen.getByPlaceholderText('My OwnCloud'), '   Finance Vault   ')
    await user.type(screen.getByPlaceholderText('https://cloud.example.com'), 'https://files.example.com')
    await user.type(screen.getByPlaceholderText('your-username'), 'finance-user')
    await user.type(passwordInput, 'app-password-1')
    await user.click(screen.getByRole('button', { name: 'Connect' }))

    await waitFor(() => {
      expect(createConnectionMock).toHaveBeenCalledWith({
        provider: 'owncloud',
        display_name: 'Finance Vault',
        server_url: 'https://files.example.com',
        username: 'finance-user',
        password: 'app-password-1',
      })
    })
    expect(testConnectionMock).toHaveBeenCalledWith('connection-123')
    expect(navigateMock).toHaveBeenCalledWith('/cloud-sources/connections/connection-123')
  })

  it('shows connection test feedback and create errors without navigating away', async () => {
    const user = userEvent.setup()

    render(<CloudSourceConnectPage />)

    await screen.findByText('Connect OwnCloud')
    await user.type(screen.getByPlaceholderText('My OwnCloud'), 'Operations Sync')
    await user.type(screen.getByPlaceholderText('https://cloud.example.com'), 'https://ops.example.com')
    await user.type(screen.getByPlaceholderText('your-username'), 'ops-user')
    await user.type(screen.getByPlaceholderText('Enter password or app token'), 'secret-pass')

    testConnectionMock.mockResolvedValueOnce({
      success: false,
      message: 'Connected, but the server rejected the folder permissions.',
    })

    await user.click(screen.getByRole('button', { name: 'Connect' }))

    expect(
      await screen.findByText('Connected, but the server rejected the folder permissions.')
    ).toBeInTheDocument()
    expect(navigateMock).not.toHaveBeenCalledWith('/cloud-sources/connections/connection-123')

    createConnectionMock.mockRejectedValueOnce(new Error('Failed to create connection'))
    await user.clear(screen.getByPlaceholderText('My OwnCloud'))
    await user.type(screen.getByPlaceholderText('My OwnCloud'), 'Retry Sync')
    await user.click(screen.getByRole('button', { name: 'Connect' }))

    expect(await screen.findByText('Failed to create connection')).toBeInTheDocument()
  })
})
