import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ProfilesPage from './ProfilesPage'

const navigateMock = vi.fn()
const listProfilesMock = vi.fn()
const switchProfileMock = vi.fn()
const deleteProfileMock = vi.fn()
const createProfileMock = vi.fn()
const updateProfileMock = vi.fn()
const getAccessMatrixMock = vi.fn()
const setProfileAccessMock = vi.fn()

let authState: {
  user: { id: string; name: string; email: string; is_admin: boolean } | null
  isLoading: boolean
}

const translations: Record<string, string> = {
  'profiles.title': 'Profiles',
  'profiles.subtitle': 'Manage retrieval profiles',
  'profiles.accessRights': 'Access rights',
  'profiles.newProfile': 'New profile',
  'profiles.createNew': 'Create new profile',
  'profiles.profileKey': 'Profile key',
  'profiles.displayName': 'Display name',
  'profiles.adminAccess': 'Admin access only',
  'profiles.loadFailed': 'Failed to load profiles.',
  'profiles.matrixFailed': 'Failed to load access matrix.',
  'profiles.accessFailed': 'Failed to update access.',
  'profiles.switchFailed': 'Failed to switch profile.',
  'profiles.deleteFailed': 'Failed to delete profile.',
  'profiles.createFailed': 'Failed to create profile.',
  'common.refresh': 'Refresh',
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
    t: (key: string, paramsOrFallback?: Record<string, unknown> | string) => {
      if (typeof paramsOrFallback === 'string') return paramsOrFallback
      if (key === 'profiles.switchSuccess') return `Switched to ${paramsOrFallback?.name ?? ''}`
      if (key === 'confirm.deleteProfile') return `Delete ${paramsOrFallback?.name ?? ''}?`
      return translations[key] ?? key
    },
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  profilesApi: {
    list: (...args: unknown[]) => listProfilesMock(...args),
    switch: (...args: unknown[]) => switchProfileMock(...args),
    delete: (...args: unknown[]) => deleteProfileMock(...args),
    create: (...args: unknown[]) => createProfileMock(...args),
    update: (...args: unknown[]) => updateProfileMock(...args),
  },
  authApi: {
    getAccessMatrix: (...args: unknown[]) => getAccessMatrixMock(...args),
    setProfileAccess: (...args: unknown[]) => setProfileAccessMock(...args),
  },
}))

const profilesResponse = {
  profiles: {
    default: {
      name: 'Default Profile',
      description: 'Base profile',
      documents_folders: ['./documents'],
      database: 'default_db',
    },
    research: {
      name: 'Research',
      description: 'Research profile',
      documents_folders: ['./research', './reports'],
      database: 'research_db',
    },
  },
  active_profile: 'default',
}

const accessMatrixResponse = {
  profiles: ['default', 'research'],
  users: [
    { id: 'admin-1', name: 'Admin', email: 'admin@example.com', is_admin: true },
    { id: 'user-1', name: 'Analyst', email: 'analyst@example.com', is_admin: false },
  ],
  access: {
    'user-1': ['research'],
  },
}

describe('ProfilesPage', () => {
  beforeEach(() => {
    authState = {
      user: { id: 'admin-1', name: 'Admin', email: 'admin@example.com', is_admin: true },
      isLoading: false,
    }
    navigateMock.mockReset()
    listProfilesMock.mockReset()
    switchProfileMock.mockReset()
    deleteProfileMock.mockReset()
    createProfileMock.mockReset()
    updateProfileMock.mockReset()
    getAccessMatrixMock.mockReset()
    setProfileAccessMock.mockReset()
    listProfilesMock.mockResolvedValue(profilesResponse)
    switchProfileMock.mockResolvedValue({ message: 'Switched successfully' })
    deleteProfileMock.mockResolvedValue({ success: true })
    createProfileMock.mockResolvedValue({ success: true })
    updateProfileMock.mockResolvedValue({ success: true })
    getAccessMatrixMock.mockResolvedValue(accessMatrixResponse)
    setProfileAccessMock.mockResolvedValue({ success: true })
    vi.stubGlobal('confirm', vi.fn(() => true))
    vi.stubGlobal('alert', vi.fn())
  })

  it('renders profiles and supports create, switch, edit, and delete flows for admins', async () => {
    const user = userEvent.setup()

    render(<ProfilesPage />)

    expect(await screen.findByText('Default Profile')).toBeInTheDocument()
    expect(screen.getByText('Research')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'New profile' }))
    await user.type(screen.getByPlaceholderText('my-project'), 'analytics')
    await user.type(screen.getByPlaceholderText('My Project'), 'Analytics')
    await user.type(screen.getByPlaceholderText('./documents, ./data (comma-separated)'), './analytics, ./shared')
    await user.type(screen.getByPlaceholderText('Optional (defaults to profile key)'), 'analytics_db')
    await user.click(screen.getByRole('button', { name: 'Create Profile' }))

    expect(createProfileMock).toHaveBeenCalledWith({
      key: 'analytics',
      name: 'Analytics',
      description: undefined,
      documents_folders: ['./analytics', './shared'],
      database: 'analytics_db',
    })

    await user.click(screen.getByRole('button', { name: 'Activate' }))
    await waitFor(() => expect(switchProfileMock).toHaveBeenCalledWith('research'))

    await user.click(screen.getAllByTitle('Edit profile')[0])
    const displayNameInputs = screen.getAllByDisplayValue(/Default Profile|Research/)
    fireEvent.change(displayNameInputs[0], { target: { value: 'Default Profile Updated' } })
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(updateProfileMock).toHaveBeenCalledWith('default', {
      name: 'Default Profile Updated',
      description: 'Base profile',
      documents_folders: ['./documents'],
      database: 'default_db',
    })

    await user.click(screen.getByTitle('Delete profile'))
    expect(deleteProfileMock).toHaveBeenCalledWith('research')
    expect(listProfilesMock).toHaveBeenCalled()
  }, 15000)

  it('loads the access matrix and toggles profile access for non-admin users', async () => {
    const user = userEvent.setup()

    render(<ProfilesPage />)

    await screen.findByText('Default Profile')
    await user.click(screen.getByRole('button', { name: 'Access rights' }))

    expect(await screen.findByText('Access Rights Matrix')).toBeInTheDocument()
    expect(screen.getByText('Analyst')).toBeInTheDocument()
    expect(screen.getAllByText('Research').length).toBeGreaterThan(0)

    await user.click(screen.getByTitle('Revoke access'))
    expect(setProfileAccessMock).toHaveBeenCalledWith({
      user_id: 'user-1',
      profile_key: 'research',
      has_access: false,
    })
  })

  it('redirects non-admins and shows translated load errors', async () => {
    authState = {
      user: { id: 'user-1', name: 'Analyst', email: 'analyst@example.com', is_admin: false },
      isLoading: false,
    }
    listProfilesMock.mockRejectedValueOnce({ status: 403 })

    render(<ProfilesPage />)

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/dashboard'))
    expect(await screen.findByText('Admin access only')).toBeInTheDocument()
  })
})
