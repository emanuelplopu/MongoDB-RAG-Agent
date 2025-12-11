import { useState, useEffect } from 'react'
import {
  UserCircleIcon,
  PlusIcon,
  CheckCircleIcon,
  TrashIcon,
  ArrowPathIcon,
  FolderIcon,
  CircleStackIcon,
} from '@heroicons/react/24/outline'
import { profilesApi, Profile, ProfileListResponse } from '../api/client'

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [activeProfile, setActiveProfile] = useState<string>('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreateForm, setShowCreateForm] = useState(false)

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
      setError('Failed to load profiles.')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchProfiles()
  }, [])

  const handleSwitch = async (profileKey: string) => {
    try {
      await profilesApi.switch(profileKey)
      setActiveProfile(profileKey)
    } catch (err) {
      console.error('Error switching profile:', err)
      setError('Failed to switch profile.')
    }
  }

  const handleDelete = async (profileKey: string) => {
    if (profileKey === 'default') {
      alert('Cannot delete the default profile.')
      return
    }
    if (!confirm(`Are you sure you want to delete profile "${profileKey}"?`)) {
      return
    }

    try {
      await profilesApi.delete(profileKey)
      fetchProfiles()
    } catch (err) {
      console.error('Error deleting profile:', err)
      setError('Failed to delete profile.')
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
      setError('Failed to create profile.')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900">Profiles</h2>
          <p className="text-sm text-secondary">
            Manage knowledge base profiles for different projects
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchProfiles}
            disabled={isLoading}
            className="flex items-center gap-2 rounded-xl bg-surface-variant px-4 py-2 text-sm font-medium text-primary-700 transition-all hover:bg-primary-100"
          >
            <ArrowPathIcon className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-all hover:bg-primary-700"
          >
            <PlusIcon className="h-4 w-4" />
            New Profile
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-2xl bg-red-50 p-4 text-red-700">{error}</div>
      )}

      {/* Create form */}
      {showCreateForm && (
        <div className="rounded-2xl bg-surface p-6 shadow-elevation-1">
          <h3 className="text-lg font-medium text-primary-900 mb-4">Create New Profile</h3>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-primary-900 mb-1">
                  Profile Key *
                </label>
                <input
                  type="text"
                  value={newProfile.key}
                  onChange={(e) => setNewProfile({ ...newProfile, key: e.target.value })}
                  placeholder="my-project"
                  required
                  className="w-full rounded-xl border border-surface-variant bg-white px-4 py-2 text-primary-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-primary-900 mb-1">
                  Display Name *
                </label>
                <input
                  type="text"
                  value={newProfile.name}
                  onChange={(e) => setNewProfile({ ...newProfile, name: e.target.value })}
                  placeholder="My Project"
                  required
                  className="w-full rounded-xl border border-surface-variant bg-white px-4 py-2 text-primary-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-primary-900 mb-1">
                Description
              </label>
              <input
                type="text"
                value={newProfile.description}
                onChange={(e) => setNewProfile({ ...newProfile, description: e.target.value })}
                placeholder="Optional description"
                className="w-full rounded-xl border border-surface-variant bg-white px-4 py-2 text-primary-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-primary-900 mb-1">
                Documents Folders *
              </label>
              <input
                type="text"
                value={newProfile.documents_folders}
                onChange={(e) => setNewProfile({ ...newProfile, documents_folders: e.target.value })}
                placeholder="./documents, ./data (comma-separated)"
                required
                className="w-full rounded-xl border border-surface-variant bg-white px-4 py-2 text-primary-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-primary-900 mb-1">
                Database Name
              </label>
              <input
                type="text"
                value={newProfile.database}
                onChange={(e) => setNewProfile({ ...newProfile, database: e.target.value })}
                placeholder="Optional (defaults to profile key)"
                className="w-full rounded-xl border border-surface-variant bg-white px-4 py-2 text-primary-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
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
                className="rounded-xl bg-surface-variant px-6 py-2 font-medium text-secondary hover:bg-primary-100 transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Profiles list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
        </div>
      ) : Object.keys(profiles).length === 0 ? (
        <div className="rounded-2xl bg-surface-variant p-8 text-center">
          <UserCircleIcon className="mx-auto h-12 w-12 text-secondary mb-3" />
          <p className="text-secondary">No profiles found.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Object.entries(profiles).map(([key, profile]) => (
            <div
              key={key}
              className={`rounded-2xl bg-surface p-5 shadow-elevation-1 transition-all hover:shadow-elevation-2 ${
                activeProfile === key ? 'ring-2 ring-primary' : ''
              }`}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <UserCircleIcon className="h-6 w-6 text-primary" />
                  <h3 className="font-medium text-primary-900">{profile.name}</h3>
                </div>
                {activeProfile === key && (
                  <span className="flex items-center gap-1 rounded-lg bg-green-100 px-2 py-1 text-xs font-medium text-green-700">
                    <CheckCircleIcon className="h-3 w-3" />
                    Active
                  </span>
                )}
              </div>

              {profile.description && (
                <p className="text-sm text-secondary mb-3">{profile.description}</p>
              )}

              <div className="space-y-2 text-xs text-secondary mb-4">
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

              <div className="flex gap-2 pt-3 border-t border-surface-variant">
                {activeProfile !== key && (
                  <button
                    onClick={() => handleSwitch(key)}
                    className="flex-1 rounded-lg bg-primary-100 px-3 py-2 text-sm font-medium text-primary-700 hover:bg-primary-200 transition-colors"
                  >
                    Activate
                  </button>
                )}
                {key !== 'default' && (
                  <button
                    onClick={() => handleDelete(key)}
                    className="rounded-lg p-2 text-red-500 hover:bg-red-50 transition-colors"
                    title="Delete profile"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
