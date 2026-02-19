import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { 
  promptsApi, 
  PromptTemplate, 
  ToolSchema, 
  PromptTestResult,
  PromptComparison 
} from '../api/client'
import CopyButton, { CopyIconButton } from '../components/CopyButton'
import { useUnsavedChangesWarning } from '../hooks/useLocalStorage'
import { useEscapeKey, useFocusTrap } from '../hooks/useKeyboardShortcuts'

// ============== Version Comparison Modal ==============
interface CompareModalProps {
  template: PromptTemplate
  onClose: () => void
}

const CompareModal: React.FC<CompareModalProps> = ({ template, onClose }) => {
  const { t } = useTranslation()
  const [versionA, setVersionA] = useState<number>(1)
  const [versionB, setVersionB] = useState<number>(template.active_version)
  const [comparison, setComparison] = useState<PromptComparison | null>(null)
  const [loading, setLoading] = useState(false)
  const modalRef = useRef<HTMLDivElement>(null)
  
  // Close on Escape key
  useEscapeKey(onClose)
  
  // Trap focus within modal
  useFocusTrap(modalRef)

  const handleCompare = async () => {
    if (!template.id || versionA === versionB) return
    setLoading(true)
    try {
      const result = await promptsApi.compare({
        template_id: template.id,
        version_a: versionA,
        version_b: versionB
      })
      setComparison(result)
    } catch (err) {
      console.error('Compare failed:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div ref={modalRef} className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden">
        <div className="p-6 border-b dark:border-gray-700">
          <div className="flex justify-between items-center">
            <h3 className="text-xl font-semibold dark:text-white">{t('prompts.compareVersions')}</h3>
            <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        
        <div className="p-6 space-y-4">
          <div className="flex gap-4 items-center">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{t('prompts.versionA')}</label>
              <select 
                value={versionA} 
                onChange={(e) => setVersionA(Number(e.target.value))}
                className="border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
              >
                {template.versions.map(v => (
                  <option key={v.version} value={v.version}>v{v.version}</option>
                ))}
              </select>
            </div>
            <span className="text-gray-500 mt-6">vs</span>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{t('prompts.versionB')}</label>
              <select 
                value={versionB} 
                onChange={(e) => setVersionB(Number(e.target.value))}
                className="border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
              >
                {template.versions.map(v => (
                  <option key={v.version} value={v.version}>v{v.version}</option>
                ))}
              </select>
            </div>
            <button
              onClick={handleCompare}
              disabled={loading || versionA === versionB}
              className="mt-6 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? t('prompts.comparing') : t('prompts.compare')}
            </button>
          </div>

          {comparison && (
            <div className="space-y-4 overflow-auto max-h-[60vh]">
              <div>
                <h4 className="font-medium text-gray-900 dark:text-white mb-2">{t('prompts.toolChanges')}</h4>
                <div className="flex gap-4 text-sm">
                  {comparison.tools_added.length > 0 && (
                    <span className="text-green-600">+ {t('prompts.added')}: {comparison.tools_added.join(', ')}</span>
                  )}
                  {comparison.tools_removed.length > 0 && (
                    <span className="text-red-600">- {t('prompts.removed')}: {comparison.tools_removed.join(', ')}</span>
                  )}
                  {comparison.tools_modified.length > 0 && (
                    <span className="text-yellow-600">~ {t('prompts.modified')}: {comparison.tools_modified.join(', ')}</span>
                  )}
                  {comparison.tools_added.length === 0 && comparison.tools_removed.length === 0 && comparison.tools_modified.length === 0 && (
                    <span className="text-gray-500">{t('prompts.noToolChanges')}</span>
                  )}
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="font-medium text-gray-900 dark:text-white">{t('prompts.promptDiff')}</h4>
                  <CopyButton 
                    text={comparison.prompt_diff || ''} 
                    size="xs" 
                    successMessage={t('prompts.diffCopied')}
                  />
                </div>
                <pre className="bg-gray-100 dark:bg-gray-900 p-4 rounded text-sm overflow-x-auto font-mono whitespace-pre-wrap">
                  {comparison.prompt_diff || t('prompts.noChanges')}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============== Test Modal ==============
interface TestModalProps {
  template: PromptTemplate
  onClose: () => void
}

const TestModal: React.FC<TestModalProps> = ({ template, onClose }) => {
  const { t } = useTranslation()
  const [testMessage, setTestMessage] = useState('')
  const [selectedVersion, setSelectedVersion] = useState<number | undefined>(undefined)
  const [result, setResult] = useState<PromptTestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const modalRef = useRef<HTMLDivElement>(null)
  
  // Close on Escape key
  useEscapeKey(onClose)
  
  // Trap focus within modal
  useFocusTrap(modalRef)

  const handleTest = async () => {
    if (!template.id || !testMessage.trim()) return
    setLoading(true)
    setResult(null)
    try {
      const res = await promptsApi.test({
        template_id: template.id,
        version: selectedVersion,
        test_message: testMessage
      })
      setResult(res)
    } catch (err) {
      console.error('Test failed:', err)
      setResult({
        success: false,
        response: '',
        tool_calls: [],
        tokens_used: 0,
        duration_ms: 0,
        error: err instanceof Error ? err.message : 'Unknown error'
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div ref={modalRef} className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-3xl w-full max-h-[90vh] overflow-hidden">
        <div className="p-6 border-b dark:border-gray-700">
          <div className="flex justify-between items-center">
            <h3 className="text-xl font-semibold dark:text-white">{t('prompts.testPrompt')}</h3>
            <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        
        <div className="p-6 space-y-4 overflow-auto max-h-[calc(90vh-100px)]">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{t('prompts.version')}</label>
            <select 
              value={selectedVersion ?? template.active_version}
              onChange={(e) => setSelectedVersion(Number(e.target.value))}
              className="border rounded px-3 py-2 w-full dark:bg-gray-700 dark:border-gray-600 dark:text-white"
            >
              {template.versions.map(v => (
                <option key={v.version} value={v.version}>
                  v{v.version} {v.is_active ? `(${t('prompts.activeVersion')})` : ''}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{t('prompts.testMessage')}</label>
            <textarea
              value={testMessage}
              onChange={(e) => setTestMessage(e.target.value)}
              rows={4}
              className="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
              placeholder={t('prompts.testMessagePlaceholder')}
            />
          </div>

          <button
            onClick={handleTest}
            disabled={loading || !testMessage.trim()}
            className="w-full px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
          >
            {loading ? t('prompts.testing') : t('prompts.runTest')}
          </button>

          {result && (
            <div className="space-y-3 mt-4">
              <div className={`p-3 rounded ${result.success ? 'bg-green-100 dark:bg-green-900' : 'bg-red-100 dark:bg-red-900'}`}>
                <span className="font-medium">{result.success ? `✓ ${t('prompts.testSuccess')}` : `✗ ${t('prompts.testFailed')}`}</span>
                {result.error && <span className="ml-2 text-red-600">{result.error}</span>}
              </div>

              {result.tool_calls.length > 0 && (
                <div>
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">{t('prompts.toolCalls')}</h4>
                  <div className="space-y-2">
                    {result.tool_calls.map((tc, i) => (
                      <div key={i} className="bg-blue-50 dark:bg-blue-900 p-3 rounded">
                        <div className="flex items-center justify-between">
                          <span className="font-mono font-medium">{tc.name}</span>
                          <CopyIconButton text={tc.arguments} size="xs" />
                        </div>
                        <pre className="text-sm mt-1 text-gray-600 dark:text-gray-300">{tc.arguments}</pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.response && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="font-medium text-gray-900 dark:text-white">{t('prompts.response')}</h4>
                    <CopyButton 
                      text={result.response} 
                      size="xs" 
                      successMessage={t('prompts.responseCopied')}
                    />
                  </div>
                  <div className="bg-gray-100 dark:bg-gray-900 p-3 rounded">
                    <p className="text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{result.response}</p>
                  </div>
                </div>
              )}

              <div className="text-sm text-gray-500 flex gap-4">
                <span>{t('prompts.tokens')}: {result.tokens_used}</span>
                <span>{t('prompts.duration')}: {result.duration_ms.toFixed(0)}ms</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============== Editor Panel ==============
interface EditorPanelProps {
  template: PromptTemplate | null
  onSave: (data: { system_prompt: string; tools: ToolSchema[]; notes: string }) => Promise<void>
  onActivate: (version: number) => Promise<void>
  onRefresh: () => void
}

const EditorPanel: React.FC<EditorPanelProps> = ({ template, onSave, onActivate, onRefresh }) => {
  const { t } = useTranslation()
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState<ToolSchema[]>([])
  const [notes, setNotes] = useState('')
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [showCompare, setShowCompare] = useState(false)
  const [showTest, setShowTest] = useState(false)
  const [originalPrompt, setOriginalPrompt] = useState('')
  const [originalTools, setOriginalTools] = useState<ToolSchema[]>([])
  
  // Track if form has unsaved changes
  const hasUnsavedChanges = systemPrompt !== originalPrompt || 
    JSON.stringify(tools) !== JSON.stringify(originalTools)
  
  // Warn user before leaving with unsaved changes
  useUnsavedChangesWarning(hasUnsavedChanges, 'You have unsaved prompt changes. Are you sure you want to leave?')

  useEffect(() => {
    if (template) {
      const activeVersion = template.versions.find(v => v.version === template.active_version)
      if (activeVersion) {
        setSystemPrompt(activeVersion.system_prompt)
        setTools(activeVersion.tools)
        setSelectedVersion(activeVersion.version)
        setOriginalPrompt(activeVersion.system_prompt)
        setOriginalTools(activeVersion.tools)
      }
    }
  }, [template])

  const handleVersionChange = (version: number) => {
    const v = template?.versions.find(ver => ver.version === version)
    if (v) {
      setSystemPrompt(v.system_prompt)
      setTools(v.tools)
      setSelectedVersion(version)
      setOriginalPrompt(v.system_prompt)
      setOriginalTools(v.tools)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({ system_prompt: systemPrompt, tools, notes })
      setNotes('')
      // Reset dirty tracking after successful save
      setOriginalPrompt(systemPrompt)
      setOriginalTools(tools)
      onRefresh()
    } finally {
      setSaving(false)
    }
  }

  const handleActivate = async () => {
    if (selectedVersion) {
      await onActivate(selectedVersion)
      onRefresh()
    }
  }

  if (!template) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500 dark:text-gray-400">
        {t('prompts.selectTemplate')}
      </div>
    )
  }

  const isActiveVersion = selectedVersion === template.active_version

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="p-4 border-b dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-800">
        <div>
          <h2 className="text-xl font-semibold dark:text-white">{template.name}</h2>
          <p className="text-sm text-gray-500">{template.description}</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedVersion ?? ''}
            onChange={(e) => handleVersionChange(Number(e.target.value))}
            className="border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          >
            {template.versions.map(v => (
              <option key={v.version} value={v.version}>
                v{v.version} {v.is_active ? `(${t('prompts.activeVersion')})` : ''}
              </option>
            ))}
          </select>
          <button
            onClick={() => setShowCompare(true)}
            className="px-3 py-2 border rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:border-gray-600 dark:text-white"
          >
            {t('prompts.compare')}
          </button>
          <button
            onClick={() => setShowTest(true)}
            className="px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700"
          >
            {t('common.test')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4">
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('prompts.systemPrompt')}
            </label>
            <CopyButton 
              text={systemPrompt} 
              size="xs" 
              successMessage={t('prompts.systemPromptCopied')}
              label={t('prompts.copySystemPrompt')}
            />
          </div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={15}
            className="w-full border rounded px-3 py-2 font-mono text-sm dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('prompts.tools')} ({tools.length})
            </label>
          </div>
          <div className="space-y-2">
            {tools.map((tool, idx) => (
              <div key={idx} className="border rounded p-3 dark:border-gray-600">
                <div className="flex items-center justify-between">
                  <span className="font-mono font-medium dark:text-white">{tool.name}</span>
                  <div className="flex items-center gap-2">
                    <CopyIconButton 
                      text={JSON.stringify(tool, null, 2)} 
                      size="xs"
                    />
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={tool.enabled}
                        onChange={(e) => {
                          const newTools = [...tools]
                          newTools[idx] = { ...tool, enabled: e.target.checked }
                          setTools(newTools)
                        }}
                        className="rounded"
                      />
                      <span className="dark:text-gray-300">{t('common.enabled')}</span>
                    </label>
                  </div>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">{tool.description}</p>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('prompts.versionNotes')}
          </label>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
            placeholder={t('prompts.describeChanges')}
          />
        </div>
      </div>

      <div className="p-4 border-t dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-800">
        <div className="text-sm text-gray-500 flex items-center gap-3">
          {isActiveVersion ? (
            <span className="text-green-600 font-medium">✓ {t('prompts.thisIsActiveVersion')}</span>
          ) : (
            <button
              onClick={handleActivate}
              className="text-blue-600 hover:underline"
            >
              {t('prompts.activateVersion', { version: selectedVersion })}
            </button>
          )}
          {hasUnsavedChanges && (
            <span className="text-amber-600 dark:text-amber-400 flex items-center gap-1">
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
              {t('prompts.unsavedChanges')}
            </span>
          )}
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className={`px-6 py-2 text-white rounded transition-colors disabled:opacity-50 ${
            hasUnsavedChanges 
              ? 'bg-amber-600 hover:bg-amber-700' 
              : 'bg-blue-600 hover:bg-blue-700'
          }`}
        >
          {saving ? t('prompts.saving') : hasUnsavedChanges ? t('prompts.saveChanges') : t('prompts.saveAsNewVersion')}
        </button>
      </div>

      {showCompare && <CompareModal template={template} onClose={() => setShowCompare(false)} />}
      {showTest && <TestModal template={template} onClose={() => setShowTest(false)} />}
    </div>
  )
}

// ============== Main Page ==============
const PromptManagementPage: React.FC = () => {
  const { t } = useTranslation()
  const [templates, setTemplates] = useState<PromptTemplate[]>([])
  const [selectedTemplate, setSelectedTemplate] = useState<PromptTemplate | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadTemplates = useCallback(async () => {
    try {
      const { templates: data } = await promptsApi.list()
      setTemplates(data)
      if (data.length > 0 && !selectedTemplate) {
        setSelectedTemplate(data[0])
      } else if (selectedTemplate) {
        const updated = data.find(t => t.id === selectedTemplate.id)
        if (updated) setSelectedTemplate(updated)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('prompts.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [selectedTemplate, t])

  useEffect(() => {
    loadTemplates()
  }, [])

  const handleSave = async (data: { system_prompt: string; tools: ToolSchema[]; notes: string }) => {
    if (!selectedTemplate?.id) return
    await promptsApi.createVersion(selectedTemplate.id, data)
    await loadTemplates()
  }

  const handleActivate = async (version: number) => {
    if (!selectedTemplate?.id) return
    await promptsApi.activateVersion(selectedTemplate.id, version)
    await loadTemplates()
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-red-600">{error}</div>
      </div>
    )
  }

  return (
    <div className="h-full flex">
      {/* Sidebar */}
      <div className="w-64 border-r dark:border-gray-700 bg-gray-50 dark:bg-gray-900 flex flex-col">
        <div className="p-4 border-b dark:border-gray-700">
          <h1 className="text-lg font-semibold dark:text-white">{t('prompts.promptTemplates')}</h1>
        </div>
        <div className="flex-1 overflow-auto">
          {templates.map(template => (
            <button
              key={template.id}
              onClick={() => setSelectedTemplate(template)}
              className={`w-full text-left p-4 border-b dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 ${
                selectedTemplate?.id === template.id ? 'bg-blue-50 dark:bg-blue-900' : ''
              }`}
            >
              <div className="font-medium dark:text-white">{template.name}</div>
              <div className="text-sm text-gray-500 dark:text-gray-400">
                {template.versions.length} {t('prompts.versions')} • v{template.active_version} {t('prompts.activeVersion')}
              </div>
              <div className="text-xs text-gray-400 mt-1">{template.category}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Editor */}
      <EditorPanel
        template={selectedTemplate}
        onSave={handleSave}
        onActivate={handleActivate}
        onRefresh={loadTemplates}
      />
    </div>
  )
}

export default PromptManagementPage
