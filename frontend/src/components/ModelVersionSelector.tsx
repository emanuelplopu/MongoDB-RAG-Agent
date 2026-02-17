/**
 * Model Version Selector Component
 * 
 * Allows users to browse, search, and switch between different model versions
 * with detailed information about capabilities, pricing, and compatibility.
 */

import React, { useState, useEffect } from 'react'
import { 
  MagnifyingGlassIcon, 
  ArrowsRightLeftIcon,
  InformationCircleIcon,
  CurrencyDollarIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  XMarkIcon
} from '@heroicons/react/24/outline'
import { modelVersionsApi, ModelVersion, ModelListResponse } from '../api/modelVersions'

interface ModelVersionSelectorProps {
  currentOrchestrator?: string
  currentWorker?: string
  currentEmbedding?: string
  onModelChange?: (modelType: 'orchestrator' | 'worker' | 'embedding', modelId: string) => void
  showSwitchButton?: boolean
  className?: string
}

const ModelVersionSelector: React.FC<ModelVersionSelectorProps> = ({
  currentOrchestrator = '',
  currentWorker = '',
  currentEmbedding = '',
  onModelChange,
  showSwitchButton = true,
  className = ''
}) => {
  const [models, setModels] = useState<ModelVersion[]>([])
  const [filteredModels, setFilteredModels] = useState<ModelVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<string>('all')
  const [selectedType, setSelectedType] = useState<string>('all')
  const [showDeprecated, setShowDeprecated] = useState(false)
  const [sortBy, setSortBy] = useState<'release_date' | 'cost' | 'name'>('release_date')
  
  const [switching, setSwitching] = useState(false)
  const [switchSuccess, setSwitchSuccess] = useState<string | null>(null)
  const [switchError, setSwitchError] = useState<string | null>(null)

  // Load models on mount
  useEffect(() => {
    loadModels()
  }, [])

  // Filter models when search term or filters change
  useEffect(() => {
    filterModels()
  }, [models, searchTerm, selectedProvider, selectedType, showDeprecated, sortBy])

  const loadModels = async () => {
    try {
      setLoading(true)
      setError(null)
      const response: ModelListResponse = await modelVersionsApi.listModels({
        show_deprecated: showDeprecated,
        sort_by: sortBy,
        limit: 100
      })
      setModels(response.models)
    } catch (err: any) {
      setError(err.message || 'Failed to load models')
    } finally {
      setLoading(false)
    }
  }

  const filterModels = () => {
    let filtered = [...models]
    
    // Apply search filter
    if (searchTerm) {
      const term = searchTerm.toLowerCase()
      filtered = filtered.filter(model => 
        model.name.toLowerCase().includes(term) ||
        model.id.toLowerCase().includes(term) ||
        model.provider.toLowerCase().includes(term) ||
        model.capabilities.some(cap => cap.toLowerCase().includes(term))
      )
    }
    
    // Apply provider filter
    if (selectedProvider !== 'all') {
      filtered = filtered.filter(model => model.provider === selectedProvider)
    }
    
    // Apply type filter
    if (selectedType !== 'all') {
      filtered = filtered.filter(model => model.type === selectedType)
    }
    
    // Apply sorting
    filtered.sort((a, b) => {
      if (sortBy === 'release_date') {
        const dateA = a.release_date ? new Date(a.release_date).getTime() : 0
        const dateB = b.release_date ? new Date(b.release_date).getTime() : 0
        return dateB - dateA
      } else if (sortBy === 'cost') {
        const costA = (a.pricing_input || 0) + (a.pricing_output || 0)
        const costB = (b.pricing_input || 0) + (b.pricing_output || 0)
        return costA - costB
      } else {
        return a.name.localeCompare(b.name)
      }
    })
    
    setFilteredModels(filtered)
  }

  const handleSwitchModel = async (modelType: 'orchestrator' | 'worker' | 'embedding', modelId: string) => {
    if (!onModelChange) return
    
    try {
      setSwitching(true)
      setSwitchError(null)
      setSwitchSuccess(null)
      
      // Call the parent callback
      onModelChange(modelType, modelId)
      
      setSwitchSuccess(`Successfully switched ${modelType} to ${modelId}`)
      setTimeout(() => setSwitchSuccess(null), 3000)
    } catch (err: any) {
      setSwitchError(err.message || `Failed to switch ${modelType} model`)
    } finally {
      setSwitching(false)
    }
  }

  const getProviderColor = (provider: string) => {
    switch (provider) {
      case 'openai': return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
      case 'google': return 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400'
      case 'anthropic': return 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400'
      default: return 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'
    }
  }

  const getTypeColor = (type: string) => {
    switch (type) {
      case 'chat': return 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400'
      case 'embedding': return 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400'
      default: return 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'
    }
  }

  const getCapabilityIcon = (capability: string) => {
    switch (capability) {
      case 'multimodal': return '🖼️'
      case 'reasoning': return '🧠'
      case 'code_generation': return '💻'
      case 'function_calling': return '⚙️'
      case 'audio_input': return '🎤'
      case 'audio_output': return '🔊'
      default: return '✨'
    }
  }

  if (loading) {
    return (
      <div className={`flex items-center justify-center p-8 ${className}`}>
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
        <span className="ml-3 text-primary">Loading models...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className={`p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 ${className}`}>
        <div className="flex items-center">
          <ExclamationTriangleIcon className="h-5 w-5 text-red-500 mr-2" />
          <span className="text-red-700 dark:text-red-300">Error: {error}</span>
        </div>
        <button
          onClick={loadModels}
          className="mt-2 px-3 py-1 bg-red-100 dark:bg-red-800 text-red-700 dark:text-red-300 rounded-md hover:bg-red-200 dark:hover:bg-red-700 transition-colors"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className={`bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 ${className}`}>
      {/* Header */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center">
            <ArrowsRightLeftIcon className="h-5 w-5 mr-2 text-primary" />
            Model Version Manager
          </h3>
          
          {showSwitchButton && (
            <div className="flex items-center space-x-2">
              {switchSuccess && (
                <div className="flex items-center px-3 py-1 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 rounded-full text-sm">
                  <CheckCircleIcon className="h-4 w-4 mr-1" />
                  {switchSuccess}
                </div>
              )}
              {switchError && (
                <div className="flex items-center px-3 py-1 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-full text-sm">
                  <XMarkIcon className="h-4 w-4 mr-1" />
                  {switchError}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Filters */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="relative">
            <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search models..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full pl-10 pr-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-primary focus:border-transparent"
            />
          </div>
          
          <select
            value={selectedProvider}
            onChange={(e) => setSelectedProvider(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-primary focus:border-transparent"
          >
            <option value="all">All Providers</option>
            <option value="openai">OpenAI</option>
            <option value="google">Google</option>
            <option value="anthropic">Anthropic</option>
          </select>
          
          <select
            value={selectedType}
            onChange={(e) => setSelectedType(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-primary focus:border-transparent"
          >
            <option value="all">All Types</option>
            <option value="chat">Chat</option>
            <option value="embedding">Embedding</option>
          </select>
          
          <div className="flex items-center space-x-2">
            <input
              type="checkbox"
              id="show-deprecated"
              checked={showDeprecated}
              onChange={(e) => setShowDeprecated(e.target.checked)}
              className="rounded border-gray-300 text-primary focus:ring-primary"
            />
            <label htmlFor="show-deprecated" className="text-sm text-gray-700 dark:text-gray-300">
              Show Deprecated
            </label>
          </div>
        </div>
        
        <div className="mt-3 flex items-center justify-between">
          <div className="text-sm text-gray-500 dark:text-gray-400">
            Showing {filteredModels.length} of {models.length} models
          </div>
          
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as any)}
            className="px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          >
            <option value="release_date">Sort by Release Date</option>
            <option value="cost">Sort by Cost</option>
            <option value="name">Sort by Name</option>
          </select>
        </div>
      </div>

      {/* Model List */}
      <div className="max-h-96 overflow-y-auto">
        {filteredModels.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <InformationCircleIcon className="h-12 w-12 mx-auto mb-3 text-gray-300 dark:text-gray-600" />
            <p>No models found matching your criteria</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {filteredModels.map((model) => (
              <div key={model.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-750/50 transition-colors">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <h4 className="font-medium text-gray-900 dark:text-white">{model.name}</h4>
                      <span className={`px-2 py-1 text-xs rounded-full ${getProviderColor(model.provider)}`}>
                        {model.provider}
                      </span>
                      <span className={`px-2 py-1 text-xs rounded-full ${getTypeColor(model.type)}`}>
                        {model.type}
                      </span>
                      {model.is_deprecated && (
                        <span className="px-2 py-1 text-xs rounded-full bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400">
                          Deprecated
                        </span>
                      )}
                      {model.is_experimental && (
                        <span className="px-2 py-1 text-xs rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400">
                          Experimental
                        </span>
                      )}
                    </div>
                    
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
                      {model.id} • Version {model.version}
                      {model.release_date && ` • Released ${new Date(model.release_date).toLocaleDateString()}`}
                    </p>
                    
                    <div className="flex flex-wrap gap-1 mb-2">
                      {model.capabilities.map((cap) => (
                        <span 
                          key={cap} 
                          className="inline-flex items-center px-2 py-1 text-xs rounded-md bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300"
                          title={cap}
                        >
                          {getCapabilityIcon(cap)} {cap.replace('_', ' ')}
                        </span>
                      ))}
                    </div>
                    
                    <div className="flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400">
                      <span className="flex items-center">
                        <ClockIcon className="h-4 w-4 mr-1" />
                        {model.context_window.toLocaleString()} tokens context
                      </span>
                      {model.pricing_input !== null && (
                        <span className="flex items-center">
                          <CurrencyDollarIcon className="h-4 w-4 mr-1" />
                          ${(model.pricing_input + (model.pricing_output || 0)).toFixed(3)}/1K tokens
                        </span>
                      )}
                    </div>
                  </div>
                  
                  {showSwitchButton && (
                    <div className="flex flex-col gap-2 ml-4">
                      <button
                        onClick={() => handleSwitchModel('orchestrator', model.id)}
                        disabled={switching || model.id === currentOrchestrator}
                        className={`px-3 py-1 text-xs rounded-md transition-colors ${
                          model.id === currentOrchestrator
                            ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 cursor-not-allowed'
                            : 'bg-blue-100 text-blue-800 hover:bg-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:hover:bg-blue-800/40'
                        }`}
                      >
                        {model.id === currentOrchestrator ? 'Current Orchestrator' : 'Set Orchestrator'}
                      </button>
                      
                      <button
                        onClick={() => handleSwitchModel('worker', model.id)}
                        disabled={switching || model.id === currentWorker}
                        className={`px-3 py-1 text-xs rounded-md transition-colors ${
                          model.id === currentWorker
                            ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 cursor-not-allowed'
                            : 'bg-purple-100 text-purple-800 hover:bg-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:hover:bg-purple-800/40'
                        }`}
                      >
                        {model.id === currentWorker ? 'Current Worker' : 'Set Worker'}
                      </button>
                      
                      {model.type === 'embedding' && (
                        <button
                          onClick={() => handleSwitchModel('embedding', model.id)}
                          disabled={switching || model.id === currentEmbedding}
                          className={`px-3 py-1 text-xs rounded-md transition-colors ${
                            model.id === currentEmbedding
                              ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 cursor-not-allowed'
                              : 'bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:hover:bg-amber-800/40'
                          }`}
                        >
                          {model.id === currentEmbedding ? 'Current Embedding' : 'Set Embedding'}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default ModelVersionSelector