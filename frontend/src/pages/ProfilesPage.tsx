import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  UserCircleIcon,
  PlusIcon,
  CheckCircleIcon,
  TrashIcon,
  ArrowPathIcon,
  FolderIcon,
  CircleStackIcon,
  ShieldCheckIcon,
  PencilIcon,
  XMarkIcon,
  FolderPlusIcon,
} from '@heroicons/react/24/outline'
import { profilesApi, authApi, Profile, ProfileListResponse, ProfileAccessMatrix } from '../api/client'
import { getErrorStatus } from '../api/errors'
import { canManageProfiles, isOrgAdmin } from '../api/roles'
import { useAuth } from '../contexts/AuthContext'
import ServerFolderPicker from '../components/ServerFolderPicker'

export default function ProfilesPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { user, isLoading: isAuthLoading } = useAuth()
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [activeProfile, setActiveProfile] = useState<string>('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [showAccessMatrix, setShowAccessMatrix] = useState(false)
  
  // Access matrix state (admin only)
  const [accessMatrix, setAccessMatrix] = useState<ProfileAccessMatrix | null>(null)
  const [isLoadingMatrix, setIsLoadingMatrix] = useState(false)
  const [matrixError, setMatrixError] = useState<string | null>(null)

  const [switchMessage, setSwitchMessage] = useState<string | null>(null)

  // Edit form state. Folders are kept as a list: they are picked from the
  // server's document roots (or typed) one at a time, never comma-joined.
  const [editingProfile, setEditingProfile] = useState<string | null>(null)
  const [editProfile, setEditProfile] = useState<{
    name: string
    description: string
    documents_folders: string[]
    database: string
  }>({
    name: '',
    description: '',
    documents_folders: [],
    database: '',
  })
  const [newFolderPath, setNewFolderPath] = useState('')
  const [showFolderPicker, setShowFolderPicker] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  const canEditProfiles = canManageProfiles(user)
  const isSuperAdminUser = Boolean(user?.is_admin)

  // Create form state
  const [newProfile, setNewProfile] = useState({
    key: '',
    name: '',
    description: '',
    documents_folders: '',
    database: '',
  })

  const fetchProfiles = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const response: ProfileListResponse = await profilesApi.list()
      setProfiles(response.profiles)
      setActiveProfile(response.active_profile)
    } catch (err) {
      console.error('Error fetching profiles:', err)
      // Handle 403 by redirecting non-admins
      if (getErrorStatus(err) === 403) {
        setError(t('profiles.managerAccess'))
      } else {
        setError(t('profiles.loadFailed', 'Failed to load profiles.'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  // Redirect members after auth is loaded; superadmins and organisation admins stay
  useEffect(() => {
    if (!isAuthLoading && user && !canManageProfiles(user)) {
      navigate('/dashboard')
    }
  }, [isAuthLoading, user, navigate])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial load; fetchProfiles flips the loading flag before awaiting the API
    fetchProfiles()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once on mount; fetchProfiles is called again explicitly after every mutation
  }, [])

  // Fetch access matrix for admins
  const fetchAccessMatrix = async () => {
    if (!user?.is_admin) return
    
    setIsLoadingMatrix(true)
    setMatrixError(null)
    try {
      const matrix = await authApi.getAccessMatrix()
      setAccessMatrix(matrix)
    } catch (err) {
      console.error('Error fetching access matrix:', err)
      setMatrixError(t('profiles.matrixFailed', 'Failed to load access matrix.'))
    } finally {
      setIsLoadingMatrix(false)
    }
  }

  useEffect(() => {
    if (showAccessMatrix && user?.is_admin) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- matrix load when the panel opens; fetchAccessMatrix flips its loading flag before awaiting the API
      fetchAccessMatrix()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-runs only when the panel opens or the role changes; fetchAccessMatrix is called again explicitly after toggles
  }, [showAccessMatrix, user?.is_admin])

  const handleToggleAccess = async (userId: string, profileKey: string, currentlyHasAccess: boolean) => {
    try {
      await authApi.setProfileAccess({
        user_id: userId,
        profile_key: profileKey,
        has_access: !currentlyHasAccess
      })
      // Refresh the matrix
      fetchAccessMatrix()
    } catch (err) {
      console.error('Error toggling access:', err)
      setMatrixError(t('profiles.accessFailed', 'Failed to update access.'))
    }
  }

  const handleSwitch = async (profileKey: string) => {
    try {
      setSwitchMessage(null)
      const response = await profilesApi.switch(profileKey)
      setActiveProfile(profileKey)
      setSwitchMessage(response.message || t('profiles.switchSuccess', { name: profileKey }))
      
      // Clear message after 3 seconds
      setTimeout(() => setSwitchMessage(null), 3000)
    } catch (err) {
      console.error('Error switching profile:', err)
      setError(t('profiles.switchFailed', 'Failed to switch profile.'))
    }
  }

  const handleDelete = async (profileKey: string) => {
    if (profileKey === 'default') {
      alert(t('profiles.cannotDeleteDefault', 'Cannot delete the default profile.'))
      return
    }
    if (!confirm(t('confirm.deleteProfile', { name: profileKey }))) {
      return
    }

    try {
      await profilesApi.delete(profileKey)
      fetchProfiles()
    } catch (err) {
      console.error('Error deleting profile:', err)
      setError(t('profiles.deleteFailed', 'Failed to delete profile.'))
    }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await profilesApi.create({
        key: newProfile.key,
        name: newProfile.name,
        description: newProfile.description || undefined,
        documents_folders: newProfile.documents_folders
          .split(',')
          .map((f) => f.trim())
          .filter(Boolean),
        database: newProfile.database || undefined,
      })
      setShowCreateForm(false)
      setNewProfile({ key: '', name: '', description: '', documents_folders: '', database: '' })
      fetchProfiles()
    } catch (err) {
      console.error('Error creating profile:', err)
      setError(t('profiles.createFailed', 'Failed to create profile.'))
    }
  }

  const handleStartEdit = (key: string, profile: Profile) => {
    setEditingProfile(key)
    setEditError(null)
    setNewFolderPath('')
    setEditProfile({
      name: profile.name,
      description: profile.description || '',
      documents_folders: [...profile.documents_folders],
      database: profile.database,
    })
  }

  const handleCancelEdit = () => {
    setEditingProfile(null)
    setEditError(null)
    setNewFolderPath('')
    setShowFolderPicker(false)
    setEditProfile({ name: '', description: '', documents_folders: [], database: '' })
  }

  const addFolder = (path: string) => {
    const folder = path.trim()
    if (!folder) return
    setEditProfile((prev) =>
      prev.documents_folders.includes(folder)
        ? prev
        : { ...prev, documents_folders: [...prev.documents_folders, folder] }
    )
    setNewFolderPath('')
    setShowFolderPicker(false)
  }

  const removeFolder = (path: string) => {
    setEditProfile((prev) => ({
      ...prev,
      documents_folders: prev.documents_folders.filter((f) => f !== path),
    }))
  }

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editingProfile) return
    if (editProfile.documents_folders.length === 0) {
      setEditError(t('profiles.noFolders', 'No folders yet'))
      return
    }

    try {
      setEditError(null)
      await profilesApi.update(editingProfile, {
        name: editProfile.name,
        description: editProfile.description || undefined,
        documents_folders: editProfile.documents_folders,
        // Only superadmins may move a profile to another database
        ...(isSuperAdminUser ? { database: editProfile.database || undefined } : {}),
      })
      setEditingProfile(null)
      setEditProfile({ name: '', description: '', documents_folders: [], database: '' })
      setSwitchMessage(`Updated profile: ${editingProfile}`)
      setTimeout(() => setSwitchMessage(null), 3000)
      fetchProfiles()
    } catch (err) {
      console.error('Error updating profile:', err)
      // The backend explains refused folders (outside the document roots, not mounted)
      const message = err instanceof Error && err.message ? err.message : ''
      setEditError(message || t('profiles.updateFailed', 'Failed to update profile.'))
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-display font-semibold text-primary-900 dark:text-gray-200">{t('profiles.title')}</h2>
          <p className="text-sm text-primary-700 dark:text-gray-400">
            {t('profiles.subtitle')}
          </p>
        </div>
        <div className="flex gap-2">
          {user?.is_admin && (
            <button
              onClick={() => setShowAccessMatrix(!showAccessMatrix)}
              className={`flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${
                showAccessMatrix
                  ? 'bg-primary text-white'
                  : 'bg-surface-variant dark:bg-gray-700 text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-600'
              }`}
            >
              <ShieldCheckIcon className="h-4 w-4" />
              {t('profiles.accessRights')}
            </button>
          )}
          <button
            onClick={fetchProfiles}
            disabled={isLoading}
            className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
          >
            <ArrowPathIcon className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            {t('common.refresh')}
          </button>
          {user?.is_admin && (
            <button
              onClick={() => setShowCreateForm(!showCreateForm)}
              className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-all hover:bg-primary-700"
            >
              <PlusIcon className="h-4 w-4" />
              {t('profiles.newProfile')}
            </button>
          )}
        </div>
      </div>

      {/* Success message */}
      {switchMessage && (
        <div className="rounded-2xl bg-green-50 dark:bg-green-900/30 p-4 text-green-700 dark:text-green-400 flex items-center gap-2">
          <CheckCircleIcon className="h-5 w-5" />
          {switchMessage}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}

      {/* What organisation admins can do here */}
      {isOrgAdmin(user) && (
        <div className="rounded-2xl bg-primary-50 dark:bg-primary-900/20 p-4 text-sm text-primary-800 dark:text-primary-200">
          {t('profiles.orgAdminNote')}
        </div>
      )}

      {/* Create form */}
      {showCreateForm && user?.is_admin && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">{t('profiles.createNew')}</h3>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                  {t('profiles.profileKey')} *
                </label>
                <input
                  type="text"
                  value={newProfile.key}
                  onChange={(e) => setNewProfile({ ...newProfile, key: e.target.value })}
                  placeholder="my-project"
                  required
                  className="w-full rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-4 py-2 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-hidden focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                  {t('profiles.displayName')} *
                </label>
                <input
                  type="text"
                  value={newProfile.name}
                  onChange={(e) => setNewProfile({ ...newProfile, name: e.target.value })}
                  placeholder="My Project"
                  required
                  className="w-full rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-4 py-2 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-hidden focus:ring-2 focus:ring-primary/20"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                Description
              </label>
              <input
                type="text"
                value={newProfile.description}
                onChange={(e) => setNewProfile({ ...newProfile, description: e.target.value })}
                placeholder="Optional description"
                className="w-full rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-4 py-2 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-hidden focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                Documents Folders *
              </label>
              <input
                type="text"
                value={newProfile.documents_folders}
                onChange={(e) => setNewProfile({ ...newProfile, documents_folders: e.target.value })}
                placeholder="./documents, ./data (comma-separated)"
                required
                className="w-full rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-4 py-2 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-hidden focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-1">
                Database Name
              </label>
              <input
                type="text"
                value={newProfile.database}
                onChange={(e) => setNewProfile({ ...newProfile, database: e.target.value })}
                placeholder="Optional (defaults to profile key)"
                className="w-full rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-4 py-2 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-hidden focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="submit"
                className="rounded-xl bg-primary px-6 py-2 font-medium text-white hover:bg-primary-700 transition-colors"
              >
                Create Profile
              </button>
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="rounded-xl bg-surface-variant dark:bg-gray-700 px-6 py-2 font-medium text-secondary dark:text-gray-300 hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Access Rights Matrix (Admin Only) */}
      {showAccessMatrix && user?.is_admin && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4 flex items-center gap-2">
            <ShieldCheckIcon className="h-5 w-5 text-primary" />
            Access Rights Matrix
          </h3>
          
          {matrixError && (
            <div className="rounded-xl bg-red-50 dark:bg-red-900/30 p-3 text-red-700 dark:text-red-400 mb-4">
              {matrixError}
            </div>
          )}
          
          {isLoadingMatrix ? (
            <div className="flex justify-center py-8">
              <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : accessMatrix ? (
            <div className="overflow-x-auto">
              <table className="min-w-full border-collapse">
                <thead>
                  <tr>
                    <th className="sticky left-0 bg-surface dark:bg-gray-800 px-4 py-3 text-left text-xs font-semibold text-primary-600 dark:text-primary-400 uppercase tracking-wider border-b border-surface-variant dark:border-gray-700">
                      Profile / User
                    </th>
                    {accessMatrix.users.filter(u => !u.is_admin).map((u) => (
                      <th key={u.id} className="px-4 py-3 text-center text-xs font-semibold text-primary-600 dark:text-primary-400 uppercase tracking-wider border-b border-surface-variant dark:border-gray-700 min-w-[120px]">
                        <div className="truncate max-w-[120px]" title={u.email}>
                          {u.name}
                        </div>
                        <div className="text-[10px] font-normal normal-case text-secondary/70 dark:text-gray-500 truncate" title={u.email}>
                          {u.email}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {accessMatrix.profiles.map((profileKey) => (
                    <tr key={profileKey} className="hover:bg-surface-variant/50 dark:hover:bg-gray-700/50">
                      <td className="sticky left-0 bg-surface dark:bg-gray-800 px-4 py-3 text-sm font-medium text-primary-900 dark:text-gray-200 border-b border-surface-variant dark:border-gray-700">
                        <div className="flex items-center gap-2">
                          <FolderIcon className="h-4 w-4 text-primary" />
                          {profiles[profileKey]?.name || profileKey}
                        </div>
                      </td>
                      {accessMatrix.users.filter(u => !u.is_admin).map((u) => {
                        const hasAccess = accessMatrix.access[u.id]?.includes(profileKey) || false
                        return (
                          <td key={u.id} className="px-4 py-3 text-center border-b border-surface-variant dark:border-gray-700">
                            <button
                              onClick={() => handleToggleAccess(u.id, profileKey, hasAccess)}
                              className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
                                hasAccess
                                  ? 'bg-green-100 dark:bg-green-900/50 text-green-600 dark:text-green-400 hover:bg-green-200 dark:hover:bg-green-900'
                                  : 'bg-gray-100 dark:bg-gray-700 text-gray-400 dark:text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-600'
                              }`}
                              title={hasAccess ? 'Revoke access' : 'Grant access'}
                            >
                              {hasAccess ? (
                                <CheckCircleIcon className="h-5 w-5" />
                              ) : (
                                <div className="w-3 h-3 rounded-full border-2 border-current" />
                              )}
                            </button>
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              
              {accessMatrix.users.filter(u => !u.is_admin).length === 0 && (
                <div className="text-center py-8 text-secondary dark:text-gray-400">
                  No non-admin users to manage. Register more users to set their access rights.
                </div>
              )}
              
              <div className="mt-4 text-xs text-secondary dark:text-gray-500">
                <strong>Note:</strong> Admin users (like you) automatically have access to all profiles.
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* Profiles list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
        </div>
      ) : Object.keys(profiles).length === 0 ? (
        <div className="rounded-2xl bg-surface-variant dark:bg-gray-800 p-8 text-center">
          <UserCircleIcon className="mx-auto h-12 w-12 text-secondary dark:text-gray-500 mb-3" />
          <p className="text-secondary dark:text-gray-400">No profiles found.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Object.entries(profiles).map(([key, profile]) => (
            <div
              key={key}
              className={`rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1 transition-all hover:shadow-elevation-2 ${
                activeProfile === key ? 'ring-2 ring-primary' : ''
              }`}
            >
              {editingProfile === key ? (
                // Edit form inline
                <form onSubmit={handleUpdate} className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-secondary dark:text-gray-400 mb-1">
                      Display Name
                    </label>
                    <input
                      type="text"
                      value={editProfile.name}
                      onChange={(e) => setEditProfile({ ...editProfile, name: e.target.value })}
                      required
                      className="w-full rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-hidden focus:ring-1 focus:ring-primary/20"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-secondary dark:text-gray-400 mb-1">
                      Description
                    </label>
                    <input
                      type="text"
                      value={editProfile.description}
                      onChange={(e) => setEditProfile({ ...editProfile, description: e.target.value })}
                      className="w-full rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-hidden focus:ring-1 focus:ring-primary/20"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-secondary dark:text-gray-400 mb-1">
                      {t('profiles.documentsFolders', 'Documents Folders')}
                    </label>
                    {editProfile.documents_folders.length === 0 ? (
                      <p className="text-xs text-secondary dark:text-gray-400 mb-2">{t('profiles.noFolders', 'No folders yet')}</p>
                    ) : (
                      <ul className="space-y-1 mb-2">
                        {editProfile.documents_folders.map((folder) => (
                          <li
                            key={folder}
                            className="flex items-center gap-2 rounded-lg bg-surface-variant dark:bg-gray-700 px-2 py-1 text-xs text-primary-900 dark:text-gray-200"
                          >
                            <FolderIcon className="h-4 w-4 text-secondary dark:text-gray-400 shrink-0" />
                            <span className="truncate flex-1" title={folder}>{folder}</span>
                            <button
                              type="button"
                              onClick={() => removeFolder(folder)}
                              className="rounded-sm p-1 text-secondary hover:text-red-600 dark:hover:text-red-400"
                              title={t('profiles.removeFolder', 'Remove folder')}
                              aria-label={`${t('profiles.removeFolder', 'Remove folder')}: ${folder}`}
                            >
                              <XMarkIcon className="h-4 w-4" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={newFolderPath}
                        onChange={(e) => setNewFolderPath(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            addFolder(newFolderPath)
                          }
                        }}
                        placeholder={t('profiles.folderPathPlaceholder')}
                        aria-label={t('profiles.addFolder', 'Add folder')}
                        className="flex-1 min-w-0 rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-hidden focus:ring-1 focus:ring-primary/20"
                      />
                      <button
                        type="button"
                        onClick={() => addFolder(newFolderPath)}
                        disabled={!newFolderPath.trim()}
                        className="rounded-lg bg-surface-variant dark:bg-gray-700 px-3 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-600 disabled:opacity-50"
                      >
                        {t('profiles.addFolder', 'Add folder')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowFolderPicker(true)}
                        className="rounded-lg bg-primary-100 dark:bg-primary-900/50 p-2 text-primary-700 dark:text-primary-300 hover:bg-primary-200 dark:hover:bg-primary-900"
                        title={t('profiles.browseFolders', 'Browse server folders')}
                        aria-label={t('profiles.browseFolders', 'Browse server folders')}
                      >
                        <FolderPlusIcon className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                  {isSuperAdminUser && (
                    <div>
                      <label className="block text-xs font-medium text-secondary dark:text-gray-400 mb-1">
                        Database Name
                      </label>
                      <input
                        type="text"
                        value={editProfile.database}
                        onChange={(e) => setEditProfile({ ...editProfile, database: e.target.value })}
                        className="w-full rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-hidden focus:ring-1 focus:ring-primary/20"
                      />
                    </div>
                  )}
                  {editError && (
                    <div className="rounded-lg bg-red-50 dark:bg-red-900/30 p-2 text-xs text-red-700 dark:text-red-400">{editError}</div>
                  )}
                  {showFolderPicker && (
                    <ServerFolderPicker
                      onSelect={addFolder}
                      onClose={() => setShowFolderPicker(false)}
                      initialPath={editProfile.documents_folders[0] ?? null}
                    />
                  )}
                  <div className="flex gap-2 pt-2">
                    <button
                      type="submit"
                      className="flex-1 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelEdit}
                      className="rounded-lg bg-surface-variant dark:bg-gray-700 px-3 py-2 text-sm font-medium text-secondary dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                // Normal view
                <>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <UserCircleIcon className="h-6 w-6 text-primary" />
                      <h3 className="font-medium text-primary-900 dark:text-gray-200">{profile.name}</h3>
                    </div>
                    {activeProfile === key && (
                      <span className="flex items-center gap-1 rounded-lg bg-green-100 dark:bg-green-900/50 px-2 py-1 text-xs font-medium text-green-700 dark:text-green-400">
                        <CheckCircleIcon className="h-3 w-3" />
                        Active
                      </span>
                    )}
                  </div>

                  {profile.description && (
                    <p className="text-sm text-secondary dark:text-gray-400 mb-3">{profile.description}</p>
                  )}

                  <div className="space-y-2 text-xs text-secondary dark:text-gray-400 mb-4">
                    <div className="flex items-center gap-2">
                      <FolderIcon className="h-4 w-4" />
                      <span className="truncate">
                        {profile.documents_folders.join(', ')}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <CircleStackIcon className="h-4 w-4" />
                      <span>{profile.database}</span>
                    </div>
                  </div>

                  <div className="flex gap-2 pt-3 border-t border-surface-variant dark:border-gray-700">
                    {activeProfile !== key && (
                      <button
                        onClick={() => handleSwitch(key)}
                        className="flex-1 rounded-lg bg-primary-100 dark:bg-primary-900/50 px-3 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-200 dark:hover:bg-primary-900 transition-colors"
                      >
                        Activate
                      </button>
                    )}
                    {canEditProfiles && (
                      <button
                        onClick={() => handleStartEdit(key, profile)}
                        className="rounded-lg p-2 text-primary-600 dark:text-primary-400 hover:bg-primary-50 dark:hover:bg-primary-900/30 transition-colors"
                        title="Edit profile"
                      >
                        <PencilIcon className="h-4 w-4" />
                      </button>
                    )}
                    {key !== 'default' && user?.is_admin && (
                      <button
                        onClick={() => handleDelete(key)}
                        className="rounded-lg p-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors"
                        title="Delete profile"
                      >
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
