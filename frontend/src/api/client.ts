import axios, { AxiosError, AxiosInstance, AxiosResponse } from 'axios'

// API base URL - uses Vite proxy in development, nginx proxy in production
const API_BASE = '/api/v1'

// Auth token storage key
const AUTH_TOKEN_KEY = 'auth_token'

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
      
      // Handle 401 Unauthorized - clear token and optionally redirect
      if (status === 401) {
        clearAuthToken()
        // Dispatch custom event for auth context to handle
        window.dispatchEvent(new CustomEvent('auth:unauthorized'))
      }
      
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

// Request interceptor for auth token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Helper functions for auth token
export const setAuthToken = (token: string) => {
  localStorage.setItem(AUTH_TOKEN_KEY, token)
}

export const clearAuthToken = () => {
  localStorage.removeItem(AUTH_TOKEN_KEY)
}

export const getAuthToken = (): string | null => {
  return localStorage.getItem(AUTH_TOKEN_KEY)
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

export interface DocumentChunk {
  id: string
  content: string
  chunk_index: number
  token_count?: number
  metadata: Record<string, unknown>
  created_at?: string
  has_embedding: boolean
  embedding_dimensions?: number
}

export interface DocumentFullInfo {
  id: string
  title: string
  source: string
  content: string
  content_length: number
  created_at?: string
  metadata: Record<string, unknown>
  file_path: string
  file_exists: boolean
  file_stats?: {
    size_bytes: number
    modified_time: number
    extension: string
  }
  chunks: DocumentChunk[]
  chunks_count: number
  total_tokens: number
}

export interface OpenExplorerResponse {
  success: boolean
  message: string
  file_path?: string
  source?: string
}

export interface DocumentListResponse {
  documents: Document[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface IndexStats {
  indexed_documents: number
  total_index_size_bytes: number
  storage_size_bytes: number
  last_document_indexed?: string
}

export interface IndexInfo {
  indexes?: Array<{ name: string; status: string; type: string }>
  vector_index?: string
  text_index?: string
  stats?: IndexStats
  error?: string
}

export interface CreateIndexResult {
  success: boolean
  vector_index?: { name: string; status: string; dimensions: number }
  text_index?: { name: string; status: string }
  documents_to_index?: number
  errors: string[]
  warning?: string
  message?: string
  created_at?: string
}

export interface SystemStats {
  database: {
    documents: { count: number; size_bytes: number; avg_doc_size: number }
    chunks: { count: number; size_bytes: number; avg_doc_size: number }
    database: string
    error?: string
  }
  indexes: IndexInfo
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

export interface LLMModel {
  id: string
  owned_by: string
  created?: number
}

export interface LLMModelsResponse {
  models: LLMModel[]
  provider: string
  cached: boolean
  fallback?: boolean
}

export interface EmbeddingModel {
  id: string
  dimension: number
  provider: string
}

export interface EmbeddingModelsResponse {
  models: EmbeddingModel[]
  provider: string
}

export interface ConfigOptions {
  current: {
    llm_model: string
    embedding_model: string
    embedding_dimension: number
    default_match_count: number
  }
  options: {
    embedding_models: EmbeddingModel[]
    match_count_options: number[]
    embedding_dimensions: number[]
  }
}

export interface ConfigUpdateRequest {
  llm_model?: string
  embedding_model?: string
  embedding_dimension?: number
  default_match_count?: number
}

export interface ConfigUpdateResponse {
  success: boolean
  message: string
  updated?: Record<string, unknown>
  current?: {
    llm_model: string
    embedding_model: string
    embedding_dimension: number
    default_match_count: number
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
  excluded_files: number
  document_count: number
  image_count: number
  audio_count: number
  video_count: number
  chunks_created: number
  current_file?: string
  errors: string[]
  progress_percent: number
  elapsed_seconds: number
  estimated_remaining_seconds?: number
  is_paused: boolean
  can_pause: boolean
  can_stop: boolean
}

export interface IngestionRunSummary {
  job_id: string
  status: string
  started_at?: string
  completed_at?: string
  total_files: number
  processed_files: number
  failed_files: number
  excluded_files: number
  document_count: number
  image_count: number
  audio_count: number
  video_count: number
  chunks_created: number
  elapsed_seconds: number
  profile?: string
}

export interface IngestionRunsResponse {
  runs: IngestionRunSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
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

// ============== Chat Sessions Types ==============

export interface MessageStats {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
  tokens_per_second: number
  latency_ms: number
}

export interface SessionMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  stats?: MessageStats
  model?: string
  sources?: Array<{
    title: string
    source: string
    relevance: number
    excerpt: string
  }>
}

export interface SessionStats {
  total_messages: number
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  total_cost_usd: number
  avg_tokens_per_second: number
  avg_latency_ms: number
}

export interface ChatSession {
  id: string
  title: string
  folder_id?: string
  model: string
  created_at: string
  updated_at: string
  messages: SessionMessage[]
  stats: SessionStats
  is_pinned: boolean
  profile?: string
}

export interface ChatFolder {
  id: string
  name: string
  color: string
  created_at: string
  is_expanded: boolean
}

export interface SessionListResponse {
  sessions: ChatSession[]
  folders: ChatFolder[]
}

export interface ModelPricing {
  input: number
  output: number
}

export interface ModelPricingInfo {
  id: string
  pricing: ModelPricing
}

export interface SendMessageResponse {
  user_message: SessionMessage
  assistant_message: SessionMessage
  session_stats: SessionStats
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

  update: async (profileKey: string, data: { name?: string; description?: string; documents_folders?: string[]; database?: string }) => {
    const response = await api.put(`/profiles/${profileKey}`, data)
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

  getFullInfo: async (documentId: string): Promise<DocumentFullInfo> => {
    const response = await api.get(`/ingestion/documents/${documentId}/info`)
    return response.data
  },

  delete: async (documentId: string) => {
    const response = await api.delete(`/ingestion/documents/${documentId}`)
    return response.data
  },

  openInExplorer: async (documentId: string): Promise<OpenExplorerResponse> => {
    const response = await api.post(`/ingestion/documents/${documentId}/open-explorer`)
    return response.data
  },

  getFileUrl: (documentId: string): string => {
    return `${API_BASE}/ingestion/documents/${documentId}/file`
  },

  findBySource: async (source: string): Promise<Document | null> => {
    // Search through documents to find by source
    const response = await api.get('/ingestion/documents', { params: { page: 1, page_size: 100 } })
    const docs = response.data.documents as Document[]
    return docs.find(d => d.source === source || d.title === source) || null
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

  pause: async (): Promise<{ success: boolean; message: string }> => {
    const response = await api.post('/ingestion/pause')
    return response.data
  },

  resume: async (): Promise<{ success: boolean; message: string }> => {
    const response = await api.post('/ingestion/resume')
    return response.data
  },

  stop: async (): Promise<{ success: boolean; message: string }> => {
    const response = await api.post('/ingestion/stop')
    return response.data
  },

  getRuns: async (page: number = 1, pageSize: number = 5): Promise<IngestionRunsResponse> => {
    const response = await api.get('/ingestion/runs', { params: { page, page_size: pageSize } })
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

  getLogsStreamUrl: (): string => {
    return `${API_BASE}/ingestion/logs/stream`
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

  indexes: async (): Promise<IndexInfo> => {
    const response = await api.get('/system/indexes')
    return response.data
  },

  createIndexes: async (): Promise<CreateIndexResult> => {
    const response = await api.post('/system/indexes/create')
    return response.data
  },

  databaseStats: async () => {
    const response = await api.get('/system/database-stats')
    return response.data
  },

  listLLMModels: async (): Promise<LLMModelsResponse> => {
    const response = await api.get('/system/models/llm')
    return response.data
  },

  listEmbeddingModels: async (): Promise<EmbeddingModelsResponse> => {
    const response = await api.get('/system/models/embedding')
    return response.data
  },

  getConfigOptions: async (): Promise<ConfigOptions> => {
    const response = await api.get('/system/config/options')
    return response.data
  },

  updateConfig: async (update: ConfigUpdateRequest): Promise<ConfigUpdateResponse> => {
    const response = await api.post('/system/config/update', update)
    return response.data
  },

  saveConfigToDb: async (update: ConfigUpdateRequest): Promise<ConfigUpdateResponse> => {
    const response = await api.post('/system/config/save', update)
    return response.data
  },

  getSavedConfig: async (): Promise<{ exists: boolean; config: ConfigUpdateRequest | null }> => {
    const response = await api.get('/system/config/saved')
    return response.data
  },
}

// ============== Chat Sessions API ==============

export const sessionsApi = {
  // Sessions
  list: async (folderId?: string): Promise<SessionListResponse> => {
    const params = folderId !== undefined ? { folder_id: folderId } : {}
    const response = await api.get('/sessions', { params })
    return response.data
  },

  create: async (data?: { title?: string; folder_id?: string; model?: string }): Promise<ChatSession> => {
    const response = await api.post('/sessions', data || {})
    return response.data
  },

  get: async (sessionId: string): Promise<ChatSession> => {
    const response = await api.get(`/sessions/${sessionId}`)
    return response.data
  },

  update: async (sessionId: string, data: { title?: string; folder_id?: string; model?: string; is_pinned?: boolean }) => {
    const response = await api.put(`/sessions/${sessionId}`, data)
    return response.data
  },

  delete: async (sessionId: string) => {
    const response = await api.delete(`/sessions/${sessionId}`)
    return response.data
  },

  // Messages
  sendMessage: async (
    sessionId: string,
    content: string,
    options?: { search_type?: string; match_count?: number; include_sources?: boolean }
  ): Promise<SendMessageResponse> => {
    const response = await api.post(`/sessions/${sessionId}/messages`, {
      content,
      search_type: options?.search_type || 'hybrid',
      match_count: options?.match_count || 10,
      include_sources: options?.include_sources ?? true,
    })
    return response.data
  },

  clearMessages: async (sessionId: string) => {
    const response = await api.delete(`/sessions/${sessionId}/messages`)
    return response.data
  },

  // Folders
  listFolders: async (): Promise<{ folders: ChatFolder[] }> => {
    const response = await api.get('/sessions/folders')
    return response.data
  },

  createFolder: async (name: string, color?: string): Promise<ChatFolder> => {
    const response = await api.post('/sessions/folders', { name, color })
    return response.data
  },

  updateFolder: async (folderId: string, data: { name?: string; color?: string; is_expanded?: boolean }) => {
    const response = await api.put(`/sessions/folders/${folderId}`, data)
    return response.data
  },

  deleteFolder: async (folderId: string) => {
    const response = await api.delete(`/sessions/folders/${folderId}`)
    return response.data
  },

  // Model pricing
  getModelPricing: async (): Promise<{ models: ModelPricingInfo[] }> => {
    const response = await api.get('/sessions/meta/models')
    return response.data
  },
}

// ============== Auth Types ==============

export interface User {
  id: string
  email: string
  name: string
  created_at: string
  is_active: boolean
  is_admin: boolean
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  name: string
  password: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

// ============== Auth API ==============

// Admin types
export interface UserListItem {
  id: string
  email: string
  name: string
  is_active: boolean
  is_admin: boolean
  created_at: string
}

export interface ProfileAccessMatrix {
  users: UserListItem[]
  profiles: string[]
  access: Record<string, string[]> // user_id -> list of profile_keys
}

export interface SetAccessRequest {
  user_id: string
  profile_key: string
  has_access: boolean
}

export const authApi = {
  register: async (data: RegisterRequest): Promise<AuthResponse> => {
    const response = await api.post('/auth/register', data)
    return response.data
  },

  login: async (data: LoginRequest): Promise<AuthResponse> => {
    const response = await api.post('/auth/login', data)
    return response.data
  },

  logout: async (): Promise<{ success: boolean }> => {
    const response = await api.post('/auth/logout')
    return response.data
  },

  getMe: async (): Promise<User> => {
    const response = await api.get('/auth/me')
    return response.data
  },

  updateMe: async (data: { name?: string }): Promise<{ success: boolean }> => {
    const response = await api.put('/auth/me', data)
    return response.data
  },

  changePassword: async (currentPassword: string, newPassword: string): Promise<{ success: boolean }> => {
    const response = await api.put('/auth/me/password', {
      current_password: currentPassword,
      new_password: newPassword
    })
    return response.data
  },

  // Admin endpoints
  listUsers: async (): Promise<UserListItem[]> => {
    const response = await api.get('/auth/users')
    return response.data
  },

  getAccessMatrix: async (): Promise<ProfileAccessMatrix> => {
    const response = await api.get('/auth/access-matrix')
    return response.data
  },

  setProfileAccess: async (data: SetAccessRequest): Promise<{ success: boolean }> => {
    const response = await api.post('/auth/access', data)
    return response.data
  },
}

export default api
