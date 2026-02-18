import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import ModelVersionSelector from '../components/ModelVersionSelector'
import {
  Cog6ToothIcon,
  ArrowPathIcon,
  CpuChipIcon,
  WifiIcon,
  CloudIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  ArrowDownTrayIcon,
  PlayIcon,
  SignalIcon,
  BoltIcon,
  XCircleIcon,
  GlobeAltIcon,
  PlusIcon,
  TrashIcon,
  MagnifyingGlassIcon,
  WrenchScrewdriverIcon,
  InformationCircleIcon,
} from '@heroicons/react/24/outline'
import {
  localLlmApi, systemApi, profilesApi,
  DiscoveryResult, LocalProvider, ModelRecommendation, OfflineModeConfig,
  Profile, SystemStats, CustomEndpoint, NetworkScanResult, ConfigOptions,
  LLMModel, EmbeddingModel, LLMProviderConfigResponse, LLMProviderConfigRequest,
  ProviderModelInfo, FetchModelsResponse, ProviderTestResponse,
  AgentTool, ToolTestResponse
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'

// Preset network ranges for common VPN and local networks
const NETWORK_PRESETS = [
  { name: 'Docker Host (Windows/Mac)', range: 'host.docker.internal', description: 'Docker Desktop host access' },
  { name: 'Docker Gateway (Linux)', range: '172.17.0.1', description: 'Default Docker bridge' },
  { name: 'Common Local Network', range: '192.168.1.1-254', description: '192.168.1.x range' },
  { name: 'Local Network .0.x', range: '192.168.0.1-254', description: '192.168.0.x range' },
  { name: 'OpenVPN Default', range: '10.8.0.1-254', description: '10.8.0.x VPN range' },
  { name: 'WireGuard Default', range: '10.0.0.1-254', description: '10.0.0.x VPN range' },
  { name: 'Tailscale/ZeroTier', range: '100.64.0.1-100', description: '100.64.x.x CGNAT range' },
  { name: 'VPN 10.5.x.x', range: '10.5.0.1-254', description: '10.5.0.x VPN range' },
  { name: 'VPN 10.10.x.x', range: '10.10.0.1-254', description: '10.10.0.x VPN range' },
  { name: 'VPN 10.20.x.x', range: '10.20.0.1-254', description: '10.20.0.x VPN range' },
  { name: 'Corporate 172.16.x.x', range: '172.16.0.1-254', description: '172.16.0.x private' },
  { name: 'Corporate 172.31.x.x', range: '172.31.0.1-254', description: '172.31.0.x private' },
]

export default function ConfigurationPage() {
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  
  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null)
  const [offlineConfig, setOfflineConfig] = useState<OfflineModeConfig>({ enabled: false })
  const [_systemStats, setSystemStats] = useState<SystemStats | null>(null)
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [isDiscovering, setIsDiscovering] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isPulling, setIsPulling] = useState<string | null>(null)
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)
  const [activeTab, setActiveTab] = useState<'offline' | 'network' | 'models' | 'profiles' | 'search' | 'llm' | 'tools' | 'agent'>('llm')
    
    // Search settings state
    const [configOptions, setConfigOptions] = useState<ConfigOptions | null>(null)
    const [defaultMatchCount, setDefaultMatchCount] = useState<number>(10)
    const [isSavingSearch, setIsSavingSearch] = useState(false)
    
    // Editable model config state
    const [llmModel, setLlmModel] = useState<string>('')
    const [embeddingModel, setEmbeddingModel] = useState<string>('')
    const [embeddingDimension, setEmbeddingDimension] = useState<number>(1536)
    const [llmModels, setLlmModels] = useState<LLMModel[]>([])
    const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModel[]>([])
    
    // LLM Provider config state
    const [llmProviderConfig, setLlmProviderConfig] = useState<LLMProviderConfigResponse | null>(null)
    const [orchestratorProvider, setOrchestratorProvider] = useState<string>('openai')
    const [orchestratorModel, setOrchestratorModel] = useState<string>('')
    const [workerProvider, setWorkerProvider] = useState<string>('google')
    const [workerModel, setWorkerModel] = useState<string>('')
    const [openaiApiKey, setOpenaiApiKey] = useState<string>('')
    const [googleApiKey, setGoogleApiKey] = useState<string>('')
    const [anthropicApiKey, setAnthropicApiKey] = useState<string>('')
    const [fastLlmApiKey, setFastLlmApiKey] = useState<string>('')
    const [isSavingLlmConfig, setIsSavingLlmConfig] = useState(false)
    
    // Provider models and testing state
    const [_providerModels, setProviderModels] = useState<Record<string, ProviderModelInfo[]>>({})
    const [isFetchingModels, setIsFetchingModels] = useState<string | null>(null)
    const [isTestingProvider, setIsTestingProvider] = useState<string | null>(null)
    const [testLogs, setTestLogs] = useState<string[]>([])
    const [testResult, setTestResult] = useState<ProviderTestResponse | null>(null)
    const [selectedTestProvider, setSelectedTestProvider] = useState<string>('openai')
    const [selectedTestModel, setSelectedTestModel] = useState<string>('')
    const [testPrompt, setTestPrompt] = useState<string>('Hello! Please respond with a brief greeting to test the connection.')
    
    // Agent Tools state
    const [agentTools, setAgentTools] = useState<AgentTool[]>([])
    const [selectedTool, setSelectedTool] = useState<AgentTool | null>(null)
    const [isTestingTool, setIsTestingTool] = useState<string | null>(null)
    const [toolTestResult, setToolTestResult] = useState<ToolTestResponse | null>(null)
    const [toolTestLogs, setToolTestLogs] = useState<string[]>([])
    const [toolTestParams, setToolTestParams] = useState<Record<string, string>>({})
  
    // Agent Performance state
    const [agentConfig, setAgentConfig] = useState({
      parallel_workers: 4,
      max_iterations: 3,
      global_max_orchestrators: 10,
      global_max_workers: 20,
      worker_timeout: 60,
      orchestrator_timeout: 120,
      total_timeout: 300,
      default_mode: 'auto',
      auto_fast_threshold: 50,
      skip_evaluation: false,
      max_sources_per_search: 10
    })
    const [isSavingAgentConfig, setIsSavingAgentConfig] = useState(false)
    const [agentConfigErrors, setAgentConfigErrors] = useState<Record<string, string>>({})

    // Validation rules for agent config fields
    const agentConfigValidation: Record<string, { min: number; max: number; label: string }> = {
      parallel_workers: { min: 1, max: 20, label: 'Parallel Workers' },
      max_iterations: { min: 1, max: 10, label: 'Max Iterations' },
      max_sources_per_search: { min: 1, max: 50, label: 'Max Sources' },
      global_max_orchestrators: { min: 1, max: 50, label: 'Max Orchestrators' },
      global_max_workers: { min: 1, max: 100, label: 'Max Workers' },
      worker_timeout: { min: 10, max: 300, label: 'Worker Timeout' },
      orchestrator_timeout: { min: 30, max: 600, label: 'Orchestrator Timeout' },
      total_timeout: { min: 60, max: 3600, label: 'Total Timeout' },
      auto_fast_threshold: { min: 10, max: 500, label: 'Auto-Fast Threshold' }
    }

    // Validate a single field and return error message or empty string
    const validateAgentField = (field: string, value: number): string => {
      const rules = agentConfigValidation[field]
      if (!rules) return ''
      if (isNaN(value)) return `${rules.label} must be a number`
      if (value < rules.min) return `${rules.label} must be at least ${rules.min}`
      if (value > rules.max) return `${rules.label} must be at most ${rules.max}`
      return ''
    }

    // Handle agent config change with validation
    const handleAgentConfigChange = (field: string, value: number) => {
      setAgentConfig(prev => ({ ...prev, [field]: value }))
      const error = validateAgentField(field, value)
      setAgentConfigErrors(prev => ({ ...prev, [field]: error }))
    }

    // Check if there are any validation errors
    const hasAgentConfigErrors = (): boolean => {
      const errors: Record<string, string> = {}
      for (const field of Object.keys(agentConfigValidation)) {
        const value = agentConfig[field as keyof typeof agentConfig] as number
        const error = validateAgentField(field, value)
        if (error) errors[field] = error
      }
      setAgentConfigErrors(errors)
      return Object.keys(errors).length > 0
    }

  // Network scanning state
  const [customEndpoints, setCustomEndpoints] = useState<CustomEndpoint[]>([])
  const [isScanning, setIsScanning] = useState(false)
  const [scanResult, setScanResult] = useState<NetworkScanResult | null>(null)
  const [selectedPresets, setSelectedPresets] = useState<string[]>([])
  const [customIps, setCustomIps] = useState('')
  const [customRange, setCustomRange] = useState('')
  const [newEndpoint, setNewEndpoint] = useState({ name: '', url: '', provider_type: 'ollama' })
  
  const fetchData = useCallback(async () => {
    setIsLoading(true)
    try {
      const [statsRes, profilesRes, configRes, endpointsRes, configOptionsRes, llmModelsRes, embeddingModelsRes, llmProviderRes] = await Promise.all([
        systemApi.stats(),
        profilesApi.list(),
        localLlmApi.getOfflineConfig(),
        localLlmApi.getCustomEndpoints(),
        systemApi.getConfigOptions(),
        systemApi.listLLMModels(),
        systemApi.listEmbeddingModels(),
        systemApi.getLLMProviderConfig(),
      ])
      setSystemStats(statsRes)
      setProfiles(profilesRes.profiles)
      setOfflineConfig(configRes)
      setCustomEndpoints(endpointsRes.endpoints)
      setConfigOptions(configOptionsRes)
      setDefaultMatchCount(configOptionsRes.current.default_match_count)
      setLlmModel(configOptionsRes.current.llm_model)
      setEmbeddingModel(configOptionsRes.current.embedding_model)
      setEmbeddingDimension(configOptionsRes.current.embedding_dimension)
      setLlmModels(llmModelsRes.models)
      setEmbeddingModels(embeddingModelsRes.models)
      // Set LLM provider config
      setLlmProviderConfig(llmProviderRes)
      setOrchestratorProvider(llmProviderRes.orchestrator_provider)
      setOrchestratorModel(llmProviderRes.orchestrator_model)
      setWorkerProvider(llmProviderRes.worker_provider)
      setWorkerModel(llmProviderRes.worker_model)
      // Fetch agent tools
      try {
        const toolsRes = await systemApi.listTools()
        setAgentTools(toolsRes.tools)
      } catch (toolsErr) {
        console.error('Error fetching tools:', toolsErr)
      }
      // Fetch agent performance config
      try {
        const agentConfigRes = await systemApi.getAgentPerformanceConfig()
        setAgentConfig(agentConfigRes)
      } catch (agentErr) {
        console.error('Error fetching agent config:', agentErr)
      }
    } catch (err) {
      console.error('Error fetching data:', err)
    } finally {
      setIsLoading(false)
    }
  }, [])
  
  const handleDiscover = async () => {
    setIsDiscovering(true)
    try {
      const result = await localLlmApi.discover()
      setDiscovery(result)
    } catch (err) {
      console.error('Error discovering:', err)
      setMessage({ type: 'error', text: 'Failed to discover local LLMs' })
    } finally {
      setIsDiscovering(false)
    }
  }
  
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/dashboard')
    }
  }, [user, authLoading, navigate])
  
  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      fetchData()
      handleDiscover()
    }
  }, [authLoading, user, fetchData])
  
  if (authLoading || isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  
  if (!user?.is_admin) return null

  const handleSaveOfflineConfig = async () => {
    setIsSaving(true)
    setMessage(null)
    try {
      await localLlmApi.saveOfflineConfig(offlineConfig)
      setMessage({ type: 'success', text: 'Configuration saved successfully' })
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to save configuration' })
    } finally {
      setIsSaving(false)
    }
  }

  const handleSaveAgentConfig = async () => {
    // Validate all fields before saving
    if (hasAgentConfigErrors()) {
      setMessage({ type: 'error', text: 'Please fix validation errors before saving' })
      return
    }
    
    setIsSavingAgentConfig(true)
    setMessage(null)
    try {
      await systemApi.saveAgentPerformanceConfig(agentConfig)
      setMessage({ type: 'success', text: 'Agent performance configuration saved successfully' })
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to save agent configuration' })
    } finally {
      setIsSavingAgentConfig(false)
    }
  }

  const handlePullModel = async (providerId: string, modelName: string) => {
    setIsPulling(modelName)
    setMessage(null)
    try {
      const result = await localLlmApi.pullModel(providerId, modelName)
      if (result.success) {
        setMessage({ type: 'success', text: `Model ${modelName} pulled successfully` })
        handleDiscover() // Refresh
      } else {
        setMessage({ type: 'error', text: result.error || 'Failed to pull model' })
      }
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to pull model' })
    } finally {
      setIsPulling(null)
    }
  }

  const handleTestModel = async (providerId: string, modelName: string, modelType: string) => {
    setMessage(null)
    try {
      const result = await localLlmApi.testModel(providerId, modelName, modelType)
      if (result.success) {
        setMessage({ type: 'success', text: `Model ${modelName} is working! ${modelType === 'embedding' ? `(${result.dimensions} dimensions)` : ''}` })
      } else {
        setMessage({ type: 'error', text: result.error || 'Model test failed' })
      }
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to test model' })
    }
  }

  const selectModel = (provider: LocalProvider, modelName: string, type: 'chat' | 'embedding' | 'vision' | 'audio') => {
    if (type === 'chat') {
      setOfflineConfig(prev => ({
        ...prev,
        chat_provider: provider.id,
        chat_model: modelName,
        chat_url: provider.url
      }))
    } else if (type === 'embedding') {
      setOfflineConfig(prev => ({
        ...prev,
        embedding_provider: provider.id,
        embedding_model: modelName,
        embedding_url: provider.url
      }))
    } else if (type === 'vision') {
      setOfflineConfig(prev => ({
        ...prev,
        vision_provider: provider.id,
        vision_model: modelName,
        vision_url: provider.url
      }))
    } else if (type === 'audio') {
      setOfflineConfig(prev => ({
        ...prev,
        audio_provider: provider.id,
        audio_model: modelName,
        audio_url: provider.url
      }))
    }
  }

  // Network scanning handlers
  const handleNetworkScan = async () => {
    setIsScanning(true)
    setScanResult(null)
    setMessage(null)
    
    try {
      // Collect IPs from selected presets and custom inputs
      const customIpList = customIps
        .split(/[,\s]+/)
        .map(ip => ip.trim())
        .filter(ip => ip.length > 0)
      
      const presetRanges = selectedPresets
        .map(name => NETWORK_PRESETS.find(p => p.name === name)?.range)
        .filter(Boolean) as string[]
      
      // Combine custom range with preset ranges
      let ipRange = customRange.trim() || undefined
      if (!ipRange && presetRanges.length === 1) {
        ipRange = presetRanges[0]
      }
      
      // Add preset single IPs to customIpList
      const presetSingleIps = presetRanges.filter(r => !r.includes('-') && !r.includes('/'))
      const presetRangeIps = presetRanges.filter(r => r.includes('-') || r.includes('/'))
      
      const allCustomIps = [...customIpList, ...presetSingleIps]
      
      // Run scan for each range preset separately if multiple
      if (presetRangeIps.length > 1) {
        let allFound: LocalProvider[] = []
        let allScanned: string[] = []
        
        for (const range of presetRangeIps) {
          const result = await localLlmApi.scanNetwork({
            ip_range: range,
            custom_ips: allCustomIps
          })
          if (result.found) allFound = [...allFound, ...result.found]
          if (result.scanned) allScanned = [...allScanned, ...result.scanned]
        }
        
        setScanResult({
          success: true,
          found: allFound,
          scanned: [...new Set(allScanned)],
          scanned_count: new Set(allScanned).size,
          found_count: allFound.length
        })
      } else {
        const result = await localLlmApi.scanNetwork({
          ip_range: ipRange || presetRangeIps[0],
          custom_ips: allCustomIps
        })
        setScanResult(result)
      }
      
      setMessage({ type: 'success', text: 'Network scan completed' })
    } catch (err) {
      console.error('Error scanning network:', err)
      setMessage({ type: 'error', text: 'Network scan failed' })
    } finally {
      setIsScanning(false)
    }
  }

  const handleAddCustomEndpoint = async () => {
    if (!newEndpoint.name || !newEndpoint.url) {
      setMessage({ type: 'error', text: 'Name and URL are required' })
      return
    }
    
    try {
      const endpoint: CustomEndpoint = {
        id: `custom-${Date.now()}`,
        name: newEndpoint.name,
        url: newEndpoint.url,
        provider_type: newEndpoint.provider_type,
        enabled: true
      }
      await localLlmApi.addCustomEndpoint(endpoint)
      setCustomEndpoints(prev => [...prev, endpoint])
      setNewEndpoint({ name: '', url: '', provider_type: 'ollama' })
      setMessage({ type: 'success', text: 'Custom endpoint added' })
      handleDiscover() // Refresh discovery
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to add endpoint' })
    }
  }

  const handleDeleteCustomEndpoint = async (endpointId: string) => {
    try {
      await localLlmApi.deleteCustomEndpoint(endpointId)
      setCustomEndpoints(prev => prev.filter(e => e.id !== endpointId))
      setMessage({ type: 'success', text: 'Endpoint deleted' })
      handleDiscover()
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to delete endpoint' })
    }
  }

  const handleAddFromScan = async (provider: LocalProvider) => {
    const endpoint: CustomEndpoint = {
      id: `scan-${Date.now()}`,
      name: `${provider.name} @ ${provider.host}`,
      url: provider.url,
      provider_type: provider.id === 'ollama' ? 'ollama' : 'openai-compatible',
      enabled: true
    }
    
    try {
      await localLlmApi.addCustomEndpoint(endpoint)
      setCustomEndpoints(prev => [...prev, endpoint])
      setMessage({ type: 'success', text: `Added ${endpoint.name} as custom endpoint` })
      handleDiscover()
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to add endpoint' })
    }
  }

  const togglePreset = (presetName: string) => {
    setSelectedPresets(prev => 
      prev.includes(presetName) 
        ? prev.filter(p => p !== presetName)
        : [...prev, presetName]
    )
  }

  // Fetch models from provider API
  const handleFetchModels = async (providerId: string) => {
    setIsFetchingModels(providerId)
    setTestLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] Fetching models from ${providerId}...`])
    
    try {
      const result: FetchModelsResponse = await systemApi.fetchModelsFromApi(providerId)
      
      // Add logs from response
      if (result.logs) {
        setTestLogs(prev => [...prev, ...result.logs])
      }
      
      if (result.success) {
        // Update provider models
        setProviderModels(prev => ({
          ...prev,
          [providerId]: result.models
        }))
        
        // Update the llmProviderConfig with new models
        if (llmProviderConfig) {
          const updatedProviders = llmProviderConfig.providers.map(p => 
            p.id === providerId 
              ? { ...p, models: result.models.map(m => m.id) }
              : p
          )
          setLlmProviderConfig({ ...llmProviderConfig, providers: updatedProviders })
        }
        
        setMessage({ type: 'success', text: `Fetched ${result.total || result.models.length} models from ${providerId}` })
      } else {
        setMessage({ type: 'error', text: result.error || 'Failed to fetch models' })
      }
    } catch (err) {
      console.error('Error fetching models:', err)
      setTestLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ERROR: ${err}`])
      setMessage({ type: 'error', text: 'Failed to fetch models from API' })
    } finally {
      setIsFetchingModels(null)
    }
  }

  // Test provider connection
  const handleTestProvider = async () => {
    if (!selectedTestModel) {
      setMessage({ type: 'error', text: 'Please select a model to test' })
      return
    }
    
    setIsTestingProvider(selectedTestProvider)
    setTestLogs([`[${new Date().toLocaleTimeString()}] Starting connection test...`])
    setTestResult(null)
    
    try {
      const result: ProviderTestResponse = await systemApi.testProviderConnection({
        provider: selectedTestProvider,
        model: selectedTestModel,
        prompt: testPrompt
      })
      
      // Add logs from response
      if (result.logs) {
        setTestLogs(result.logs)
      }
      
      setTestResult(result)
      
      if (result.success) {
        setMessage({ type: 'success', text: `Connection test passed! Latency: ${result.latency_ms.toFixed(0)}ms` })
      } else {
        setMessage({ type: 'error', text: result.error || 'Connection test failed' })
      }
    } catch (err) {
      console.error('Error testing provider:', err)
      setTestLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ERROR: ${err}`])
      setMessage({ type: 'error', text: 'Failed to test connection' })
    } finally {
      setIsTestingProvider(null)
    }
  }

  // Test agent tool
  const handleTestTool = async (tool: AgentTool) => {
    setIsTestingTool(tool.id)
    setToolTestLogs([`[${new Date().toLocaleTimeString()}] Starting ${tool.name} test...`])
    setToolTestResult(null)
    
    try {
      const result: ToolTestResponse = await systemApi.testTool({
        tool_id: tool.id,
        parameters: toolTestParams
      })
      
      // Add logs from response
      if (result.logs) {
        setToolTestLogs(result.logs)
      }
      
      setToolTestResult(result)
      
      if (result.success) {
        setMessage({ type: 'success', text: `${tool.name} test passed! Latency: ${result.latency_ms.toFixed(0)}ms` })
      } else {
        setMessage({ type: 'error', text: result.error || 'Tool test failed' })
      }
    } catch (err) {
      console.error('Error testing tool:', err)
      setToolTestLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ERROR: ${err}`])
      setMessage({ type: 'error', text: 'Failed to test tool' })
    } finally {
      setIsTestingTool(null)
    }
  }

  // Handle model version switching
  const handleModelChange = async (modelType: 'orchestrator' | 'worker' | 'embedding', modelId: string) => {
    try {
      const switchRequest: any = {}
      
      switch (modelType) {
        case 'orchestrator':
          switchRequest.orchestrator_model = modelId
          setOrchestratorModel(modelId)
          break
        case 'worker':
          switchRequest.worker_model = modelId
          setWorkerModel(modelId)
          break
        case 'embedding':
          switchRequest.embedding_model = modelId
          setEmbeddingModel(modelId)
          break
      }
      
      const result = await systemApi.switchModelVersions(switchRequest)
      
      if (result.success) {
        setMessage({ type: 'success', text: `Successfully switched ${modelType} to ${modelId}` })
        // Refresh the LLM provider config to reflect changes
        const updatedConfig = await systemApi.getLLMProviderConfig()
        setLlmProviderConfig(updatedConfig)
      } else {
        setMessage({ type: 'error', text: result.message || `Failed to switch ${modelType} model` })
      }
    } catch (err: any) {
      setMessage({ type: 'error', text: `Error switching ${modelType} model: ${err.message}` })
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">Configuration</h2>
          <p className="text-sm text-secondary dark:text-gray-400">Offline mode and local LLM settings</p>
        </div>
        <button
          onClick={handleDiscover}
          disabled={isDiscovering}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 hover:bg-primary-100 dark:hover:bg-gray-600 disabled:opacity-50"
        >
          {isDiscovering ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <SignalIcon className="h-4 w-4" />}
          Discover LLMs
        </button>
      </div>

      {message && (
        <div className={`rounded-2xl p-4 ${message.type === 'success' ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400' : 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400'}`}>
          {message.text}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200 dark:border-gray-700 overflow-x-auto">
        {[
          { id: 'llm', label: 'LLM Providers', icon: BoltIcon },
          { id: 'tools', label: 'Agent Tools', icon: WrenchScrewdriverIcon },
          { id: 'agent', label: 'Agent Performance', icon: CpuChipIcon },
          { id: 'offline', label: 'Offline Mode', icon: WifiIcon },
          { id: 'network', label: 'Network Scan', icon: GlobeAltIcon },
          { id: 'models', label: 'Local Models', icon: CpuChipIcon },
          { id: 'profiles', label: 'Profile Settings', icon: Cog6ToothIcon },
                    { id: 'search', label: 'Search Settings', icon: MagnifyingGlassIcon },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab.id
                ? 'border-primary text-primary'
                : 'border-transparent text-secondary hover:text-primary-700 dark:text-gray-400'
            }`}
          >
            <tab.icon className="h-4 w-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* LLM Providers Tab */}
      {activeTab === 'llm' && (
        <div className="space-y-6">
          {/* Orchestrator LLM */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <BoltIcon className="h-5 w-5 text-primary" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Orchestrator LLM (Thinking Model)</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              The orchestrator handles complex reasoning, planning, and synthesis. Choose a powerful model for best results.
            </p>
            
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Provider</label>
                <select
                  value={orchestratorProvider}
                  onChange={(e) => {
                    setOrchestratorProvider(e.target.value)
                    // Reset model when provider changes
                    const provider = llmProviderConfig?.providers.find(p => p.id === e.target.value)
                    if (provider && provider.models.length > 0) {
                      setOrchestratorModel(provider.models[0])
                    }
                  }}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
                >
                  {llmProviderConfig?.providers.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Model</label>
                <select
                  value={orchestratorModel}
                  onChange={(e) => setOrchestratorModel(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
                >
                  {(llmProviderConfig?.providers.find(p => p.id === orchestratorProvider)?.models || []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  {orchestratorModel && !llmProviderConfig?.providers.find(p => p.id === orchestratorProvider)?.models.includes(orchestratorModel) && (
                    <option value={orchestratorModel}>{orchestratorModel}</option>
                  )}
                </select>
              </div>
            </div>
          </div>

          {/* Worker/Fast LLM */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <BoltIcon className="h-5 w-5 text-green-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Worker LLM (Fast Model)</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              The worker handles parallel tasks like search summarization and quick responses. Choose a fast, cost-effective model.
            </p>
            
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Provider</label>
                <select
                  value={workerProvider}
                  onChange={(e) => {
                    setWorkerProvider(e.target.value)
                    // Reset model when provider changes
                    const provider = llmProviderConfig?.providers.find(p => p.id === e.target.value)
                    if (provider && provider.models.length > 0) {
                      setWorkerModel(provider.models[0])
                    }
                  }}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
                >
                  {llmProviderConfig?.providers.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Model</label>
                <select
                  value={workerModel}
                  onChange={(e) => setWorkerModel(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
                >
                  {(llmProviderConfig?.providers.find(p => p.id === workerProvider)?.models || []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  {workerModel && !llmProviderConfig?.providers.find(p => p.id === workerProvider)?.models.includes(workerModel) && (
                    <option value={workerModel}>{workerModel}</option>
                  )}
                </select>
              </div>
            </div>
          </div>

          {/* API Keys */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <Cog6ToothIcon className="h-5 w-5 text-amber-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">API Keys</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              Configure API keys for each provider. Keys are stored securely and only masked values are displayed.
            </p>
            
            <div className="space-y-4">
              {/* OpenAI API Key */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200">
                    OpenAI API Key
                    {llmProviderConfig?.openai_api_key_set && (
                      <span className="ml-2 text-xs text-green-500">✓ Set ({llmProviderConfig.openai_api_key_masked})</span>
                    )}
                  </label>
                  <button
                    onClick={() => handleFetchModels('openai')}
                    disabled={isFetchingModels === 'openai'}
                    className="flex items-center gap-1 px-3 py-1 text-xs font-medium rounded-lg bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 hover:bg-primary-200 dark:hover:bg-primary-800/40 disabled:opacity-50"
                  >
                    {isFetchingModels === 'openai' ? <ArrowPathIcon className="h-3 w-3 animate-spin" /> : <ArrowDownTrayIcon className="h-3 w-3" />}
                    Fetch Models
                  </button>
                </div>
                <input
                  type="password"
                  value={openaiApiKey}
                  onChange={(e) => setOpenaiApiKey(e.target.value)}
                  placeholder={llmProviderConfig?.openai_api_key_set ? '••••••••••••' : 'sk-...'}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200"
                />
                <p className="text-xs text-secondary dark:text-gray-500 mt-1">Required for OpenAI models (GPT-5, GPT-4, etc.)</p>
              </div>

              {/* Google API Key */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200">
                    Google API Key (Gemini)
                    {llmProviderConfig?.google_api_key_set && (
                      <span className="ml-2 text-xs text-green-500">✓ Set ({llmProviderConfig.google_api_key_masked})</span>
                    )}
                  </label>
                  <button
                    onClick={() => handleFetchModels('google')}
                    disabled={isFetchingModels === 'google'}
                    className="flex items-center gap-1 px-3 py-1 text-xs font-medium rounded-lg bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 hover:bg-primary-200 dark:hover:bg-primary-800/40 disabled:opacity-50"
                  >
                    {isFetchingModels === 'google' ? <ArrowPathIcon className="h-3 w-3 animate-spin" /> : <ArrowDownTrayIcon className="h-3 w-3" />}
                    Fetch Models
                  </button>
                </div>
                <input
                  type="password"
                  value={googleApiKey}
                  onChange={(e) => setGoogleApiKey(e.target.value)}
                  placeholder={llmProviderConfig?.google_api_key_set ? '••••••••••••' : 'AIza...'}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200"
                />
                <p className="text-xs text-secondary dark:text-gray-500 mt-1">Required for Google Gemini models</p>
              </div>

              {/* Anthropic API Key */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-primary-900 dark:text-gray-200">
                    Anthropic API Key (Claude)
                    {llmProviderConfig?.anthropic_api_key_set && (
                      <span className="ml-2 text-xs text-green-500">✓ Set ({llmProviderConfig.anthropic_api_key_masked})</span>
                    )}
                  </label>
                  <button
                    onClick={() => handleFetchModels('anthropic')}
                    disabled={isFetchingModels === 'anthropic'}
                    className="flex items-center gap-1 px-3 py-1 text-xs font-medium rounded-lg bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 hover:bg-primary-200 dark:hover:bg-primary-800/40 disabled:opacity-50"
                  >
                    {isFetchingModels === 'anthropic' ? <ArrowPathIcon className="h-3 w-3 animate-spin" /> : <ArrowDownTrayIcon className="h-3 w-3" />}
                    Fetch Models
                  </button>
                </div>
                <input
                  type="password"
                  value={anthropicApiKey}
                  onChange={(e) => setAnthropicApiKey(e.target.value)}
                  placeholder={llmProviderConfig?.anthropic_api_key_set ? '••••••••••••' : 'sk-ant-...'}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200"
                />
                <p className="text-xs text-secondary dark:text-gray-500 mt-1">Required for Anthropic Claude models</p>
              </div>

              {/* Ollama (Local) - Fetch Only */}
              <div className="rounded-xl bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 p-4">
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-purple-900 dark:text-purple-200">
                    Ollama (Local LLMs)
                  </label>
                  <button
                    onClick={() => handleFetchModels('ollama')}
                    disabled={isFetchingModels === 'ollama'}
                    className="flex items-center gap-1 px-3 py-1 text-xs font-medium rounded-lg bg-purple-100 dark:bg-purple-800/30 text-purple-700 dark:text-purple-300 hover:bg-purple-200 dark:hover:bg-purple-700/40 disabled:opacity-50"
                  >
                    {isFetchingModels === 'ollama' ? <ArrowPathIcon className="h-3 w-3 animate-spin" /> : <ArrowDownTrayIcon className="h-3 w-3" />}
                    Discover Models
                  </button>
                </div>
                <p className="text-xs text-purple-600 dark:text-purple-400">
                  No API key needed. Click "Discover Models" to find locally installed Ollama models.
                </p>
              </div>

              {/* Fast LLM API Key (optional separate key) */}
              <div className="rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4">
                <label className="block text-sm font-medium text-blue-900 dark:text-blue-200 mb-2">
                  Fast LLM API Key (Optional)
                  {llmProviderConfig?.fast_llm_api_key_set && (
                    <span className="ml-2 text-xs text-green-500">✓ Set</span>
                  )}
                </label>
                <input
                  type="password"
                  value={fastLlmApiKey}
                  onChange={(e) => setFastLlmApiKey(e.target.value)}
                  placeholder="Optional separate API key for worker LLM"
                  className="w-full rounded-lg border border-blue-300 dark:border-blue-700 bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200"
                />
                <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
                  If set, the worker LLM will use this key instead of the provider's main key. Useful for using different billing accounts.
                </p>
              </div>
            </div>
          </div>

          {/* Save Button */}
          <div className="flex justify-end gap-3">
            <button
              onClick={async () => {
                setIsSavingLlmConfig(true)
                setMessage(null)
                try {
                  const config: LLMProviderConfigRequest = {
                    orchestrator_provider: orchestratorProvider,
                    orchestrator_model: orchestratorModel,
                    worker_provider: workerProvider,
                    worker_model: workerModel,
                  }
                  // Only include API keys if they've been changed
                  if (openaiApiKey) config.openai_api_key = openaiApiKey
                  if (googleApiKey) config.google_api_key = googleApiKey
                  if (anthropicApiKey) config.anthropic_api_key = anthropicApiKey
                  if (fastLlmApiKey) config.fast_llm_api_key = fastLlmApiKey
                  
                  await systemApi.saveLLMProviderConfig(config)
                  setMessage({ type: 'success', text: 'LLM provider configuration saved! Changes take effect immediately.' })
                  // Clear API key inputs after saving
                  setOpenaiApiKey('')
                  setGoogleApiKey('')
                  setAnthropicApiKey('')
                  setFastLlmApiKey('')
                  // Refresh config
                  const newConfig = await systemApi.getLLMProviderConfig()
                  setLlmProviderConfig(newConfig)
                } catch (err) {
                  setMessage({ type: 'error', text: 'Failed to save LLM configuration' })
                } finally {
                  setIsSavingLlmConfig(false)
                }
              }}
              disabled={isSavingLlmConfig}
              className="flex items-center gap-2 rounded-xl bg-primary px-6 py-2 font-medium text-white transition-all hover:bg-primary-700 disabled:opacity-50"
            >
              {isSavingLlmConfig && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
              Save LLM Configuration
            </button>
          </div>

          {/* Test Provider Connection */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <PlayIcon className="h-5 w-5 text-green-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Test Provider Connection</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              Test your API configuration by sending a simple prompt to verify the connection is working.
            </p>
            
            <div className="grid gap-4 md:grid-cols-3 mb-4">
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Provider</label>
                <select
                  value={selectedTestProvider}
                  onChange={(e) => {
                    setSelectedTestProvider(e.target.value)
                    // Reset model and set default
                    const provider = llmProviderConfig?.providers.find(p => p.id === e.target.value)
                    if (provider && provider.models.length > 0) {
                      setSelectedTestModel(provider.models[0])
                    } else {
                      setSelectedTestModel('')
                    }
                  }}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
                >
                  {llmProviderConfig?.providers.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Model</label>
                <select
                  value={selectedTestModel}
                  onChange={(e) => setSelectedTestModel(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
                >
                  <option value="">Select a model...</option>
                  {(llmProviderConfig?.providers.find(p => p.id === selectedTestProvider)?.models || []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div className="flex items-end">
                <button
                  onClick={handleTestProvider}
                  disabled={isTestingProvider !== null || !selectedTestModel}
                  className="flex items-center gap-2 rounded-xl bg-green-600 px-6 py-2 font-medium text-white transition-all hover:bg-green-700 disabled:opacity-50 w-full justify-center"
                >
                  {isTestingProvider ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <PlayIcon className="h-4 w-4" />}
                  Test Connection
                </button>
              </div>
            </div>
            
            <div className="mb-4">
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Test Prompt</label>
              <input
                type="text"
                value={testPrompt}
                onChange={(e) => setTestPrompt(e.target.value)}
                placeholder="Enter a test prompt..."
                className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-primary-900 dark:text-gray-200"
              />
            </div>
            
            {/* Test Result */}
            {testResult && (
              <div className={`rounded-xl p-4 mb-4 ${testResult.success ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800'}`}>
                <div className="flex items-center gap-2 mb-2">
                  {testResult.success ? (
                    <CheckCircleIcon className="h-5 w-5 text-green-600 dark:text-green-400" />
                  ) : (
                    <ExclamationTriangleIcon className="h-5 w-5 text-red-600 dark:text-red-400" />
                  )}
                  <span className={`font-medium ${testResult.success ? 'text-green-900 dark:text-green-200' : 'text-red-900 dark:text-red-200'}`}>
                    {testResult.success ? `Success - ${testResult.latency_ms.toFixed(0)}ms` : 'Failed'}
                  </span>
                </div>
                {testResult.response && (
                  <div className="bg-white dark:bg-gray-800 rounded-lg p-3 text-sm text-primary-900 dark:text-gray-200 mt-2">
                    <strong>Response:</strong> {testResult.response}
                  </div>
                )}
                {testResult.error && (
                  <p className="text-sm text-red-600 dark:text-red-400 mt-2">{testResult.error}</p>
                )}
              </div>
            )}
            
            {/* Logs Textarea */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200">Logs</label>
                <button
                  onClick={() => setTestLogs([])}
                  className="text-xs text-secondary hover:text-primary-700 dark:text-gray-400 dark:hover:text-gray-200"
                >
                  Clear Logs
                </button>
              </div>
              <textarea
                readOnly
                value={testLogs.join('\n')}
                className="w-full h-48 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 px-3 py-2 text-xs font-mono text-primary-900 dark:text-gray-200 resize-none"
                placeholder="Logs will appear here..."
              />
            </div>
          </div>

          {/* Info Card */}
          <div className="rounded-2xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-6">
            <h4 className="text-sm font-medium text-blue-900 dark:text-blue-200 mb-2">How it works</h4>
            <ul className="text-sm text-blue-800 dark:text-blue-300 space-y-1">
              <li>• <strong>Orchestrator LLM:</strong> Handles complex reasoning, planning search strategies, and synthesizing final answers</li>
              <li>• <strong>Worker LLM:</strong> Performs fast parallel tasks like summarizing search results and generating quick responses</li>
              <li>• You can mix providers (e.g., GPT-5 for thinking + Gemini Flash for fast tasks)</li>
              <li>• API keys are stored securely and loaded at startup</li>
              <li>• Ollama (local) doesn't require an API key</li>
            </ul>
          </div>
        </div>
      )}

      {/* Advanced Model Version Management */}
      {activeTab === 'llm' && (
        <div className="space-y-6">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <WrenchScrewdriverIcon className="h-5 w-5 text-purple-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Advanced Model Version Management</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              Browse and switch between all available model versions with detailed information about capabilities, pricing, and compatibility.
            </p>
            
            <ModelVersionSelector 
              currentOrchestrator={orchestratorModel}
              currentWorker={workerModel}
              currentEmbedding={embeddingModel}
              onModelChange={handleModelChange}
              showSwitchButton={true}
              className="border-0"
            />
          </div>
        </div>
      )}

      {/* Agent Tools Tab */}
      {activeTab === 'tools' && (
        <div className="space-y-6">
          {/* Tools Overview Help */}
          <div className="rounded-2xl bg-blue-50 dark:bg-blue-900/20 p-6">
            <div className="flex items-start gap-3">
              <InformationCircleIcon className="h-6 w-6 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
              <div>
                <h3 className="text-lg font-medium text-blue-900 dark:text-blue-100 mb-2">About Agent Tools</h3>
                <p className="text-sm text-blue-800 dark:text-blue-200 mb-3">
                  Tools are capabilities that the AI agent can use to help answer your questions. The agent automatically decides which tools to use based on your query.
                </p>
                <ul className="text-sm text-blue-700 dark:text-blue-300 space-y-1 list-disc list-inside">
                  <li>The agent can call multiple tools in sequence to gather information</li>
                  <li>Tool calls are shown in the chat interface as "thinking" steps</li>
                  <li>You can test each tool below to verify it's working correctly</li>
                </ul>
              </div>
            </div>
          </div>

          {/* Tools List */}
          <div className="space-y-4">
            {agentTools.map(tool => (
              <div key={tool.id} className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{tool.icon}</span>
                    <div>
                      <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">{tool.name}</h3>
                      <span className="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded text-gray-600 dark:text-gray-400">
                        {tool.category}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      setSelectedTool(tool)
                      setToolTestParams({})
                      setToolTestResult(null)
                      setToolTestLogs([])
                    }}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      selectedTool?.id === tool.id
                        ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300'
                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                    }`}
                  >
                    {selectedTool?.id === tool.id ? 'Selected' : 'Select to Test'}
                  </button>
                </div>

                <p className="text-sm text-secondary dark:text-gray-400 mb-4">{tool.description}</p>

                {/* Expandable Help Text */}
                <details className="mb-4">
                  <summary className="text-sm font-medium text-primary-700 dark:text-primary-300 cursor-pointer hover:text-primary-800 dark:hover:text-primary-200">
                    View detailed help
                  </summary>
                  <div className="mt-3 p-4 bg-gray-50 dark:bg-gray-900 rounded-lg">
                    <pre className="text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-mono">
                      {tool.help_text}
                    </pre>
                  </div>
                </details>

                {/* Parameters */}
                <div className="mb-4">
                  <h4 className="text-sm font-medium text-primary-900 dark:text-gray-300 mb-2">Parameters</h4>
                  <div className="space-y-2">
                    {tool.parameters.map(param => (
                      <div key={param.name} className="flex items-center gap-2 text-sm">
                        <code className="px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-gray-800 dark:text-gray-300">
                          {param.name}
                        </code>
                        <span className="text-gray-500 dark:text-gray-500">({param.type})</span>
                        {param.required && <span className="text-red-500 text-xs">required</span>}
                        <span className="text-gray-600 dark:text-gray-400">- {param.description}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Test Section (shown when tool is selected) */}
                {selectedTool?.id === tool.id && (
                  <div className="border-t border-gray-200 dark:border-gray-700 pt-4 mt-4">
                    <h4 className="text-sm font-medium text-primary-900 dark:text-gray-300 mb-3">Test Tool</h4>
                    
                    {/* Parameter inputs */}
                    <div className="space-y-3 mb-4">
                      {tool.parameters.map(param => (
                        <div key={param.name}>
                          <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                            {param.name} {param.required && <span className="text-red-500">*</span>}
                          </label>
                          {param.type === 'enum' && param.options ? (
                            <select
                              value={toolTestParams[param.name] || param.default || ''}
                              onChange={(e) => setToolTestParams(prev => ({ ...prev, [param.name]: e.target.value }))}
                              className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
                            >
                              {param.options.map(opt => (
                                <option key={opt} value={opt}>{opt}</option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type="text"
                              value={toolTestParams[param.name] || ''}
                              onChange={(e) => setToolTestParams(prev => ({ ...prev, [param.name]: e.target.value }))}
                              placeholder={param.description}
                              className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
                            />
                          )}
                        </div>
                      ))}
                    </div>

                    {/* Test Button */}
                    <button
                      onClick={() => handleTestTool(tool)}
                      disabled={isTestingTool === tool.id}
                      className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
                    >
                      {isTestingTool === tool.id ? (
                        <>
                          <ArrowPathIcon className="h-4 w-4 animate-spin" />
                          Testing...
                        </>
                      ) : (
                        <>
                          <PlayIcon className="h-4 w-4" />
                          Run Test
                        </>
                      )}
                    </button>

                    {/* Test Result */}
                    {toolTestResult && toolTestResult.tool_id === tool.id && (
                      <div className={`mt-4 p-4 rounded-lg ${
                        toolTestResult.success 
                          ? 'bg-green-50 dark:bg-green-900/20' 
                          : 'bg-red-50 dark:bg-red-900/20'
                      }`}>
                        <div className="flex items-center gap-2 mb-2">
                          {toolTestResult.success ? (
                            <CheckCircleIcon className="h-5 w-5 text-green-600 dark:text-green-400" />
                          ) : (
                            <XCircleIcon className="h-5 w-5 text-red-600 dark:text-red-400" />
                          )}
                          <span className={`font-medium ${
                            toolTestResult.success 
                              ? 'text-green-700 dark:text-green-300' 
                              : 'text-red-700 dark:text-red-300'
                          }`}>
                            {toolTestResult.success ? 'Test Passed' : 'Test Failed'}
                          </span>
                          <span className="text-sm text-gray-500 dark:text-gray-400">
                            ({toolTestResult.latency_ms.toFixed(0)}ms)
                          </span>
                        </div>
                        
                        {toolTestResult.result && (
                          <p className="text-sm text-gray-700 dark:text-gray-300 mb-2">
                            {toolTestResult.result}
                          </p>
                        )}
                        
                        {toolTestResult.result_preview && (
                          <div className="mt-2 p-3 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700">
                            <pre className="text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap overflow-x-auto">
                              {toolTestResult.result_preview}
                            </pre>
                          </div>
                        )}
                        
                        {toolTestResult.error && (
                          <p className="text-sm text-red-600 dark:text-red-400 mt-2">
                            Error: {toolTestResult.error}
                          </p>
                        )}
                      </div>
                    )}

                    {/* Test Logs */}
                    {toolTestLogs.length > 0 && selectedTool?.id === tool.id && (
                      <div className="mt-4">
                        <h5 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Test Logs</h5>
                        <textarea
                          readOnly
                          value={toolTestLogs.join('\n')}
                          className="w-full h-32 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 px-3 py-2 text-xs font-mono text-gray-700 dark:text-gray-300"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {agentTools.length === 0 && (
              <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                <WrenchScrewdriverIcon className="h-12 w-12 mx-auto mb-3 opacity-50" />
                <p>No tools available</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Offline Mode Tab */}
      {activeTab === 'offline' && (
        <div className="space-y-6">
          {/* System Resources */}
          {discovery && (
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">System Resources</h3>
              <div className="grid gap-4 md:grid-cols-4">
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400">CPU Cores</p>
                  <p className="text-xl font-semibold text-primary-900 dark:text-gray-200">{discovery.resources.cpu_cores}</p>
                </div>
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400">Total RAM</p>
                  <p className="text-xl font-semibold text-primary-900 dark:text-gray-200">{discovery.resources.ram_total_gb.toFixed(1)} GB</p>
                </div>
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400">Available RAM</p>
                  <p className="text-xl font-semibold text-primary-900 dark:text-gray-200">{discovery.resources.ram_available_gb.toFixed(1)} GB</p>
                </div>
                <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                  <p className="text-sm text-secondary dark:text-gray-400">GPU</p>
                  <p className="text-xl font-semibold text-primary-900 dark:text-gray-200">
                    {discovery.resources.gpu_available ? discovery.resources.gpu_name : 'None'}
                  </p>
                  {discovery.resources.gpu_memory_gb && (
                    <p className="text-xs text-secondary dark:text-gray-500">{discovery.resources.gpu_memory_gb} GB VRAM</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Offline Mode Configuration */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <WifiIcon className="h-5 w-5 text-primary" />
                <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Offline Mode</h3>
              </div>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={offlineConfig.enabled}
                  onChange={e => setOfflineConfig(prev => ({ ...prev, enabled: e.target.checked }))}
                  className="rounded"
                />
                <span className="text-sm font-medium text-primary-900 dark:text-gray-200">Enable Offline Mode</span>
              </label>
            </div>

            {/* Offline Readiness */}
            {discovery && (
              <div className={`mb-4 p-4 rounded-xl ${
                discovery.offline_ready
                  ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800'
                  : 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800'
              }`}>
                <div className="flex items-center gap-2">
                  {discovery.offline_ready ? (
                    <>
                      <CheckCircleIcon className="h-5 w-5 text-green-500" />
                      <span className="font-medium text-green-700 dark:text-green-400">Ready for Offline Mode</span>
                    </>
                  ) : (
                    <>
                      <ExclamationTriangleIcon className="h-5 w-5 text-amber-500" />
                      <span className="font-medium text-amber-700 dark:text-amber-400">Not Ready for Offline Mode</span>
                    </>
                  )}
                </div>
                <div className="flex flex-wrap gap-3 mt-2 text-sm">
                  <span className={discovery.has_chat_model ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}>
                    {discovery.has_chat_model ? '✓' : '✗'} Chat
                  </span>
                  <span className={discovery.has_embedding_model ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}>
                    {discovery.has_embedding_model ? '✓' : '✗'} Embedding
                  </span>
                  <span className={discovery.has_vision_model ? 'text-purple-600 dark:text-purple-400' : 'text-gray-400'}>
                    {discovery.has_vision_model ? '✓' : '✗'} Vision (Images)
                  </span>
                  <span className={discovery.has_audio_model ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400'}>
                    {discovery.has_audio_model ? '✓' : '✗'} Audio (Whisper)
                  </span>
                  <span className={discovery.has_video_model ? 'text-indigo-600 dark:text-indigo-400' : 'text-gray-400'}>
                    {discovery.has_video_model ? '✓' : '✗'} Video
                  </span>
                </div>
              </div>
            )}

            {/* Model Selection */}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">Chat Model</h4>
                {offlineConfig.chat_model ? (
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-primary-900 dark:text-gray-200">{offlineConfig.chat_model}</p>
                      <p className="text-xs text-secondary dark:text-gray-500">{offlineConfig.chat_provider} @ {offlineConfig.chat_url}</p>
                    </div>
                    <button
                      onClick={() => setOfflineConfig(prev => ({ ...prev, chat_model: undefined, chat_provider: undefined }))}
                      className="p-1 text-red-500"
                    >
                      <XCircleIcon className="h-5 w-5" />
                    </button>
                  </div>
                ) : (
                  <p className="text-sm text-secondary dark:text-gray-400">Select from available models below</p>
                )}
              </div>
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">Embedding Model</h4>
                {offlineConfig.embedding_model ? (
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-primary-900 dark:text-gray-200">{offlineConfig.embedding_model}</p>
                      <p className="text-xs text-secondary dark:text-gray-500">{offlineConfig.embedding_provider} @ {offlineConfig.embedding_url}</p>
                    </div>
                    <button
                      onClick={() => setOfflineConfig(prev => ({ ...prev, embedding_model: undefined, embedding_provider: undefined }))}
                      className="p-1 text-red-500"
                    >
                      <XCircleIcon className="h-5 w-5" />
                    </button>
                  </div>
                ) : (
                  <p className="text-sm text-secondary dark:text-gray-400">Select from available models below</p>
                )}
              </div>
              <div className="rounded-xl bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 p-4">
                <h4 className="font-medium text-purple-900 dark:text-purple-200 mb-2">Vision Model (Images)</h4>
                {offlineConfig.vision_model ? (
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-purple-900 dark:text-purple-200">{offlineConfig.vision_model}</p>
                      <p className="text-xs text-purple-600 dark:text-purple-400">{offlineConfig.vision_provider} @ {offlineConfig.vision_url}</p>
                    </div>
                    <button
                      onClick={() => setOfflineConfig(prev => ({ ...prev, vision_model: undefined, vision_provider: undefined, vision_url: undefined }))}
                      className="p-1 text-red-500"
                    >
                      <XCircleIcon className="h-5 w-5" />
                    </button>
                  </div>
                ) : (
                  <p className="text-sm text-purple-600 dark:text-purple-400">Select a vision model (LLaVA, etc.) for image processing</p>
                )}
              </div>
              <div className="rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4">
                <h4 className="font-medium text-blue-900 dark:text-blue-200 mb-2">Audio Transcription</h4>
                {offlineConfig.audio_model ? (
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-blue-900 dark:text-blue-200">{offlineConfig.audio_model}</p>
                      <p className="text-xs text-blue-600 dark:text-blue-400">{offlineConfig.audio_provider} @ {offlineConfig.audio_url}</p>
                    </div>
                    <button
                      onClick={() => setOfflineConfig(prev => ({ ...prev, audio_model: undefined, audio_provider: undefined, audio_url: undefined }))}
                      className="p-1 text-red-500"
                    >
                      <XCircleIcon className="h-5 w-5" />
                    </button>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <p className="text-sm text-blue-600 dark:text-blue-400">
                      <span className="font-medium">Built-in Whisper:</span> Audio transcription uses local Whisper automatically in offline mode.
                    </p>
                    <p className="text-xs text-blue-500 dark:text-blue-500">
                      Note: Ollama doesn't have Whisper models. For custom audio servers (faster-whisper), add them as custom endpoints.
                    </p>
                  </div>
                )}
              </div>
            </div>

            <button
              onClick={handleSaveOfflineConfig}
              disabled={isSaving}
              className="mt-4 flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {isSaving ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <CheckCircleIcon className="h-4 w-4" />}
              Save Configuration
            </button>
          </div>

          {/* Discovered Providers */}
          {discovery && (
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Local LLM Providers</h3>
                {discovery.scanned_hosts && discovery.scanned_hosts.length > 0 && (
                  <span className="text-xs text-secondary dark:text-gray-500">
                    Scanned: {discovery.scanned_hosts.join(', ')}
                  </span>
                )}
              </div>
              <div className="space-y-4">
                {discovery.providers.map(provider => (
                  <ProviderCard
                    key={`${provider.id}-${provider.url}`}
                    provider={provider}
                    onSelectModel={selectModel}
                    onTestModel={handleTestModel}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Network Scan Tab */}
      {activeTab === 'network' && (
        <div className="space-y-6">
          {/* Preset Network Ranges */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-2 mb-4">
              <GlobeAltIcon className="h-5 w-5 text-primary" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Network Presets</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              Select network ranges to scan for LLM providers. Common VPN and local network ranges are available.
            </p>
            <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
              {NETWORK_PRESETS.map(preset => (
                <label
                  key={preset.name}
                  className={`flex items-start gap-3 p-3 rounded-xl cursor-pointer transition-colors ${
                    selectedPresets.includes(preset.name)
                      ? 'bg-primary-100 dark:bg-primary-900/30 border-2 border-primary'
                      : 'bg-surface-variant dark:bg-gray-700 border-2 border-transparent hover:border-primary-300'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedPresets.includes(preset.name)}
                    onChange={() => togglePreset(preset.name)}
                    className="mt-1 rounded"
                  />
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-primary-900 dark:text-gray-200 text-sm">{preset.name}</p>
                    <p className="text-xs text-secondary dark:text-gray-500 truncate" title={preset.range}>
                      {preset.range}
                    </p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Custom IP/Range Input */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Custom Network Scan</h3>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">Custom IPs</label>
                <input
                  type="text"
                  value={customIps}
                  onChange={e => setCustomIps(e.target.value)}
                  placeholder="192.168.1.100, 10.0.0.50"
                  className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                />
                <p className="text-xs text-secondary dark:text-gray-500 mt-1">Comma-separated IP addresses</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">IP Range</label>
                <input
                  type="text"
                  value={customRange}
                  onChange={e => setCustomRange(e.target.value)}
                  placeholder="10.8.0.1-100 or 192.168.2.0/24"
                  className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-200"
                />
                <p className="text-xs text-secondary dark:text-gray-500 mt-1">Range format: x.x.x.1-254 or CIDR</p>
              </div>
            </div>
            <button
              onClick={handleNetworkScan}
              disabled={isScanning || (selectedPresets.length === 0 && !customIps && !customRange)}
              className="mt-4 flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {isScanning ? (
                <ArrowPathIcon className="h-4 w-4 animate-spin" />
              ) : (
                <MagnifyingGlassIcon className="h-4 w-4" />
              )}
              {isScanning ? 'Scanning...' : 'Start Network Scan'}
            </button>
          </div>

          {/* Scan Results */}
          {scanResult && (
            <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Scan Results</h3>
                <span className="text-sm text-secondary dark:text-gray-400">
                  Found {scanResult.found_count} providers from {scanResult.scanned_count} IPs
                </span>
              </div>
              {scanResult.found.length > 0 ? (
                <div className="space-y-3">
                  {scanResult.found.map((provider, i) => (
                    <div key={i} className="flex items-center justify-between p-4 rounded-xl bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800">
                      <div className="flex items-center gap-3">
                        <CheckCircleIcon className="h-5 w-5 text-green-500" />
                        <div>
                          <p className="font-medium text-primary-900 dark:text-gray-200">{provider.name}</p>
                          <p className="text-sm text-secondary dark:text-gray-400">{provider.url}</p>
                          <p className="text-xs text-secondary dark:text-gray-500">
                            {provider.models?.length || 0} models available
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => handleAddFromScan(provider)}
                        className="flex items-center gap-2 px-3 py-2 rounded-xl bg-green-600 text-white text-sm font-medium hover:bg-green-700"
                      >
                        <PlusIcon className="h-4 w-4" />
                        Add
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-secondary dark:text-gray-400">
                  <GlobeAltIcon className="h-12 w-12 mx-auto mb-3 text-gray-400" />
                  <p>No LLM providers found in scanned range</p>
                  <p className="text-sm mt-1">Try different IP ranges or check if services are running</p>
                </div>
              )}
            </div>
          )}

          {/* Custom Endpoints */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Custom Endpoints</h3>
            
            {/* Add New Endpoint Form */}
            <div className="grid gap-3 md:grid-cols-4 mb-4 p-4 rounded-xl bg-surface-variant dark:bg-gray-700">
              <input
                type="text"
                value={newEndpoint.name}
                onChange={e => setNewEndpoint(prev => ({ ...prev, name: e.target.value }))}
                placeholder="Endpoint name"
                className="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-primary-900 dark:text-gray-200 text-sm"
              />
              <input
                type="text"
                value={newEndpoint.url}
                onChange={e => setNewEndpoint(prev => ({ ...prev, url: e.target.value }))}
                placeholder="http://192.168.1.100:11434"
                className="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-primary-900 dark:text-gray-200 text-sm"
              />
              <select
                value={newEndpoint.provider_type}
                onChange={e => setNewEndpoint(prev => ({ ...prev, provider_type: e.target.value }))}
                className="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-primary-900 dark:text-gray-200 text-sm"
              >
                <option value="ollama">Ollama</option>
                <option value="openai-compatible">OpenAI Compatible</option>
              </select>
              <button
                onClick={handleAddCustomEndpoint}
                className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary-700"
              >
                <PlusIcon className="h-4 w-4" />
                Add
              </button>
            </div>

            {/* Existing Endpoints */}
            {customEndpoints.length > 0 ? (
              <div className="space-y-2">
                {customEndpoints.map(endpoint => (
                  <div key={endpoint.id} className="flex items-center justify-between p-3 rounded-xl bg-surface-variant dark:bg-gray-700">
                    <div>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{endpoint.name}</p>
                      <p className="text-sm text-secondary dark:text-gray-400">{endpoint.url}</p>
                      <span className="text-xs px-2 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400">
                        {endpoint.provider_type}
                      </span>
                    </div>
                    <button
                      onClick={() => handleDeleteCustomEndpoint(endpoint.id)}
                      className="p-2 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg"
                    >
                      <TrashIcon className="h-5 w-5" />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-secondary dark:text-gray-400 text-center py-4">
                No custom endpoints configured. Add endpoints manually or use network scan to discover them.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Models Tab */}
      {activeTab === 'models' && discovery && (
        <div className="space-y-6">
          {/* Recommendations */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Recommended Models</h3>
            <p className="text-sm text-secondary dark:text-gray-400 mb-4">
              Based on your system ({discovery.resources.ram_total_gb.toFixed(0)} GB RAM, {discovery.resources.cpu_cores} cores)
            </p>
            <div className="space-y-3">
              {discovery.recommendations.map((rec, i) => (
                <RecommendationCard
                  key={i}
                  recommendation={rec}
                  onPull={handlePullModel}
                  isPulling={isPulling === rec.name}
                />
              ))}
            </div>
          </div>

          {/* Installed Models */}
          {discovery.providers.filter(p => p.status === 'available' && p.models.length > 0).map(provider => (
            <div key={provider.id} className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
              <div className="flex items-center gap-2 mb-4">
                <CheckCircleIcon className="h-5 w-5 text-green-500" />
                <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">{provider.name} Models</h3>
              </div>
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {provider.models.map((model, i) => (
                  <div key={i} className="flex items-center justify-between p-3 rounded-xl bg-surface-variant dark:bg-gray-700">
                    <div>
                      <p className="font-medium text-primary-900 dark:text-gray-200">{model.name}</p>
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-xs text-secondary dark:text-gray-500">
                          {model.size_gb ? `${model.size_gb} GB` : ''}
                        </span>
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          model.type === 'vision' ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400' :
                          model.type === 'audio' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400' :
                          model.type === 'video' ? 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400' :
                          model.type === 'embedding' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400' :
                          'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-400'
                        }`}>
                          {model.type || 'chat'}
                        </span>
                      </div>
                    </div>
                    <div className="flex gap-1">
                      <button
                        onClick={() => handleTestModel(provider.id, model.name, model.type || 'chat')}
                        className="p-2 text-primary hover:bg-primary-100 dark:hover:bg-primary-900/30 rounded-lg"
                        title="Test"
                      >
                        <PlayIcon className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => selectModel(provider, model.name, (model.type as 'chat' | 'embedding' | 'vision' | 'audio') || 'chat')}
                        className="p-2 text-green-500 hover:bg-green-100 dark:hover:bg-green-900/30 rounded-lg"
                        title="Select"
                      >
                        <CheckCircleIcon className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Profile Settings Tab */}
      {activeTab === 'profiles' && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
          <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200 mb-4">Profile Configuration</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700">
                  <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Profile</th>
                  <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Database</th>
                  <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Documents Folders</th>
                  <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">LLM Model</th>
                  <th className="text-left py-3 px-4 text-secondary dark:text-gray-400 font-medium">Embedding Model</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(profiles).map(([key, profile]) => (
                  <tr key={key} className="border-b border-gray-100 dark:border-gray-800">
                    <td className="py-3 px-4">
                      <p className="font-medium text-primary-900 dark:text-gray-200">{profile.name}</p>
                      <p className="text-xs text-secondary dark:text-gray-500">{key}</p>
                    </td>
                    <td className="py-3 px-4 text-secondary dark:text-gray-400">{profile.database}</td>
                    <td className="py-3 px-4 text-secondary dark:text-gray-400 max-w-xs truncate">
                      {profile.documents_folders?.join(', ') || 'None'}
                    </td>
                    <td className="py-3 px-4 text-secondary dark:text-gray-400">{profile.llm_model || 'Default'}</td>
                    <td className="py-3 px-4 text-secondary dark:text-gray-400">{profile.embedding_model || 'Default'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-sm text-secondary dark:text-gray-500 mt-4">
            Edit profiles in the Profiles page to configure per-profile models.
          </p>
        </div>
      )}

      {/* Search Settings Tab */}
      {activeTab === 'search' && (
        <div className="space-y-6">
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <MagnifyingGlassIcon className="h-5 w-5 text-primary" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Agent Search Settings</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-6">
              Configure how the chat agent retrieves information from your knowledge base.
            </p>

            <div className="grid gap-6 md:grid-cols-2">
              {/* Default Match Count */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Default Results Count
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Number of document chunks the agent retrieves per search (higher = more context but slower)
                </p>
                <select
                  value={defaultMatchCount}
                  onChange={(e) => setDefaultMatchCount(Number(e.target.value))}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none"
                >
                  {(configOptions?.options.match_count_options || [5, 10, 15, 20, 25, 50, 100]).map((n) => (
                    <option key={n} value={n}>
                      {n} results
                    </option>
                  ))}
                </select>
                {configOptions && defaultMatchCount !== configOptions.current.default_match_count && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-2">
                    Unsaved change (current: {configOptions.current.default_match_count})
                  </p>
                )}
              </div>

              {/* Current Config Display - Now Editable */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <h4 className="text-sm font-medium text-primary-900 dark:text-gray-200 mb-3">Model Configuration</h4>
                <div className="space-y-4">
                  {/* LLM Model */}
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">LLM Model</label>
                    <select
                      value={llmModel}
                      onChange={(e) => setLlmModel(e.target.value)}
                      className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none"
                    >
                      {llmModels.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.id}
                        </option>
                      ))}
                      {/* Add current model if not in list */}
                      {llmModel && !llmModels.find(m => m.id === llmModel) && (
                        <option value={llmModel}>{llmModel}</option>
                      )}
                    </select>
                    {configOptions && llmModel !== configOptions.current.llm_model && (
                      <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                        Changed from: {configOptions.current.llm_model}
                      </p>
                    )}
                  </div>

                  {/* Embedding Model */}
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">Embedding Model</label>
                    <select
                      value={embeddingModel}
                      onChange={(e) => {
                        const selected = e.target.value
                        setEmbeddingModel(selected)
                        // Auto-set dimension based on model
                        const modelInfo = embeddingModels.find(m => m.id === selected)
                        if (modelInfo) {
                          setEmbeddingDimension(modelInfo.dimension)
                        }
                      }}
                      className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none"
                    >
                      {embeddingModels.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.id} ({model.dimension}d)
                        </option>
                      ))}
                      {/* Add current model if not in list */}
                      {embeddingModel && !embeddingModels.find(m => m.id === embeddingModel) && (
                        <option value={embeddingModel}>{embeddingModel}</option>
                      )}
                    </select>
                    {configOptions && embeddingModel !== configOptions.current.embedding_model && (
                      <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                        Changed from: {configOptions.current.embedding_model}
                      </p>
                    )}
                  </div>

                  {/* Embedding Dimensions */}
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">Embedding Dimensions</label>
                    <select
                      value={embeddingDimension}
                      onChange={(e) => setEmbeddingDimension(Number(e.target.value))}
                      className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none"
                    >
                      {(configOptions?.options.embedding_dimensions || [256, 512, 768, 1024, 1536, 3072]).map((dim) => (
                        <option key={dim} value={dim}>
                          {dim} dimensions
                        </option>
                      ))}
                    </select>
                    {configOptions && embeddingDimension !== configOptions.current.embedding_dimension && (
                      <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                        Changed from: {configOptions.current.embedding_dimension}
                      </p>
                    )}
                    <p className="text-xs text-secondary dark:text-gray-500 mt-1">
                      Note: Changing dimensions requires re-indexing all documents
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Save Button */}
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={async () => {
                  setIsSavingSearch(true)
                  setMessage(null)
                  try {
                    await systemApi.saveConfigToDb({ 
                      default_match_count: defaultMatchCount,
                      llm_model: llmModel,
                      embedding_model: embeddingModel,
                      embedding_dimension: embeddingDimension
                    })
                    setMessage({ type: 'success', text: 'Configuration saved successfully! Changes will persist across restarts.' })
                    // Refresh config options
                    const newConfig = await systemApi.getConfigOptions()
                    setConfigOptions(newConfig)
                    setLlmModel(newConfig.current.llm_model)
                    setEmbeddingModel(newConfig.current.embedding_model)
                    setEmbeddingDimension(newConfig.current.embedding_dimension)
                    setDefaultMatchCount(newConfig.current.default_match_count)
                  } catch (err) {
                    setMessage({ type: 'error', text: 'Failed to save configuration' })
                  } finally {
                    setIsSavingSearch(false)
                  }
                }}
                disabled={isSavingSearch || !!(configOptions && 
                  defaultMatchCount === configOptions.current.default_match_count &&
                  llmModel === configOptions.current.llm_model &&
                  embeddingModel === configOptions.current.embedding_model &&
                  embeddingDimension === configOptions.current.embedding_dimension
                )}
                className="flex items-center gap-2 rounded-xl bg-primary px-6 py-2 font-medium text-white transition-all hover:bg-primary-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
              >
                {isSavingSearch && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
                Save Configuration
              </button>
            </div>
          </div>

          {/* Info Card */}
          <div className="rounded-2xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-6">
            <h4 className="text-sm font-medium text-blue-900 dark:text-blue-200 mb-2">How it works</h4>
            <ul className="text-sm text-blue-800 dark:text-blue-300 space-y-1">
              <li>• The agent uses this setting when searching your knowledge base for relevant context</li>
              <li>• Higher values provide more comprehensive results but may slow down responses</li>
              <li>• Lower values are faster but may miss relevant information</li>
              <li>• Recommended: 10-20 for most use cases, 50+ for thorough research queries</li>
            </ul>
          </div>
        </div>
      )}

      {/* Agent Performance Tab */}
      {activeTab === 'agent' && (
        <div className="space-y-6">
          {/* Per-Session Settings */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <BoltIcon className="h-5 w-5 text-primary" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Per-Session Settings</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-6">
              Configure how the agent processes requests within a single chat session.
            </p>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
              {/* Parallel Workers */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Parallel Workers per Chat
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Number of concurrent worker tasks per chat session (1-20)
                </p>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={agentConfig.parallel_workers}
                  onChange={(e) => handleAgentConfigChange('parallel_workers', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.parallel_workers ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.parallel_workers && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.parallel_workers}</p>
                )}
              </div>

              {/* Max Iterations */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Max Orchestrator Iterations
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Maximum refine cycles before stopping (1-10)
                </p>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={agentConfig.max_iterations}
                  onChange={(e) => handleAgentConfigChange('max_iterations', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.max_iterations ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.max_iterations && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.max_iterations}</p>
                )}
              </div>

              {/* Max Sources Per Search */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Max Sources per Search
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Data sources searched in parallel (1-50)
                </p>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={agentConfig.max_sources_per_search}
                  onChange={(e) => handleAgentConfigChange('max_sources_per_search', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.max_sources_per_search ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.max_sources_per_search && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.max_sources_per_search}</p>
                )}
              </div>
            </div>
          </div>

          {/* Global Pool Settings */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <CpuChipIcon className="h-5 w-5 text-orange-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Global Pool Settings</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-6">
              Limit total concurrent operations across all users to manage server resources.
            </p>

            <div className="grid gap-6 md:grid-cols-2">
              {/* Global Max Orchestrators */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Max Concurrent Orchestrators (All Users)
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Total orchestrator instances allowed simultaneously (1-50)
                </p>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={agentConfig.global_max_orchestrators}
                  onChange={(e) => handleAgentConfigChange('global_max_orchestrators', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.global_max_orchestrators ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.global_max_orchestrators && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.global_max_orchestrators}</p>
                )}
              </div>

              {/* Global Max Workers */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Max Concurrent Workers (All Users)
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Total worker tasks allowed simultaneously (1-100)
                </p>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={agentConfig.global_max_workers}
                  onChange={(e) => handleAgentConfigChange('global_max_workers', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.global_max_workers ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.global_max_workers && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.global_max_workers}</p>
                )}
              </div>
            </div>
          </div>

          {/* Timeout Settings */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <ArrowPathIcon className="h-5 w-5 text-red-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Timeout Settings</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-6">
              Set timeouts to prevent runaway requests from blocking resources.
            </p>

            <div className="grid gap-6 md:grid-cols-3">
              {/* Worker Timeout */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Worker Timeout (seconds)
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Max time for a single worker task (10-300)
                </p>
                <input
                  type="number"
                  min={10}
                  max={300}
                  value={agentConfig.worker_timeout}
                  onChange={(e) => handleAgentConfigChange('worker_timeout', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.worker_timeout ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.worker_timeout && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.worker_timeout}</p>
                )}
              </div>

              {/* Orchestrator Timeout */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Orchestrator Timeout (seconds)
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Max time for orchestrator phases (30-600)
                </p>
                <input
                  type="number"
                  min={30}
                  max={600}
                  value={agentConfig.orchestrator_timeout}
                  onChange={(e) => handleAgentConfigChange('orchestrator_timeout', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.orchestrator_timeout ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.orchestrator_timeout && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.orchestrator_timeout}</p>
                )}
              </div>

              {/* Total Timeout */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Total Request Timeout (seconds)
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Max total time for complete request (60-3600)
                </p>
                <input
                  type="number"
                  min={60}
                  max={3600}
                  value={agentConfig.total_timeout}
                  onChange={(e) => handleAgentConfigChange('total_timeout', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.total_timeout ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.total_timeout && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.total_timeout}</p>
                )}
              </div>
            </div>
          </div>

          {/* Mode & Optimization Settings */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
            <div className="flex items-center gap-3 mb-4">
              <Cog6ToothIcon className="h-5 w-5 text-green-500" />
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">Mode & Optimization</h3>
            </div>
            <p className="text-sm text-secondary dark:text-gray-400 mb-6">
              Fine-tune how the agent decides to process queries.
            </p>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
              {/* Default Mode */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Default Agent Mode
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  How the agent processes queries by default
                </p>
                <select
                  value={agentConfig.default_mode}
                  onChange={(e) => setAgentConfig(prev => ({ ...prev, default_mode: e.target.value }))}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none"
                >
                  <option value="auto">Auto (Adaptive)</option>
                  <option value="fast">Fast (Skip orchestration)</option>
                  <option value="thinking">Thinking (Full orchestration)</option>
                </select>
              </div>

              {/* Auto Fast Threshold */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Auto-Fast Query Threshold
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Use fast mode for queries shorter than this (chars)
                </p>
                <input
                  type="number"
                  min={10}
                  max={500}
                  value={agentConfig.auto_fast_threshold}
                  onChange={(e) => handleAgentConfigChange('auto_fast_threshold', Number(e.target.value))}
                  className={`w-full rounded-lg border ${agentConfigErrors.auto_fast_threshold ? 'border-red-500 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-white dark:bg-gray-800 px-3 py-2 text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none`}
                />
                {agentConfigErrors.auto_fast_threshold && (
                  <p className="mt-1 text-xs text-red-500">{agentConfigErrors.auto_fast_threshold}</p>
                )}
              </div>

              {/* Skip Evaluation */}
              <div className="rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  Skip Evaluation Phase
                </label>
                <p className="text-xs text-secondary dark:text-gray-500 mb-3">
                  Skip result evaluation for faster responses
                </p>
                <label className="relative inline-flex items-center cursor-pointer mt-2">
                  <input
                    type="checkbox"
                    checked={agentConfig.skip_evaluation}
                    onChange={(e) => setAgentConfig(prev => ({ ...prev, skip_evaluation: e.target.checked }))}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-gray-300 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer dark:bg-gray-600 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                  <span className="ml-3 text-sm text-secondary dark:text-gray-400">
                    {agentConfig.skip_evaluation ? 'Enabled (Faster)' : 'Disabled (More thorough)'}
                  </span>
                </label>
              </div>
            </div>
          </div>

          {/* Save Button */}
          <div className="flex justify-end gap-3">
            <button
              onClick={handleSaveAgentConfig}
              disabled={isSavingAgentConfig || Object.values(agentConfigErrors).some(e => e)}
              className="flex items-center gap-2 rounded-xl bg-primary px-6 py-2 font-medium text-white transition-all hover:bg-primary-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
            >
              {isSavingAgentConfig && <ArrowPathIcon className="h-4 w-4 animate-spin" />}
              Save Agent Configuration
            </button>
          </div>

          {/* Info Card */}
          <div className="rounded-2xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-6">
            <h4 className="text-sm font-medium text-blue-900 dark:text-blue-200 mb-2">Performance Tips</h4>
            <ul className="text-sm text-blue-800 dark:text-blue-300 space-y-1">
              <li>• <strong>Fast mode</strong> skips orchestration for simple queries (~3-5x faster)</li>
              <li>• <strong>Increase parallel workers</strong> if you have many data sources to search</li>
              <li>• <strong>Skip evaluation</strong> trades quality for speed on straightforward queries</li>
              <li>• <strong>Lower timeouts</strong> prevent stuck requests but may cut off complex queries</li>
              <li>• <strong>Global limits</strong> protect your server when many users are active</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}

function ProviderCard({ provider, onSelectModel, onTestModel }: {
  provider: LocalProvider
  onSelectModel: (provider: LocalProvider, modelName: string, type: 'chat' | 'embedding' | 'vision' | 'audio') => void
  onTestModel: (providerId: string, modelName: string, modelType: string) => void
}) {
  const [expanded, setExpanded] = useState(provider.status === 'available')
  
  return (
    <div className={`rounded-xl border ${
      provider.status === 'available'
        ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/10'
        : 'border-gray-200 dark:border-gray-700 bg-surface-variant dark:bg-gray-700'
    }`}>
      <div
        className="flex items-center justify-between p-4 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          {provider.status === 'available' ? (
            <CheckCircleIcon className="h-5 w-5 text-green-500" />
          ) : (
            <XCircleIcon className="h-5 w-5 text-gray-400" />
          )}
          <div>
            <p className="font-medium text-primary-900 dark:text-gray-200">{provider.name}</p>
            <p className="text-xs text-secondary dark:text-gray-500">{provider.url}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {provider.status === 'available' && (
            <>
              <span className="px-2 py-1 text-xs font-medium bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200 rounded">
                {provider.models.length} models
              </span>
              {provider.supports_vision && (
                <span className="px-2 py-1 text-xs font-medium bg-purple-200 dark:bg-purple-800 text-purple-800 dark:text-purple-200 rounded">
                  Vision
                </span>
              )}
              {provider.supports_audio && (
                <span className="px-2 py-1 text-xs font-medium bg-blue-200 dark:bg-blue-800 text-blue-800 dark:text-blue-200 rounded">
                  Audio
                </span>
              )}
              {provider.supports_video && (
                <span className="px-2 py-1 text-xs font-medium bg-indigo-200 dark:bg-indigo-800 text-indigo-800 dark:text-indigo-200 rounded">
                  Video
                </span>
              )}
            </>
          )}
          {provider.status === 'unavailable' && (
            <span className="text-xs text-gray-500">{provider.error}</span>
          )}
        </div>
      </div>
      
      {expanded && provider.status === 'available' && provider.models.length > 0 && (
        <div className="border-t border-green-200 dark:border-green-800 p-4">
          <div className="grid gap-2 md:grid-cols-2">
            {provider.models.map((model, i) => (
              <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-white dark:bg-gray-800">
                <div>
                  <p className="text-sm font-medium text-primary-900 dark:text-gray-200">{model.name}</p>
                  <div className="flex items-center gap-1 flex-wrap">
                    <span className="text-xs text-secondary dark:text-gray-500">
                      {model.size_gb ? `${model.size_gb} GB` : ''}
                    </span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      model.type === 'vision' ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400' :
                      model.type === 'audio' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400' :
                      model.type === 'video' ? 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400' :
                      model.type === 'embedding' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400' :
                      'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-400'
                    }`}>
                      {model.type || 'chat'}
                    </span>
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    onClick={(e) => { e.stopPropagation(); onTestModel(provider.id, model.name, model.type || 'chat') }}
                    className="p-1.5 text-primary hover:bg-primary-100 dark:hover:bg-primary-900/30 rounded"
                    title="Test"
                  >
                    <PlayIcon className="h-4 w-4" />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onSelectModel(provider, model.name, (model.type as 'chat' | 'embedding' | 'vision' | 'audio') || 'chat') }}
                    className="p-1.5 text-green-500 hover:bg-green-100 dark:hover:bg-green-900/30 rounded"
                    title="Select"
                  >
                    <CheckCircleIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function RecommendationCard({ recommendation, onPull, isPulling }: {
  recommendation: ModelRecommendation
  onPull: (providerId: string, modelName: string) => void
  isPulling: boolean
}) {
  return (
    <div className={`flex items-center justify-between p-4 rounded-xl ${
      recommendation.warning
        ? 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800'
        : 'bg-surface-variant dark:bg-gray-700'
    }`}>
      <div className="flex items-center gap-4">
        <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${
          recommendation.is_installed
            ? 'bg-green-100 dark:bg-green-900/30'
            : 'bg-gray-100 dark:bg-gray-600'
        }`}>
          {recommendation.is_installed ? (
            <CheckCircleIcon className="h-6 w-6 text-green-500" />
          ) : (
            <CloudIcon className="h-6 w-6 text-gray-400" />
          )}
        </div>
        <div>
          <div className="flex items-center gap-2">
            <p className="font-medium text-primary-900 dark:text-gray-200">{recommendation.name}</p>
            <span className={`px-2 py-0.5 text-xs rounded font-medium ${
              recommendation.type === 'embedding'
                ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                : recommendation.type === 'vision'
                ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400'
                : recommendation.type === 'audio'
                ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
                : 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400'
            }`}>
              {recommendation.type}
            </span>
          </div>
          <p className="text-sm text-secondary dark:text-gray-400">
            {recommendation.size_gb} GB • {recommendation.provider}
          </p>
          {recommendation.warning && (
            <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
              <ExclamationTriangleIcon className="h-3 w-3 inline mr-1" />
              {recommendation.warning}
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3">
        {/* Performance Score */}
        <div className="text-right">
          <div className="flex items-center gap-1">
            <BoltIcon className={`h-4 w-4 ${
              recommendation.performance_score >= 80 ? 'text-green-500' :
              recommendation.performance_score >= 50 ? 'text-amber-500' : 'text-red-500'
            }`} />
            <span className={`font-semibold ${
              recommendation.performance_score >= 80 ? 'text-green-600 dark:text-green-400' :
              recommendation.performance_score >= 50 ? 'text-amber-600 dark:text-amber-400' : 'text-red-600 dark:text-red-400'
            }`}>
              {recommendation.performance_score}%
            </span>
          </div>
          <p className="text-xs text-secondary dark:text-gray-500">Performance</p>
        </div>
        
        {/* Action */}
        {!recommendation.is_installed && (
          <button
            onClick={() => onPull(recommendation.provider, recommendation.name)}
            disabled={isPulling}
            className="flex items-center gap-2 px-3 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
          >
            {isPulling ? (
              <ArrowPathIcon className="h-4 w-4 animate-spin" />
            ) : (
              <ArrowDownTrayIcon className="h-4 w-4" />
            )}
            Pull
          </button>
        )}
      </div>
    </div>
  )
}
