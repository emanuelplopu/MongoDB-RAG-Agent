import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useDataSource } from '../api'

export default function ImportPage() {
  const { state, actions } = useDataSource()
  const { t } = useTranslation()
  const [remoteUrl, setRemoteUrl] = useState(localStorage.getItem('telemetry_remote_url') || 'http://localhost:11000')
  const [remoteToken, setRemoteToken] = useState('')
  const [dateRange, setDateRange] = useState('7d')
  const [importDate, setImportDate] = useState(new Date().toISOString().split('T')[0])
  const [importSource, setImportSource] = useState<'protected' | 'raw'>('protected')
  const [dragOver, setDragOver] = useState(false)
  const [dismissedError, setDismissedError] = useState(false)

  const isConnected = state.connectionInfo?.connected ?? false

  const summary = useMemo(() => {
    const records = state.records
    const sessionIds = new Set(records.map(r => (r as any).session_id || (r as any).sessionId).filter(Boolean))
    let minTs: string | null = null
    let maxTs: string | null = null
    for (const r of records) {
      const ts = (r as any).timestamp || (r as any).created_at
      if (ts) {
        if (!minTs || ts < minTs) minTs = ts
        if (!maxTs || ts > maxTs) maxTs = ts
      }
    }
    return { count: records.length, sessions: sessionIds.size, minTs, maxTs }
  }, [state.records])

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    const files = Array.from(e.dataTransfer.files)
    for (const file of files) {
      if ((file as any).path) {
        await actions.loadFromPath((file as any).path)
      }
    }
  }

  const handleConnect = async () => {
    const success = await actions.connect(remoteUrl, remoteToken)
    if (success) {
      localStorage.setItem('telemetry_remote_url', remoteUrl)
    }
  }

  const handleImportDay = () => {
    actions.fetchByDate(importDate, importSource)
  }

  const handleImportRange = () => {
    actions.fetchAll(dateRange)
  }

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6 relative">
      {/* Loading overlay */}
      {state.loading && (
        <div className="absolute inset-0 bg-black/40 z-50 flex items-center justify-center rounded-xl">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
            <span className="text-gray-300 text-sm">{t('import.loading')}</span>
          </div>
        </div>
      )}

      {/* Error alert */}
      {state.error && !dismissedError && (
        <div className="flex items-center justify-between bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3">
          <span className="text-accent-red text-sm">{state.error}</span>
          <button
            onClick={() => setDismissedError(true)}
            className="text-accent-red hover:text-red-300 ml-4 text-lg leading-none"
          >
            ×
          </button>
        </div>
      )}

      {/* Section 1: Local File Import */}
      <div className="bg-gray-50 dark:bg-surface-light rounded-xl border border-gray-200 dark:border-gray-700/50 p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-4">
          <svg className="w-5 h-5 text-accent-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
          {t('import.localFiles')}
        </h2>

        <div className="flex gap-3 mb-4">
          <button
            onClick={() => actions.openFile()}
            disabled={state.loading}
            className="px-4 py-2 rounded-lg font-medium transition-colors bg-accent-blue text-white hover:bg-accent-blue/80 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {t('import.openFile')}
          </button>
          <button
            onClick={() => actions.openDirectory()}
            disabled={state.loading}
            className="px-4 py-2 rounded-lg font-medium transition-colors bg-gray-200 dark:bg-surface-lighter text-gray-800 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {t('import.openDirectory')}
          </button>
        </div>

        <div
          onDragOver={handleDragOver}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
            dragOver
              ? 'border-accent-blue bg-accent-blue/5'
              : 'border-gray-300 dark:border-gray-600'
          }`}
        >
          <svg className="w-10 h-10 mx-auto mb-3 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <p className="text-gray-500 dark:text-gray-400">{t('import.dropZone')}</p>
        </div>
      </div>

      {/* Section 2: Remote API Import */}
      <div className="bg-gray-50 dark:bg-surface-light rounded-xl border border-gray-200 dark:border-gray-700/50 p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-4">
          <svg className="w-5 h-5 text-accent-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
          </svg>
          {t('import.remoteApi')}
        </h2>

        {/* Connection fields */}
        <div className="space-y-3 mb-4">
          <div className="flex gap-3">
            <input
              type="text"
              value={remoteUrl}
              onChange={e => setRemoteUrl(e.target.value)}
              placeholder="http://localhost:11000"
              className="flex-1 bg-white dark:bg-surface border border-gray-200 dark:border-gray-600 rounded-lg px-4 py-2 text-gray-800 dark:text-gray-200 focus:border-accent-blue focus:outline-none"
            />
            <input
              type="password"
              value={remoteToken}
              onChange={e => setRemoteToken(e.target.value)}
              placeholder={t('import.remote.token')}
              className="w-48 bg-white dark:bg-surface border border-gray-200 dark:border-gray-600 rounded-lg px-4 py-2 text-gray-800 dark:text-gray-200 focus:border-accent-blue focus:outline-none"
            />
            <button
              onClick={handleConnect}
              disabled={state.loading}
              className="px-4 py-2 rounded-lg font-medium transition-colors bg-accent-blue text-white hover:bg-accent-blue/80 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('import.remote.connect')}
            </button>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className={`w-2.5 h-2.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            <span className="text-gray-500 dark:text-gray-400">{isConnected ? t('import.remote.connected') : t('import.remote.disconnected')}</span>
          </div>
        </div>

        {/* Import controls */}
        <div className="border-t border-gray-200 dark:border-gray-700/50 pt-4 space-y-4">
          {/* Day import */}
          <div className="flex items-center gap-3 flex-wrap">
            <input
              type="date"
              value={importDate}
              onChange={e => setImportDate(e.target.value)}
              disabled={!isConnected || state.loading}
              className="bg-white dark:bg-surface border border-gray-200 dark:border-gray-600 rounded-lg px-4 py-2 text-gray-800 dark:text-gray-200 focus:border-accent-blue focus:outline-none disabled:opacity-50"
            />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                <input
                  type="radio"
                  name="importSource"
                  value="protected"
                  checked={importSource === 'protected'}
                  onChange={() => setImportSource('protected')}
                  disabled={!isConnected || state.loading}
                  className="accent-accent-blue"
                />
                Protected
              </label>
              <label className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                <input
                  type="radio"
                  name="importSource"
                  value="raw"
                  checked={importSource === 'raw'}
                  onChange={() => setImportSource('raw')}
                  disabled={!isConnected || state.loading}
                  className="accent-accent-blue"
                />
                Raw
              </label>
            </div>
            <button
              onClick={handleImportDay}
              disabled={!isConnected || state.loading}
              className="px-4 py-2 rounded-lg font-medium transition-colors bg-accent-blue text-white hover:bg-accent-blue/80 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('import.remote.importDay')}
            </button>
          </div>

          {/* Range import */}
          <div className="flex items-center gap-3">
            <select
              value={dateRange}
              onChange={e => setDateRange(e.target.value)}
              disabled={!isConnected || state.loading}
              className="bg-white dark:bg-surface border border-gray-200 dark:border-gray-600 rounded-lg px-4 py-2 text-gray-800 dark:text-gray-200 focus:border-accent-blue focus:outline-none disabled:opacity-50"
            >
              <option value="7d">{t('import.remote.last7d')}</option>
              <option value="30d">{t('import.remote.last30d')}</option>
              <option value="all">{t('import.remote.all')}</option>
            </select>
            <button
              onClick={handleImportRange}
              disabled={!isConnected || state.loading}
              className="px-4 py-2 rounded-lg font-medium transition-colors bg-gray-200 dark:bg-surface-lighter text-gray-800 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('import.remote.importRange')}
            </button>
          </div>
        </div>
      </div>

      {/* Section 3: Imported Data Summary */}
      <div className="bg-gray-50 dark:bg-surface-light rounded-xl border border-gray-200 dark:border-gray-700/50 p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-4">
          <svg className="w-5 h-5 text-accent-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          {t('import.summary.title')}
        </h2>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <div className="bg-white dark:bg-surface rounded-lg p-3">
            <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('import.summary.records')}</p>
            <p className="text-xl font-semibold text-gray-900 dark:text-gray-100">{summary.count.toLocaleString()}</p>
          </div>
          <div className="bg-white dark:bg-surface rounded-lg p-3">
            <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('import.summary.sessions')}</p>
            <p className="text-xl font-semibold text-gray-900 dark:text-gray-100">{summary.sessions.toLocaleString()}</p>
          </div>
          <div className="bg-white dark:bg-surface rounded-lg p-3">
            <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('import.summary.from')}</p>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{summary.minTs ? new Date(summary.minTs).toLocaleDateString() : '—'}</p>
          </div>
          <div className="bg-white dark:bg-surface rounded-lg p-3">
            <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('import.summary.to')}</p>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{summary.maxTs ? new Date(summary.maxTs).toLocaleDateString() : '—'}</p>
          </div>
        </div>

        {/* Files loaded */}
        {state.filesLoaded.length > 0 && (
          <div className="mb-4">
            <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">{t('import.summary.filesLoaded')} ({state.filesLoaded.length})</p>
            <div className="bg-white dark:bg-surface rounded-lg p-3 max-h-32 overflow-y-auto space-y-1">
              {state.filesLoaded.map((f, i) => (
                <p key={i} className="text-sm text-gray-700 dark:text-gray-300 font-mono truncate">{f}</p>
              ))}
            </div>
          </div>
        )}

        <button
          onClick={() => actions.clearRecords()}
          disabled={state.loading || summary.count === 0}
          className="px-4 py-2 rounded-lg font-medium transition-colors bg-accent-red/20 text-accent-red hover:bg-accent-red/30 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {t('import.clearAll')}
        </button>
      </div>
    </div>
  )
}
