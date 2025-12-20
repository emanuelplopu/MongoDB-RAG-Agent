import { useState, useEffect } from 'react'
import { sessionsApi, ChatSession } from '../api/client'
import { ArchiveBoxIcon, ArrowPathIcon, TrashIcon, ArrowDownTrayIcon } from '@heroicons/react/24/outline'

export default function ArchivedChatsPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [operating, setOperating] = useState(false)

  useEffect(() => {
    loadArchivedSessions()
  }, [])

  const loadArchivedSessions = async () => {
    try {
      setLoading(true)
      const data = await sessionsApi.listArchived()
      setSessions(data.sessions)
      setError(null)
    } catch (err) {
      setError('Failed to load archived chats')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const toggleSelection = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const selectAll = () => {
    setSelectedIds(new Set(sessions.map(s => s.id)))
  }

  const clearSelection = () => {
    setSelectedIds(new Set())
  }

  const handleRestore = async (ids: string[]) => {
    if (ids.length === 0) return
    try {
      setOperating(true)
      await sessionsApi.restoreSessions(ids)
      setSessions(prev => prev.filter(s => !ids.includes(s.id)))
      setSelectedIds(new Set())
    } catch (err) {
      setError('Failed to restore chats')
      console.error(err)
    } finally {
      setOperating(false)
    }
  }

  const handlePermanentDelete = async (ids: string[]) => {
    if (ids.length === 0) return
    if (!confirm(`Permanently delete ${ids.length} chat(s)? This cannot be undone.`)) {
      return
    }
    try {
      setOperating(true)
      await sessionsApi.deletePermanently(ids)
      setSessions(prev => prev.filter(s => !ids.includes(s.id)))
      setSelectedIds(new Set())
    } catch (err) {
      setError('Failed to delete chats')
      console.error(err)
    } finally {
      setOperating(false)
    }
  }

  const handleExport = async (sessionId: string) => {
    try {
      const data = await sessionsApi.exportSession(sessionId)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `chat-${data.session.title.replace(/[^a-z0-9]/gi, '_')}-${sessionId.slice(0, 8)}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      setError('Failed to export chat')
      console.error(err)
    }
  }

  const formatDate = (dateStr: string | undefined) => {
    if (!dateStr) return 'Unknown'
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-3">
            <ArchiveBoxIcon className="h-8 w-8 text-amber-600" />
            <div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Archived Chats</h1>
              <p className="text-gray-600 dark:text-gray-400">
                Restore, download, or permanently delete archived conversations
              </p>
            </div>
          </div>
        </div>

        {/* Error display */}
        {error && (
          <div className="mb-4 p-4 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg">
            {error}
            <button onClick={() => setError(null)} className="float-right text-red-500 hover:text-red-700">×</button>
          </div>
        )}

        {/* Actions bar */}
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4 bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
          <div className="flex items-center gap-3">
            <button
              onClick={loadArchivedSessions}
              disabled={loading}
              className="px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 flex items-center gap-2"
            >
              <ArrowPathIcon className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            {sessions.length > 0 && (
              <>
                <button
                  onClick={selectAll}
                  className="px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
                >
                  Select All
                </button>
                {selectedIds.size > 0 && (
                  <button
                    onClick={clearSelection}
                    className="px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
                  >
                    Clear ({selectedIds.size})
                  </button>
                )}
              </>
            )}
          </div>
          
          {selectedIds.size > 0 && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleRestore(Array.from(selectedIds))}
                disabled={operating}
                className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 flex items-center gap-2"
              >
                <ArrowPathIcon className="h-4 w-4" />
                Restore ({selectedIds.size})
              </button>
              <button
                onClick={() => handlePermanentDelete(Array.from(selectedIds))}
                disabled={operating}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 flex items-center gap-2"
              >
                <TrashIcon className="h-4 w-4" />
                Delete Forever ({selectedIds.size})
              </button>
            </div>
          )}
        </div>

        {/* Sessions list */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="animate-spin h-8 w-8 border-2 border-indigo-600 border-t-transparent rounded-full" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="py-12 text-center text-gray-500 dark:text-gray-400">
              <ArchiveBoxIcon className="h-12 w-12 mx-auto mb-4 text-gray-300 dark:text-gray-600" />
              <p>No archived chats</p>
              <p className="text-sm mt-1">Archived conversations will appear here</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-200 dark:divide-gray-700">
              {sessions.map(session => (
                <div
                  key={session.id}
                  className={`p-4 flex items-center gap-4 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors ${
                    selectedIds.has(session.id) ? 'bg-indigo-50 dark:bg-indigo-900/20' : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(session.id)}
                    onChange={() => toggleSelection(session.id)}
                    className="h-5 w-5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-medium text-gray-900 dark:text-white truncate">
                      {session.title || 'Untitled Chat'}
                    </h3>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 flex items-center gap-4">
                      <span>Archived: {formatDate(session.archived_at)}</span>
                      <span>Created: {formatDate(session.created_at)}</span>
                      {session.stats && (
                        <span>{session.stats.total_messages || 0} messages</span>
                      )}
                    </div>
                  </div>
                  
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleExport(session.id)}
                      className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                      title="Download"
                    >
                      <ArrowDownTrayIcon className="h-5 w-5" />
                    </button>
                    <button
                      onClick={() => handleRestore([session.id])}
                      disabled={operating}
                      className="p-2 text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-lg disabled:opacity-50"
                      title="Restore"
                    >
                      <ArrowPathIcon className="h-5 w-5" />
                    </button>
                    <button
                      onClick={() => handlePermanentDelete([session.id])}
                      disabled={operating}
                      className="p-2 text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg disabled:opacity-50"
                      title="Delete Forever"
                    >
                      <TrashIcon className="h-5 w-5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Info box */}
        <div className="mt-6 p-4 bg-amber-50 dark:bg-amber-900/20 rounded-lg">
          <h3 className="font-semibold text-amber-800 dark:text-amber-200 mb-2">About Archived Chats</h3>
          <ul className="text-sm text-amber-700 dark:text-amber-300 space-y-1">
            <li>• Archived chats are removed from your main chat list</li>
            <li>• You can restore them at any time to continue the conversation</li>
            <li>• Download exports include full message history</li>
            <li>• Deleting forever is permanent and cannot be undone</li>
          </ul>
        </div>
      </div>
    </div>
  )
}
