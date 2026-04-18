import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.fn()
const mockPost = vi.fn()
const requestUse = vi.fn()
const responseUse = vi.fn()
const create = vi.fn(() => ({
  get: mockGet,
  post: mockPost,
  interceptors: {
    request: { use: requestUse },
    response: { use: responseUse },
  },
}))

vi.mock('axios', () => ({
  default: { create },
}))

async function loadModule() {
  const module = await import('./modelVersions')
  return module.default
}

describe('modelVersionsApi', () => {
  beforeEach(() => {
    vi.resetModules()
    mockGet.mockReset()
    mockPost.mockReset()
    create.mockClear()
    requestUse.mockClear()
    responseUse.mockClear()
    localStorage.clear()
    window.location.hash = ''
  })

  it('configures the axios client and auth interceptors', async () => {
    const modelVersionsApi = await loadModule()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(create).toHaveBeenCalledWith({
      baseURL: 'http://localhost:8000/api/v1',
      timeout: 30000,
      withCredentials: true,
    })
    expect(requestUse).toHaveBeenCalledTimes(1)
    expect(responseUse).toHaveBeenCalledTimes(1)

    localStorage.setItem('token', 'secret-token')
    const requestInterceptor = requestUse.mock.calls[0][0] as (config: {
      headers: Record<string, string>
    }) => { headers: Record<string, string> }

    const config = requestInterceptor({ headers: {} })
    expect(config.headers.Authorization).toBe('Bearer secret-token')

    const responseErrorInterceptor = responseUse.mock.calls[0][1] as (error: {
      response?: { status?: number }
    }) => Promise<unknown>

    localStorage.setItem('token', 'secret-token')
    await expect(responseErrorInterceptor({ response: { status: 500 } })).rejects.toEqual({
      response: { status: 500 },
    })
    expect(localStorage.getItem('token')).toBe('secret-token')

    localStorage.setItem('token', 'secret-token')
    await expect(responseErrorInterceptor({ response: { status: 401 } })).rejects.toEqual({
      response: { status: 401 },
    })
    expect(localStorage.getItem('token')).toBeNull()
    consoleError.mockRestore()
  })

  it('lists model versions with filters', async () => {
    const modelVersionsApi = await loadModule()
    const response = { data: { models: [], total: 0, provider_filter: 'openai', capability_filter: null, type_filter: null } }
    mockGet.mockResolvedValue(response)

    await expect(
      modelVersionsApi.listModels({
        provider: 'openai',
        capability: 'vision',
        model_type: 'chat',
        show_deprecated: false,
        show_experimental: true,
        sort_by: 'release_date',
        limit: 25,
      })
    ).resolves.toEqual(response.data)

    expect(mockGet).toHaveBeenCalledWith('/model-versions', {
      params: {
        provider: 'openai',
        capability: 'vision',
        model_type: 'chat',
        show_deprecated: false,
        show_experimental: true,
        sort_by: 'release_date',
        limit: 25,
      },
    })
  })

  it('fetches individual and derived model lists', async () => {
    const modelVersionsApi = await loadModule()
    const details = { data: { id: 'gpt-4.1', name: 'GPT-4.1' } }
    const latest = { data: { models: [{ id: 'newest' }], total: 1, provider_filter: null, capability_filter: null, type_filter: null } }
    const costEffective = { data: { models: [{ id: 'cheap' }], total: 1, provider_filter: null, capability_filter: null, type_filter: null } }
    const current = {
      data: {
        orchestrator: { model: 'gpt-4.1', provider: 'openai', details: null },
        worker: { model: 'gpt-4o-mini', provider: 'openai', details: null },
        embedding: { model: 'text-embedding-3-small', provider: 'openai', details: null },
      },
    }

    mockGet
      .mockResolvedValueOnce(details)
      .mockResolvedValueOnce(latest)
      .mockResolvedValueOnce(costEffective)
      .mockResolvedValueOnce(current)

    await expect(modelVersionsApi.getModelDetails('gpt-4.1')).resolves.toEqual(details.data)
    await expect(modelVersionsApi.getLatestModels(5)).resolves.toEqual(latest.data)
    await expect(modelVersionsApi.getCostEffectiveModels(3)).resolves.toEqual(costEffective.data)
    await expect(modelVersionsApi.getCurrentModels()).resolves.toEqual(current.data)

    expect(mockGet).toHaveBeenNthCalledWith(1, '/model-versions/gpt-4.1')
    expect(mockGet).toHaveBeenNthCalledWith(2, '/model-versions/latest', { params: { limit: 5 } })
    expect(mockGet).toHaveBeenNthCalledWith(3, '/model-versions/cost-effective', { params: { limit: 3 } })
    expect(mockGet).toHaveBeenNthCalledWith(4, '/model-versions/current')
  })

  it('switches model versions and checks compatibility', async () => {
    const modelVersionsApi = await loadModule()
    const switchRequest = {
      orchestrator_model: 'gpt-4.1',
      worker_model: 'gpt-4o-mini',
      embedding_model: 'text-embedding-3-small',
    }
    const switchResponse = {
      data: {
        success: true,
        message: 'Updated active models',
        updates: { orchestrator: 'gpt-4.1' },
        warnings: ['worker fallback applied'],
      },
    }
    const compatibilityResponse = {
      data: {
        model_id: 'gpt-4.1',
        is_compatible: false,
        incompatible_parameters: ['temperature'],
        suggested_mappings: { temperature: 'reasoning_effort' },
        warnings: ['parameter renamed'],
      },
    }

    mockPost
      .mockResolvedValueOnce(switchResponse)
      .mockResolvedValueOnce(compatibilityResponse)

    await expect(modelVersionsApi.switchModelVersions(switchRequest)).resolves.toEqual(switchResponse.data)
    await expect(
      modelVersionsApi.checkModelCompatibility('gpt-4.1', { temperature: 0.2, max_tokens: 1000 })
    ).resolves.toEqual(compatibilityResponse.data)

    expect(mockPost).toHaveBeenNthCalledWith(1, '/model-versions/switch', switchRequest)
    expect(mockPost).toHaveBeenNthCalledWith(2, '/model-versions/check-compatibility', {
      model_id: 'gpt-4.1',
      parameters: { temperature: 0.2, max_tokens: 1000 },
    })
  })

  it('fetches recommendations with task constraints', async () => {
    const modelVersionsApi = await loadModule()
    const response = {
      data: [
        {
          model: { id: 'gpt-4.1', name: 'GPT-4.1' },
          score: 0.98,
          reasons: ['best accuracy'],
          estimated_cost_per_1k_tokens: 0.02,
        },
      ],
    }
    mockGet.mockResolvedValue(response)

    await expect(
      modelVersionsApi.getModelRecommendations({
        task_type: 'analysis',
        budget_limit: 1,
        context_required: 32000,
        capabilities_required: ['reasoning', 'tools'],
      })
    ).resolves.toEqual(response.data)

    expect(mockGet).toHaveBeenCalledWith('/model-versions/recommendations', {
      params: {
        task_type: 'analysis',
        budget_limit: 1,
        context_required: 32000,
        capabilities_required: ['reasoning', 'tools'],
      },
    })
  })
})
