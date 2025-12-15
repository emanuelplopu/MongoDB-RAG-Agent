import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
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
} from '@heroicons/react/24/outline'
import {
  localLlmApi, systemApi, profilesApi,
  DiscoveryResult, LocalProvider, ModelRecommendation, OfflineModeConfig,
  Profile, SystemStats, CustomEndpoint, NetworkScanResult
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
  const [activeTab, setActiveTab] = useState<'offline' | 'network' | 'models' | 'profiles'>('offline')
  
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
      const [statsRes, profilesRes, configRes, endpointsRes] = await Promise.all([
        systemApi.stats(),
        profilesApi.list(),
        localLlmApi.getOfflineConfig(),
        localLlmApi.getCustomEndpoints()
      ])
      setSystemStats(statsRes)
      setProfiles(profilesRes.profiles)
      setOfflineConfig(configRes)
      setCustomEndpoints(endpointsRes.endpoints)
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
      navigate('/')
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
          { id: 'offline', label: 'Offline Mode', icon: WifiIcon },
          { id: 'network', label: 'Network Scan', icon: GlobeAltIcon },
          { id: 'models', label: 'Local Models', icon: CpuChipIcon },
          { id: 'profiles', label: 'Profile Settings', icon: Cog6ToothIcon },
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
