import axios from 'axios'

const createClient = (baseUrl: string, token: string) => {
  return axios.create({
    baseURL: `${baseUrl}/api/v1/admin/telemetry`,
    headers: { Authorization: `Bearer ${token}` },
  })
}

export const remoteApi = {
  connect: async (url: string, token: string) => {
    const client = createClient(url, token)
    const response = await client.get('/status')
    return response.data
  },
  fetchFiles: async (url: string, token: string) => {
    const client = createClient(url, token)
    const response = await client.get('/files')
    return response.data
  },
  fetchRecords: async (url: string, token: string, params: { date: string; source?: string; offset?: number; limit?: number }) => {
    const client = createClient(url, token)
    const response = await client.get('/records', { params })
    return response.data
  },
  fetchRecord: async (url: string, token: string, id: string, source = 'protected') => {
    const client = createClient(url, token)
    const response = await client.get(`/records/${id}`, { params: { source } })
    return response.data
  },
  searchRecords: async (url: string, token: string, filters: any) => {
    const client = createClient(url, token)
    const response = await client.post('/search', filters)
    return response.data
  },
  fetchStats: async (url: string, token: string, range = '7d') => {
    const client = createClient(url, token)
    const response = await client.get('/stats', { params: { range } })
    return response.data
  },

  // -- Strategy OS endpoints --------------------------------------------

  fetchStrategyRuns: async (
    url: string,
    token: string,
    params: {
      since?: string
      until?: string
      strategy_id?: string
      status?: string
      limit?: number
      offset?: number
    }
  ) => {
    const client = createClient(url, token)
    const response = await client.get('/strategy/runs', { params })
    return response.data
  },

  fetchStrategyRun: async (url: string, token: string, traceId: string) => {
    const client = createClient(url, token)
    const response = await client.get(`/strategy/runs/${traceId}`)
    return response.data
  },

  fetchStrategyLLMCalls: async (
    url: string,
    token: string,
    traceId: string
  ) => {
    const client = createClient(url, token)
    const response = await client.get(`/strategy/runs/${traceId}/llm-calls`)
    return response.data
  },

  fetchAdaptiveDecisions: async (
    url: string,
    token: string,
    params: {
      since?: string
      until?: string
      capability_id?: string
      limit?: number
      offset?: number
    }
  ) => {
    const client = createClient(url, token)
    const response = await client.get('/strategy/decisions', { params })
    return response.data
  },

  fetchStrategyStats: async (url: string, token: string, range = '7d') => {
    const client = createClient(url, token)
    const response = await client.get('/strategy/stats', { params: { range } })
    return response.data
  },
}
