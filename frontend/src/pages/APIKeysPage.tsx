import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { apiKeysApi, APIKeyResponse, APIKeyCreatedResponse } from '../api/client'

export default function APIKeysPage() {
  const { t } = useTranslation()
  const [keys, setKeys] = useState<APIKeyResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  // Create key modal state
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeyExpiry, setNewKeyExpiry] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  
  // Created key display
  const [createdKey, setCreatedKey] = useState<APIKeyCreatedResponse | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    loadKeys()
  }, [])

  const loadKeys = async () => {
    try {
      setLoading(true)
      const data = await apiKeysApi.list()
      setKeys(data)
      setError(null)
    } catch (err) {
      setError(t('apiKeys.loadFailed'))
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const handleCreateKey = async () => {
    if (!newKeyName.trim()) return
    
    try {
      setCreating(true)
      const response = await apiKeysApi.create({
        name: newKeyName.trim(),
        expires_in_days: newKeyExpiry || undefined
      })
      setCreatedKey(response)
      setShowCreateModal(false)
      setNewKeyName('')
      setNewKeyExpiry(null)
      loadKeys()
    } catch (err) {
      setError(t('apiKeys.createFailed'))
      console.error(err)
    } finally {
      setCreating(false)
    }
  }

  const handleRevokeKey = async (keyId: string, keyName: string) => {
    if (!confirm(t('apiKeys.confirmRevokeDesc', { name: keyName }))) {
      return
    }
    
    try {
      await apiKeysApi.revoke(keyId)
      loadKeys()
    } catch (err) {
      setError(t('apiKeys.revokeFailed'))
      console.error(err)
    }
  }

  const handleToggleKey = async (keyId: string) => {
    try {
      await apiKeysApi.toggle(keyId)
      loadKeys()
    } catch (err) {
      setError(t('apiKeys.toggleFailed'))
      console.error(err)
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return t('apiKeys.never')
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
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{t('apiKeys.title')}</h1>
          <p className="text-gray-600 dark:text-gray-400 mt-1">
            {t('apiKeys.subtitle')}
          </p>
        </div>

        {/* Error display */}
        {error && (
          <div className="mb-4 p-4 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg">
            {error}
            <button onClick={() => setError(null)} className="float-right text-red-500 hover:text-red-700">×</button>
          </div>
        )}

        {/* Created Key Display */}
        {createdKey && (
          <div className="mb-6 p-6 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg">
            <div className="flex items-center gap-2 mb-3">
              <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h3 className="font-semibold text-green-800 dark:text-green-200">{t('apiKeys.apiKeyCreated')}</h3>
            </div>
            <p className="text-sm text-green-700 dark:text-green-300 mb-3">
              <strong>{t('apiKeys.keyWarning')}</strong>
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-white dark:bg-gray-800 px-4 py-2 rounded font-mono text-sm border border-green-300 dark:border-green-700">
                {createdKey.key}
              </code>
              <button
                onClick={() => copyToClipboard(createdKey.key)}
                className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition-colors"
              >
                {copied ? t('apiKeys.keyCopied') : t('apiKeys.copyKey')}
              </button>
            </div>
            <button
              onClick={() => setCreatedKey(null)}
              className="mt-3 text-sm text-green-600 dark:text-green-400 hover:underline"
            >
              {t('common.dismiss')}
            </button>
          </div>
        )}

        {/* Actions */}
        <div className="mb-6 flex justify-between items-center">
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            {t('apiKeys.create')}
          </button>
          <button
            onClick={loadKeys}
            className="px-4 py-2 text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
          >
            {t('common.refresh')}
          </button>
        </div>

        {/* Usage Instructions */}
        <div className="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
          <h3 className="font-semibold text-blue-800 dark:text-blue-200 mb-2">{t('apiKeys.howToUse')}</h3>
          <p className="text-sm text-blue-700 dark:text-blue-300 mb-2">
            {t('apiKeys.howToUseDesc')}
          </p>
          <pre className="bg-gray-800 text-gray-100 p-3 rounded text-xs overflow-x-auto">
{`curl -X POST "${window.location.origin}/api/v1/search/hybrid" \\
  -H "X-API-Key: rag_your_api_key_here" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "search term"}'`}
          </pre>
        </div>

        {/* Keys Table */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  {t('apiKeys.name')}
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  {t('apiKeys.key')}
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  {t('apiKeys.createdAt')}
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  {t('apiKeys.lastUsed')}
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  {t('apiKeys.status')}
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  {t('common.actions')}
                </th>
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-indigo-600 mx-auto mb-2"></div>
                    {t('common.loading')}
                  </td>
                </tr>
              ) : keys.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                    {t('apiKeys.noKeysHint')}
                  </td>
                </tr>
              ) : (
                keys.map(key => (
                  <tr key={key.id} className={!key.is_active ? 'opacity-60' : ''}>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="text-sm font-medium text-gray-900 dark:text-white">
                        {key.name}
                      </div>
                      {key.expires_at && (
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {t('apiKeys.expiresAt')}: {formatDate(key.expires_at)}
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <code className="text-sm text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded">
                        {key.key_prefix}
                      </code>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {formatDate(key.created_at)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {key.last_used_at ? formatDate(key.last_used_at) : t('apiKeys.never')}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex px-2 py-1 text-xs font-medium rounded-full ${
                        key.is_active
                          ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
                          : 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'
                      }`}>
                        {key.is_active ? t('common.active') : t('common.disabled')}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <button
                        onClick={() => handleToggleKey(key.id)}
                        className="text-indigo-600 dark:text-indigo-400 hover:text-indigo-900 dark:hover:text-indigo-300 mr-3"
                      >
                        {key.is_active ? t('common.disable') : t('common.enable')}
                      </button>
                      <button
                        onClick={() => handleRevokeKey(key.id, key.name)}
                        className="text-red-600 dark:text-red-400 hover:text-red-900 dark:hover:text-red-300"
                      >
                        {t('apiKeys.revoke')}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Create Modal */}
        {showCreateModal && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl p-6 w-full max-w-md">
              <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-4">
                {t('apiKeys.createNew')}
              </h2>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    {t('apiKeys.keyName')} *
                  </label>
                  <input
                    type="text"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    placeholder="e.g., Production API"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                    autoFocus
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    {t('apiKeys.expiration')}
                  </label>
                  <select
                    value={newKeyExpiry || ''}
                    onChange={(e) => setNewKeyExpiry(e.target.value ? parseInt(e.target.value) : null)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  >
                    <option value="">{t('apiKeys.neverExpires')}</option>
                    <option value="7">{t('apiKeys.days7')}</option>
                    <option value="30">{t('apiKeys.days30')}</option>
                    <option value="90">{t('apiKeys.days90')}</option>
                    <option value="180">{t('apiKeys.months6')}</option>
                    <option value="365">{t('apiKeys.year1')}</option>
                  </select>
                </div>
              </div>
              
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={() => {
                    setShowCreateModal(false)
                    setNewKeyName('')
                    setNewKeyExpiry(null)
                  }}
                  className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleCreateKey}
                  disabled={!newKeyName.trim() || creating}
                  className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {creating ? t('common.loading') : t('apiKeys.create')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
