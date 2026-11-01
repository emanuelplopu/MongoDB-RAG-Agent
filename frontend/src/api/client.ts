import axios, { AxiosError, AxiosInstance, AxiosResponse } from 'axios'

// API base URL - uses Vite proxy in development, nginx proxy in production
const API_BASE = '/api/v1'

// Auth token storage key
const AUTH_TOKEN_KEY = 'auth_token'

// Retry configuration
const MAX_RETRIES = 3
const RETRY_DELAY = 1000 // ms
const TIMEOUT = 300000 // 300 seconds
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
      // If the server provided a specific message (like "Invalid email or password"), use it
      // Otherwise, show the generic session expired message
      if (this.message && this.message !== 'An error occurred' && !this.message.includes('Not authenticated')) {
        return this.message
      }
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

/**
 * Authorization header for requests made with raw fetch instead of the axios
 * instance. The axios interceptor adds the token per request, so
 * api.defaults.headers.common never contains it; raw fetch calls must add it
 * themselves or the backend's login enforcement rejects them.
 */
export const getAuthHeaders = (): Record<string, string> => {
  const token = getAuthToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
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

/** One directory offered by the profile folder picker. */
export interface FolderEntry {
  name: string
  /** Absolute container path, the value stored in `Profile.documents_folders`. */
  path: string
  /** False for a configured document root that is not mounted on the server. */
  exists: boolean
}

/** Listing returned by `GET /profiles/folders/browse`. */
export interface FolderBrowseResponse {
  roots: string[]
  /** Directory that was listed; null for the root listing. */
  path: string | null
  /** Parent directory; null at a root. */
  parent: string | null
  entries: FolderEntry[]
  truncated: boolean
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
  host_path?: string
  source?: string
  is_docker?: boolean
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

/**
 * Provenance of each metric in {@link HostStats}: `"host"` (Windows host agent),
 * `"gpu"` (nvidia-smi on the host, via the agent), `"container"` (the API
 * container's own view — the Docker Desktop VM — used while the agent is
 * offline) or `"unavailable"` (value is null).
 */
export interface HostStatsSources {
  cpu: string
  ram: string
  disk: string
  cpu_temp: string
  gpu_temp: string
  cpu_power: string
  gpu_power: string
  uptime: string
  note: string
}

/** One container's resource use, from the host agent. */
export interface HostContainerStat {
  name: string
  /** CPU in use, in cores (1.0 = one full core). */
  cpu_cores: number | null
  mem_bytes: number | null
  mem_limit_bytes: number | null
}

/** What a backend reported as running when the host sample was taken. */
export interface HostActivityState {
  tenant: string | null
  source: string | null
  job_id: string | null
  job_status: string | null
  phase: string | null
  step: string | null
  current_file: string | null
  processed_files: number | null
  total_files: number | null
  chat_active: boolean
}

/** Live host/GPU/container machine stats (GET /system/host-stats, superadmin). */
export interface HostStats {
  sampled_at: string
  cached: boolean
  /** 'host-agent' (true host figures) or 'container' (fallback view while the agent is offline). */
  source: 'host-agent' | 'container'
  agent_online: boolean
  agent_sample_age_seconds: number | null
  /** Host agent sensor driver state: ok, driver_missing, disabled or error. */
  sensors_status: string | null
  cpu_percent: number | null
  ram_used_bytes: number | null
  ram_total_bytes: number | null
  ram_percent: number | null
  disk_path: string | null
  disk_used_bytes: number | null
  disk_total_bytes: number | null
  disk_percent: number | null
  cpu_temp_c: number | null
  gpu_temp_c: number | null
  gpu_power_watts: number | null
  cpu_power_watts: number | null
  uptime_seconds: number | null
  gpu_name: string | null
  gpu_percent: number | null
  vram_used_bytes: number | null
  vram_total_bytes: number | null
  containers: HostContainerStat[]
  activity: HostActivityState[]
  sources: HostStatsSources
}

export interface LLMModel {
  id: string
  owned_by: string
  created?: number
  provider?: string
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

// LLM Provider Configuration Types
export interface LLMProviderInfo {
  id: string
  name: string
  models: string[]
  categories?: string[]
  supports_fetch?: boolean
}

export interface ProviderModelInfo {
  id: string
  name: string
  type?: string
  category?: string
  context?: number
  dimension?: number
  multimodal?: boolean
  audio?: boolean
  description?: string
  installed?: boolean
  size_gb?: number
  owned_by?: string
  created?: number
}

export interface FetchModelsResponse {
  success: boolean
  provider: string
  models: ProviderModelInfo[]
  categories?: string[]
  total?: number
  logs: string[]
  is_default?: boolean
  error?: string
  note?: string
  ollama_url?: string
}

export interface ProviderTestRequest {
  provider: string
  model: string
  api_key?: string
  prompt?: string
}

export interface ProviderTestResponse {
  success: boolean
  provider: string
  model: string
  response?: string
  error?: string
  latency_ms: number
  logs: string[]
}

// Model Capability Testing types
export interface ModelTestResult {
  success: boolean
  model_id: string
  role: string
  scores?: { reasoning: number; instruction_following: number; coherence: number; speed_score: number }
  overall?: number
  auto_pass?: boolean
  pass_threshold?: number
  latency_ms?: number
  tokens_generated?: number
  response_preview?: string
  error?: string
}

export interface ModelCapabilityRole {
  tested: boolean
  auto_score: number
  auto_pass: boolean
  admin_override: boolean | null
  approved: boolean
  test_results?: {
    reasoning: number
    instruction_following: number
    coherence: number
    speed_score: number
    latency_ms: number
    tokens_generated: number
    tested_at: string
    response_preview?: string
  }
}

export interface ModelCapability {
  id: string
  provider: string
  model_name: string
  orchestrator?: ModelCapabilityRole
  worker?: ModelCapabilityRole
  updated_at: string
}

// Agent Tools types
export interface ToolParameter {
  name: string
  type: string
  required: boolean
  description: string
  options?: string[]
  default?: string
}

export interface AgentTool {
  id: string
  name: string
  icon: string
  category: string
  description: string
  help_text: string
  parameters: ToolParameter[]
  test_config: {
    requires_db?: boolean
    requires_api_key?: string
    test_query?: string
    test_url?: string
    expected_response?: string
  }
}

export interface ToolsListResponse {
  tools: AgentTool[]
  total: number
  categories: string[]
}

export interface ToolTestRequest {
  tool_id: string
  parameters?: Record<string, string>
}

export interface ToolTestResponse {
  success: boolean
  tool_id: string
  tool_name: string
  result?: string
  result_preview?: string
  error?: string
  latency_ms: number
  logs: string[]
}

export interface AgentPerformanceConfig {
  parallel_workers: number
  max_iterations: number
  global_max_orchestrators: number
  global_max_workers: number
  worker_timeout: number
  orchestrator_timeout: number
  total_timeout: number
  default_mode: string
  auto_fast_threshold: number
  skip_evaluation: boolean
  max_sources_per_search: number
}

/** Load profile of the performance config: how many files a run converts at once, by time of day. */
export type IngestionLoadProfile = 'server_room' | 'office' | 'office_lite'

/** Office hours of the "office" load profile: local time in `timezone`, weekdays 0 = Monday to 6 = Sunday. */
export interface IngestionOfficeHours {
  timezone: string
  /** HH:MM */
  start: string
  /** HH:MM, after start */
  end: string
  weekdays: number[]
  /** Files converted at once during office hours (never above max_concurrent_files). */
  max_concurrent_files: number
  /** Files converted at once during office hours under the office_lite profile. */
  lite_max_concurrent_files: number
  /** Cap the CPU clock during office hours (Windows power plan, applied by the host agent). */
  cap_cpu_boost: boolean
}

/** What the host agent last reported about the Windows power plan (host_control "power_plan"). */
export interface PowerPlanAgentStatus {
  /** "capped", "normal" or "unknown". */
  state: string
  desired?: string | null
  max_state_ac?: number | null
  boost_mode_ac?: number | null
  applied_at?: string | null
  updated_at?: string | null
  error?: string | null
  agent_version?: string | null
}

/** GET /system/power-plan: this tenant's desire, every tenant's desire and the agent's report. */
export interface PowerPlanStatus {
  desired: string
  reason: string
  tenants: { tenant: string; desired: string; reason: string; updated_at?: string | null; fresh: boolean }[]
  agent?: PowerPlanAgentStatus | null
  agent_seen: boolean
  stale_after_seconds: number
}

/** One llama.cpp embedding server watched by the host agent's watchdog (host_control "embedder_watchdog"). */
export interface EmbedderWatchdogTarget {
  name: string
  url: string
  containers: string[]
  /** The container found running behind the URL. */
  container?: string | null
  container_state?: string | null
  container_health?: string | null
  started_at?: string | null
  /** "healthy", "slow", "degraded", "cooldown", "warming_up", "unreachable", "absent" or "unknown". */
  state: string
  reason: string
  slow_checks: number
  latency_ms?: number | null
  prompt_tokens?: number | null
  tokens_per_second?: number | null
  cpu_cores?: number | null
  mem_bytes?: number | null
  last_check?: string | null
  last_error?: string | null
  last_restart?: string | null
  last_restart_reason?: string | null
  restart_count: number
  next_restart_allowed_at?: string | null
}

/** GET /system/embedder-watchdog: the host agent's last watchdog report. */
export interface EmbedderWatchdogStatus {
  /** The agent has ever written a report. */
  reported: boolean
  /** The report is younger than stale_after_seconds. */
  agent_seen: boolean
  enabled?: boolean | null
  check_seconds?: number | null
  thresholds?: {
    slow_ms?: number | null
    probe_timeout_seconds?: number | null
    consecutive_checks?: number | null
    window_seconds?: number | null
    min_cpu_cores?: number | null
    warmup_seconds?: number | null
    min_restart_interval_seconds?: number | null
    probe_words?: number | null
  } | null
  targets: EmbedderWatchdogTarget[]
  updated_at?: string | null
  agent_version?: string | null
  host?: string | null
  stale_after_seconds: number
}

export interface IngestionPerformanceConfig {
  process_isolation_enabled: boolean
  max_concurrent_files: number
  embedding_batch_size: number
  thread_pool_workers: number
  embedding_requests_per_minute: number
  file_processing_timeout: number
  job_poll_interval_seconds: number
  /** server_room: max_concurrent_files around the clock; office / office_lite: office_hours.max_concurrent_files / lite_max_concurrent_files during office hours. */
  load_profile: IngestionLoadProfile
  office_hours: IngestionOfficeHours
}

export interface LLMProviderConfigResponse {
  orchestrator_provider: string
  orchestrator_model: string
  worker_provider: string
  worker_model: string
  embedding_provider: string
  openai_api_key_set: boolean
  openai_api_key_masked: string
  google_api_key_set: boolean
  google_api_key_masked: string
  anthropic_api_key_set: boolean
  anthropic_api_key_masked: string
  fast_llm_api_key_set: boolean
  updated_at: string | null
  providers: LLMProviderInfo[]
}

export interface LLMProviderConfigRequest {
  orchestrator_provider?: string
  orchestrator_model?: string
  worker_provider?: string
  worker_model?: string
  embedding_provider?: string
  openai_api_key?: string
  google_api_key?: string
  anthropic_api_key?: string
  fast_llm_api_key?: string
  embedding_api_key?: string
}

export interface DiscoveryProgress {
  folders_scanned: number
  total_folders: number
  files_found: number
  files_to_process: number
  files_skipped: number
  current_folder?: string
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
  /** Per-extension breakdown behind estimated_remaining_seconds (shared with the machine-stats widget). */
  eta?: IngestionEta | null
  is_paused: boolean
  can_pause: boolean
  can_stop: boolean
  // Phase tracking for transparency
  phase?: string
  phase_message?: string
  discovery_progress?: DiscoveryProgress
  processing_rate?: number  // files per minute
  /** "api" (in-process) or "worker" (ingestion worker container). */
  runner?: string | null
  /** Load profile period in force: "server_room", "office_hours" or "off_hours". */
  load_period?: string | null
  /** File slots in use right now (the office profile lowers them during office hours). */
  concurrency_slots?: number | null
  // Per-outcome counters, updated while the job runs
  image_only_pdfs?: number
  image_only_pdfs_ocr?: number
  no_chunks_files?: number
  timeout_files?: number
  error_files?: number
  /** Documents stored with part of their chunks while the rest waits in the run's embedding backlog. */
  partial_files?: number
}

/** Remaining work and measured pace of one file extension. */
export interface IngestionEtaExtension {
  extension: string
  remaining: number
  files_per_minute: number | null
  seconds: number | null
  pace_source: 'window' | 'lifetime' | 'overall' | string | null
}

/** Per-extension estimate of the remaining time of a running job. */
export interface IngestionEta {
  seconds: number | null
  method: 'per_extension' | 'overall' | string
  remaining_files: number
  unknown_remaining: number
  by_extension: IngestionEtaExtension[]
}

export interface IngestionStreamEvent {
  type: 'state'
  is_running: boolean
  is_paused: boolean
  job_id?: string
  phase?: string
  phase_message?: string
  status?: string
  discovery_progress?: DiscoveryProgress
  progress?: {
    processed_files: number
    total_files: number
    progress_percent: number
    chunks_created: number
    failed_files: number
    current_file?: string
  }
  metrics?: {
    processing_rate: number
    elapsed_seconds: number
    estimated_remaining_seconds?: number
    eta?: IngestionEta | null
  }
  counts?: {
    document_count: number
    image_count: number
    audio_count: number
    video_count: number
  }
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

export interface IngestEstimateByType {
  extension: string
  file_count: number
  total_bytes: number
  estimated_chunks: number
  estimated_tokens: number
  avg_processing_time_ms?: number | null
}

export interface IngestEstimate {
  profile_key: string
  folders: string[]
  missing_folders: string[]
  incremental: boolean
  scanned_files: number
  skipped_existing: number
  file_count: number
  total_bytes: number
  total_size_gb: number
  estimated_chunks: number
  estimated_tokens: number
  estimated_tokens_low: number
  estimated_tokens_high: number
  token_method: string
  estimated_time_seconds: number
  estimated_time_low_seconds: number
  estimated_time_high_seconds: number
  estimated_time_human: string
  estimated_embedding_seconds?: number | null
  concurrency: number
  time_method: string
  estimated_cost_usd: number
  estimated_cost_low_usd: number
  estimated_cost_high_usd: number
  embedding_model: string
  cost_per_million_tokens?: number | null
  embedding_throughput_tokens_per_sec?: number | null
  benchmark_date?: string | null
  is_rough: boolean
  assumptions: string[]
  by_extension: IngestEstimateByType[]
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

export interface SearchResultExcerpt {
  title: string
  excerpt: string
  score: number | null
}

export interface SearchOperation {
  index_type: 'vector' | 'text'
  index_name: string
  query: string
  results_count: number
  duration_ms: number
  top_score: number | null
  top_results: SearchResultExcerpt[] | null
}

export interface SearchThinking {
  search_type: string
  query: string
  total_results: number
  operations: SearchOperation[]
  total_duration_ms: number
}

export interface ToolOperation {
  tool_name: string
  tool_input: Record<string, unknown>
  success: boolean
  result_summary: string
  duration_ms: number
  error: string | null
}

export interface AgentThinking {
  search: SearchThinking | null
  tool_calls: ToolOperation[]
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
    database?: string
    url?: string
  }>
  attachments?: Array<AttachmentInfo>
  thinking?: AgentThinking
  agent_trace?: FederatedAgentTrace
  strategy_trace?: StrategyTraceResponse | null
}

// Federated Agent Trace - new orchestrator-worker system
export interface FederatedAgentTrace {
  id: string
  mode: string
  models: {
    orchestrator: string
    worker: string
  }
  iterations: number
  orchestrator_steps: Array<{
    phase: string
    reasoning: string
    output: string
    duration_ms: number
    tokens: number
  }>
  worker_steps: Array<{
    task_id: string
    task_type: string
    tool: string
    input: Record<string, unknown>
    output: string
    documents: Array<{
      id: string
      title: string
      source: string
      excerpt: string
      score: number
    }>
    web_links: Array<{
      url: string
      title: string
    }>
    duration_ms: number
    success: boolean
  }>
  sources: {
    documents: Array<{
      id: string
      document_id: string
      title: string
      source_type: string
      source_database: string
      excerpt: string
      score: number
    }>
    web_links: Array<{
      url: string
      title: string
      excerpt: string
    }>
  }
  timing: {
    total_ms: number
    orchestrator_ms: number
    worker_ms: number
  }
  tokens: {
    total: number
    orchestrator: number
    worker: number
  }
  cost_usd: number
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
  orchestrator_model?: string | null
  worker_model?: string | null
  created_at: string
  updated_at: string
  messages: SessionMessage[]
  stats: SessionStats
  is_pinned: boolean
  is_archived: boolean
  archived_at?: string
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
  title?: string | null  // Updated session title if changed
  agent_trace?: FederatedAgentTrace  // Full trace for transparency
  strategy_trace?: StrategyTraceResponse | null  // Strategy OS execution trace (Task 91/92)
}

// ── Strategy OS streaming/trace types (Task 92 / T4) ───────────────────────

export interface StrategyNodeEvent {
  node_id: string
  node_type: string
  status: 'success' | 'skipped' | 'failed' | 'error' | 'timed_out' | 'empty' | string
  duration_ms: number
}

export interface NodeTraceEntry {
  node_id: string
  node_type: string
  status: string
  duration_ms: number
  tokens_used: number
  llm_call_ids: string[]
  error: string | null
}

export interface ContextBudgetSummary {
  total_budget_tokens: number
  tokens_used: number
  included_count: number
  dropped_count: number
  dropped_items: Array<{ kind: string; tokens: number; reason: string }>
}

export interface StrategyTraceResponse {
  active: boolean
  spec_selected: string | null
  spec_version: string | null
  routing_reason: string
  adaptive_scores: Array<Record<string, unknown>> | null
  fast_path_eligible: boolean
  nodes_executed: NodeTraceEntry[]
  total_duration_ms: number
  context_budget: ContextBudgetSummary | null
  trace_id: string | null
  llm_call_count: number
  total_llm_tokens: number
  fallback_reason: string | null
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

  /**
   * List the server's document roots (no path) or the sub-folders of one
   * directory below a root. Superadmins and organisation admins only.
   */
  browseFolders: async (path?: string | null): Promise<FolderBrowseResponse> => {
    const response = await api.get('/profiles/folders/browse', { params: path ? { path } : {} })
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

  getEventsStreamUrl: (): string => {
    return `${API_BASE}/ingestion/events/stream`
  },

  getPendingFiles: async (limit: number = 500): Promise<PendingFilesResponse> => {
    const response = await api.get('/ingestion/pending-files', { params: { limit } })
    return response.data
  },

  // Pre-ingestion estimate for a profile's document folders (rough forecast).
  estimate: async (profileKey: string, incremental: boolean = true): Promise<IngestEstimate> => {
    const response = await api.get(
      `/profiles/${encodeURIComponent(profileKey)}/ingest/estimate`,
      { params: { incremental } }
    )
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

  /** Live host/GPU/container machine stats for the superadmin stats widget. */
  hostStats: async (): Promise<HostStats> => {
    const response = await api.get('/system/host-stats')
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

  // LLM Provider Configuration
  getLLMProviderConfig: async (): Promise<LLMProviderConfigResponse> => {
    const response = await api.get('/system/llm-providers')
    return response.data
  },

  saveLLMProviderConfig: async (config: LLMProviderConfigRequest): Promise<{ success: boolean; message: string }> => {
    const response = await api.post('/system/llm-providers', config)
    return response.data
  },

  // Provider Models Fetch & Test
  getProviderModelsDetailed: async (providerId: string): Promise<{
    provider: string
    models: ProviderModelInfo[]
    categories: string[]
    total: number
  }> => {
    const response = await api.get(`/system/llm-providers/models/${providerId}`)
    return response.data
  },

  fetchModelsFromApi: async (providerId: string, apiKey?: string): Promise<FetchModelsResponse> => {
    const response = await api.post(`/system/llm-providers/fetch-models/${providerId}`, null, {
      params: apiKey ? { api_key: apiKey } : {}
    })
    return response.data
  },

  testProviderConnection: async (request: ProviderTestRequest): Promise<ProviderTestResponse> => {
    const response = await api.post('/system/llm-providers/test', request)
    return response.data
  },

  // Agent Tools
  listTools: async (): Promise<ToolsListResponse> => {
    const response = await api.get('/system/tools')
    return response.data
  },

  getToolDetails: async (toolId: string): Promise<AgentTool> => {
    const response = await api.get(`/system/tools/${toolId}`)
    return response.data
  },

  testTool: async (request: ToolTestRequest): Promise<ToolTestResponse> => {
    const response = await api.post('/system/tools/test', request)
    return response.data
  },

  switchModelVersions: async (switchRequest: {
    orchestrator_model?: string
    orchestrator_provider?: string
    worker_model?: string
    worker_provider?: string
    embedding_model?: string
    embedding_provider?: string
  }): Promise<any> => {
    const response = await api.post('/model-versions/switch', switchRequest)
    return response.data
  },

  // CPU clock cap (load profile office hours), applied by the host agent
  getPowerPlanStatus: async (): Promise<PowerPlanStatus> => {
    const response = await api.get('/system/power-plan')
    return response.data
  },

  // Embedder watchdog (host agent restarts a degraded llama.cpp embedding server)
  getEmbedderWatchdogStatus: async (): Promise<EmbedderWatchdogStatus> => {
    const response = await api.get('/system/embedder-watchdog')
    return response.data
  },

  // Agent Performance Configuration
  getAgentPerformanceConfig: async (): Promise<AgentPerformanceConfig> => {
    const response = await api.get('/system/agent-performance')
    return response.data
  },

  saveAgentPerformanceConfig: async (config: AgentPerformanceConfig): Promise<{ success: boolean; message: string }> => {
    const response = await api.post('/system/agent-performance', config)
    return response.data
  },

  // Ingestion Performance Configuration
  getIngestionPerformanceConfig: async (): Promise<IngestionPerformanceConfig> => {
    const response = await api.get('/system/ingestion-performance')
    return response.data
  },

  saveIngestionPerformanceConfig: async (config: IngestionPerformanceConfig): Promise<{ success: boolean; message: string }> => {
    const response = await api.post('/system/ingestion-performance', config)
    return response.data
  },

  // Model Capability Testing
  testModelCapability: async (modelId: string, role: 'orchestrator' | 'worker'): Promise<ModelTestResult> => {
    const response = await api.post(`/system/models/${encodeURIComponent(modelId)}/test?role=${role}`)
    return response.data
  },

  getModelCapabilities: async (): Promise<{ models: ModelCapability[] }> => {
    const response = await api.get('/system/models/capabilities')
    return response.data
  },

  approveModel: async (modelId: string, role: 'orchestrator' | 'worker', approved: boolean): Promise<any> => {
    const response = await api.post(`/system/models/${encodeURIComponent(modelId)}/approve?role=${role}&approved=${approved}`)
    return response.data
  }
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

  update: async (sessionId: string, data: { title?: string; folder_id?: string; model?: string; is_pinned?: boolean; orchestrator_model?: string | null; worker_model?: string | null }) => {
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
      agent_mode?: 'auto' | 'thinking' | 'fast'
      strategy_id?: string  // Strategy for A/B testing
      language?: string  // UI language code (e.g. "en", "de")
    }
  ): Promise<SendMessageResponse> => {
    const response = await api.post(`/sessions/${sessionId}/messages`, {
      content,
      search_type: options?.search_type || 'hybrid',
      match_count: options?.match_count || 10,
      include_sources: options?.include_sources ?? true,
      attachments: options?.attachments || null,
      agent_mode: options?.agent_mode || null,
      strategy_id: options?.strategy_id || null,
      language: options?.language || null,
    })
    return response.data
  },

  // Streaming message endpoint
  sendMessageStream: (
    sessionId: string,
    content: string,
    options?: {
      attachments?: AttachmentInfo[]
      agent_mode?: 'auto' | 'thinking' | 'fast'
      strategy_id?: string  // Strategy for A/B testing
      language?: string  // UI language code (e.g. "en", "de")
    },
    callbacks?: {
      onStart?: (data: { mode: string; models: { orchestrator: string; worker: string } }) => void
      onOrchestratorStep?: (step: {
        phase: string
        reasoning: string
        output: string
        duration_ms: number
        tokens: number
        tasks?: Array<{ id: string; type: string; query: string }>
      }) => void
      onWorkerStep?: (step: {
        task_id: string
        task_type: string
        tool: string
        /** The query the worker ran, truncated by the backend. */
        input: string
        /** Every document found; `documents` below is a three-entry preview. */
        documents_count: number
        links_count: number
        duration_ms: number
        success: boolean
        documents: Array<{ title: string; score: number; excerpt: string }>
      }) => void
      onResponse?: (response: {
        content: string
        sources: Array<{
          title: string
          source: string
          database: string
          relevance: number
          excerpt: string
        }>
        stats: {
          total_tokens: number
          orchestrator_tokens: number
          worker_tokens: number
          cost_usd: number
          tokens_per_second: number
          latency_ms: number
        }
        trace: FederatedAgentTrace
        strategy_trace?: StrategyTraceResponse | null
      }) => void
      onStrategyNode?: (data: StrategyNodeEvent) => void
      onError?: (error: string) => void
      onDone?: () => void
      onTitleUpdate?: (title: string) => void
    }
  ): { abort: () => void } => {
    const abortController = new AbortController()
    
    const fetchStream = async () => {
      try {
        const response = await fetch(`${api.defaults.baseURL}/sessions/${sessionId}/messages/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...api.defaults.headers.common as Record<string, string>,
            ...getAuthHeaders(),
          },
          body: JSON.stringify({
            content,
            search_type: 'hybrid',
            match_count: 10,
            include_sources: true,
            attachments: options?.attachments || null,
            agent_mode: options?.agent_mode || null,
            strategy_id: options?.strategy_id || null,
            language: options?.language || null,
          }),
          signal: abortController.signal,
        })
        
        if (!response.ok) {
          throw new Error(`HTTP error ${response.status}`)
        }
        
        const reader = response.body?.getReader()
        if (!reader) throw new Error('No response body')
        
        const decoder = new TextDecoder()
        let buffer = ''
        
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                switch (data.type) {
                  case 'start':
                    callbacks?.onStart?.(data)
                    break
                  case 'orchestrator_step':
                    callbacks?.onOrchestratorStep?.(data)
                    break
                  case 'worker_step':
                    callbacks?.onWorkerStep?.(data)
                    break
                  case 'strategy_node':
                    callbacks?.onStrategyNode?.(data as StrategyNodeEvent)
                    break
                  case 'response':
                    callbacks?.onResponse?.(data)
                    break
                  case 'error':
                    callbacks?.onError?.(data.message)
                    break
                  case 'title_update':
                    callbacks?.onTitleUpdate?.(data.title)
                    break
                  case 'done':
                    callbacks?.onDone?.()
                    break
                }
              } catch (e) {
                console.error('Failed to parse SSE event:', e)
              }
            }
          }
        }
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          callbacks?.onError?.((error as Error).message)
        }
      }
    }
    
    fetchStream()
    
    return {
      abort: () => abortController.abort()
    }
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

  // Archive operations
  listArchived: async (): Promise<{ sessions: ChatSession[] }> => {
    const response = await api.get('/sessions/archived/list')
    return response.data
  },

  archiveSessions: async (sessionIds: string[]): Promise<{ success: boolean; archived_count: number }> => {
    const response = await api.post('/sessions/archive', { session_ids: sessionIds })
    return response.data
  },

  restoreSessions: async (sessionIds: string[]): Promise<{ success: boolean; restored_count: number }> => {
    const response = await api.post('/sessions/restore', { session_ids: sessionIds })
    return response.data
  },

  deletePermanently: async (sessionIds: string[]): Promise<{ success: boolean; deleted_count: number }> => {
    const response = await api.post('/sessions/delete-permanent', { session_ids: sessionIds })
    return response.data
  },

  moveToFolder: async (sessionIds: string[], folderId: string | null): Promise<{ success: boolean; moved_count: number }> => {
    const response = await api.post('/sessions/move-to-folder', { session_ids: sessionIds, folder_id: folderId })
    return response.data
  },

  exportSession: async (sessionId: string): Promise<any> => {
    const response = await api.get(`/sessions/${sessionId}/export`)
    return response.data
  },
}

// ============== Auth Types ==============

export interface UserPreferencesSync {
  language?: string | null
  theme?: string | null
  default_model?: string | null
  default_search_type?: string | null
  default_match_count?: number | null
  ui_density?: string | null
  items_per_page?: number | null
  show_line_numbers?: boolean | null
  code_theme?: string | null
  streaming_enabled?: boolean | null
  show_timestamps?: boolean | null
  message_grouping?: boolean | null
  enter_to_send?: boolean | null
  sound_enabled?: boolean | null
  notifications_enabled?: boolean | null
  show_toast_duration?: number | null
  developer_mode?: boolean | null
  experimental_features?: boolean | null
  auto_save_enabled?: boolean | null
  auto_save_interval?: number | null
}

export interface User {
  id: string
  email: string
  name: string
  title_prefix?: string | null
  title_suffix?: string | null
  created_at: string
  is_active: boolean
  is_admin: boolean
  is_org_admin?: boolean
  preferences?: UserPreferencesSync | null
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  name: string
  title_prefix?: string
  title_suffix?: string
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
  title_prefix?: string | null
  title_suffix?: string | null
  is_active: boolean
  is_admin: boolean
  is_org_admin?: boolean
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
  title_prefix?: string
  title_suffix?: string
  password: string
  is_admin?: boolean
  is_org_admin?: boolean
}

export interface UpdateUserRequest {
  name?: string
  email?: string
  title_prefix?: string
  title_suffix?: string
  is_admin?: boolean
  is_org_admin?: boolean
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

  updateMe: async (data: { name?: string; title_prefix?: string; title_suffix?: string }): Promise<{ success: boolean }> => {
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

  // Preferences sync
  getPreferences: async (): Promise<UserPreferencesSync> => {
    const response = await api.get('/auth/me/preferences')
    return response.data
  },

  updatePreferences: async (prefs: UserPreferencesSync): Promise<{ success: boolean; preferences: UserPreferencesSync }> => {
    const response = await api.put('/auth/me/preferences', prefs)
    return response.data
  },
}

// ============== API Keys Types ==============

export interface APIKeyResponse {
  id: string
  name: string
  key_prefix: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  is_active: boolean
  scopes: string[]
}

export interface APIKeyCreatedResponse {
  id: string
  name: string
  key: string  // Only returned on creation
  key_prefix: string
  created_at: string
  expires_at: string | null
  scopes: string[]
  warning: string
}

export interface CreateAPIKeyRequest {
  name: string
  expires_in_days?: number
  scopes?: string[]
}

// ============== API Keys API ==============

export const apiKeysApi = {
  list: async (): Promise<APIKeyResponse[]> => {
    const response = await api.get('/auth/api-keys')
    return response.data
  },

  create: async (data: CreateAPIKeyRequest): Promise<APIKeyCreatedResponse> => {
    const response = await api.post('/auth/api-keys', data)
    return response.data
  },

  revoke: async (keyId: string): Promise<{ success: boolean; message: string }> => {
    const response = await api.delete(`/auth/api-keys/${keyId}`)
    return response.data
  },

  toggle: async (keyId: string): Promise<{ success: boolean; is_active: boolean; message: string }> => {
    const response = await api.put(`/auth/api-keys/${keyId}/toggle`)
    return response.data
  },

  // Admin: list all keys
  listAll: async (): Promise<APIKeyResponse[]> => {
    const response = await api.get('/auth/admin/api-keys')
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
  // Selective ingestion filters
  retry_image_only_pdfs?: boolean
  retry_timeouts?: boolean
  retry_errors?: boolean
  retry_no_chunks?: boolean
  skip_image_only_pdfs?: boolean
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
  // Selective ingestion filters
  retry_image_only_pdfs?: boolean
  retry_timeouts?: boolean
  retry_errors?: boolean
  retry_no_chunks?: boolean
  skip_image_only_pdfs?: boolean
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
  // Selective ingestion filters
  retry_image_only_pdfs?: boolean
  retry_timeouts?: boolean
  retry_errors?: boolean
  retry_no_chunks?: boolean
  skip_image_only_pdfs?: boolean
}

export interface ScheduleRequest {
  profile_key: string
  file_types?: string[]
  incremental?: boolean
  frequency: string
  hour?: number
  day_of_week?: number
  day_of_month?: number
  // Selective ingestion filters
  retry_image_only_pdfs?: boolean
  retry_timeouts?: boolean
  retry_errors?: boolean
  retry_no_chunks?: boolean
  skip_image_only_pdfs?: boolean
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

// ============== File Registry Types ==============

export type FileClassification = 'normal' | 'image_only_pdf' | 'no_chunks' | 'timeout' | 'error' | 'pending'

export interface FileRegistryStats {
  total_files: number
  by_classification: Record<FileClassification, number>
  profile_key?: string
}

export interface FileRegistryEntry {
  file_path: string
  file_name: string
  file_size_bytes: number
  content_hash: string
  file_modified_at: string
  classification: FileClassification
  last_processed_at?: string
  last_job_id?: string
  chunks_created: number
  processing_time_ms: number
  error_message?: string
  retry_count: number
  profile_key: string
  created_at: string
  updated_at: string
}

export interface FileRegistryListResponse {
  files: FileRegistryEntry[]
  count: number
  limit: number
  skip: number
}

export const fileRegistryApi = {
  getStats: async (profileKey?: string): Promise<FileRegistryStats> => {
    const response = await api.get('/file-registry/stats', {
      params: profileKey ? { profile_key: profileKey } : {}
    })
    return response.data
  },

  listFiles: async (params: {
    profile_key?: string
    classification?: FileClassification
    limit?: number
    skip?: number
  }): Promise<FileRegistryListResponse> => {
    const response = await api.get('/file-registry/files', { params })
    return response.data
  },

  reclassifyFile: async (filePath: string, newClassification: FileClassification) => {
    const response = await api.post(
      `/file-registry/reclassify/${encodeURIComponent(filePath)}`,
      null,
      { params: { new_classification: newClassification } }
    )
    return response.data
  },

  clearRegistry: async (params?: {
    profile_key?: string
    classification?: FileClassification
  }) => {
    const response = await api.delete('/file-registry/clear', { params })
    return response.data
  },

  retryCategory: async (profileKey: string, classification: FileClassification) => {
    const response = await api.post('/file-registry/retry-category', null, {
      params: { profile_key: profileKey, classification }
    })
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

// ============== Prompt Management Types ==============

export interface ToolParameter {
  name: string
  type: string
  description: string
  required: boolean
}

export interface ToolSchema {
  name: string
  description: string
  parameters: ToolParameter[]
  enabled: boolean
}

export interface PromptVersion {
  version: number
  system_prompt: string
  tools: ToolSchema[]
  created_at: string
  created_by: string | null
  notes: string
  is_active: boolean
}

export interface PromptTemplate {
  id: string | null
  name: string
  description: string
  category: string
  versions: PromptVersion[]
  active_version: number
  created_at: string
  updated_at: string
  created_by: string | null
}

export interface PromptTestResult {
  success: boolean
  response: string
  tool_calls: Array<{ name: string; arguments: string }>
  tokens_used: number
  duration_ms: number
  error: string | null
}

export interface PromptComparison {
  version_a: PromptVersion
  version_b: PromptVersion
  prompt_diff: string
  tools_added: string[]
  tools_removed: string[]
  tools_modified: string[]
}

// ============== Prompt Management API ==============

export const promptsApi = {
  list: async (category?: string): Promise<{ templates: PromptTemplate[]; total: number }> => {
    const params = category ? { category } : {}
    const response = await api.get('/prompts', { params })
    return response.data
  },

  get: async (templateId: string): Promise<PromptTemplate> => {
    const response = await api.get(`/prompts/${templateId}`)
    return response.data
  },

  create: async (data: {
    name: string
    description?: string
    category?: string
    system_prompt: string
    tools?: ToolSchema[]
    notes?: string
  }): Promise<PromptTemplate> => {
    const response = await api.post('/prompts', data)
    return response.data
  },

  update: async (templateId: string, data: {
    name?: string
    description?: string
    category?: string
  }): Promise<PromptTemplate> => {
    const response = await api.patch(`/prompts/${templateId}`, data)
    return response.data
  },

  delete: async (templateId: string): Promise<void> => {
    await api.delete(`/prompts/${templateId}`)
  },

  createVersion: async (templateId: string, data: {
    system_prompt: string
    tools?: ToolSchema[]
    notes?: string
  }): Promise<PromptTemplate> => {
    const response = await api.post(`/prompts/${templateId}/versions`, data)
    return response.data
  },

  activateVersion: async (templateId: string, version: number): Promise<PromptTemplate> => {
    const response = await api.post(`/prompts/${templateId}/versions/${version}/activate`)
    return response.data
  },

  getVersion: async (templateId: string, version: number): Promise<PromptVersion> => {
    const response = await api.get(`/prompts/${templateId}/versions/${version}`)
    return response.data
  },

  test: async (data: {
    template_id: string
    version?: number
    test_message: string
    mock_tool_responses?: Record<string, string>
  }): Promise<PromptTestResult> => {
    const response = await api.post('/prompts/test', data)
    return response.data
  },

  compare: async (data: {
    template_id: string
    version_a: number
    version_b: number
  }): Promise<PromptComparison> => {
    const response = await api.post('/prompts/compare', data)
    return response.data
  },
}


// ============== Strategies API ==============

export interface StrategyInfo {
  id: string
  name: string
  version: string
  description: string
  domains: string[]
  tags: string[]
  is_default: boolean
  is_legacy: boolean
  author: string
}

export interface StrategyDetail extends StrategyInfo {
  config: {
    max_iterations: number
    confidence_threshold: number
    early_exit_enabled: boolean
    cross_search_boost: number
    content_length_penalty: number
    custom_params: Record<string, unknown>
  }
  prompts_preview: {
    analyze: string
    plan: string
    evaluate: string
    synthesize: string
  }
}

export interface StrategyStats {
  strategy_id: string
  execution_count: number
  avg_latency_ms: number
  median_latency_ms: number
  avg_iterations: number
  avg_confidence: number
  quality_score: number
  quality_distribution: Record<string, number>
  avg_user_feedback: number | null
  feedback_count: number
}

export interface ResponseScore {
  quality: number
  hallucination: number
  readability: number
  factuality: number
  relevance: number
  overall: number
}

export interface ABCompareResult {
  query: string
  strategy_a: string
  strategy_b: string
  scores_a: ResponseScore
  scores_b: ResponseScore
  speed_winner: string
  quality_winner: string
  overall_winner: string
  analysis: string
  recommendation: string
}

export const strategiesApi = {
  list: async (domain?: string): Promise<StrategyInfo[]> => {
    const params = domain ? { domain } : {}
    const response = await api.get('/strategies', { params })
    return response.data
  },

  getDefault: async (): Promise<StrategyInfo> => {
    const response = await api.get('/strategies/default')
    return response.data
  },

  get: async (strategyId: string): Promise<StrategyDetail> => {
    const response = await api.get(`/strategies/${strategyId}`)
    return response.data
  },

  getMetrics: async (strategyId: string, hours?: number, domain?: string): Promise<StrategyStats> => {
    const params: Record<string, unknown> = {}
    if (hours) params.hours = hours
    if (domain) params.domain = domain
    const response = await api.get(`/strategies/${strategyId}/metrics`, { params })
    return response.data
  },

  getAllMetrics: async (hours?: number, domain?: string): Promise<StrategyStats[]> => {
    const params: Record<string, unknown> = {}
    if (hours) params.hours = hours
    if (domain) params.domain = domain
    const response = await api.get('/strategies/metrics/all', { params })
    return response.data
  },

  getForDomain: async (domain: string): Promise<StrategyInfo> => {
    const response = await api.get(`/strategies/for-domain/${domain}`)
    return response.data
  },

  autoDetect: async (query: string): Promise<StrategyInfo> => {
    const response = await api.post('/strategies/auto-detect', { query })
    return response.data
  },

  compare: async (strategyA: string, strategyB: string, hours?: number, domain?: string): Promise<{
    strategy_a: string
    strategy_b: string
    filters: Record<string, unknown>
    comparison: Record<string, unknown>
    winner: string | null
    confidence_in_winner: string | null
  }> => {
    const response = await api.post('/strategies/compare', {
      strategy_a: strategyA,
      strategy_b: strategyB,
      hours,
      domain,
    })
    return response.data
  },

  recordFeedback: async (strategyId: string, sessionId: string, score: number, text?: string): Promise<void> => {
    await api.post('/strategies/feedback', {
      strategy_id: strategyId,
      session_id: sessionId,
      score,
      text,
    })
  },

  abCompareResponses: async (data: {
    query: string
    response_a: string
    response_b: string
    strategy_a: string
    strategy_b: string
    latency_a_ms: number
    latency_b_ms: number
  }): Promise<ABCompareResult> => {
    const response = await api.post('/strategies/ab-compare-responses', data)
    return response.data
  },
}

export default api

// ==================== Backup Types ====================

export type BackupType = 'full' | 'incremental' | 'checkpoint' | 'post_ingestion'
export type BackupStatus = 'pending' | 'in_progress' | 'completed' | 'failed'
export type RestoreMode = 'full' | 'merge' | 'selective'

export interface BackupMetadata {
  backup_id: string
  backup_type: BackupType
  status: BackupStatus
  profile_key: string
  database_name: string
  name?: string
  description?: string
  created_at: string
  completed_at?: string
  size_bytes: number
  file_path: string
  collections_included: string[]
  document_counts: Record<string, number>
  parent_backup_id?: string
  checkpoint_data?: Record<string, unknown>
  ingestion_job_id?: string
  ingestion_stats?: Record<string, unknown>
  error_message?: string
}

export interface BackupListResponse {
  backups: BackupMetadata[]
  total: number
  profile_key?: string
}

export interface BackupConfig {
  auto_backup_after_ingestion: boolean
  retention_days: number
  max_backups_per_profile: number
  backup_location: string
  compression_enabled: boolean
  include_embeddings: boolean
  backup_schedule?: string
}

export interface StorageStats {
  total_size_bytes: number
  total_backups: number
  backups_by_type: Record<string, number>
  backups_by_profile: Record<string, number>
  oldest_backup?: string
  newest_backup?: string
  available_space_bytes?: number
}

export interface RestoreResult {
  success: boolean
  backup_id: string
  restore_mode: RestoreMode
  collections_restored: string[]
  documents_restored: Record<string, number>
  started_at: string
  completed_at?: string
  duration_seconds: number
  error_message?: string
  warnings: string[]
}

export interface BackupProgress {
  backup_id: string
  status: BackupStatus
  progress_percent: number
  current_collection?: string
  collections_completed: number
  total_collections: number
  documents_processed: number
  bytes_written: number
  started_at: string
  estimated_completion?: string
  message?: string
}

export interface CreateBackupRequest {
  backup_type: BackupType
  profile_key?: string
  name?: string
  include_embeddings?: boolean
  include_system_collections?: boolean
}

export interface CreateCheckpointRequest {
  name: string
  profile_key?: string
  description?: string
}

export interface RestoreBackupRequest {
  backup_id: string
  restore_mode: RestoreMode
  collections?: string[]
  skip_users?: boolean
  skip_sessions?: boolean
  target_database?: string
}

export interface UpdateBackupConfigRequest {
  auto_backup_after_ingestion?: boolean
  retention_days?: number
  max_backups_per_profile?: number
  compression_enabled?: boolean
  include_embeddings?: boolean
  backup_schedule?: string
}

// ==================== Backup API ====================

export const backupsApi = {
  // Create a new backup
  create: async (request: CreateBackupRequest): Promise<BackupMetadata> => {
    const response = await api.post('/backups/create', request)
    return response.data
  },

  // Create a checkpoint
  createCheckpoint: async (request: CreateCheckpointRequest): Promise<BackupMetadata> => {
    const response = await api.post('/backups/checkpoint', request)
    return response.data
  },

  // List all backups
  list: async (profileKey?: string, backupType?: BackupType, limit: number = 50, skip: number = 0): Promise<BackupListResponse> => {
    const params: Record<string, unknown> = { limit, skip }
    if (profileKey) params.profile_key = profileKey
    if (backupType) params.backup_type = backupType
    const response = await api.get('/backups/', { params })
    return response.data
  },

  // List checkpoints only
  listCheckpoints: async (profileKey?: string, limit: number = 50): Promise<BackupListResponse> => {
    const params: Record<string, unknown> = { limit }
    if (profileKey) params.profile_key = profileKey
    const response = await api.get('/backups/checkpoints', { params })
    return response.data
  },

  // Get backup details
  getDetails: async (backupId: string): Promise<BackupMetadata> => {
    const response = await api.get(`/backups/${backupId}`)
    return response.data
  },

  // Get backup chain (for incremental backups)
  getChain: async (backupId: string): Promise<BackupMetadata[]> => {
    const response = await api.get(`/backups/${backupId}/chain`)
    return response.data
  },

  // Restore from backup
  restore: async (backupId: string, request: RestoreBackupRequest): Promise<RestoreResult> => {
    const response = await api.post(`/backups/${backupId}/restore`, request)
    return response.data
  },

  // Delete a backup
  delete: async (backupId: string): Promise<{ success: boolean; message: string }> => {
    const response = await api.delete(`/backups/${backupId}`)
    return response.data
  },

  // Get backup configuration
  getConfig: async (): Promise<BackupConfig> => {
    const response = await api.get('/backups/config')
    return response.data
  },

  // Update backup configuration
  updateConfig: async (config: UpdateBackupConfigRequest): Promise<BackupConfig> => {
    const response = await api.put('/backups/config', config)
    return response.data
  },

  // Get current backup progress
  getStatus: async (): Promise<BackupProgress | null> => {
    const response = await api.get('/backups/status')
    return response.data
  },

  // Get storage statistics
  getStorageStats: async (): Promise<StorageStats> => {
    const response = await api.get('/backups/storage')
    return response.data
  },
}

// ============== Embedding Benchmark Types ==============

export interface BenchmarkProviderConfig {
  provider_type: string  // "openai", "ollama", "vllm", "custom", "llamacpp"
  model: string
  base_url?: string
  api_key?: string
  name?: string
  // Task prefixes (auto-detected from the model family when both are empty)
  query_prefix?: string
  document_prefix?: string
  // Client-side Matryoshka truncation target (undefined = native dimension)
  truncate_dim?: number | null
}

export interface ChunkConfig {
  chunk_size: number
  chunk_overlap: number
  max_tokens: number
}

export interface BenchmarkRequest {
  providers: BenchmarkProviderConfig[]
  file_content: string  // Base64 encoded
  file_name: string
  chunk_config?: ChunkConfig
}

export interface BenchmarkMetrics {
  provider: string
  model: string
  provider_type: string
  total_time_ms: number
  chunking_time_ms: number
  embedding_time_ms: number
  avg_latency_ms: number
  tokens_processed: number
  chunks_created: number
  embedding_dimension: number
  memory_before_mb: number
  memory_after_mb: number
  memory_peak_mb: number
  cpu_percent: number
  cost_estimate_usd?: number | null
  success: boolean
  error?: string | null
}

export interface BenchmarkResult {
  id: string
  timestamp: string
  file_name: string
  file_size_bytes: number
  content_preview: string
  chunk_config: ChunkConfig
  results: BenchmarkMetrics[]
  winner?: string | null
}

export interface BenchmarkModelInfo {
  id: string
  name: string
  dimension: number
  multimodal?: boolean
}

export interface ProviderAvailability {
  name: string
  available: boolean
  url?: string | null
  models: BenchmarkModelInfo[]
}

export interface AvailableProviders {
  openai: ProviderAvailability
  ollama: ProviderAvailability
  llamacpp?: ProviderAvailability
  vllm: ProviderAvailability
}

export interface ProviderTestResult {
  success: boolean
  provider: string
  model: string
  latency_ms: number
  dimension: number
  error?: string | null
}

// ============== Embedding Validation Checks Types ==============
// Mirrors backend/services/embedding_checks.py

export interface EmbeddingCheckOptions {
  sample_size?: number
  batch_sizes?: number[]
  single_batch_limit?: number
  truncate_dims?: number[]
  latency_runs?: number
  top_k?: number
  pseudo_query_every?: number
  check_images?: boolean
  query_text?: string
}

export interface EmbeddingCheckSample {
  source: 'index' | 'file'
  limit?: number
  collection?: string
  compare_with_index?: boolean
  file_content?: string  // Base64, when source === 'file'
  file_name?: string
  chunk_config?: ChunkConfig
}

export interface EmbeddingChecksRequest {
  providers: BenchmarkProviderConfig[]
  options?: EmbeddingCheckOptions
  sample?: EmbeddingCheckSample
}

export interface EmbeddingLatencyStats {
  cold_ms: number
  p50_ms: number
  p95_ms: number
  runs: number
}

export interface EmbeddingThroughputPoint {
  batch_size: number
  texts: number
  wall_ms: number
  texts_per_s: number
  tokens: number
  tokens_per_s: number
  token_source: 'server' | 'estimate' | string
}

export interface EmbeddingVectorStats {
  dimension: number
  expected_dimension?: number | null
  dimension_matches?: boolean | null
  norm_mean: number
  norm_min: number
  norm_max: number
  normalized: boolean
}

export interface EmbeddingTruncationCheck {
  dim: number
  topk_overlap: number
}

export interface EmbeddingRetrievalSanity {
  queries: number
  top1_hits: number
  top5_hits: number
  top1_rate: number
  top5_rate: number
}

export interface EmbeddingImageProbe {
  supported: boolean
  tokens?: number | null
  latency_ms?: number | null
  vector_changed?: boolean | null
  error?: string | null
}

export interface EmbeddingServerCapabilities {
  build?: string | null
  model_alias?: string | null
  modalities: Record<string, boolean>
  context_length?: number | null
  slots?: number | null
}

export interface EmbeddingGpuInfo {
  name: string
  memory_total_mb?: number | null
  memory_used_mb?: number | null
  driver_version?: string | null
  utilization_percent?: number | null
}

export interface EmbeddingHardwareInfo {
  hostname: string
  cpu_count?: number | null
  ram_gb?: number | null
  gpus: EmbeddingGpuInfo[]
  note: string
}

export interface EmbeddingProviderCheckResult {
  provider: string
  model: string
  provider_type: string
  base_url?: string | null
  success: boolean
  error?: string | null
  warnings: string[]
  query_prefix: string
  document_prefix: string
  truncate_dim?: number | null
  capabilities?: EmbeddingServerCapabilities | null
  hardware?: EmbeddingHardwareInfo | null
  latency?: EmbeddingLatencyStats | null
  throughput: EmbeddingThroughputPoint[]
  vectors?: EmbeddingVectorStats | null
  truncation: EmbeddingTruncationCheck[]
  retrieval?: EmbeddingRetrievalSanity | null
  image?: EmbeddingImageProbe | null
  agreement_with_index?: number | null
  total_ms: number
}

export interface EmbeddingAgreementPair {
  a: string
  b: string
  topk_overlap: number
}

export interface EmbeddingSampleInfo {
  source: 'index' | 'file' | string
  count: number
  avg_chars: number
  database?: string | null
  collection?: string | null
  file_name?: string | null
  reference_model?: string | null
  reference_dimension?: number | null
}

export interface EmbeddingChecksResult {
  id: string
  timestamp: string
  kind: 'checks'
  sample: EmbeddingSampleInfo
  options: Required<EmbeddingCheckOptions>
  results: EmbeddingProviderCheckResult[]
  agreement: EmbeddingAgreementPair[]
}

// ============== Embedding Benchmark API ==============

export const benchmarkApi = {
  // Run benchmark with multiple providers
  runBenchmark: async (request: BenchmarkRequest): Promise<BenchmarkResult> => {
    const response = await api.post('/benchmark/run', request)
    return response.data
  },

  // Get available embedding providers
  getProviders: async (): Promise<AvailableProviders> => {
    const response = await api.get('/benchmark/providers')
    return response.data
  },

  // Test a single provider
  testProvider: async (config: BenchmarkProviderConfig): Promise<ProviderTestResult> => {
    const response = await api.post('/benchmark/test-provider', config)
    return response.data
  },

  // Get benchmark history
  getHistory: async (limit: number = 20): Promise<{ results: BenchmarkResult[]; total: number }> => {
    const response = await api.get('/benchmark/results', { params: { limit } })
    return response.data
  },

  // Get specific benchmark result
  getResult: async (benchmarkId: string): Promise<BenchmarkResult> => {
    const response = await api.get(`/benchmark/results/${benchmarkId}`)
    return response.data
  },

  // Delete benchmark result
  deleteResult: async (benchmarkId: string): Promise<{ success: boolean; message: string }> => {
    const response = await api.delete(`/benchmark/results/${benchmarkId}`)
    return response.data
  },

  // Run the embedding validation checks (latency, throughput, MRL, retrieval sanity, image probe, index agreement)
  runChecks: async (request: EmbeddingChecksRequest): Promise<EmbeddingChecksResult> => {
    const response = await api.post('/benchmark/checks', request, { timeout: 30 * 60 * 1000 })
    return response.data
  },

  // Get validation-check history
  getChecksHistory: async (limit: number = 20): Promise<{ results: EmbeddingChecksResult[]; total: number }> => {
    const response = await api.get('/benchmark/checks/results', { params: { limit } })
    return response.data
  },
}

// ============== Support API ==============

export interface SupportDiagnosticResponse {
  diagnostic: string
}

export interface SupportRequestPayload {
  diagnostic_text: string
  session_id?: string
  user_description?: string
}

export interface SupportRequestResponse {
  success: boolean
  message: string
}

export const supportApi = {
  // Get session diagnostic info for support request
  getSessionDiagnostic: async (sessionId: string): Promise<SupportDiagnosticResponse> => {
    const response = await api.get(`/support/diagnostic/${sessionId}`)
    return response.data
  },

  // Submit a support request with diagnostic data
  submitSupportRequest: async (data: SupportRequestPayload): Promise<SupportRequestResponse> => {
    const response = await api.post('/support/request', data)
    return response.data
  },
}

// =============================================================================
// Debug API (admin only)
// =============================================================================

export interface DebugSystemState {
  orchestrator_model: string
  orchestrator_provider: string
  worker_model: string
  worker_provider: string
  ollama_url: string
  active_profile: string
  database: string
  uptime_seconds: number
  active_requests: number
}

export interface DebugActiveRequest {
  request_id: string
  session_id: string
  model: string
  started_at: string
  /** Elapsed time in seconds (returned by the backend). */
  elapsed_seconds: number
  /** Elapsed time in ms (computed client-side from elapsed_seconds). */
  elapsed_ms: number
  user_id?: string
  is_admin?: boolean
  started_ts?: number
}

export interface DebugActivityEntry {
  type: string
  phase?: string
  model?: string
  duration_ms?: number
  tokens?: number
  query?: string
  results_count?: number
  error?: string
  timestamp?: string
  [key: string]: unknown
}

export interface DebugActivityItem {
  request_id: string
  session_id?: string
  model?: string
  started_at: string
  completed_at?: string
  duration_ms: number
  total_tokens?: number
  phases_count?: number
  status: 'complete' | 'error' | 'in_progress'
  entries?: DebugActivityEntry[]
}

/** Backend response shape for GET /debug/activity/live. */
export interface DebugLiveActivity {
  activity: DebugActivityItem[]
  count: number
}

/** Backend response shape for GET /debug/activity/active. */
export interface DebugActiveRequestsResponse {
  active: DebugActiveRequest[]
  count: number
}

export interface DebugRequestDetail {
  request_id: string
  session_id?: string
  model?: string
  started_at: string
  completed_at?: string
  duration_ms: number
  total_tokens?: number
  status: string
  entries: DebugActivityEntry[]
}

// Internal raw shapes returned by the backend, used only inside debugApi wrappers.
interface RawSystemState {
  orchestrator?: string
  worker?: string
  ollama_url?: string
  active_profile?: string
  database?: string
  uptime_seconds?: number
  active_requests_count?: number
  tenant_id?: string
  timestamp?: string
}

interface RawActiveRequest {
  request_id: string
  session_id: string
  user_id?: string
  model: string
  is_admin?: boolean
  started_at: string
  started_ts?: number
  elapsed_seconds?: number
}

interface RawActivityItem {
  request_id: string
  session_id?: string
  user_id?: string
  is_admin?: boolean
  started_at: string
  completed_at?: string
  duration_ms: number
  summary?: {
    total_tokens?: number
    total_errors?: number
    phases_completed?: string[]
    models_used?: string[]
    [key: string]: unknown
  }
}

function splitProviderModel(combined?: string): { provider: string; model: string } {
  if (!combined) return { provider: '', model: '' }
  const idx = combined.indexOf('/')
  if (idx < 0) return { provider: '', model: combined }
  return { provider: combined.slice(0, idx), model: combined.slice(idx + 1) }
}

function normalizeActivityItem(raw: RawActivityItem): DebugActivityItem {
  const summary = raw.summary || {}
  const models = Array.isArray(summary.models_used) ? summary.models_used : []
  const phases = Array.isArray(summary.phases_completed) ? summary.phases_completed : []
  const errors = typeof summary.total_errors === 'number' ? summary.total_errors : 0
  const status: DebugActivityItem['status'] = raw.completed_at
    ? (errors > 0 ? 'error' : 'complete')
    : 'in_progress'
  return {
    request_id: raw.request_id,
    session_id: raw.session_id,
    started_at: raw.started_at,
    completed_at: raw.completed_at,
    duration_ms: raw.duration_ms,
    model: models[0],
    total_tokens: typeof summary.total_tokens === 'number' ? summary.total_tokens : undefined,
    phases_count: phases.length || undefined,
    status,
  }
}

export const debugApi = {
  getLiveActivity: async (): Promise<DebugLiveActivity> => {
    const response = await api.get('/debug/activity/live')
    const raw = response.data || {}
    const items: RawActivityItem[] = Array.isArray(raw.activity) ? raw.activity : []
    return {
      activity: items.map(normalizeActivityItem),
      count: typeof raw.count === 'number' ? raw.count : items.length,
    }
  },

  getActiveRequests: async (): Promise<DebugActiveRequestsResponse> => {
    const response = await api.get('/debug/activity/active')
    const raw = response.data || {}
    const items: RawActiveRequest[] = Array.isArray(raw.active) ? raw.active : []
    const active: DebugActiveRequest[] = items.map((r) => {
      const elapsed_seconds = typeof r.elapsed_seconds === 'number' ? r.elapsed_seconds : 0
      return {
        request_id: r.request_id,
        session_id: r.session_id,
        model: r.model,
        started_at: r.started_at,
        elapsed_seconds,
        elapsed_ms: Math.round(elapsed_seconds * 1000),
        user_id: r.user_id,
        is_admin: r.is_admin,
        started_ts: r.started_ts,
      }
    })
    return { active, count: typeof raw.count === 'number' ? raw.count : active.length }
  },

  getSystemState: async (): Promise<DebugSystemState> => {
    const response = await api.get('/debug/system-state')
    const raw: RawSystemState = response.data || {}
    const orch = splitProviderModel(raw.orchestrator)
    const wrk = splitProviderModel(raw.worker)
    return {
      orchestrator_provider: orch.provider,
      orchestrator_model: orch.model,
      worker_provider: wrk.provider,
      worker_model: wrk.model,
      ollama_url: raw.ollama_url || '',
      active_profile: raw.active_profile || '',
      database: raw.database || '',
      uptime_seconds: raw.uptime_seconds || 0,
      active_requests: raw.active_requests_count || 0,
    }
  },

  getRequestDetail: async (requestId: string): Promise<DebugRequestDetail> => {
    const response = await api.get(`/debug/activity/${requestId}`)
    return response.data
  },
}

// ============== Telemetry history (host agent + activity, superadmin) ==============

/** One aggregation bucket of host metrics (averages unless the name ends in `_max`). */
export interface TelemetryHostPoint {
  t: string
  samples: number
  cpu_pct: number | null
  cpu_pct_max: number | null
  mem_pct: number | null
  mem_pct_max: number | null
  mem_used_bytes: number | null
  mem_total_bytes: number | null
  gpu_util_pct: number | null
  gpu_util_pct_max: number | null
  vram_used_bytes: number | null
  vram_total_bytes: number | null
  gpu_temp_c: number | null
  gpu_temp_c_max: number | null
  cpu_temp_c: number | null
  cpu_temp_c_max: number | null
  gpu_power_w: number | null
  gpu_power_w_max: number | null
  cpu_power_w: number | null
  cpu_power_w_max: number | null
  disk_read_bps: number | null
  disk_write_bps: number | null
  net_rx_bps: number | null
  net_tx_bps: number | null
  backend_ms_max: number | null
  backend_down: number
  gpu_throttled: number
}

export interface TelemetryContainerPoint {
  t: string
  cpu_cores: number | null
  cpu_cores_max: number | null
  mem_bytes: number | null
  mem_bytes_max: number | null
}

export interface TelemetryActivityPoint {
  t: string
  files_ok: number
  files_failed: number
  files_other: number
  file_ms_avg: number | null
  chats: number
  chat_errors: number
  chat_ms_avg: number | null
}

export interface TelemetryPhaseInterval {
  job_id: string
  phase: string
  start: string
  end: string
  open: boolean
}

export interface TelemetryJobSummary {
  job_id: string
  status: string | null
  tenant: string | null
  source: string | null
  first_ts: string
  last_ts: string
  files: number
  failed: number
}

export interface TelemetryHistory {
  start: string
  end: string
  bucket_seconds: number
  agent_samples: number
  host: TelemetryHostPoint[]
  containers: Record<string, TelemetryContainerPoint[]>
  activity: TelemetryActivityPoint[]
  phases: TelemetryPhaseInterval[]
  jobs: TelemetryJobSummary[]
}

export interface TelemetryEpisode {
  start: string
  end: string
  samples: number
  peak: number | null
  detail: string | null
}

export type TelemetrySeverity = 'critical' | 'warning' | 'info'

export interface TelemetryAnomaly {
  kind: string
  severity: TelemetrySeverity
  title: string
  detail: string
  count: number
  episodes: TelemetryEpisode[]
  examples: Array<Record<string, unknown>>
}

export interface TelemetryAnomalyReport {
  start: string
  end: string
  anomalies: TelemetryAnomaly[]
}

export interface TelemetryStepStat {
  step: string
  files: number
  p50_ms: number | null
  p95_ms: number | null
  max_ms: number | null
  total_ms: number | null
  share_pct: number | null
}

export interface TelemetryExtStat {
  ext: string
  files: number
  ok: number
  failed: number
  empty: number
  p50_ms: number | null
  p95_ms: number | null
  max_ms: number | null
  bytes: number | null
  pages: number | null
  ms_per_page: number | null
}

export interface TelemetryFileRow {
  path: string
  ext: string | null
  outcome: string | null
  duration_ms: number | null
  size_bytes: number | null
  pages: number | null
  chunks: number | null
  steps: Record<string, number>
  error_type: string | null
  error: string | null
  ts: string | null
}

export interface TelemetryPhaseResources {
  phase: string
  start: string
  end: string
  duration_s: number
  host: Record<string, number | null>
  containers: Record<string, Record<string, number | null>>
}

export interface TelemetryJobReport {
  job_id: string
  status: string | null
  tenant: string | null
  source: string | null
  start: string
  end: string
  running: boolean
  files: number
  outcomes: Record<string, number>
  total_bytes: number | null
  total_pages: number | null
  total_chunks: number | null
  files_per_hour: number | null
  phases: TelemetryPhaseResources[]
  steps: TelemetryStepStat[]
  by_ext: TelemetryExtStat[]
  slowest: TelemetryFileRow[]
  failures: Array<Record<string, unknown>>
  unfinished: TelemetryFileRow[]
  throughput: TelemetryActivityPoint[]
  /** Bucket size of `throughput`; buckets without files are omitted (zero). */
  throughput_bucket_seconds: number
  anomalies: TelemetryAnomaly[]
  agent_samples: number
}

// ── MongoDB statistics (db_samples) and the health overview ──────────────────

export interface DbCollectionStats {
  count: number | null
  /** Uncompressed BSON size. */
  data_bytes: number | null
  /** Compressed size on disk, indexes excluded. */
  storage_bytes: number | null
  index_bytes: number | null
  avg_bytes: number | null
}

export interface DbSearchIndex {
  name: string
  /** 'search' or 'vectorSearch'. */
  type: string | null
  /** READY, BUILDING, FAILED, ... */
  status: string | null
  queryable: boolean | null
  dimensions: number | null
  similarity: string | null
  quantization: string | null
}

export interface DbSearchLag {
  index: string
  indexed_count: number
  collection_count: number
  lag: number
}

export interface DbChunkSample {
  size: number
  with_embedding: number
  embedding_coverage_pct: number | null
  /** Vector length -> chunks in the sample. */
  embedding_dims: Record<string, number>
  embedding_models: Record<string, number>
  chunk_methods: Record<string, number>
  with_context_pct: number | null
  token_p50: number | null
  token_p95: number | null
}

export interface DbDocumentSample {
  size: number
  zero_chunks: number
  zero_chunks_pct: number | null
}

export interface DbBackupInfo {
  count: number
  newest_at: string | null
  newest_status: string | null
  newest_type: string | null
  newest_size_bytes: number | null
}

export interface DbDatabaseStats {
  name: string
  profile_key: string | null
  data_bytes: number | null
  storage_bytes: number | null
  index_bytes: number | null
  size_on_disk_bytes: number | null
  collections: number | null
  objects: number | null
  documents: DbCollectionStats | null
  chunks: DbCollectionStats | null
  sample: DbChunkSample | null
  document_sample: DbDocumentSample | null
  search_indexes: DbSearchIndex[]
  search_lag: DbSearchLag | null
  jobs_by_status: Record<string, number>
  backup: DbBackupInfo | null
  failed_documents: number | null
  file_registry: number | null
}

export interface DbConnections {
  current: number | null
  active: number | null
  available: number | null
  total_created: number | null
}

export interface DbWiredTigerCache {
  used_bytes: number | null
  max_bytes: number | null
  dirty_bytes: number | null
}

export interface DbServerStats {
  version: string | null
  uptime_s: number | null
  connections: DbConnections
  wt_cache: DbWiredTigerCache
  opcounters: Record<string, number>
  mem_resident_bytes: number | null
}

export interface DbOplogStats {
  data_bytes: number | null
  storage_bytes: number | null
  max_bytes: number | null
  window_s: number | null
  first_at: string | null
  last_at: string | null
}

export interface DbFsStats {
  used_bytes: number | null
  total_bytes: number | null
  pct: number | null
}

export interface DbSizes {
  data_bytes: number | null
  storage_bytes: number | null
  index_bytes: number | null
  size_on_disk_bytes: number | null
}

export interface DbTotals {
  databases: number
  documents: number
  chunks: number
  data_bytes: number
  storage_bytes: number
  index_bytes: number
  size_on_disk_bytes: number
}

/** One db-stats run (`GET /telemetry/db-stats` → `latest`, `POST /telemetry/db-stats/collect`). */
export interface DbSample {
  ts: string
  collector: string | null
  forced: boolean
  duration_ms: number | null
  server: DbServerStats | null
  oplog: DbOplogStats | null
  fs: DbFsStats | null
  telemetry: DbSizes | null
  totals: DbTotals
  databases: DbDatabaseStats[]
  errors: Record<string, string>
}

export interface DbSeriesDatabasePoint {
  name: string
  documents: number | null
  chunks: number | null
  storage_bytes: number | null
  data_bytes: number | null
  index_bytes: number | null
  embedding_coverage_pct: number | null
  search_lag: number | null
}

/** Charting fields of one db-stats sample (the last sample of its bucket). */
export interface DbSeriesPoint {
  t: string
  connections_current: number | null
  connections_total_created: number | null
  connections_created_per_min: number | null
  wt_cache_used_bytes: number | null
  wt_cache_max_bytes: number | null
  ops_insert: number | null
  ops_query: number | null
  inserts_per_min: number | null
  oplog_bytes: number | null
  oplog_max_bytes: number | null
  oplog_window_s: number | null
  fs_used_bytes: number | null
  fs_total_bytes: number | null
  size_on_disk_bytes: number | null
  data_bytes: number | null
  storage_bytes: number | null
  index_bytes: number | null
  chunks: number | null
  documents: number | null
  databases: DbSeriesDatabasePoint[]
}

export interface DbStatsResponse {
  start: string
  end: string
  bucket_seconds: number
  interval_seconds: number
  samples: number
  points: DbSeriesPoint[]
  latest: DbSample | null
  next_due_at: string | null
  lease_holder: string | null
}

export interface OverviewDb {
  sampled_at: string
  age_seconds: number
  databases: number
  documents: number
  chunks: number
  data_bytes: number
  storage_bytes: number
  index_bytes: number
  size_on_disk_bytes: number
  oplog_bytes: number | null
  embedding_coverage_pct: number | null
  search_indexes_ok: boolean | null
  search_lag: number | null
}

export interface OverviewAnomalies {
  hours: number
  critical: number
  warning: number
  info: number
  top: string[]
}

export interface OverviewProcess {
  key: string
  tenant: string | null
  source: 'api' | 'worker' | string | null
  host: string | null
  last_seen_at: string | null
  age_seconds: number | null
  online: boolean
  job_id: string | null
  job_status: string | null
  phase: string | null
  step: string | null
  processed_files: number | null
  total_files: number | null
  files_per_hour: number | null
  eta_seconds: number | null
  /** per_extension (shared estimator), overall, or pace (remaining files at files_per_hour). */
  eta_method?: string | null
  eta_by_extension?: OverviewEtaExtension[]
}

/** Remaining files of one extension and the pace they are estimated at. */
export interface OverviewEtaExtension {
  extension: string
  remaining: number
  files_per_minute: number | null
  seconds: number | null
  pace_source: string | null
}

export interface OverviewChat {
  hours: number
  requests: number
  errors: number
  p50_ms: number | null
  p95_ms: number | null
}

export interface OverviewBackup {
  database: string
  profile_key: string | null
  count: number
  newest_at: string | null
  age_seconds: number | null
  size_bytes: number | null
  status: string | null
}

export interface OverviewLlmModel {
  name: string
  size_bytes: number | null
  vram_bytes: number | null
  ctx: number | null
}

export interface OverviewLlm {
  sampled_at: string | null
  models: OverviewLlmModel[]
}

/** Health at a glance (`GET /telemetry/overview`), the machine-stats widget's second poll. */
export interface TelemetryOverview {
  generated_at: string
  db: OverviewDb | null
  anomalies: OverviewAnomalies
  processes: OverviewProcess[]
  chat: OverviewChat
  backups: OverviewBackup[]
  llm: OverviewLlm
}

/** Telemetry history API (`/api/v1/telemetry`, superadmin only; 503 when history is disabled). */
export const telemetryApi = {
  history: async (hours: number, bucket?: number): Promise<TelemetryHistory> => {
    const response = await api.get('/telemetry/history', { params: { hours, ...(bucket ? { bucket } : {}) } })
    return response.data
  },

  jobs: async (hours = 168, limit = 50): Promise<TelemetryJobSummary[]> => {
    const response = await api.get('/telemetry/jobs', { params: { hours, limit } })
    return response.data
  },

  job: async (jobId: string): Promise<TelemetryJobReport> => {
    const response = await api.get(`/telemetry/jobs/${encodeURIComponent(jobId)}`)
    return response.data
  },

  anomalies: async (hours: number): Promise<TelemetryAnomalyReport> => {
    const response = await api.get('/telemetry/anomalies', { params: { hours } })
    return response.data
  },

  /** The job's self-contained HTML report as a Blob (fetched with the session token). */
  jobReportHtml: async (jobId: string): Promise<Blob> => {
    const response = await api.get(`/telemetry/jobs/${encodeURIComponent(jobId)}/report.html`, { responseType: 'blob' })
    return response.data
  },

  /** MongoDB statistics series over the range plus the newest full sample. */
  dbStats: async (hours: number, bucket?: number): Promise<DbStatsResponse> => {
    const response = await api.get('/telemetry/db-stats', { params: { hours, ...(bucket ? { bucket } : {}) } })
    return response.data
  },

  /** Take a db-stats sample now (503 when the collector is not running on this backend). */
  collectDbStats: async (): Promise<DbSample> => {
    const response = await api.post('/telemetry/db-stats/collect')
    return response.data
  },

  /** Health at a glance for the machine-stats widget. */
  overview: async (hours = 24): Promise<TelemetryOverview> => {
    const response = await api.get('/telemetry/overview', { params: { hours } })
    return response.data
  },
}
