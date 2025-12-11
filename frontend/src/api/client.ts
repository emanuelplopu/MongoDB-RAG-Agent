import axios, { AxiosError, AxiosInstance, AxiosResponse } from 'axios'

// API base URL - uses Vite proxy in development, nginx proxy in production
const API_BASE = '/api/v1'

// Retry configuration
const MAX_RETRIES = 3
const RETRY_DELAY = 1000 // ms
const TIMEOUT = 30000 // 30 seconds

// Custom error class for API errors
export class ApiError extends Error {
  status: number
  code: string
  details?: unknown

  constructor(message: string, status: number, code: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

// Create axios instance with defaults
const api: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Response interceptor for error handling
api.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const config = error.config
    
    // Handle network errors with retry
    if (!error.response && config) {
      const retryCount = (config as any)._retryCount || 0
      
      if (retryCount < MAX_RETRIES) {
        (config as any)._retryCount = retryCount + 1
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAY * (retryCount + 1)))
        return api.request(config)
      }
      
      throw new ApiError(
        'Network error - unable to connect to server',
        0,
        'NETWORK_ERROR'
      )
    }
    
    // Handle HTTP errors
    if (error.response) {
      const { status, data } = error.response
      const message = (data as any)?.message || (data as any)?.detail || 'An error occurred'
      const code = (data as any)?.error || `HTTP_${status}`
      
      throw new ApiError(message, status, code, data)
    }
    
    throw error
  }
)

// Request interceptor for logging (development)
if (import.meta.env.DEV) {
  api.interceptors.request.use((config) => {
    console.debug(`[API] ${config.method?.toUpperCase()} ${config.url}`)
    return config
  })
}

// Types
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
}

export interface ChatResponse {
  message: string
  conversation_id: string
  sources?: Array<{
    title: string
    source: string
    relevance: number
    excerpt: string
  }>
  search_performed: boolean
  model: string
  tokens_used?: number
  processing_time_ms: number
}

export interface SearchResult {
  chunk_id: string
  document_id: string
  document_title: string
  document_source: string
  content: string
  similarity: number
  metadata: Record<string, unknown>
}

export interface SearchResponse {
  query: string
  search_type: string
  results: SearchResult[]
  total_results: number
  processing_time_ms: number
}

export interface Profile {
  name: string
  description?: string
  documents_folders: string[]
  database: string
  collection_documents: string
  collection_chunks: string
  vector_index: string
  text_index: string
  embedding_model?: string
  llm_model?: string
}

export interface ProfileListResponse {
  profiles: Record<string, Profile>
  active_profile: string
}

export interface Document {
  id: string
  title: string
  source: string
  chunks_count: number
  created_at?: string
  metadata: Record<string, unknown>
}

export interface DocumentListResponse {
  documents: Document[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface SystemStats {
  database: {
    documents: { count: number; size_bytes: number; avg_doc_size: number }
    chunks: { count: number; size_bytes: number; avg_doc_size: number }
    database: string
    error?: string
  }
  indexes: {
    indexes?: Array<{ name: string; status: string; type: string }>
    vector_index?: string
    text_index?: string
    error?: string
  }
  config: {
    llm_provider: string
    llm_model: string
    embedding_provider: string
    embedding_model: string
    embedding_dimension: number
    default_match_count: number
    database: string
  }
}

export interface IngestionStatus {
  status: string
  job_id?: string
  started_at?: string
  completed_at?: string
  total_files: number
  processed_files: number
  failed_files: number
  chunks_created: number
  current_file?: string
  errors: string[]
  progress_percent: number
  elapsed_seconds: number
  estimated_remaining_seconds?: number
}

export interface LogEntry {
  timestamp: string
  level: string
  message: string
  logger: string
}

export interface IngestionLogs {
  logs: LogEntry[]
  total: number
  start_index: number
  max_lines: number
}

// API Functions
export const chatApi = {
  send: async (message: string, conversationId?: string): Promise<ChatResponse> => {
    const response = await api.post('/chat', {
      message,
      conversation_id: conversationId,
      search_type: 'hybrid',
      match_count: 10,
      include_sources: true,
    })
    return response.data
  },

  getConversation: async (conversationId: string) => {
    const response = await api.get(`/chat/conversations/${conversationId}`)
    return response.data
  },

  listConversations: async () => {
    const response = await api.get('/chat/conversations')
    return response.data
  },

  deleteConversation: async (conversationId: string) => {
    const response = await api.delete(`/chat/conversations/${conversationId}`)
    return response.data
  },
}

export const searchApi = {
  search: async (
    query: string,
    searchType: 'semantic' | 'text' | 'hybrid' = 'hybrid',
    matchCount: number = 10
  ): Promise<SearchResponse> => {
    const response = await api.post('/search', {
      query,
      search_type: searchType,
      match_count: matchCount,
    })
    return response.data
  },

  semanticSearch: async (query: string, matchCount: number = 10): Promise<SearchResponse> => {
    const response = await api.post('/search/semantic', { query, match_count: matchCount })
    return response.data
  },

  textSearch: async (query: string, matchCount: number = 10): Promise<SearchResponse> => {
    const response = await api.post('/search/text', { query, match_count: matchCount })
    return response.data
  },

  hybridSearch: async (query: string, matchCount: number = 10): Promise<SearchResponse> => {
    const response = await api.post('/search/hybrid', { query, match_count: matchCount })
    return response.data
  },
}

export const profilesApi = {
  list: async (): Promise<ProfileListResponse> => {
    const response = await api.get('/profiles')
    return response.data
  },

  getActive: async () => {
    const response = await api.get('/profiles/active')
    return response.data
  },

  switch: async (profileKey: string) => {
    const response = await api.post('/profiles/switch', { profile_key: profileKey })
    return response.data
  },

  create: async (data: { key: string; name: string; description?: string; documents_folders: string[]; database?: string }) => {
    const response = await api.post('/profiles/create', data)
    return response.data
  },

  delete: async (profileKey: string) => {
    const response = await api.delete(`/profiles/${profileKey}`)
    return response.data
  },
}

export const documentsApi = {
  list: async (page: number = 1, pageSize: number = 20): Promise<DocumentListResponse> => {
    const response = await api.get('/ingestion/documents', { params: { page, page_size: pageSize } })
    return response.data
  },

  get: async (documentId: string) => {
    const response = await api.get(`/ingestion/documents/${documentId}`)
    return response.data
  },

  delete: async (documentId: string) => {
    const response = await api.delete(`/ingestion/documents/${documentId}`)
    return response.data
  },
}

export const ingestionApi = {
  start: async (options: {
    profile?: string
    documents_folder?: string
    clean_before_ingest?: boolean
    incremental?: boolean
  }): Promise<IngestionStatus> => {
    const response = await api.post('/ingestion/start', options)
    return response.data
  },

  getStatus: async (): Promise<IngestionStatus> => {
    const response = await api.get('/ingestion/status')
    return response.data
  },

  getJobStatus: async (jobId: string): Promise<IngestionStatus> => {
    const response = await api.get(`/ingestion/status/${jobId}`)
    return response.data
  },

  cancel: async (jobId: string) => {
    const response = await api.post(`/ingestion/cancel/${jobId}`)
    return response.data
  },

  setupIndexes: async () => {
    const response = await api.post('/ingestion/setup-indexes')
    return response.data
  },

  getLogs: async (since: number = 0, limit: number = 1000): Promise<IngestionLogs> => {
    const response = await api.get('/ingestion/logs', { params: { since, limit } })
    return response.data
  },

  clearLogs: async () => {
    const response = await api.delete('/ingestion/logs')
    return response.data
  },
}

export const systemApi = {
  health: async () => {
    const response = await api.get('/system/health')
    return response.data
  },

  stats: async (): Promise<SystemStats> => {
    const response = await api.get('/system/stats')
    return response.data
  },

  config: async () => {
    const response = await api.get('/system/config')
    return response.data
  },

  info: async () => {
    const response = await api.get('/system/info')
    return response.data
  },

  indexes: async () => {
    const response = await api.get('/system/indexes')
    return response.data
  },

  databaseStats: async () => {
    const response = await api.get('/system/database-stats')
    return response.data
  },
}

export default api
