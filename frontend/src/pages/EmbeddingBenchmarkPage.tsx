import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowPathIcon,
  ArrowLeftIcon,
  BeakerIcon,
  PlayIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  CloudIcon,
  ServerIcon,
  GlobeAltIcon,
  DocumentTextIcon,
  ClockIcon,
  CpuChipIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import { useLanguage } from '../contexts/LanguageContext'
import { benchmarkApi, BenchmarkProviderConfig, BenchmarkResult, BenchmarkMetrics, AvailableProviders } from '../api/client'

// Provider type options
const PROVIDER_TYPES = [
  { id: 'openai', name: 'OpenAI', icon: CloudIcon, color: 'text-green-500' },
  { id: 'ollama', name: 'Ollama (Local)', icon: ServerIcon, color: 'text-blue-500' },
  { id: 'vllm', name: 'vLLM / Custom', icon: GlobeAltIcon, color: 'text-purple-500' },
]

// Provider card component
interface ProviderCardProps {
  index: number
  config: BenchmarkProviderConfig | null
  availableProviders: AvailableProviders | null
  onUpdate: (config: BenchmarkProviderConfig | null) => void
  onTest: () => void
  testResult: { success: boolean; latency_ms: number; dimension: number; error?: string } | null
  isTesting: boolean
  disabled: boolean
}

function ProviderCard({
  index,
  config,
  availableProviders,
  onUpdate,
  onTest,
  testResult,
  isTesting,
  disabled,
}: ProviderCardProps) {
  const { t } = useTranslation()
  const [providerType, setProviderType] = useState<string>(config?.provider_type || '')
  const [model, setModel] = useState<string>(config?.model || '')
  const [baseUrl, setBaseUrl] = useState<string>(config?.base_url || '')
  const [apiKey, setApiKey] = useState<string>(config?.api_key || '')

  // Get models for selected provider type
  const getModels = () => {
    if (!availableProviders) return []
    switch (providerType) {
      case 'openai':
        return availableProviders.openai?.models || []
      case 'ollama':
        return availableProviders.ollama?.models || []
      case 'vllm':
        return availableProviders.vllm?.models || []
      default:
        return []
    }
  }

  // Update parent when config changes
  useEffect(() => {
    if (providerType && model) {
      onUpdate({
        provider_type: providerType,
        model,
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
        name: `Provider ${index + 1}`,
      })
    } else {
      onUpdate(null)
    }
  }, [providerType, model, baseUrl, apiKey])

  // Set default URL when provider type changes
  useEffect(() => {
    if (providerType === 'ollama' && availableProviders?.ollama?.url) {
      setBaseUrl(availableProviders.ollama.url)
    } else if (providerType === 'openai') {
      setBaseUrl('')
    }
    setModel('')
  }, [providerType, availableProviders])

  const providerInfo = PROVIDER_TYPES.find((p) => p.id === providerType)
  const Icon = providerInfo?.icon || BeakerIcon
  const models = getModels()

  return (
    <div className={`rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Icon className={`h-5 w-5 ${providerInfo?.color || 'text-gray-500'}`} />
          <span className="font-semibold text-primary-900 dark:text-gray-200">
            {t('benchmark.provider', 'Provider')} {index + 1}
          </span>
        </div>
        {config && (
          <button
            onClick={() => {
              setProviderType('')
              setModel('')
              setBaseUrl('')
              setApiKey('')
            }}
            className="p-1 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 text-secondary"
          >
            <XMarkIcon className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="space-y-4">
        {/* Provider Type */}
        <div>
          <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
            {t('benchmark.providerType', 'Provider Type')}
          </label>
          <select
            value={providerType}
            onChange={(e) => setProviderType(e.target.value)}
            disabled={disabled}
            className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
          >
            <option value="">{t('benchmark.selectProvider', 'Select provider...')}</option>
            {PROVIDER_TYPES.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        {/* Model */}
        {providerType && (
          <div>
            <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
              {t('benchmark.model', 'Model')}
            </label>
            {models.length > 0 ? (
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={disabled}
                className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
              >
                <option value="">{t('benchmark.selectModel', 'Select model...')}</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} ({m.dimension}d)
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={disabled}
                placeholder={t('benchmark.modelPlaceholder', 'Enter model name...')}
                className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary placeholder-gray-400"
              />
            )}
          </div>
        )}

        {/* Base URL (for ollama/vllm) */}
        {providerType && providerType !== 'openai' && (
          <div>
            <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
              {t('benchmark.baseUrl', 'Base URL')}
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              disabled={disabled}
              placeholder="http://localhost:11434"
              className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary placeholder-gray-400"
            />
          </div>
        )}

        {/* API Key (for openai/vllm) */}
        {providerType && providerType !== 'ollama' && (
          <div>
            <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
              {t('benchmark.apiKey', 'API Key')} {providerType === 'openai' ? '' : `(${t('benchmark.optional', 'optional')})`}
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={disabled}
              placeholder={providerType === 'openai' ? 'sk-...' : t('benchmark.apiKeyPlaceholder', 'Leave empty if not required')}
              className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary placeholder-gray-400"
            />
          </div>
        )}

        {/* Test Connection Button */}
        {config && (
          <div className="pt-2">
            <button
              onClick={onTest}
              disabled={isTesting || disabled}
              className="w-full flex items-center justify-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 transition-colors"
            >
              {isTesting ? (
                <>
                  <ArrowPathIcon className="h-4 w-4 animate-spin" />
                  {t('benchmark.testing', 'Testing...')}
                </>
              ) : (
                <>
                  <BeakerIcon className="h-4 w-4" />
                  {t('benchmark.testConnection', 'Test Connection')}
                </>
              )}
            </button>

            {/* Test Result */}
            {testResult && (
              <div
                className={`mt-3 p-3 rounded-xl text-sm ${
                  testResult.success
                    ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'
                    : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'
                }`}
              >
                {testResult.success ? (
                  <div className="flex items-center gap-2">
                    <CheckCircleIcon className="h-4 w-4" />
                    <span>
                      {t('benchmark.testSuccess', 'Connected')} - {testResult.latency_ms.toFixed(0)}ms, {testResult.dimension}d
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <ExclamationTriangleIcon className="h-4 w-4" />
                    <span>{testResult.error || t('benchmark.testFailed', 'Connection failed')}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// Metric row component for comparison table
function MetricRow({
  label,
  values,
  unit,
  format,
  highlight,
}: {
  label: string
  values: (number | string | null | undefined)[]
  unit?: string
  format?: (v: number) => string
  highlight?: 'min' | 'max'
}) {
  const numericValues = values.map((v) => (typeof v === 'number' ? v : null))
  const validValues = numericValues.filter((v) => v !== null) as number[]

  let bestIndex = -1
  if (highlight && validValues.length > 0) {
    const targetValue = highlight === 'min' ? Math.min(...validValues) : Math.max(...validValues)
    bestIndex = numericValues.findIndex((v) => v === targetValue)
  }

  return (
    <tr className="border-b border-gray-100 dark:border-gray-700">
      <td className="py-3 px-4 text-sm text-secondary dark:text-gray-400">{label}</td>
      {values.map((value, idx) => {
        const isBest = idx === bestIndex
        const displayValue =
          value === null || value === undefined
            ? '-'
            : typeof value === 'number'
              ? format
                ? format(value)
                : value.toFixed(2)
              : value

        return (
          <td
            key={idx}
            className={`py-3 px-4 text-sm text-center ${
              isBest ? 'text-green-600 dark:text-green-400 font-semibold' : 'text-primary-900 dark:text-gray-200'
            }`}
          >
            {displayValue}
            {unit && value !== null && value !== undefined && <span className="text-xs ml-1 text-secondary">{unit}</span>}
          </td>
        )
      })}
    </tr>
  )
}

// Result card for a single provider
function ResultCard({ metrics, isWinner }: { metrics: BenchmarkMetrics; isWinner: boolean }) {
  const { t } = useTranslation()

  return (
    <div
      className={`rounded-xl p-4 ${
        isWinner ? 'bg-green-50 dark:bg-green-900/20 ring-2 ring-green-500' : 'bg-surface-variant dark:bg-gray-700'
      }`}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          {metrics.provider_type === 'openai' && <CloudIcon className="h-5 w-5 text-green-500" />}
          {metrics.provider_type === 'ollama' && <ServerIcon className="h-5 w-5 text-blue-500" />}
          {metrics.provider_type === 'vllm' && <GlobeAltIcon className="h-5 w-5 text-purple-500" />}
          <span className="font-semibold text-primary-900 dark:text-gray-200">{metrics.model}</span>
        </div>
        {isWinner && (
          <span className="flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
            <CheckCircleIcon className="h-4 w-4" />
            {t('benchmark.winner', 'Fastest')}
          </span>
        )}
      </div>

      {metrics.success ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
            <p className="text-xl font-bold text-primary-900 dark:text-gray-200">
              {(metrics.embedding_time_ms / 1000).toFixed(2)}s
            </p>
            <p className="text-xs text-secondary dark:text-gray-400">{t('benchmark.embedTime', 'Embed Time')}</p>
          </div>
          <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
            <p className="text-xl font-bold text-primary-900 dark:text-gray-200">{metrics.avg_latency_ms.toFixed(0)}ms</p>
            <p className="text-xs text-secondary dark:text-gray-400">{t('benchmark.avgLatency', 'Avg Latency')}</p>
          </div>
          <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
            <p className="text-xl font-bold text-primary-900 dark:text-gray-200">{metrics.embedding_dimension}</p>
            <p className="text-xs text-secondary dark:text-gray-400">{t('benchmark.dimension', 'Dimensions')}</p>
          </div>
          <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
            <p className="text-xl font-bold text-primary-900 dark:text-gray-200">
              {metrics.cost_estimate_usd ? `$${metrics.cost_estimate_usd.toFixed(4)}` : '-'}
            </p>
            <p className="text-xs text-secondary dark:text-gray-400">{t('benchmark.cost', 'Est. Cost')}</p>
          </div>
        </div>
      ) : (
        <div className="p-4 bg-red-50 dark:bg-red-900/20 rounded-lg">
          <div className="flex items-center gap-2 text-red-600 dark:text-red-400">
            <ExclamationTriangleIcon className="h-5 w-5" />
            <span className="font-medium">{t('benchmark.failed', 'Failed')}</span>
          </div>
          {metrics.error && <p className="text-sm text-red-500 mt-2">{metrics.error}</p>}
        </div>
      )}
    </div>
  )
}

export default function EmbeddingBenchmarkPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { language } = useLanguage()
  const { user, isLoading: authLoading } = useAuth()

  // State
  const [providers, setProviders] = useState<(BenchmarkProviderConfig | null)[]>([null, null, null])
  const [availableProviders, setAvailableProviders] = useState<AvailableProviders | null>(null)
  const [testResults, setTestResults] = useState<(null | { success: boolean; latency_ms: number; dimension: number; error?: string })[]>([
    null,
    null,
    null,
  ])
  const [testingIndex, setTestingIndex] = useState<number | null>(null)

  // File state
  const [file, setFile] = useState<File | null>(null)
  const [fileContent, setFileContent] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Config state
  const [showConfig, setShowConfig] = useState(false)
  const [chunkSize, setChunkSize] = useState(1000)
  const [chunkOverlap, setChunkOverlap] = useState(200)
  const [maxTokens, setMaxTokens] = useState(512)

  // Benchmark state
  const [isRunning, setIsRunning] = useState(false)
  const [result, setResult] = useState<BenchmarkResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // History state
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<BenchmarkResult[]>([])
  const [loadingHistory, setLoadingHistory] = useState(false)

  // Admin check
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate(`/${language}/dashboard`)
    }
  }, [user, authLoading, navigate, language])

  // Load available providers
  const loadProviders = useCallback(async () => {
    try {
      const data = await benchmarkApi.getProviders()
      setAvailableProviders(data)
    } catch (err) {
      console.error('Failed to load providers:', err)
    }
  }, [])

  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      loadProviders()
    }
  }, [authLoading, user, loadProviders])

  // Handle file selection
  const handleFileSelect = async (selectedFile: File) => {
    setFile(selectedFile)
    setResult(null)
    setError(null)

    // Read file as base64
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = (reader.result as string).split(',')[1]
      setFileContent(base64)
    }
    reader.readAsDataURL(selectedFile)
  }

  // Handle drag and drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const droppedFile = e.dataTransfer.files[0]
    if (droppedFile) {
      handleFileSelect(droppedFile)
    }
  }

  // Update provider at index
  const updateProvider = (index: number, config: BenchmarkProviderConfig | null) => {
    const newProviders = [...providers]
    newProviders[index] = config
    setProviders(newProviders)
    // Clear test result when config changes
    const newTestResults = [...testResults]
    newTestResults[index] = null
    setTestResults(newTestResults)
  }

  // Test provider
  const testProvider = async (index: number) => {
    const config = providers[index]
    if (!config) return

    setTestingIndex(index)
    try {
      const result = await benchmarkApi.testProvider(config)
      const newTestResults = [...testResults]
      newTestResults[index] = {
        success: result.success,
        latency_ms: result.latency_ms,
        dimension: result.dimension,
        error: result.error || undefined,
      }
      setTestResults(newTestResults)
    } catch (err) {
      const newTestResults = [...testResults]
      newTestResults[index] = {
        success: false,
        latency_ms: 0,
        dimension: 0,
        error: err instanceof Error ? err.message : 'Test failed',
      }
      setTestResults(newTestResults)
    } finally {
      setTestingIndex(null)
    }
  }

  // Run benchmark
  const runBenchmark = async () => {
    const validProviders = providers.filter((p) => p !== null) as BenchmarkProviderConfig[]
    if (validProviders.length < 1 || !fileContent || !file) return

    setIsRunning(true)
    setError(null)
    setResult(null)

    try {
      const benchmarkResult = await benchmarkApi.runBenchmark({
        providers: validProviders,
        file_content: fileContent,
        file_name: file.name,
        chunk_config: {
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
          max_tokens: maxTokens,
        },
      })
      setResult(benchmarkResult)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Benchmark failed')
    } finally {
      setIsRunning(false)
    }
  }

  // Load history
  const loadHistory = async () => {
    setLoadingHistory(true)
    try {
      const data = await benchmarkApi.getHistory(10)
      setHistory(data.results)
    } catch (err) {
      console.error('Failed to load history:', err)
    } finally {
      setLoadingHistory(false)
    }
  }

  useEffect(() => {
    if (showHistory) {
      loadHistory()
    }
  }, [showHistory])

  // Check if we can run
  const validProviderCount = providers.filter((p) => p !== null).length
  const canRun = validProviderCount >= 1 && fileContent && !isRunning

  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (!user?.is_admin) return null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            to={`/${language}/system`}
            className="p-2 rounded-xl bg-surface-variant dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
          >
            <ArrowLeftIcon className="h-5 w-5 text-secondary" />
          </Link>
          <div>
            <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">
              {t('benchmark.title', 'Embedding Benchmark')}
            </h2>
            <p className="text-sm text-secondary dark:text-gray-400">
              {t('benchmark.subtitle', 'Compare embedding providers side by side')}
            </p>
          </div>
        </div>
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-surface-variant dark:bg-gray-700 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
        >
          <ClockIcon className="h-4 w-4" />
          {t('benchmark.history', 'History')}
        </button>
      </div>

      {/* History Panel */}
      {showHistory && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <h3 className="font-semibold text-primary-900 dark:text-gray-200 mb-4">
            {t('benchmark.recentBenchmarks', 'Recent Benchmarks')}
          </h3>
          {loadingHistory ? (
            <div className="flex items-center justify-center py-8">
              <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : history.length === 0 ? (
            <p className="text-secondary dark:text-gray-400 text-center py-8">
              {t('benchmark.noHistory', 'No benchmark history yet')}
            </p>
          ) : (
            <div className="space-y-3">
              {history.map((h) => (
                <div
                  key={h.id}
                  className="flex items-center justify-between p-3 bg-surface-variant dark:bg-gray-700 rounded-xl"
                >
                  <div>
                    <p className="font-medium text-primary-900 dark:text-gray-200">{h.file_name}</p>
                    <p className="text-xs text-secondary dark:text-gray-400">
                      {new Date(h.timestamp).toLocaleString()} - {h.results.length} providers
                    </p>
                  </div>
                  {h.winner && (
                    <span className="text-xs text-green-600 dark:text-green-400">
                      Winner: {h.winner}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Provider Selection */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[0, 1, 2].map((index) => (
          <ProviderCard
            key={index}
            index={index}
            config={providers[index]}
            availableProviders={availableProviders}
            onUpdate={(config) => updateProvider(index, config)}
            onTest={() => testProvider(index)}
            testResult={testResults[index]}
            isTesting={testingIndex === index}
            disabled={isRunning}
          />
        ))}
      </div>

      {/* File Upload */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
        <h3 className="font-semibold text-primary-900 dark:text-gray-200 mb-4">
          {t('benchmark.fileUpload', 'File Upload')}
        </h3>

        <div
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          onClick={() => fileInputRef.current?.click()}
          className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
            file
              ? 'border-primary bg-primary/5'
              : 'border-gray-300 dark:border-gray-600 hover:border-primary hover:bg-primary/5'
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            onChange={(e) => e.target.files?.[0] && handleFileSelect(e.target.files[0])}
            accept=".txt,.md,.pdf,.docx"
            className="hidden"
          />
          <DocumentTextIcon className="h-12 w-12 mx-auto text-secondary dark:text-gray-400 mb-3" />
          {file ? (
            <div>
              <p className="font-medium text-primary-900 dark:text-gray-200">{file.name}</p>
              <p className="text-sm text-secondary dark:text-gray-400">
                {(file.size / 1024).toFixed(1)} KB
              </p>
            </div>
          ) : (
            <div>
              <p className="font-medium text-primary-900 dark:text-gray-200">
                {t('benchmark.dropFile', 'Drop file here or click to upload')}
              </p>
              <p className="text-sm text-secondary dark:text-gray-400">
                {t('benchmark.supportedFormats', 'Supports: TXT, MD, PDF, DOCX')}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Configuration (Collapsible) */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1">
        <button
          onClick={() => setShowConfig(!showConfig)}
          className="w-full flex items-center justify-between p-5"
        >
          <h3 className="font-semibold text-primary-900 dark:text-gray-200">
            {t('benchmark.configuration', 'Configuration')}
          </h3>
          {showConfig ? (
            <ChevronUpIcon className="h-5 w-5 text-secondary" />
          ) : (
            <ChevronDownIcon className="h-5 w-5 text-secondary" />
          )}
        </button>
        {showConfig && (
          <div className="px-5 pb-5 grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
                {t('benchmark.chunkSize', 'Chunk Size')}
              </label>
              <input
                type="number"
                value={chunkSize}
                onChange={(e) => setChunkSize(parseInt(e.target.value) || 1000)}
                className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
                {t('benchmark.chunkOverlap', 'Chunk Overlap')}
              </label>
              <input
                type="number"
                value={chunkOverlap}
                onChange={(e) => setChunkOverlap(parseInt(e.target.value) || 200)}
                className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-secondary dark:text-gray-400 mb-1">
                {t('benchmark.maxTokens', 'Max Tokens')}
              </label>
              <input
                type="number"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value) || 512)}
                className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2.5 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
              />
            </div>
          </div>
        )}
      </div>

      {/* Run Button */}
      <div className="flex justify-center">
        <button
          onClick={runBenchmark}
          disabled={!canRun}
          className="flex items-center gap-3 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-500 px-8 py-4 text-white font-medium transition-all hover:from-cyan-600 hover:to-blue-600 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg"
        >
          {isRunning ? (
            <>
              <ArrowPathIcon className="h-5 w-5 animate-spin" />
              {t('benchmark.running', 'Running Benchmark...')}
            </>
          ) : (
            <>
              <PlayIcon className="h-5 w-5" />
              {t('benchmark.runBenchmark', 'Run Benchmark')} ({validProviderCount} {t('benchmark.providers', 'providers')})
            </>
          )}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/20 p-5 border border-red-200 dark:border-red-800">
          <div className="flex items-center gap-2 text-red-600 dark:text-red-400">
            <ExclamationTriangleIcon className="h-5 w-5" />
            <span className="font-medium">{t('benchmark.error', 'Benchmark Failed')}</span>
          </div>
          <p className="text-sm text-red-500 mt-2">{error}</p>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-6">
          {/* Summary Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {result.results.map((metrics) => (
              <ResultCard
                key={metrics.provider}
                metrics={metrics}
                isWinner={metrics.provider === result.winner}
              />
            ))}
          </div>

          {/* Detailed Comparison Table */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1 overflow-x-auto">
            <h3 className="font-semibold text-primary-900 dark:text-gray-200 mb-4 flex items-center gap-2">
              <CpuChipIcon className="h-5 w-5 text-primary" />
              {t('benchmark.detailedComparison', 'Detailed Comparison')}
            </h3>
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700">
                  <th className="py-3 px-4 text-left text-sm font-medium text-secondary dark:text-gray-400">
                    {t('benchmark.metric', 'Metric')}
                  </th>
                  {result.results.map((m) => (
                    <th
                      key={m.provider}
                      className="py-3 px-4 text-center text-sm font-medium text-primary-900 dark:text-gray-200"
                    >
                      {m.model}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <MetricRow
                  label={t('benchmark.totalTime', 'Total Time')}
                  values={result.results.map((m) => (m.success ? m.total_time_ms : null))}
                  unit="ms"
                  format={(v) => v.toFixed(0)}
                  highlight="min"
                />
                <MetricRow
                  label={t('benchmark.embeddingTime', 'Embedding Time')}
                  values={result.results.map((m) => (m.success ? m.embedding_time_ms : null))}
                  unit="ms"
                  format={(v) => v.toFixed(0)}
                  highlight="min"
                />
                <MetricRow
                  label={t('benchmark.avgLatency', 'Avg Latency')}
                  values={result.results.map((m) => (m.success ? m.avg_latency_ms : null))}
                  unit="ms"
                  format={(v) => v.toFixed(1)}
                  highlight="min"
                />
                <MetricRow
                  label={t('benchmark.dimension', 'Dimensions')}
                  values={result.results.map((m) => (m.success ? m.embedding_dimension : null))}
                />
                <MetricRow
                  label={t('benchmark.chunks', 'Chunks')}
                  values={result.results.map((m) => m.chunks_created)}
                />
                <MetricRow
                  label={t('benchmark.tokens', 'Tokens')}
                  values={result.results.map((m) => m.tokens_processed)}
                />
                <MetricRow
                  label={t('benchmark.memoryBefore', 'Memory Before')}
                  values={result.results.map((m) => (m.success ? m.memory_before_mb : null))}
                  unit="MB"
                  format={(v) => v.toFixed(1)}
                />
                <MetricRow
                  label={t('benchmark.memoryPeak', 'Memory Peak')}
                  values={result.results.map((m) => (m.success ? m.memory_peak_mb : null))}
                  unit="MB"
                  format={(v) => v.toFixed(1)}
                  highlight="min"
                />
                <MetricRow
                  label={t('benchmark.cpuPercent', 'CPU %')}
                  values={result.results.map((m) => (m.success ? m.cpu_percent : null))}
                  unit="%"
                  format={(v) => v.toFixed(1)}
                />
                <MetricRow
                  label={t('benchmark.costEstimate', 'Est. Cost')}
                  values={result.results.map((m) =>
                    m.success && m.cost_estimate_usd ? `$${m.cost_estimate_usd.toFixed(4)}` : '-'
                  )}
                />
              </tbody>
            </table>
          </div>

          {/* File Info */}
          <div className="rounded-2xl bg-surface-variant dark:bg-gray-700 p-4">
            <div className="flex items-center gap-4 text-sm text-secondary dark:text-gray-400">
              <span>
                <strong>{t('benchmark.file', 'File')}:</strong> {result.file_name}
              </span>
              <span>
                <strong>{t('benchmark.size', 'Size')}:</strong> {(result.file_size_bytes / 1024).toFixed(1)} KB
              </span>
              <span>
                <strong>{t('benchmark.chunkConfig', 'Config')}:</strong> {result.chunk_config.chunk_size} chars,{' '}
                {result.chunk_config.chunk_overlap} overlap
              </span>
            </div>
          </div>

          {/* Run Again */}
          <div className="flex justify-center">
            <button
              onClick={() => {
                setResult(null)
                setError(null)
              }}
              className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-6 py-3 text-primary-700 dark:text-primary-300 font-medium transition-all hover:bg-gray-200 dark:hover:bg-gray-600"
            >
              <ArrowPathIcon className="h-5 w-5" />
              {t('benchmark.runAnother', 'Run Another Benchmark')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
