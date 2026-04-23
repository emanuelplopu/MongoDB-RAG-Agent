import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { sessionsApi, ChatSession } from '../api/client'
import { ArchiveBoxIcon, ArrowPathIcon, TrashIcon, ArrowDownTrayIcon } from '@heroicons/react/24/outline'

export default function ArchivedChatsPage() {
  const { t } = useTranslation()
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
      setError(t('archivedChatsPage.loadFailed'))
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
      setError(t('archivedChatsPage.restoreFailed'))
      console.error(err)
    } finally {
      setOperating(false)
    }
  }

  const handlePermanentDelete = async (ids: string[]) => {
    if (ids.length === 0) return
    if (!confirm(t('archivedChatsPage.confirmDelete', { count: ids.length }))) {
      return
    }
    try {
      setOperating(true)
      await sessionsApi.deletePermanently(ids)
      setSessions(prev => prev.filter(s => !ids.includes(s.id)))
      setSelectedIds(new Set())
    } catch (err) {
      setError(t('archivedChatsPage.deleteFailed'))
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
      setError(t('archivedChatsPage.exportFailed'))
      console.error(err)
    }
  }

  const formatDate = (dateStr: string | undefined) => {
    if (!dateStr) return t('archivedChatsPage.dateUnknown')
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
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{t('archivedChatsPage.title')}</h1>
              <p className="text-gray-600 dark:text-gray-400">
                {t('archivedChatsPage.subtitle')}
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
                  {t('archivedChatsPage.selectAll')}
                </button>
                {selectedIds.size > 0 && (
                  <button
                    onClick={clearSelection}
                    className="px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
                  >
                    {t('archivedChatsPage.clearCount', { count: selectedIds.size })}
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
                {t('archivedChatsPage.restoreCount', { count: selectedIds.size })}
              </button>
              <button
                onClick={() => handlePermanentDelete(Array.from(selectedIds))}
                disabled={operating}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 flex items-center gap-2"
              >
                <TrashIcon className="h-4 w-4" />
                {t('archivedChatsPage.deleteForeverCount', { count: selectedIds.size })}
              </button>
            </div>
          )}
        </div>

        {/* Sessions list */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="py-12 text-center text-gray-500 dark:text-gray-400">
              <ArchiveBoxIcon className="h-12 w-12 mx-auto mb-4 text-gray-300 dark:text-gray-600" />
              <p>{t('archivedChatsPage.noChats')}</p>
              <p className="text-sm mt-1">{t('archivedChatsPage.noChatsDesc')}</p>
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
                    className="h-5 w-5 rounded border-gray-300 text-primary focus:ring-primary"
                  />
                  
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-medium text-gray-900 dark:text-white truncate">
                      {session.title || t('archivedChatsPage.untitledChat')}
                    </h3>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 flex items-center gap-4">
                      <span>{t('archivedChatsPage.archivedAt', { date: formatDate(session.archived_at) })}</span>
                      <span>{t('archivedChatsPage.createdAt', { date: formatDate(session.created_at) })}</span>
                      {session.stats && (
                        <span>{t('archivedChatsPage.messagesCount', { count: session.stats.total_messages || 0 })}</span>
                      )}
                    </div>
                  </div>
                  
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleExport(session.id)}
                      className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                      title={t('archivedChatsPage.download')}
                    >
                      <ArrowDownTrayIcon className="h-5 w-5" />
                    </button>
                    <button
                      onClick={() => handleRestore([session.id])}
                      disabled={operating}
                      className="p-2 text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-lg disabled:opacity-50"
                      title={t('archivedChatsPage.restore')}
                    >
                      <ArrowPathIcon className="h-5 w-5" />
                    </button>
                    <button
                      onClick={() => handlePermanentDelete([session.id])}
                      disabled={operating}
                      className="p-2 text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg disabled:opacity-50"
                      title={t('archivedChatsPage.deleteForever')}
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
          <h3 className="font-semibold text-amber-800 dark:text-amber-200 mb-2">{t('archivedChatsPage.aboutTitle')}</h3>
          <ul className="text-sm text-amber-700 dark:text-amber-300 space-y-1">
            <li>• {t('archivedChatsPage.aboutBullet1')}</li>
            <li>• {t('archivedChatsPage.aboutBullet2')}</li>
            <li>• {t('archivedChatsPage.aboutBullet3')}</li>
            <li>• {t('archivedChatsPage.aboutBullet4')}</li>
          </ul>
        </div>
      </div>
    </div>
  )
}
