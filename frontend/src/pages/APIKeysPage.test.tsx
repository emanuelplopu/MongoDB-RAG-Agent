import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { APIKeyCreatedResponse, APIKeyResponse } from '../api/client'
import APIKeysPage from './APIKeysPage'

const listMock = vi.fn()
const createMock = vi.fn()
const revokeMock = vi.fn()
const toggleMock = vi.fn()

const translations: Record<string, string> = {
  'apiKeys.title': 'API Keys',
  'apiKeys.subtitle': 'Manage personal access keys.',
  'apiKeys.loadFailed': 'Failed to load keys',
  'apiKeys.createFailed': 'Failed to create key',
  'apiKeys.revokeFailed': 'Failed to revoke key',
  'apiKeys.toggleFailed': 'Failed to toggle key',
  'apiKeys.apiKeyCreated': 'API key created',
  'apiKeys.keyWarning': 'Copy this key now.',
  'apiKeys.keyCopied': 'Copied',
  'apiKeys.copyKey': 'Copy key',
  'apiKeys.create': 'Create Key',
  'apiKeys.howToUse': 'How to Use',
  'apiKeys.howToUseDesc': 'Use this key in the API header.',
  'apiKeys.name': 'Name',
  'apiKeys.key': 'Key',
  'apiKeys.createdAt': 'Created',
  'apiKeys.lastUsed': 'Last Used',
  'apiKeys.status': 'Status',
  'apiKeys.noKeysHint': 'No keys yet',
  'apiKeys.expiresAt': 'Expires',
  'apiKeys.never': 'Never',
  'apiKeys.revoke': 'Revoke',
  'apiKeys.createNew': 'Create New Key',
  'apiKeys.keyName': 'Key Name',
  'apiKeys.expiration': 'Expiration',
  'apiKeys.neverExpires': 'Never expires',
  'apiKeys.days7': '7 days',
  'apiKeys.days30': '30 days',
  'apiKeys.days90': '90 days',
  'apiKeys.months6': '6 months',
  'apiKeys.year1': '1 year',
  'common.refresh': 'Refresh',
  'common.actions': 'Actions',
  'common.loading': 'Loading',
  'common.active': 'Active',
  'common.disabled': 'Disabled',
  'common.disable': 'Disable',
  'common.enable': 'Enable',
  'common.cancel': 'Cancel',
  'common.dismiss': 'Dismiss',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, value?: string | Record<string, string>) => {
      if (typeof value === 'string') {
        return value
      }
      if (value && 'name' in value) {
        return `${translations[key] ?? key}: ${value.name}`
      }
      return translations[key] ?? key
    },
  }),
}))

vi.mock('../api/client', () => ({
  apiKeysApi: {
    list: (...args: unknown[]) => listMock(...args),
    create: (...args: unknown[]) => createMock(...args),
    revoke: (...args: unknown[]) => revokeMock(...args),
    toggle: (...args: unknown[]) => toggleMock(...args),
  },
}))

const makeKey = (overrides: Partial<APIKeyResponse> = {}): APIKeyResponse => ({
  id: overrides.id ?? 'key-1',
  name: overrides.name ?? 'Production',
  key_prefix: overrides.key_prefix ?? 'rag_prod_123',
  created_at: overrides.created_at ?? '2026-04-18T10:00:00Z',
  last_used_at: overrides.last_used_at ?? null,
  expires_at: overrides.expires_at ?? '2026-05-18T10:00:00Z',
  is_active: overrides.is_active ?? true,
  scopes: overrides.scopes ?? ['search:read'],
})

const createdKeyFixture: APIKeyCreatedResponse = {
  id: 'key-2',
  name: 'Automation Key',
  key: 'rag_live_secret_value',
  key_prefix: 'rag_live_123',
  created_at: '2026-04-18T11:00:00Z',
  expires_at: null,
  scopes: ['search:read'],
  warning: 'Copy this now',
}

describe('APIKeysPage', () => {
  const writeTextMock = vi.fn()
  let confirmMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    listMock.mockReset()
    createMock.mockReset()
    revokeMock.mockReset()
    toggleMock.mockReset()
    writeTextMock.mockReset()
    confirmMock = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmMock)
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: writeTextMock,
      },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the empty state and reloads on refresh', async () => {
    listMock.mockResolvedValue([])
    const user = userEvent.setup()

    render(<APIKeysPage />)

    expect(await screen.findByText('No keys yet')).toBeInTheDocument()
    expect(screen.getByText('How to Use')).toBeInTheDocument()
    expect(screen.getByText(/\/api\/v1\/search\/hybrid/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(listMock).toHaveBeenCalledTimes(2)
    })
  })

  it('creates a key, shows the created secret, and copies it to the clipboard', async () => {
    listMock
      .mockResolvedValueOnce([makeKey()])
      .mockResolvedValueOnce([makeKey(), { ...makeKey(), id: 'key-2', name: 'Automation Key' }])
    createMock.mockResolvedValue(createdKeyFixture)
    const user = userEvent.setup()

    render(<APIKeysPage />)

    expect(await screen.findByText('Production')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Create Key' }))
    await user.type(screen.getByPlaceholderText('e.g., Production API'), '  Automation Key  ')
    await user.selectOptions(screen.getByRole('combobox'), '30')
    await user.click(screen.getAllByRole('button', { name: 'Create Key' })[1])

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        name: 'Automation Key',
        expires_in_days: 30,
      })
    })

    expect(await screen.findByText('API key created')).toBeInTheDocument()
    expect(screen.getByText('rag_live_secret_value')).toBeInTheDocument()
    expect(screen.queryByText('Create New Key')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Copy key' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('API key created')).not.toBeInTheDocument()
  })

  it('toggles and revokes keys, reloading after each successful action', async () => {
    const activeKey = makeKey()
    const disabledKey = makeKey({
      id: 'key-2',
      name: 'Staging',
      key_prefix: 'rag_stage_456',
      is_active: false,
      expires_at: null,
    })
    listMock.mockResolvedValue([activeKey, disabledKey])
    toggleMock.mockResolvedValue({ success: true, is_active: false, message: 'ok' })
    revokeMock.mockResolvedValue({ success: true, message: 'revoked' })
    const user = userEvent.setup()

    render(<APIKeysPage />)

    expect(await screen.findByText('Production')).toBeInTheDocument()
    expect(screen.getByText('Staging')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Disable' }))
    await waitFor(() => {
      expect(toggleMock).toHaveBeenCalledWith('key-1')
    })

    await user.click(screen.getAllByRole('button', { name: 'Revoke' })[0])
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith('apiKeys.confirmRevokeDesc: Production')
      expect(revokeMock).toHaveBeenCalledWith('key-1')
    })

    expect(listMock).toHaveBeenCalledTimes(3)
  })

  it('shows and dismisses translated load errors', async () => {
    listMock.mockRejectedValue(new Error('boom'))
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()

    render(<APIKeysPage />)

    expect(await screen.findByText('Failed to load keys')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '×' }))
    expect(screen.queryByText('Failed to load keys')).not.toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })
})
