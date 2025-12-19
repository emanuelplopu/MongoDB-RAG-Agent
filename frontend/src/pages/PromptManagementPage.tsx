import React, { useState, useEffect, useCallback } from 'react'
import { 
  promptsApi, 
  PromptTemplate, 
  ToolSchema, 
  PromptTestResult,
  PromptComparison 
} from '../api/client'

// ============== Version Comparison Modal ==============
interface CompareModalProps {
  template: PromptTemplate
  onClose: () => void
}

const CompareModal: React.FC<CompareModalProps> = ({ template, onClose }) => {
  const [versionA, setVersionA] = useState<number>(1)
  const [versionB, setVersionB] = useState<number>(template.active_version)
  const [comparison, setComparison] = useState<PromptComparison | null>(null)
  const [loading, setLoading] = useState(false)

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
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden">
        <div className="p-6 border-b dark:border-gray-700">
          <div className="flex justify-between items-center">
            <h3 className="text-xl font-semibold dark:text-white">Compare Versions</h3>
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
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Version A</label>
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
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Version B</label>
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
              {loading ? 'Comparing...' : 'Compare'}
            </button>
          </div>

          {comparison && (
            <div className="space-y-4 overflow-auto max-h-[60vh]">
              <div>
                <h4 className="font-medium text-gray-900 dark:text-white mb-2">Tool Changes</h4>
                <div className="flex gap-4 text-sm">
                  {comparison.tools_added.length > 0 && (
                    <span className="text-green-600">+ Added: {comparison.tools_added.join(', ')}</span>
                  )}
                  {comparison.tools_removed.length > 0 && (
                    <span className="text-red-600">- Removed: {comparison.tools_removed.join(', ')}</span>
                  )}
                  {comparison.tools_modified.length > 0 && (
                    <span className="text-yellow-600">~ Modified: {comparison.tools_modified.join(', ')}</span>
                  )}
                  {comparison.tools_added.length === 0 && comparison.tools_removed.length === 0 && comparison.tools_modified.length === 0 && (
                    <span className="text-gray-500">No tool changes</span>
                  )}
                </div>
              </div>

              <div>
                <h4 className="font-medium text-gray-900 dark:text-white mb-2">Prompt Diff</h4>
                <pre className="bg-gray-100 dark:bg-gray-900 p-4 rounded text-sm overflow-x-auto font-mono whitespace-pre-wrap">
                  {comparison.prompt_diff || 'No changes'}
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
  const [testMessage, setTestMessage] = useState('')
  const [selectedVersion, setSelectedVersion] = useState<number | undefined>(undefined)
  const [result, setResult] = useState<PromptTestResult | null>(null)
  const [loading, setLoading] = useState(false)

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
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-3xl w-full max-h-[90vh] overflow-hidden">
        <div className="p-6 border-b dark:border-gray-700">
          <div className="flex justify-between items-center">
            <h3 className="text-xl font-semibold dark:text-white">Test Prompt</h3>
            <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        
        <div className="p-6 space-y-4 overflow-auto max-h-[calc(90vh-100px)]">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Version</label>
            <select 
              value={selectedVersion ?? template.active_version}
              onChange={(e) => setSelectedVersion(Number(e.target.value))}
              className="border rounded px-3 py-2 w-full dark:bg-gray-700 dark:border-gray-600 dark:text-white"
            >
              {template.versions.map(v => (
                <option key={v.version} value={v.version}>
                  v{v.version} {v.is_active ? '(active)' : ''}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Test Message</label>
            <textarea
              value={testMessage}
              onChange={(e) => setTestMessage(e.target.value)}
              rows={4}
              className="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
              placeholder="Enter a test message to send to the agent..."
            />
          </div>

          <button
            onClick={handleTest}
            disabled={loading || !testMessage.trim()}
            className="w-full px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
          >
            {loading ? 'Testing...' : 'Run Test'}
          </button>

          {result && (
            <div className="space-y-3 mt-4">
              <div className={`p-3 rounded ${result.success ? 'bg-green-100 dark:bg-green-900' : 'bg-red-100 dark:bg-red-900'}`}>
                <span className="font-medium">{result.success ? '✓ Success' : '✗ Failed'}</span>
                {result.error && <span className="ml-2 text-red-600">{result.error}</span>}
              </div>

              {result.tool_calls.length > 0 && (
                <div>
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">Tool Calls</h4>
                  <div className="space-y-2">
                    {result.tool_calls.map((tc, i) => (
                      <div key={i} className="bg-blue-50 dark:bg-blue-900 p-3 rounded">
                        <span className="font-mono font-medium">{tc.name}</span>
                        <pre className="text-sm mt-1 text-gray-600 dark:text-gray-300">{tc.arguments}</pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.response && (
                <div>
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">Response</h4>
                  <div className="bg-gray-100 dark:bg-gray-900 p-3 rounded">
                    <p className="text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{result.response}</p>
                  </div>
                </div>
              )}

              <div className="text-sm text-gray-500 flex gap-4">
                <span>Tokens: {result.tokens_used}</span>
                <span>Duration: {result.duration_ms.toFixed(0)}ms</span>
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
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState<ToolSchema[]>([])
  const [notes, setNotes] = useState('')
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [showCompare, setShowCompare] = useState(false)
  const [showTest, setShowTest] = useState(false)

  useEffect(() => {
    if (template) {
      const activeVersion = template.versions.find(v => v.version === template.active_version)
      if (activeVersion) {
        setSystemPrompt(activeVersion.system_prompt)
        setTools(activeVersion.tools)
        setSelectedVersion(activeVersion.version)
      }
    }
  }, [template])

  const handleVersionChange = (version: number) => {
    const v = template?.versions.find(ver => ver.version === version)
    if (v) {
      setSystemPrompt(v.system_prompt)
      setTools(v.tools)
      setSelectedVersion(version)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({ system_prompt: systemPrompt, tools, notes })
      setNotes('')
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
        Select a template to edit
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
                v{v.version} {v.is_active ? '(active)' : ''}
              </option>
            ))}
          </select>
          <button
            onClick={() => setShowCompare(true)}
            className="px-3 py-2 border rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:border-gray-600 dark:text-white"
          >
            Compare
          </button>
          <button
            onClick={() => setShowTest(true)}
            className="px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700"
          >
            Test
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            System Prompt
          </label>
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
              Tools ({tools.length})
            </label>
          </div>
          <div className="space-y-2">
            {tools.map((tool, idx) => (
              <div key={idx} className="border rounded p-3 dark:border-gray-600">
                <div className="flex items-center justify-between">
                  <span className="font-mono font-medium dark:text-white">{tool.name}</span>
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
                    <span className="dark:text-gray-300">Enabled</span>
                  </label>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">{tool.description}</p>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Version Notes (for new version)
          </label>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
            placeholder="Describe your changes..."
          />
        </div>
      </div>

      <div className="p-4 border-t dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-800">
        <div className="text-sm text-gray-500">
          {isActiveVersion ? (
            <span className="text-green-600 font-medium">✓ This is the active version</span>
          ) : (
            <button
              onClick={handleActivate}
              className="text-blue-600 hover:underline"
            >
              Activate v{selectedVersion}
            </button>
          )}
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save as New Version'}
        </button>
      </div>

      {showCompare && <CompareModal template={template} onClose={() => setShowCompare(false)} />}
      {showTest && <TestModal template={template} onClose={() => setShowTest(false)} />}
    </div>
  )
}

// ============== Main Page ==============
const PromptManagementPage: React.FC = () => {
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
      setError(err instanceof Error ? err.message : 'Failed to load templates')
    } finally {
      setLoading(false)
    }
  }, [selectedTemplate])

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
          <h1 className="text-lg font-semibold dark:text-white">Prompt Templates</h1>
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
                {template.versions.length} versions • v{template.active_version} active
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
