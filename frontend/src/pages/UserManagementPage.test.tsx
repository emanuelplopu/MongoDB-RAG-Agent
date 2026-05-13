import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UserManagementPage from './UserManagementPage'

const listUsersMock = vi.fn()
const createUserMock = vi.fn()
const updateUserMock = vi.fn()
const setUserStatusMock = vi.fn()
const deleteUserMock = vi.fn()

let authState: {
  user:
    | {
        id: string
        email: string
        name: string
        is_admin: boolean
      }
    | null
}

const translations: Record<string, string> = {
  'users.accessDenied': 'Access denied',
  'users.adminRequired': 'Administrator access is required.',
  'users.title': 'User management',
  'users.subtitle': 'Create, edit, and manage user accounts',
  'common.refresh': 'Refresh',
  'users.create': 'Create user',
  'users.createNew': 'Create New User',
  'users.email': 'Email',
  'users.name': 'Name',
  'users.password': 'Password',
  'users.adminPrivileges': 'Administrator privileges',
  'common.cancel': 'Cancel',
  'users.edit': 'Edit user',
  'users.newPasswordLabel': 'New Password (leave empty to keep current)',
  'common.save': 'Save',
  'users.deleteConfirm': 'Delete User?',
  'users.deleteWarning': 'Are you sure you want to delete',
  'users.cannotUndo': 'This action cannot be undone.',
  'users.delete': 'Delete',
  'users.noUsers': 'No users found.',
  'users.active': 'Active',
  'users.inactive': 'Inactive',
  'users.admin': 'Admin',
  'users.user': 'User',
  'users.createdAt': 'Created',
  'common.actions': 'Actions',
  'users.totalUsers': 'Total Users',
  'users.administrators': 'Administrators',
  'users.editUser': 'Edit user',
  'users.deleteUser': 'Delete user',
  'users.deactivate': 'Deactivate user',
  'users.activate': 'Activate user',
  'users.cannotModifyOwn': 'cannot modify your own admin status',
  'users.loadFailed': 'Failed to load users',
  'users.createFailed': 'Failed to create user',
  'users.updateFailed': 'Failed to update user',
  'users.statusFailed': 'Failed to update user status',
  'users.deleteFailed': 'Failed to delete user',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => translations[key] ?? fallback ?? key,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  authApi: {
    listUsers: (...args: unknown[]) => listUsersMock(...args),
    createUser: (...args: unknown[]) => createUserMock(...args),
    updateUser: (...args: unknown[]) => updateUserMock(...args),
    setUserStatus: (...args: unknown[]) => setUserStatusMock(...args),
    deleteUser: (...args: unknown[]) => deleteUserMock(...args),
  },
}))

const baseUsers = [
  {
    id: 'admin-1',
    email: 'admin@example.com',
    name: 'Admin Person',
    is_admin: true,
    is_active: true,
    created_at: '2025-01-01T00:00:00Z',
  },
  {
    id: 'user-2',
    email: 'analyst@example.com',
    name: 'Analyst User',
    is_admin: false,
    is_active: false,
    created_at: '2025-02-01T00:00:00Z',
  },
]

describe('UserManagementPage', () => {
  beforeEach(() => {
    authState = {
      user: {
        id: 'admin-1',
        email: 'admin@example.com',
        name: 'Admin Person',
        is_admin: true,
      },
    }
    listUsersMock.mockReset()
    createUserMock.mockReset()
    updateUserMock.mockReset()
    setUserStatusMock.mockReset()
    deleteUserMock.mockReset()
    listUsersMock.mockResolvedValue(baseUsers)
    createUserMock.mockResolvedValue({ success: true })
    updateUserMock.mockResolvedValue({ success: true })
    setUserStatusMock.mockResolvedValue({ success: true })
    deleteUserMock.mockResolvedValue({ success: true })
  })

  it('blocks non-admin users before the management UI renders', async () => {
    authState = {
      user: {
        id: 'user-2',
        email: 'analyst@example.com',
        name: 'Analyst User',
        is_admin: false,
      },
    }

    render(<UserManagementPage />)

    expect(screen.getByText('Access denied')).toBeInTheDocument()
    expect(screen.getByText('Administrator access is required.')).toBeInTheDocument()
    await waitFor(() => expect(listUsersMock).toHaveBeenCalledTimes(1))
  })

  it('renders users, supports refresh, and handles empty states and load errors', async () => {
    const user = userEvent.setup()

    render(<UserManagementPage />)

    expect(await screen.findByText('Admin Person')).toBeInTheDocument()
    expect(screen.getByText('Analyst User')).toBeInTheDocument()
    expect(screen.getByText('Total Users')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(listUsersMock).toHaveBeenCalledTimes(2))

    listUsersMock.mockResolvedValueOnce([])
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByText('No users found.')).toBeInTheDocument()

    listUsersMock.mockRejectedValueOnce({ response: { data: { detail: 'Backend unavailable' } } })
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByText('Backend unavailable')).toBeInTheDocument()
  })

  it('creates, edits, toggles, and deletes users through the management flows', async () => {
    const user = userEvent.setup()

    render(<UserManagementPage />)

    expect(await screen.findByText('Admin Person')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Create user' })[0])
    expect(await screen.findByText('Create New User')).toBeInTheDocument()

    const createPassword = screen.getByPlaceholderText('Minimum 6 characters')
    expect(createPassword).toHaveAttribute('type', 'password')
    await user.click(screen.getAllByRole('button', { name: '' })[0])
    expect(createPassword).toHaveAttribute('type', 'text')

    await user.type(screen.getByPlaceholderText('user@example.com'), 'new.user@example.com')
    await user.type(screen.getByPlaceholderText('John Doe'), 'New User')
    await user.type(createPassword, 'hunter22')
    await user.click(screen.getByRole('checkbox', { name: 'Administrator privileges' }))
    await user.click(screen.getAllByRole('button', { name: 'Create user' })[1])

    expect(createUserMock).toHaveBeenCalledWith({
      email: 'new.user@example.com',
      name: 'New User',
      password: 'hunter22',
      is_admin: true,
    })

    await user.click(screen.getAllByTitle('Edit user')[1])
    expect(await screen.findByText('Edit user')).toBeInTheDocument()
    const [editEmailInput, editNameInput] = screen.getAllByRole('textbox')
    await user.clear(editEmailInput)
    await user.type(editEmailInput, 'updated@example.com')
    await user.clear(editNameInput)
    await user.type(editNameInput, 'Analyst User Updated')
    await user.type(screen.getByPlaceholderText('Minimum 6 characters'), 'new-secret')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(updateUserMock).toHaveBeenCalledWith('user-2', {
      name: 'Analyst User Updated',
      email: 'updated@example.com',
      is_admin: false,
      new_password: 'new-secret',
    })

    await user.click(screen.getByTitle('Activate user'))
    expect(setUserStatusMock).toHaveBeenCalledWith('user-2', true)

    await user.click(screen.getAllByTitle('Delete user')[1])
    expect(await screen.findByText('Delete User?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(deleteUserMock).toHaveBeenCalledWith('user-2')
  }, 15000)
})
