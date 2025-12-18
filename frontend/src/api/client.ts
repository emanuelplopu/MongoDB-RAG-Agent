import axios, { AxiosError, AxiosInstance, AxiosResponse } from 'axios'

// API base URL - uses Vite proxy in development, nginx proxy in production
const API_BASE = '/api/v1'

// Auth token storage key
const AUTH_TOKEN_KEY = 'auth_token'

// Retry configuration
const MAX_RETRIES = 3
const RETRY_DELAY = 1000 // ms
const TIMEOUT = 30000 // 30 seconds
const AUTH_CHECK_TIMEOUT = 5000 // 5 seconds for auth verification

// Custom error class for API errors
export class ApiError extends Error {
  status: number
  code: string
  details?: unknown
  errorId?: string
  technicalDetails?: {
    exception_type?: string
    exception_message?: string
    path?: string
    method?: string
  }

  constructor(
    message: string, 
    status: number, 
    code: string, 
    details?: unknown,
    errorId?: string,
    technicalDetails?: ApiError['technicalDetails']
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.errorId = errorId
    this.technicalDetails = technicalDetails
  }

  /**
   * Get a user-friendly error message suitable for display.
   * @param isAdmin If true, may include more technical details.
   */
  getUserMessage(isAdmin: boolean = false): string {
    // Network errors
    if (this.code === 'NETWORK_ERROR') {
      return 'Unable to connect to the server. Please check your internet connection and try again.'
    }

    // Rate limiting
    if (this.status === 429) {
      return 'The service is busy. Please wait a moment and try again.'
    }

    // Authentication errors
    if (this.status === 401) {
      return 'Your session has expired. Please log in again.'
    }

    // Forbidden
    if (this.status === 403) {
      return 'You do not have permission to perform this action.'
    }

    // Not found
    if (this.status === 404) {
      return 'The requested resource was not found.'
    }

    // Validation errors
    if (this.status === 422) {
      return 'Please check your input and try again.'
    }

    // Server errors
    if (this.status >= 500) {
      if (isAdmin && this.technicalDetails) {
        const tech = this.technicalDetails
        return `Server error: ${tech.exception_type || 'Unknown'} - ${tech.exception_message || this.message}${this.errorId ? ` (Error ID: ${this.errorId})` : ''}`
      }
      return this.errorId 
        ? `Something went wrong. Please try again. If the problem persists, contact support with Error ID: ${this.errorId}`
        : 'Something went wrong. Please try again.'
    }

    // For other errors, return the message from the server or a default
    return this.message || 'An unexpected error occurred. Please try again.'
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
        console.warn(`[API] Network error, retrying (${retryCount + 1}/${MAX_RETRIES})...`)
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAY * (retryCount + 1)))
        return api.request(config)
      }
      
      console.error('[API] Network error - all retries exhausted')
      throw new ApiError(
        'Unable to connect to the server. Please check your connection.',
        0,
        'NETWORK_ERROR'
      )
    }
    
    // Handle HTTP errors
    if (error.response) {
      const { status, data } = error.response
      const message = (data as any)?.message || (data as any)?.detail || 'An error occurred'
      const code = (data as any)?.error || `HTTP_${status}`
      const errorId = (data as any)?.error_id
      const technicalDetails = (data as any)?.technical_details
      
      // Log error for debugging (in development)
      if (import.meta.env.DEV) {
        console.error(`[API Error] ${status} ${code}: ${message}`, {
          errorId,
          technicalDetails,
          data
        })
      }
      
      // Handle 401 Unauthorized - clear token and optionally redirect
      if (status === 401) {
        clearAuthToken()
        // Dispatch custom event for auth context to handle
        window.dispatchEvent(new CustomEvent('auth:unauthorized'))
      }
      
      throw new ApiError(message, status, code, data, errorId, technicalDetails)
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
  // Use shorter timeout for auth-related requests
  if (config.url?.includes('/auth/me')) {
    config.timeout = AUTH_CHECK_TIMEOUT
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

export interface PendingFile {
  name: string
  path: string
  size_bytes: number
  format: string
  created_at: string
  modified_at: string
}

export interface PendingFilesResponse {
  files: PendingFile[]
  total: number
  is_running: boolean
  error?: string
}

export interface MetadataRebuildStatus {
  status: 'starting' | 'running' | 'completed' | 'failed' | 'cancelled'
  started_at: string
  completed_at?: string
  total: number
  processed: number
  updated: number
  progress_percent: number
  message?: string
  error?: string
}

// ============== Chat Sessions Types ==============

export interface SearchOperation {
  index_type: 'vector' | 'text'
  index_name: string
  query: string
  results_count: number
  duration_ms: number
  top_score: number | null
}

export interface SearchThinking {
  search_type: string
  query: string
  total_results: number
  operations: SearchOperation[]
  total_duration_ms: number
}

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
  attachments?: Array<AttachmentInfo>
  thinking?: SearchThinking
}

export interface AttachmentInfo {
  filename: string
  content_type: string
  size_bytes: number
  data_url?: string
  token_estimate: number
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

export interface FolderInfo {
  path: string
  name: string
  depth: number
  count: number
}

export interface FoldersResponse {
  folders: FolderInfo[]
  total_folders: number
  total_documents: number
  error?: string
}

export type SortField = 'name' | 'modified' | 'size' | 'type'
export type SortOrder = 'asc' | 'desc'

export const documentsApi = {
  list: async (
    page: number = 1, 
    pageSize: number = 20, 
    folder?: string, 
    search?: string, 
    exactFolder: boolean = false,
    sortBy: SortField = 'modified',
    sortOrder: SortOrder = 'desc'
  ): Promise<DocumentListResponse> => {
    const params: Record<string, any> = { page, page_size: pageSize }
    if (folder) params.folder = folder
    if (search) params.search = search
    if (exactFolder) params.exact_folder = true
    params.sort_by = sortBy
    params.sort_order = sortOrder
    const response = await api.get('/ingestion/documents', { params })
    return response.data
  },

  getFolders: async (): Promise<FoldersResponse> => {
    const response = await api.get('/ingestion/documents/folders')
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
    // Use dedicated lookup endpoint for efficient source matching
    try {
      const response = await api.get('/ingestion/documents/lookup', { params: { source } })
      if (response.data.found && response.data.document) {
        return {
          id: response.data.document.id,
          title: response.data.document.title,
          source: response.data.document.source,
          chunks_count: 0,
          metadata: {}
        }
      }
      return null
    } catch (err) {
      // Silently fail lookup - source links will be non-clickable
      return null
    }
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

  getPendingFiles: async (limit: number = 500): Promise<PendingFilesResponse> => {
    const response = await api.get('/ingestion/pending-files', { params: { limit } })
    return response.data
  },

  // Metadata rebuild
  startMetadataRebuild: async (): Promise<{
    success: boolean
    message: string
    status: MetadataRebuildStatus
  }> => {
    const response = await api.post('/ingestion/rebuild-metadata')
    return response.data
  },

  getMetadataRebuildStatus: async (): Promise<{
    running: boolean
    status: MetadataRebuildStatus | null
    message?: string
  }> => {
    const response = await api.get('/ingestion/rebuild-metadata')
    return response.data
  },

  cancelMetadataRebuild: async (): Promise<{ success: boolean; message: string }> => {
    const response = await api.delete('/ingestion/rebuild-metadata')
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
    options?: { 
      search_type?: string
      match_count?: number
      include_sources?: boolean
      attachments?: AttachmentInfo[]
    }
  ): Promise<SendMessageResponse> => {
    const response = await api.post(`/sessions/${sessionId}/messages`, {
      content,
      search_type: options?.search_type || 'hybrid',
      match_count: options?.match_count || 10,
      include_sources: options?.include_sources ?? true,
      attachments: options?.attachments || null,
    })
    return response.data
  },

  estimateTokens: async (attachments: AttachmentInfo[]): Promise<{
    attachments: Array<{
      filename: string
      content_type: string
      size_bytes: number
      token_estimate: number
    }>
    total_tokens: number
    cost_estimates: Record<string, number>
  }> => {
    const response = await api.post('/sessions/meta/estimate-tokens', attachments)
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

// User management types
export interface CreateUserRequest {
  email: string
  name: string
  password: string
  is_admin?: boolean
}

export interface UpdateUserRequest {
  name?: string
  email?: string
  is_admin?: boolean
  new_password?: string
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

  // User management endpoints (admin)
  createUser: async (data: CreateUserRequest): Promise<UserListItem> => {
    const response = await api.post('/auth/users/create', data)
    return response.data
  },

  updateUser: async (userId: string, data: UpdateUserRequest): Promise<UserListItem> => {
    const response = await api.put(`/auth/users/${userId}`, data)
    return response.data
  },

  setUserStatus: async (userId: string, isActive: boolean): Promise<{ success: boolean; message: string }> => {
    const response = await api.put(`/auth/users/${userId}/status`, { is_active: isActive })
    return response.data
  },

  deleteUser: async (userId: string): Promise<{ success: boolean; message: string }> => {
    const response = await api.delete(`/auth/users/${userId}`)
    return response.data
  },
}

// ============== Status Dashboard Types ==============

export interface ProfileStats {
  profile_key: string
  profile_name: string
  database: string
  documents_count: number
  chunks_count: number
  total_tokens: number
  avg_chunk_size: number
  storage_size_bytes: number
  last_ingestion?: string
  ingestion_jobs_count: number
}

export interface SystemMetrics {
  cpu_percent: number
  memory_percent: number
  memory_used_gb: number
  memory_total_gb: number
  disk_percent: number
  disk_used_gb: number
  disk_total_gb: number
  uptime_seconds: number
}

export interface StatusDashboard {
  profiles: ProfileStats[]
  active_profile: string
  system_metrics: SystemMetrics
  total_documents: number
  total_chunks: number
  total_profiles: number
  api_uptime_seconds: number
  llm_provider: string
  llm_model: string
  embedding_model: string
}

// ============== Search Indexes Types ==============

export interface IndexMetrics {
  name: string
  type: string
  status: string
  size_bytes: number
  documents_indexed: number
  last_updated?: string
}

export interface SearchPerformance {
  avg_response_time_ms: number
  p50_response_time_ms: number
  p95_response_time_ms: number
  p99_response_time_ms: number
  total_searches: number
  searches_last_hour: number
  searches_last_24h: number
}

export interface OptimizationSuggestion {
  category: string
  severity: string
  title: string
  description: string
  action: string
  estimated_impact: string
}

export interface IndexDashboard {
  indexes: IndexMetrics[]
  performance: SearchPerformance
  suggestions: OptimizationSuggestion[]
  resource_allocation: Record<string, unknown>
}

// ============== Ingestion Queue Types ==============

export interface QueuedIngestionJob {
  id: string
  profile_key: string
  profile_name: string
  file_types: string[]
  incremental: boolean
  priority: number
  created_at: string
  status: string
  started_at?: string
  completed_at?: string
  error?: string
}

export interface ScheduledIngestionJob {
  id: string
  profile_key: string
  profile_name: string
  file_types: string[]
  incremental: boolean
  frequency: string
  hour: number
  day_of_week: number
  day_of_month: number
  enabled: boolean
  last_run?: string
  next_run?: string
  created_at: string
}

export interface QueueStatus {
  queue: QueuedIngestionJob[]
  current_job?: QueuedIngestionJob
  total_queued: number
  is_processing: boolean
}

export interface QueueRequest {
  profile_key: string
  file_types?: string[]
  incremental?: boolean
  priority?: number
}

export interface ScheduleRequest {
  profile_key: string
  file_types?: string[]
  incremental?: boolean
  frequency: string
  hour?: number
  day_of_week?: number
  day_of_month?: number
}

// ============== Local LLM Types ==============

export interface LocalProvider {
  id: string
  name: string
  url: string
  host: string  // The host where it was discovered
  location: string  // 'host', 'container', 'network', 'custom'
  status: string
  models: Array<{ name: string; size_gb?: number; type?: string; capabilities?: string[] }>
  supports_embeddings: boolean
  supports_vision: boolean    // Image processing (LLaVA, etc.)
  supports_audio: boolean     // Audio transcription (Whisper)
  supports_video: boolean     // Video understanding
  error?: string
}

export interface SystemResources {
  cpu_cores: number
  ram_total_gb: number
  ram_available_gb: number
  gpu_available: boolean
  gpu_name?: string
  gpu_memory_gb?: number
}

export interface ModelRecommendation {
  name: string
  provider: string
  type: string
  size_gb: number
  performance_score: number
  is_installed: boolean
  warning?: string
}

export interface DiscoveryResult {
  providers: LocalProvider[]
  resources: SystemResources
  recommendations: ModelRecommendation[]
  offline_ready: boolean
  has_chat_model: boolean
  has_embedding_model: boolean
  has_vision_model: boolean    // Image processing capability
  has_audio_model: boolean     // Audio transcription capability
  has_video_model: boolean     // Video understanding capability
  scanned_hosts: string[]
  custom_endpoints: CustomEndpoint[]
}

export interface CustomEndpoint {
  id: string
  name: string
  url: string
  provider_type: string  // ollama, openai-compatible
  enabled: boolean
}

export interface NetworkScanRequest {
  ip_range?: string
  custom_ips?: string[]
  ports?: number[]
}

export interface NetworkScanResult {
  success: boolean
  found: LocalProvider[]
  scanned: string[]
  scanned_count: number
  found_count: number
  error?: string
}

export interface OfflineModeConfig {
  enabled: boolean
  // Chat model
  chat_provider?: string
  chat_model?: string
  chat_url?: string
  // Embedding model
  embedding_provider?: string
  embedding_model?: string
  embedding_url?: string
  // Vision model (for image processing)
  vision_provider?: string
  vision_model?: string
  vision_url?: string
  // Audio model (for transcription - local Whisper)
  audio_provider?: string
  audio_model?: string
  audio_url?: string
}

// ============== New API Functions ==============

export const statusApi = {
  getDashboard: async (): Promise<StatusDashboard> => {
    const response = await api.get('/status/dashboard')
    return response.data
  },

  getProfileMetrics: async (profileKey: string) => {
    const response = await api.get(`/status/metrics/profile/${profileKey}`)
    return response.data
  },

  getDetailedHealth: async () => {
    const response = await api.get('/status/health/detailed')
    return response.data
  },
}

export const indexesApi = {
  getDashboard: async (): Promise<IndexDashboard> => {
    const response = await api.get('/indexes/dashboard')
    return response.data
  },

  createIndexes: async () => {
    const response = await api.post('/indexes/create')
    return response.data
  },

  getPerformanceHistory: async (hours: number = 24) => {
    const response = await api.get('/indexes/performance/history', { params: { hours } })
    return response.data
  },
}

export const ingestionQueueApi = {
  getQueue: async (): Promise<QueueStatus> => {
    const response = await api.get('/ingestion-queue/queue')
    return response.data
  },

  addToQueue: async (request: QueueRequest) => {
    const response = await api.post('/ingestion-queue/queue/add', request)
    return response.data
  },

  addMultipleToQueue: async (jobs: QueueRequest[]) => {
    const response = await api.post('/ingestion-queue/queue/add-multiple', jobs)
    return response.data
  },

  removeFromQueue: async (jobId: string) => {
    const response = await api.delete(`/ingestion-queue/queue/${jobId}`)
    return response.data
  },

  clearQueue: async () => {
    const response = await api.delete('/ingestion-queue/queue')
    return response.data
  },

  reorderQueue: async (jobIds: string[]) => {
    const response = await api.post('/ingestion-queue/queue/reorder', jobIds)
    return response.data
  },

  // Schedules
  getSchedules: async (): Promise<{ schedules: ScheduledIngestionJob[] }> => {
    const response = await api.get('/ingestion-queue/schedules')
    return response.data
  },

  createSchedule: async (request: ScheduleRequest) => {
    const response = await api.post('/ingestion-queue/schedules', request)
    return response.data
  },

  updateSchedule: async (scheduleId: string, request: ScheduleRequest) => {
    const response = await api.put(`/ingestion-queue/schedules/${scheduleId}`, request)
    return response.data
  },

  deleteSchedule: async (scheduleId: string) => {
    const response = await api.delete(`/ingestion-queue/schedules/${scheduleId}`)
    return response.data
  },

  toggleSchedule: async (scheduleId: string) => {
    const response = await api.post(`/ingestion-queue/schedules/${scheduleId}/toggle`)
    return response.data
  },

  runScheduleNow: async (scheduleId: string) => {
    const response = await api.post(`/ingestion-queue/schedules/${scheduleId}/run-now`)
    return response.data
  },
}

export const localLlmApi = {
  discover: async (): Promise<DiscoveryResult> => {
    const response = await api.get('/local-llm/discover')
    return response.data
  },

  pullModel: async (providerId: string, modelName: string, providerUrl?: string) => {
    const response = await api.post(`/local-llm/pull/${providerId}`, null, { 
      params: { model_name: modelName, provider_url: providerUrl } 
    })
    return response.data
  },

  getPullStatus: async (providerId: string) => {
    const response = await api.get(`/local-llm/pull-status/${providerId}`)
    return response.data
  },

  getOfflineConfig: async (): Promise<OfflineModeConfig> => {
    const response = await api.get('/local-llm/offline-config')
    return response.data
  },

  saveOfflineConfig: async (config: OfflineModeConfig) => {
    const response = await api.post('/local-llm/offline-config', config)
    return response.data
  },

  testModel: async (providerId: string, modelName: string, modelType: string = 'chat', providerUrl?: string) => {
    const response = await api.post('/local-llm/test-model', null, {
      params: { provider_id: providerId, model_name: modelName, model_type: modelType, provider_url: providerUrl }
    })
    return response.data
  },

  compareModels: async () => {
    const response = await api.get('/local-llm/compare-models')
    return response.data
  },

  // Network Scanning
  scanNetwork: async (request: NetworkScanRequest): Promise<NetworkScanResult> => {
    const response = await api.post('/local-llm/scan-network', request)
    return response.data
  },

  // Custom Endpoints
  getCustomEndpoints: async (): Promise<{ endpoints: CustomEndpoint[] }> => {
    const response = await api.get('/local-llm/custom-endpoints')
    return response.data
  },

  addCustomEndpoint: async (endpoint: CustomEndpoint) => {
    const response = await api.post('/local-llm/custom-endpoints', endpoint)
    return response.data
  },

  deleteCustomEndpoint: async (endpointId: string) => {
    const response = await api.delete(`/local-llm/custom-endpoints/${endpointId}`)
    return response.data
  },

  testCustomEndpoint: async (endpointId: string) => {
    const response = await api.post(`/local-llm/custom-endpoints/${endpointId}/test`)
    return response.data
  },
}

// ============== Cloud Sources Types ==============

export type CloudProviderType = 
  | 'google_drive'
  | 'onedrive'
  | 'sharepoint'
  | 'dropbox'
  | 'owncloud'
  | 'nextcloud'
  | 'confluence'
  | 'jira'
  | 'email_imap'
  | 'email_gmail'
  | 'email_outlook'

export type CloudAuthType = 'oauth2' | 'api_key' | 'password' | 'app_token'

export type CloudConnectionStatus = 'active' | 'expired' | 'revoked' | 'error' | 'pending'

export type SyncJobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'paused'

export type SyncJobType = 'full' | 'incremental' | 'manual'

export type SyncFrequency = 'hourly' | 'daily' | 'weekly' | 'monthly'

export interface CloudProvider {
  provider_type: CloudProviderType
  display_name: string
  description: string
  icon: string
  supported_auth_types: CloudAuthType[]
  supports_delta_sync: boolean
  supports_webhooks: boolean
  documentation_url?: string
  setup_instructions?: string
}

export interface CloudConnection {
  id: string
  user_id: string
  provider: CloudProviderType
  display_name: string
  auth_type: CloudAuthType
  status: CloudConnectionStatus
  server_url?: string
  oauth_email?: string
  oauth_expires_at?: string
  oauth_scopes?: string[]
  cache_size_mb: number
  last_validated_at?: string
  error_message?: string
  created_at: string
  updated_at: string
}

export interface RemoteFolder {
  id: string
  name: string
  path: string
  parent_id?: string
  has_children: boolean
  children_count?: number
  modified_at?: string
}

export interface RemoteFile {
  id: string
  name: string
  path: string
  mime_type: string
  size_bytes: number
  modified_at: string
  web_view_url?: string
}

export interface SourcePath {
  path: string
  remote_id: string
  include_subfolders: boolean
  display_name?: string
}

export interface SyncFilters {
  file_types: string[]
  exclude_patterns: string[]
  max_file_size_mb: number
  modified_after?: string
}

export interface SyncSchedule {
  enabled: boolean
  frequency: SyncFrequency
  hour: number
  day_of_week?: number
  day_of_month?: number
}

export interface SyncConfig {
  id: string
  user_id: string
  connection_id: string
  connection_display_name: string
  provider: CloudProviderType
  profile_key: string
  name: string
  source_paths: SourcePath[]
  filters: SyncFilters
  schedule: SyncSchedule
  delete_removed: boolean
  status: string
  stats: {
    total_files: number
    total_size_bytes: number
    last_sync_at?: string
    last_sync_files_processed: number
    last_sync_duration_seconds: number
    next_scheduled_run?: string
  }
  created_at: string
  updated_at: string
}

export interface SyncJobProgress {
  phase: string
  current_file?: string
  files_discovered: number
  files_processed: number
  files_skipped: number
  files_failed: number
  bytes_processed: number
}

export interface SyncJobError {
  file_path: string
  error_type: string
  message: string
  timestamp: string
}

export interface SyncJob {
  id: string
  config_id: string
  config_name: string
  user_id: string
  type: SyncJobType
  status: SyncJobStatus
  progress: SyncJobProgress
  errors: SyncJobError[]
  started_at?: string
  completed_at?: string
  duration_seconds?: number
}

export interface CloudSourceSummary {
  connection_id: string
  provider: CloudProviderType
  display_name: string
  status: CloudConnectionStatus
  sync_configs_count: number
  total_files_indexed: number
  last_sync_at?: string
  next_sync_at?: string
  has_errors: boolean
}

export interface CloudSourcesDashboard {
  total_connections: number
  active_connections: number
  total_sync_configs: number
  total_files_indexed: number
  total_size_bytes: number
  active_jobs: number
  sources: CloudSourceSummary[]
  recent_errors: SyncJobError[]
  next_scheduled_sync?: string
}

// ============== Cloud Sources API ==============

export const cloudSourcesApi = {
  // Providers
  getProviders: async (): Promise<{ providers: CloudProvider[] }> => {
    const response = await api.get('/cloud-sources/providers')
    return response.data
  },

  getProvider: async (providerType: CloudProviderType): Promise<CloudProvider> => {
    const response = await api.get(`/cloud-sources/providers/${providerType}`)
    return response.data
  },

  // Connections
  getConnections: async (params?: {
    provider?: CloudProviderType
    status?: CloudConnectionStatus
  }): Promise<{ connections: CloudConnection[]; total: number }> => {
    const response = await api.get('/cloud-sources/connections', { params })
    return response.data
  },

  createConnection: async (data: {
    provider: CloudProviderType
    display_name: string
    server_url?: string
    username?: string
    password?: string
    api_key?: string
    app_token?: string
  }): Promise<CloudConnection> => {
    const response = await api.post('/cloud-sources/connections', data)
    return response.data
  },

  getConnection: async (connectionId: string): Promise<CloudConnection> => {
    const response = await api.get(`/cloud-sources/connections/${connectionId}`)
    return response.data
  },

  updateConnection: async (connectionId: string, data: {
    display_name?: string
    password?: string
    api_key?: string
    app_token?: string
  }): Promise<CloudConnection> => {
    const response = await api.put(`/cloud-sources/connections/${connectionId}`, data)
    return response.data
  },

  deleteConnection: async (connectionId: string): Promise<void> => {
    await api.delete(`/cloud-sources/connections/${connectionId}`)
  },

  testConnection: async (connectionId: string): Promise<{
    success: boolean
    message: string
    user_info?: Record<string, unknown>
    storage_quota?: Record<string, unknown>
  }> => {
    const response = await api.post(`/cloud-sources/connections/${connectionId}/test`)
    return response.data
  },

  browseFolder: async (connectionId: string, params?: {
    path?: string
    folder_id?: string
  }): Promise<{
    current_folder: RemoteFolder
    folders: RemoteFolder[]
    files: RemoteFile[]
    has_more: boolean
    next_cursor?: string
  }> => {
    const response = await api.get(`/cloud-sources/connections/${connectionId}/browse`, { params })
    return response.data
  },

  // OAuth
  initiateOAuth: async (provider: CloudProviderType, displayName: string): Promise<{
    authorization_url: string
    state: string
  }> => {
    const response = await api.post(`/cloud-sources/oauth/${provider}/authorize`, {
      provider,
      display_name: displayName,
    })
    return response.data
  },

  refreshOAuthTokens: async (connectionId: string): Promise<CloudConnection> => {
    const response = await api.post(`/cloud-sources/oauth/${connectionId}/refresh`)
    return response.data
  },

  revokeOAuthTokens: async (connectionId: string): Promise<void> => {
    await api.delete(`/cloud-sources/oauth/${connectionId}/revoke`)
  },

  // Sync Configurations
  getSyncConfigs: async (params?: {
    connection_id?: string
  }): Promise<{ configs: SyncConfig[]; total: number }> => {
    const response = await api.get('/cloud-sources/sync-configs', { params })
    return response.data
  },

  createSyncConfig: async (data: {
    connection_id: string
    profile_key: string
    name: string
    source_paths: SourcePath[]
    filters?: Partial<SyncFilters>
    schedule?: Partial<SyncSchedule>
    delete_removed?: boolean
  }): Promise<SyncConfig> => {
    const response = await api.post('/cloud-sources/sync-configs', data)
    return response.data
  },

  getSyncConfig: async (configId: string): Promise<SyncConfig> => {
    const response = await api.get(`/cloud-sources/sync-configs/${configId}`)
    return response.data
  },

  updateSyncConfig: async (configId: string, data: Partial<{
    name: string
    source_paths: SourcePath[]
    filters: SyncFilters
    schedule: SyncSchedule
    delete_removed: boolean
  }>): Promise<SyncConfig> => {
    const response = await api.put(`/cloud-sources/sync-configs/${configId}`, data)
    return response.data
  },

  deleteSyncConfig: async (configId: string): Promise<void> => {
    await api.delete(`/cloud-sources/sync-configs/${configId}`)
  },

  // Sync Operations
  runSync: async (configId: string, options?: {
    type?: SyncJobType
    force_full?: boolean
  }): Promise<SyncJob> => {
    const response = await api.post(`/cloud-sources/sync-configs/${configId}/run`, options || {})
    return response.data
  },

  getSyncStatus: async (configId: string): Promise<SyncJob> => {
    const response = await api.get(`/cloud-sources/sync-configs/${configId}/status`)
    return response.data
  },

  pauseSync: async (configId: string): Promise<void> => {
    await api.post(`/cloud-sources/sync-configs/${configId}/pause`)
  },

  resumeSync: async (configId: string): Promise<void> => {
    await api.post(`/cloud-sources/sync-configs/${configId}/resume`)
  },

  cancelSync: async (configId: string): Promise<void> => {
    await api.post(`/cloud-sources/sync-configs/${configId}/cancel`)
  },

  getSyncHistory: async (configId: string, params?: {
    page?: number
    page_size?: number
  }): Promise<{
    jobs: SyncJob[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }> => {
    const response = await api.get(`/cloud-sources/sync-configs/${configId}/history`, { params })
    return response.data
  },

  // Dashboard
  getDashboard: async (): Promise<CloudSourcesDashboard> => {
    const response = await api.get('/cloud-sources/dashboard')
    return response.data
  },

  // Job Details
  getJob: async (jobId: string): Promise<SyncJob> => {
    const response = await api.get(`/cloud-sources/jobs/${jobId}`)
    return response.data
  },

  getJobLogsUrl: (jobId: string): string => {
    return `${API_BASE}/cloud-sources/jobs/${jobId}/logs`
  },

  // Cache API
  getCloudSourceInfo: async (documentId: string): Promise<{
    is_cloud_source: boolean
    document_id: string
    provider?: CloudProviderType
    connection_id?: string
    remote_id?: string
    remote_path?: string
    web_view_url?: string
    synced_at?: string
    is_cached?: boolean
    cached_path?: string
    cache_access_count?: number
  }> => {
    const response = await api.get(`/cloud-sources/cache/info/${documentId}`)
    return response.data
  },

  getCachedFile: async (documentId: string, connectionId: string): Promise<{
    remote_id: string
    remote_path: string
    local_path: string
    file_name: string
    size_bytes: number
    mime_type: string
    cached_at: string
    last_accessed_at: string
    access_count: number
    web_view_url?: string
  }> => {
    const response = await api.post('/cloud-sources/cache/get-file', {
      document_id: documentId,
      connection_id: connectionId,
    })
    return response.data
  },

  getCachedFileUrl: (connectionId: string, documentId: string): string => {
    return `${API_BASE}/cloud-sources/cache/serve/${connectionId}/${documentId}`
  },

  getCacheStats: async (connectionId: string): Promise<{
    connection_id: string
    cache_dir: string
    total_files: number
    total_size_bytes: number
    cache_limit_bytes: number
    usage_percent: number
    files: Array<{
      remote_id: string
      file_name: string
      size_bytes: number
      access_count: number
      last_accessed_at: string
    }>
  }> => {
    const response = await api.get(`/cloud-sources/cache/stats/${connectionId}`)
    return response.data
  },

  clearCache: async (connectionId: string): Promise<void> => {
    await api.delete(`/cloud-sources/cache/clear/${connectionId}`)
  },
}

export default api
