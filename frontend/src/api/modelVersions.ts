/**
 * Model Version Management API Client
 * 
 * Provides API functions for managing and switching between different model versions.
 */

import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true
})

// Add auth interceptor
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// ==================== Types ====================

export interface ModelVersion {
  id: string
  name: string
  provider: string
  type: string
  version: string
  release_date: string | null
  context_window: number
  max_output_tokens: number
  capabilities: string[]
  pricing_input: number | null
  pricing_output: number | null
  is_deprecated: boolean
  is_experimental: boolean
  parameter_mapping: Record<string, string>
  default_parameters: Record<string, any>
}

export interface ModelListResponse {
  models: ModelVersion[]
  total: number
  provider_filter: string | null
  capability_filter: string | null
  type_filter: string | null
}

export interface ModelSwitchRequest {
  orchestrator_model?: string
  orchestrator_provider?: string
  worker_model?: string
  worker_provider?: string
  embedding_model?: string
  embedding_provider?: string
}

export interface ModelCompatibilityCheck {
  model_id: string
  parameters: Record<string, any>
}

export interface CompatibilityResult {
  model_id: string
  is_compatible: boolean
  incompatible_parameters: string[]
  suggested_mappings: Record<string, string>
  warnings: string[]
}

export interface ModelRecommendation {
  model: ModelVersion
  score: number
  reasons: string[]
  estimated_cost_per_1k_tokens: number | null
}

// ==================== API Functions ====================

export const modelVersionsApi = {
  /**
   * List all available model versions with filtering and sorting options.
   */
  listModels: async (params?: {
    provider?: string
    capability?: string
    model_type?: string
    show_deprecated?: boolean
    show_experimental?: boolean
    sort_by?: string
    limit?: number
  }): Promise<ModelListResponse> => {
    const response = await api.get('/model-versions', { params })
    return response.data
  },

  /**
   * Get detailed information about a specific model version.
   */
  getModelDetails: async (modelId: string): Promise<ModelVersion> => {
    const response = await api.get(`/model-versions/${modelId}`)
    return response.data
  },

  /**
   * Get the latest released models across all providers.
   */
  getLatestModels: async (limit: number = 10): Promise<ModelListResponse> => {
    const response = await api.get('/model-versions/latest', { params: { limit } })
    return response.data
  },

  /**
   * Get the most cost-effective models based on pricing.
   */
  getCostEffectiveModels: async (limit: number = 10): Promise<ModelListResponse> => {
    const response = await api.get('/model-versions/cost-effective', { params: { limit } })
    return response.data
  },

  /**
   * Switch to different model versions for orchestrator, worker, and embedding.
   */
  switchModelVersions: async (switchRequest: ModelSwitchRequest): Promise<{
    success: boolean
    message: string
    updates: Record<string, string>
    warnings: string[]
  }> => {
    const response = await api.post('/model-versions/switch', switchRequest)
    return response.data
  },

  /**
   * Check if a model is compatible with specific parameters.
   */
  checkModelCompatibility: async (
    modelId: string,
    parameters: Record<string, any>
  ): Promise<CompatibilityResult> => {
    const response = await api.post('/model-versions/check-compatibility', {
      model_id: modelId,
      parameters
    })
    return response.data
  },

  /**
   * Get model recommendations based on task requirements and constraints.
   */
  getModelRecommendations: async (params?: {
    task_type?: string
    budget_limit?: number
    context_required?: number
    capabilities_required?: string[]
  }): Promise<ModelRecommendation[]> => {
    const response = await api.get('/model-versions/recommendations', { params })
    return response.data
  },

  /**
   * Get currently configured model versions.
   */
  getCurrentModels: async (): Promise<{
    orchestrator: {
      model: string
      provider: string
      details: ModelVersion | null
    }
    worker: {
      model: string
      provider: string
      details: ModelVersion | null
    }
    embedding: {
      model: string
      provider: string
      details: ModelVersion | null
    }
  }> => {
    const response = await api.get('/model-versions/current')
    return response.data
  }
}

export default modelVersionsApi