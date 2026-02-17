import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  UserPlusIcon,
  TrashIcon,
  PencilIcon,
  ShieldCheckIcon,
  ShieldExclamationIcon,
  CheckCircleIcon,
  XCircleIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  EyeIcon,
  EyeSlashIcon,
  UserCircleIcon,
} from '@heroicons/react/24/outline'
import { authApi, UserListItem, CreateUserRequest, UpdateUserRequest } from '../api/client'
import { useAuth } from '../contexts/AuthContext'

export default function UserManagementPage() {
  const { t } = useTranslation()
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState<UserListItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  // Create user form
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [createForm, setCreateForm] = useState<CreateUserRequest>({
    email: '',
    name: '',
    password: '',
    is_admin: false,
  })
  const [showPassword, setShowPassword] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  
  // Edit user
  const [editingUser, setEditingUser] = useState<UserListItem | null>(null)
  const [editForm, setEditForm] = useState<UpdateUserRequest>({})
  const [showEditPassword, setShowEditPassword] = useState(false)
  const [isUpdating, setIsUpdating] = useState(false)
  const [updateError, setUpdateError] = useState<string | null>(null)
  
  // Delete confirmation
  const [deletingUser, setDeletingUser] = useState<UserListItem | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  const loadUsers = async () => {
    try {
      setIsLoading(true)
      const data = await authApi.listUsers()
      setUsers(data)
      setError(null)
    } catch (err: any) {
      setError(err.response?.data?.detail || t('users.loadFailed', 'Failed to load users'))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadUsers()
  }, [])

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreateError(null)
    setIsCreating(true)
    
    try {
      await authApi.createUser(createForm)
      setShowCreateForm(false)
      setCreateForm({ email: '', name: '', password: '', is_admin: false })
      await loadUsers()
    } catch (err: any) {
      setCreateError(err.response?.data?.detail || t('users.createFailed', 'Failed to create user'))
    } finally {
      setIsCreating(false)
    }
  }

  const handleUpdateUser = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editingUser) return
    
    setUpdateError(null)
    setIsUpdating(true)
    
    try {
      // Only send non-empty fields
      const updateData: UpdateUserRequest = {}
      if (editForm.name) updateData.name = editForm.name
      if (editForm.email) updateData.email = editForm.email
      if (editForm.is_admin !== undefined) updateData.is_admin = editForm.is_admin
      if (editForm.new_password) updateData.new_password = editForm.new_password
      
      await authApi.updateUser(editingUser.id, updateData)
      setEditingUser(null)
      setEditForm({})
      await loadUsers()
    } catch (err: any) {
      setUpdateError(err.response?.data?.detail || t('users.updateFailed', 'Failed to update user'))
    } finally {
      setIsUpdating(false)
    }
  }

  const handleToggleStatus = async (user: UserListItem) => {
    try {
      await authApi.setUserStatus(user.id, !user.is_active)
      await loadUsers()
    } catch (err: any) {
      setError(err.response?.data?.detail || t('users.statusFailed', 'Failed to update user status'))
    }
  }

  const handleDeleteUser = async () => {
    if (!deletingUser) return
    
    setIsDeleting(true)
    try {
      await authApi.deleteUser(deletingUser.id)
      setDeletingUser(null)
      await loadUsers()
    } catch (err: any) {
      setError(err.response?.data?.detail || t('users.deleteFailed', 'Failed to delete user'))
    } finally {
      setIsDeleting(false)
    }
  }

  const startEditing = (user: UserListItem) => {
    setEditingUser(user)
    setEditForm({
      name: user.name,
      email: user.email,
      is_admin: user.is_admin,
      new_password: '',
    })
    setShowEditPassword(false)
    setUpdateError(null)
  }

  if (!currentUser?.is_admin) {
    return (
      <div className="min-h-screen bg-background dark:bg-gray-900 p-6">
        <div className="max-w-4xl mx-auto">
          <div className="rounded-2xl bg-red-50 dark:bg-red-900/20 p-8 text-center">
            <ShieldExclamationIcon className="h-16 w-16 mx-auto text-red-500 mb-4" />
            <h1 className="text-xl font-bold text-red-900 dark:text-red-200">{t('users.accessDenied')}</h1>
            <p className="text-red-700 dark:text-red-300 mt-2">
              {t('users.adminRequired')}
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background dark:bg-gray-900 p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-primary-900 dark:text-gray-100">{t('users.title')}</h1>
            <p className="text-secondary dark:text-gray-400 mt-1">
              {t('users.subtitle', 'Create, edit, and manage user accounts')}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={loadUsers}
              disabled={isLoading}
              className="flex items-center gap-2 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              <ArrowPathIcon className={`h-5 w-5 ${isLoading ? 'animate-spin' : ''}`} />
              {t('common.refresh')}
            </button>
            <button
              onClick={() => setShowCreateForm(true)}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700"
            >
              <UserPlusIcon className="h-5 w-5" />
              {t('users.create')}
            </button>
          </div>
        </div>

        {/* Error message */}
        {error && (
          <div className="rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-4">
            <div className="flex items-center gap-2 text-red-700 dark:text-red-300">
              <ExclamationTriangleIcon className="h-5 w-5" />
              {error}
            </div>
          </div>
        )}

        {/* Create User Modal */}
        {showCreateForm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-md w-full p-6">
              <h2 className="text-xl font-bold text-primary-900 dark:text-gray-100 mb-4">{t('users.createNew', 'Create New User')}</h2>
              
              <form onSubmit={handleCreateUser} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                    {t('users.email')} *
                  </label>
                  <input
                    type="email"
                    value={createForm.email}
                    onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                    required
                    className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                    placeholder="user@example.com"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                    {t('users.name')} *
                  </label>
                  <input
                    type="text"
                    value={createForm.name}
                    onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                    required
                    minLength={2}
                    className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                    placeholder="John Doe"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                    {t('users.password')} *
                  </label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={createForm.password}
                      onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                      required
                      minLength={6}
                      className="w-full px-4 py-2 pr-10 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                      placeholder="Minimum 6 characters"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      {showPassword ? <EyeSlashIcon className="h-5 w-5" /> : <EyeIcon className="h-5 w-5" />}
                    </button>
                  </div>
                </div>
                
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="is_admin"
                    checked={createForm.is_admin}
                    onChange={(e) => setCreateForm({ ...createForm, is_admin: e.target.checked })}
                    className="rounded"
                  />
                  <label htmlFor="is_admin" className="text-sm text-primary-900 dark:text-gray-200">
                    {t('users.adminPrivileges', 'Administrator privileges')}
                  </label>
                </div>
                
                {createError && (
                  <div className="text-sm text-red-600 dark:text-red-400">
                    {createError}
                  </div>
                )}
                
                <div className="flex gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      setShowCreateForm(false)
                      setCreateError(null)
                    }}
                    className="flex-1 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    type="submit"
                    disabled={isCreating}
                    className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700 disabled:opacity-50"
                  >
                    {isCreating && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
                    {t('users.create')}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Edit User Modal */}
        {editingUser && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-md w-full p-6">
              <h2 className="text-xl font-bold text-primary-900 dark:text-gray-100 mb-4">{t('users.edit')}</h2>
              
              <form onSubmit={handleUpdateUser} className="space-y-4">
                <div>
                    {t('users.email')}
                  <input
                    type="email"
                    value={editForm.email || ''}
                    onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                    className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                    {t('users.name')}
                  </label>
                  <input
                    type="text"
                    value={editForm.name || ''}
                    onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                    minLength={2}
                    className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                    {t('users.newPasswordLabel', 'New Password (leave empty to keep current)')}
                  </label>
                  <div className="relative">
                    <input
                      type={showEditPassword ? 'text' : 'password'}
                      value={editForm.new_password || ''}
                      onChange={(e) => setEditForm({ ...editForm, new_password: e.target.value })}
                      minLength={6}
                      className="w-full px-4 py-2 pr-10 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                      placeholder="Minimum 6 characters"
                    />
                    <button
                      type="button"
                      onClick={() => setShowEditPassword(!showEditPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      {showEditPassword ? <EyeSlashIcon className="h-5 w-5" /> : <EyeIcon className="h-5 w-5" />}
                    </button>
                  </div>
                </div>
                
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="edit_is_admin"
                    checked={editForm.is_admin || false}
                    onChange={(e) => setEditForm({ ...editForm, is_admin: e.target.checked })}
                    disabled={editingUser.id === currentUser?.id}
                    className="rounded"
                  />
                  <label htmlFor="edit_is_admin" className="text-sm text-primary-900 dark:text-gray-200">
                    {t('users.adminPrivileges', 'Administrator privileges')}
                  </label>
                  {editingUser.id === currentUser?.id && (
                    <span className="text-xs text-secondary dark:text-gray-500">({t('users.cannotModifyOwn', 'cannot modify your own admin status')})</span>
                  )}
                </div>
                
                {updateError && (
                  <div className="text-sm text-red-600 dark:text-red-400">
                    {updateError}
                  </div>
                )}
                
                <div className="flex gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      setEditingUser(null)
                      setUpdateError(null)
                    }}
                    className="flex-1 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    type="submit"
                    disabled={isUpdating}
                    className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700 disabled:opacity-50"
                  >
                    {isUpdating && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
                    {t('common.save')}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Delete Confirmation Modal */}
        {deletingUser && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-md w-full p-6">
              <div className="text-center">
                <ExclamationTriangleIcon className="h-16 w-16 mx-auto text-red-500 mb-4" />
                <h2 className="text-xl font-bold text-primary-900 dark:text-gray-100 mb-2">{t('users.deleteConfirm', 'Delete User?')}</h2>
                <p className="text-secondary dark:text-gray-400 mb-6">
                  {t('users.deleteWarning', 'Are you sure you want to delete')} <strong>{deletingUser.name}</strong> ({deletingUser.email})?
                  {t('users.cannotUndo', 'This action cannot be undone.')}
                </p>
                
                <div className="flex gap-3">
                  <button
                    onClick={() => setDeletingUser(null)}
                    className="flex-1 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    onClick={handleDeleteUser}
                    disabled={isDeleting}
                    className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {isDeleting && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
                    {t('users.delete')}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Users List */}
        <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1 overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : users.length === 0 ? (
            <div className="text-center py-12">
              <UserCircleIcon className="h-16 w-16 mx-auto text-gray-400 mb-4" />
              <p className="text-secondary dark:text-gray-400">{t('users.noUsers')}</p>
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-gray-50 dark:bg-gray-700/50">
                <tr>
                  <th className="text-left py-3 px-4 text-sm font-medium text-secondary dark:text-gray-400">{t('users.user', 'User')}</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-secondary dark:text-gray-400">{t('users.status')}</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-secondary dark:text-gray-400">{t('users.role')}</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-secondary dark:text-gray-400">{t('users.createdAt')}</th>
                  <th className="text-right py-3 px-4 text-sm font-medium text-secondary dark:text-gray-400">{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {users.map(user => (
                  <tr key={user.id} className={`${!user.is_active ? 'opacity-60' : ''}`}>
                    <td className="py-4 px-4">
                      <div className="flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
                          user.is_admin
                            ? 'bg-purple-100 dark:bg-purple-900/30'
                            : 'bg-gray-100 dark:bg-gray-700'
                        }`}>
                          {user.is_admin ? (
                            <ShieldCheckIcon className="h-5 w-5 text-purple-600 dark:text-purple-400" />
                          ) : (
                            <UserCircleIcon className="h-5 w-5 text-gray-500" />
                          )}
                        </div>
                        <div>
                          <p className="font-medium text-primary-900 dark:text-gray-200">{user.name}</p>
                          <p className="text-sm text-secondary dark:text-gray-500">{user.email}</p>
                        </div>
                      </div>
                    </td>
                    <td className="py-4 px-4">
                      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${
                        user.is_active
                          ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                          : 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
                      }`}>
                        {user.is_active ? (
                          <><CheckCircleIcon className="h-3 w-3" /> {t('users.active')}</>
                        ) : (
                          <><XCircleIcon className="h-3 w-3" /> {t('users.inactive')}</>
                        )}
                      </span>
                    </td>
                    <td className="py-4 px-4">
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        user.is_admin
                          ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400'
                          : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-400'
                      }`}>
                        {user.is_admin ? t('users.admin') : t('users.user')}
                      </span>
                    </td>
                    <td className="py-4 px-4 text-sm text-secondary dark:text-gray-500">
                      {new Date(user.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-4 px-4">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => startEditing(user)}
                          className="p-2 rounded-lg text-primary hover:bg-primary-100 dark:hover:bg-primary-900/30"
                          title={t('users.editUser')}
                        >
                          <PencilIcon className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => handleToggleStatus(user)}
                          disabled={user.id === currentUser?.id}
                          className={`p-2 rounded-lg ${
                            user.id === currentUser?.id
                              ? 'text-gray-300 cursor-not-allowed'
                              : user.is_active
                              ? 'text-amber-500 hover:bg-amber-100 dark:hover:bg-amber-900/30'
                              : 'text-green-500 hover:bg-green-100 dark:hover:bg-green-900/30'
                          }`}
                          title={user.is_active ? t('users.deactivate', 'Deactivate user') : t('users.activate', 'Activate user')}
                        >
                          {user.is_active ? (
                            <XCircleIcon className="h-4 w-4" />
                          ) : (
                            <CheckCircleIcon className="h-4 w-4" />
                          )}
                        </button>
                        <button
                          onClick={() => setDeletingUser(user)}
                          disabled={user.id === currentUser?.id}
                          className={`p-2 rounded-lg ${
                            user.id === currentUser?.id
                              ? 'text-gray-300 cursor-not-allowed'
                              : 'text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30'
                          }`}
                          title={t('users.deleteUser')}
                        >
                          <TrashIcon className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Summary stats */}
        <div className="grid gap-4 md:grid-cols-4">
          <div className="rounded-xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <p className="text-sm text-secondary dark:text-gray-400">{t('users.totalUsers', 'Total Users')}</p>
            <p className="text-2xl font-bold text-primary-900 dark:text-gray-100">{users.length}</p>
          </div>
          <div className="rounded-xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <p className="text-sm text-secondary dark:text-gray-400">{t('users.active')}</p>
            <p className="text-2xl font-bold text-green-600 dark:text-green-400">
              {users.filter(u => u.is_active).length}
            </p>
          </div>
          <div className="rounded-xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <p className="text-sm text-secondary dark:text-gray-400">{t('users.inactive')}</p>
            <p className="text-2xl font-bold text-red-600 dark:text-red-400">
              {users.filter(u => !u.is_active).length}
            </p>
          </div>
          <div className="rounded-xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1">
            <p className="text-sm text-secondary dark:text-gray-400">{t('users.administrators', 'Administrators')}</p>
            <p className="text-2xl font-bold text-purple-600 dark:text-purple-400">
              {users.filter(u => u.is_admin).length}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
