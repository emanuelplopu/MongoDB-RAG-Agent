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
}
