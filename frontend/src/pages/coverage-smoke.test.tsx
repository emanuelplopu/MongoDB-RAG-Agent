import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ConfigurationPage from './ConfigurationPage'
import ChatPageNew from './ChatPageNew'
import IngestionManagementPage from './IngestionManagementPage'
import BackupManagementPage, { getStatusColor } from './BackupManagementPage'
import CloudSourceConnectionsPage from './CloudSourceConnectionsPage'
import EmailCloudConfigPage from './EmailCloudConfigPage'
import EmbeddingBenchmarkPage from './EmbeddingBenchmarkPage'
import JobHistoryPage from './JobHistoryPage'
import PromptManagementPage from './PromptManagementPage'
import StrategyABTestPage from './StrategyABTestPage'
import IngestionAnalyticsPage from './IngestionAnalyticsPage'
import LiveDebugPage from './LiveDebugPage'
import StrategiesPage from './StrategiesPage'
import FailedDocumentsPage from './FailedDocumentsPage'

const authState = vi.hoisted(() => ({
  user: {
    id: 'admin-1',
    email: 'admin@example.com',
    name: 'Admin User',
    full_name: 'Admin User',
    is_admin: true,
    is_active: true,
  },
  isLoading: false,
}))

const chatSidebarState = vi.hoisted(() => ({
  currentSession: {
    id: 'session-1',
    title: 'Coverage Session',
    model: 'gpt-5.2',
    messages: [
      {
        id: 'm-user',
        role: 'user',
        content: 'Summarize the plan',
        timestamp: '2026-05-18T08:00:00Z',
        created_at: '2026-05-18T08:00:00Z',
        attachments: [
          {
            filename: 'brief.txt',
            content_type: 'text/plain',
            size_bytes: 2048,
            token_estimate: 512,
          },
          {
            filename: 'diagram.png',
            content_type: 'image/png',
            size_bytes: 4096,
            data_url: 'data:image/png;base64,abc123',
            token_estimate: 765,
          },
        ],
      },
      {
        id: 'm-assistant',
        role: 'assistant',
        content: 'The plan is ready. [Source: "Plan.md"]',
        timestamp: '2026-05-18T08:00:02Z',
        created_at: '2026-05-18T08:00:02Z',
        sources: [
          {
            document_id: 'doc-1',
            title: 'Plan.md',
            source: '/docs/Plan.md',
            score: 0.91,
            relevance: 0.91,
            chunk_id: 'chunk-1',
            excerpt: 'Planning excerpt',
          },
        ],
        stats: {
          total_tokens: 120,
          input_tokens: 50,
          output_tokens: 70,
          cost_usd: 0.002,
          tokens_per_second: 18.4,
          latency_ms: 1400,
        },
        thinking: {
          total_duration_ms: 640,
          search: {
            total_results: 3,
            operations: [
              {
                index_type: 'vector',
                index_name: 'vector_index',
                query: 'strategy operating system rollout risks and plan',
                results_count: 3,
                duration_ms: 180,
                top_score: 0.91,
                top_results: [
                  { title: 'Plan.md', score: 0.91, excerpt: 'Planning excerpt' },
                  { title: 'Risk.md', score: 0.72, excerpt: 'Risk excerpt' },
                ],
              },
            ],
          },
          tool_calls: [
            {
              tool_name: 'browse_web',
              tool_input: { url: 'https://example.com' },
              success: true,
              duration_ms: 220,
              result_summary: 'Fetched source metadata',
            },
            {
              tool_name: 'search',
              tool_input: { query: 'strategy os' },
              success: false,
              duration_ms: 90,
              error: 'temporary failure',
            },
          ],
        },
        agent_trace: {
          mode: 'federated',
          iterations: 2,
          models: {
            orchestrator: 'gpt-5.2',
            worker: 'gpt-5.2-mini',
          },
          timing: {
            total_ms: 3456,
            orchestrator_ms: 1200,
            worker_ms: 2256,
          },
          tokens: {
            total: 1234,
            orchestrator: 456,
            worker: 778,
          },
          cost_usd: 0.0123,
          orchestrator_steps: [
            {
              phase: 'analyze',
              reasoning: 'Identify the strategy OS rollout needs and the most relevant internal documents.',
              output: '',
              duration_ms: 100,
              tokens: 20,
            },
            {
              phase: 'plan',
              reasoning: 'Search project plans and validate the remaining coverage gaps.',
              output: '',
              duration_ms: 150,
              tokens: 25,
              tasks: [
                { id: 'task-1', type: 'search_profile', query: 'strategy coverage' },
                { id: 'task-2', type: 'web_search', query: 'release notes' },
              ],
            },
          ],
          worker_steps: [
            {
              task_id: 'task-1',
              task_type: 'search_profile',
              tool: 'vector',
              input: { query: 'strategy coverage' },
              duration_ms: 500,
              success: true,
              documents: [
                { title: 'Plan.md', score: 0.91, excerpt: 'Planning excerpt' },
                { title: 'Risk.md', score: 0.84, excerpt: 'Risk excerpt' },
              ],
              web_links: [{ title: 'Docs', url: 'https://example.com/docs', excerpt: 'Docs excerpt' }],
            },
            {
              task_id: 'task-2',
              task_type: 'web_search',
              tool: 'browser',
              input: { query: 'release notes' },
              duration_ms: 200,
              success: false,
              documents: [],
              web_links: [],
            },
          ],
          sources: {
            documents: [
              { title: 'Plan.md', excerpt: 'Planning excerpt', score: 0.91, source_type: 'profile', source_database: 'rag_default' },
              { title: 'Risk.md', excerpt: 'Risk excerpt', score: 0.72, source_type: 'profile', source_database: 'rag_default' },
              { title: 'Notes.md', excerpt: 'Notes excerpt', score: 0.42, source_type: 'cloud', source_database: 'cloud' },
            ],
            web_links: [{ title: 'Docs', url: 'https://example.com/docs', excerpt: 'Docs excerpt' }],
          },
        },
        metadata: { total_tokens: 120, total_cost_usd: 0.002 },
      },
    ],
    stats: { total_tokens: 120, total_cost_usd: 0.002 },
    created_at: '2026-05-18T08:00:00Z',
    updated_at: '2026-05-18T08:00:02Z',
  },
  setCurrentSession: vi.fn(),
  setSessions: vi.fn(),
  handleNewChat: vi.fn(),
  pendingMessage: '',
  pendingAttachments: [],
  setPendingMessage: vi.fn(),
}))

const defaultChatSession = chatSidebarState.currentSession

const apiMocks = vi.hoisted(() => ({
  systemApi: {
    stats: vi.fn(),
    getConfigOptions: vi.fn(),
    listLLMModels: vi.fn(),
    listEmbeddingModels: vi.fn(),
    getLLMProviderConfig: vi.fn(),
    saveLLMProviderConfig: vi.fn(),
    listTools: vi.fn(),
    testTool: vi.fn(),
    getAgentPerformanceConfig: vi.fn(),
    saveAgentPerformanceConfig: vi.fn(),
    getIngestionPerformanceConfig: vi.fn(),
    saveIngestionPerformanceConfig: vi.fn(),
    getModelCapabilities: vi.fn(),
    testModelCapability: vi.fn(),
    approveModel: vi.fn(),
    fetchModelsFromApi: vi.fn(),
    testProviderConnection: vi.fn(),
    switchModelVersions: vi.fn(),
    saveConfigToDb: vi.fn(),
  },
  localLlmApi: {
    getOfflineConfig: vi.fn(),
    saveOfflineConfig: vi.fn(),
    getCustomEndpoints: vi.fn(),
    addCustomEndpoint: vi.fn(),
    deleteCustomEndpoint: vi.fn(),
    discover: vi.fn(),
    pullModel: vi.fn(),
    testModel: vi.fn(),
    scanNetwork: vi.fn(),
  },
  profilesApi: {
    list: vi.fn(),
  },
  ingestionApi: {
    getStatus: vi.fn(),
    getLogsStreamUrl: vi.fn(),
    getEventsStreamUrl: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(),
  },
  ingestionQueueApi: {
    getQueue: vi.fn(),
    getSchedules: vi.fn(),
    addMultipleToQueue: vi.fn(),
    removeFromQueue: vi.fn(),
    createSchedule: vi.fn(),
    deleteSchedule: vi.fn(),
    toggleSchedule: vi.fn(),
    runScheduleNow: vi.fn(),
  },
  fileRegistryApi: {
    getStats: vi.fn(),
  },
  backupsApi: {
    list: vi.fn(),
    getConfig: vi.fn(),
    getStorageStats: vi.fn(),
    getStatus: vi.fn(),
    create: vi.fn(),
    restore: vi.fn(),
    delete: vi.fn(),
    updateConfig: vi.fn(),
  },
  cloudSourcesApi: {
    getConnections: vi.fn(),
    getProviders: vi.fn(),
    getConnection: vi.fn(),
    getSyncConfigs: vi.fn(),
    testConnection: vi.fn(),
    deleteConnection: vi.fn(),
    refreshOAuthTokens: vi.fn(),
    createSyncConfig: vi.fn(),
  },
  benchmarkApi: {
    getProviders: vi.fn(),
    testProvider: vi.fn(),
    runBenchmark: vi.fn(),
    getHistory: vi.fn(),
  },
  promptsApi: {
    list: vi.fn(),
    compare: vi.fn(),
    test: vi.fn(),
    createVersion: vi.fn(),
    activateVersion: vi.fn(),
  },
  strategiesApi: {
    list: vi.fn(),
    getAllMetrics: vi.fn(),
    get: vi.fn(),
    abCompareResponses: vi.fn(),
  },
  sessionsApi: {
    create: vi.fn(),
    update: vi.fn(),
    sendMessage: vi.fn(),
    sendMessageStream: vi.fn(),
  },
  documentsApi: {
    findBySource: vi.fn(),
  },
  debugApi: {
    getSystemState: vi.fn(),
    getActiveRequests: vi.fn(),
    getLiveActivity: vi.fn(),
    getRequestDetail: vi.fn(),
  },
}))

const translate = vi.hoisted(() => {
  return (key: string, optionsOrDefault?: string | { defaultValue?: string; count?: number }) => {
    if (typeof optionsOrDefault === 'string') return optionsOrDefault
    if (optionsOrDefault?.defaultValue) return optionsOrDefault.defaultValue
    return key
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: translate,
    i18n: { language: 'en', changeLanguage: async () => {} },
  }),
  Trans: ({ children }: { children?: unknown }) => children,
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../contexts/LanguageContext', () => ({
  useLanguage: () => ({ language: 'en', setLanguage: vi.fn() }),
}))

vi.mock('../contexts/ChatSidebarContext', () => ({
  useChatSidebar: () => chatSidebarState,
}))

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({
    showToast: vi.fn(),
    showSuccess: vi.fn(),
    showError: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
  }),
}))

vi.mock('../contexts/UserPreferencesContext', () => ({
  useUserPreferences: () => ({
    preferences: {
      defaultAgentMode: 'auto',
      defaultSearchType: 'hybrid',
      defaultMatchCount: 10,
      streamResponses: false,
    },
  }),
}))

vi.mock('../hooks/useKeyboardShortcuts', () => ({
  useKeyboardShortcuts: vi.fn(),
  useEscapeKey: vi.fn(),
  useFocusTrap: vi.fn(),
}))

vi.mock('../components/ModelVersionSelector', () => ({
  default: ({ label }: { label?: string }) => <div data-testid="model-version-selector">{label ?? 'model selector'}</div>,
}))

vi.mock('../components/FolderPicker', () => ({
  default: ({
    onChange,
    onSelect,
  }: {
    onChange?: (paths: string[]) => void
    onSelect?: (folder: { id: string; path: string; name: string }) => void
  }) => (
    <button
      type="button"
      onClick={() => {
        onChange?.(['/docs'])
        onSelect?.({ id: 'remote-docs', path: '/docs', name: 'Docs' })
      }}
    >
      Pick folder
    </button>
  ),
}))

vi.mock('../components/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <div data-testid="markdown-renderer">{content}</div>,
}))

vi.mock('../components/FederatedAgentPanel', () => ({
  default: () => <div data-testid="federated-agent-panel" />,
}))

vi.mock('../components/SimplifiedAgentPanel', () => ({
  default: () => <div data-testid="simplified-agent-panel" />,
}))

vi.mock('../components/SimplifiedThinkingPanel', () => ({
  default: () => <div data-testid="simplified-thinking-panel" />,
}))

vi.mock('../components/SimplifiedLiveTrace', () => ({
  default: () => <div data-testid="simplified-live-trace" />,
}))

vi.mock('../api/client', () => ({
  ...apiMocks,
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status = 500) {
      super(message)
      this.status = status
    }
  },
}))

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

function renderRoute(ui: React.ReactElement, initialRoute = '/en/test') {
  return render(<MemoryRouter initialEntries={[initialRoute]}>{ui}</MemoryRouter>)
}

function renderWithParamRoute(ui: React.ReactElement, routePath: string, initialRoute: string) {
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <Routes>
        <Route path={routePath} element={ui} />
      </Routes>
    </MemoryRouter>
  )
}

function jsonResponse(data: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  } as Response)
}

const profilesResponse = {
  profiles: {
    default: {
      name: 'Default',
      description: 'Default profile',
      database: 'rag_default',
      documents_folders: ['/docs'],
    },
  },
  active_profile: 'default',
}

const modelCapabilities = [
  {
    id: 'openai:gpt-5.2',
    model_name: 'gpt-5.2',
    provider: 'openai',
    orchestrator: { tested: true, approved: true, auto_score: 8.7 },
    worker: { tested: true, approved: true, auto_score: 8.1 },
  },
]

const strategies = [
  {
    id: 'balanced',
    name: 'Balanced',
    description: 'Balanced strategy',
    domain: 'general',
    domains: ['general'],
    tags: ['balanced', 'general'],
    version: '1.0.0',
    is_active: true,
    is_default: true,
  },
  {
    id: 'legal',
    name: 'Legal',
    description: 'Legal strategy',
    domain: 'legal',
    domains: ['legal'],
    tags: ['legal'],
    version: '1.0.0',
    is_active: true,
    is_default: false,
  },
]

function resetApiMocks() {
  Object.values(apiMocks).forEach((api) => {
    Object.values(api as Record<string, unknown>).forEach((fn) => {
      if (typeof fn === 'function' && 'mockReset' in fn) {
        ;(fn as ReturnType<typeof vi.fn>).mockReset()
      }
    })
  })

  apiMocks.systemApi.stats.mockResolvedValue({
    database: { documents: { count: 4 }, chunks: { count: 30 } },
    indexes: { indexes: [] },
    config: { llm_provider: 'openai' },
  })
  apiMocks.systemApi.getConfigOptions.mockResolvedValue({
    current: {
      default_match_count: 10,
      llm_model: 'gpt-5.2',
      embedding_model: 'text-embedding-3-small',
      embedding_dimension: 1536,
    },
    options: {
      match_count_options: [5, 10, 20],
    },
  })
  apiMocks.systemApi.listLLMModels.mockResolvedValue({
    models: [{ id: 'gpt-5.2', name: 'gpt-5.2', provider: 'openai', available: true }],
  })
  apiMocks.systemApi.listEmbeddingModels.mockResolvedValue({
    models: [{ id: 'text-embedding-3-small', name: 'text-embedding-3-small', provider: 'openai', dimensions: 1536 }],
  })
  apiMocks.systemApi.getLLMProviderConfig.mockResolvedValue({
    orchestrator_provider: 'openai',
    orchestrator_model: 'gpt-5.2',
    worker_provider: 'openai',
    worker_model: 'gpt-5.2-mini',
    openai_api_key_set: true,
    openai_api_key_masked: 'sk-...',
    google_api_key_set: false,
    anthropic_api_key_set: false,
    fast_llm_api_key_set: false,
    providers: [
      { id: 'openai', name: 'OpenAI', models: ['gpt-5.2', 'gpt-5.2-mini'], configured: true },
      { id: 'google', name: 'Google', models: ['gemini-2.5-pro'], configured: false },
    ],
  })
  apiMocks.systemApi.listTools.mockResolvedValue({
    tools: [
      {
        id: 'search',
        name: 'search',
        description: 'Search documents',
        category: 'retrieval',
        icon: 'S',
        help_text: 'Searches indexed documents.',
        parameters: [{ name: 'query', type: 'string', required: true, description: 'Search query' }],
        enabled: true,
      },
    ],
  })
  apiMocks.systemApi.getAgentPerformanceConfig.mockResolvedValue({
    parallel_workers: 4,
    max_iterations: 3,
    global_max_orchestrators: 10,
    global_max_workers: 20,
    max_sources_per_search: 10,
    worker_timeout: 60,
    orchestrator_timeout: 120,
    total_timeout: 300,
    auto_fast_threshold: 50,
    skip_evaluation: false,
    default_mode: 'auto',
  })
  apiMocks.systemApi.getIngestionPerformanceConfig.mockResolvedValue({
    process_isolation_enabled: true,
    max_concurrent_files: 2,
    embedding_batch_size: 32,
    thread_pool_workers: 4,
    embedding_requests_per_minute: 3000,
    file_processing_timeout: 300,
    job_poll_interval_seconds: 1.0,
  })
  apiMocks.systemApi.getModelCapabilities.mockResolvedValue({ models: modelCapabilities })
  apiMocks.systemApi.saveLLMProviderConfig.mockResolvedValue({ success: true })
  apiMocks.systemApi.saveAgentPerformanceConfig.mockResolvedValue({ success: true })
  apiMocks.systemApi.saveIngestionPerformanceConfig.mockResolvedValue({ success: true })
  apiMocks.systemApi.testModelCapability.mockResolvedValue({
    success: true,
    model_id: 'openai:gpt-5.2',
    role: 'orchestrator',
    overall: 8.2,
    auto_pass: true,
    scores: { reasoning: 8, instruction_following: 9, coherence: 8, speed_score: 7 },
    latency_ms: 320,
    response_preview: 'ok',
  })
  apiMocks.systemApi.approveModel.mockResolvedValue({ success: true })
  apiMocks.systemApi.fetchModelsFromApi.mockResolvedValue({
    success: true,
    models: [{ id: 'gpt-5.2', name: 'gpt-5.2' }],
  })
  apiMocks.systemApi.testProviderConnection.mockResolvedValue({
    success: true,
    message: 'Connected',
    latency_ms: 150,
    response: 'Hello from model',
    logs: ['Connected'],
  })
  apiMocks.systemApi.testTool.mockResolvedValue({
    success: true,
    tool_id: 'search',
    result: 'Tool ok',
    result_preview: '{"ok":true}',
    latency_ms: 25,
    logs: ['Tool ok'],
  })
  apiMocks.systemApi.switchModelVersions.mockResolvedValue({ success: true, message: 'Switched' })
  apiMocks.systemApi.saveConfigToDb.mockResolvedValue({ success: true })

  apiMocks.localLlmApi.getOfflineConfig.mockResolvedValue({ enabled: true, preferred_provider: 'ollama' })
  apiMocks.localLlmApi.saveOfflineConfig.mockResolvedValue({ success: true })
  apiMocks.localLlmApi.getCustomEndpoints.mockResolvedValue({
    endpoints: [{ id: 'endpoint-1', name: 'Local Ollama', url: 'http://localhost:11434', provider_type: 'ollama' }],
  })
  apiMocks.localLlmApi.addCustomEndpoint.mockResolvedValue({ success: true })
  apiMocks.localLlmApi.deleteCustomEndpoint.mockResolvedValue({ success: true })
  apiMocks.localLlmApi.discover.mockResolvedValue({
    resources: {
      cpu_cores: 8,
      ram_total_gb: 32,
      ram_available_gb: 20,
      gpu_available: true,
      gpu_name: 'RTX Test',
      gpu_memory_gb: 12,
    },
    providers: [
      {
        id: 'ollama',
        name: 'Ollama',
        url: 'http://localhost:11434',
        host: 'localhost',
        location: 'host',
        status: 'available',
        models: [
          { name: 'llama3', size_gb: 4.7, type: 'chat' },
          { name: 'nomic-embed-text', size_gb: 0.5, type: 'embedding' },
          { name: 'llava', size_gb: 7.1, type: 'vision' },
          { name: 'whisper-local', size_gb: 1.1, type: 'audio' },
          { name: 'video-llava', size_gb: 9.3, type: 'video' },
        ],
        supports_embeddings: true,
        supports_vision: true,
        supports_audio: true,
        supports_video: true,
      },
      {
        id: 'lmstudio',
        name: 'LM Studio',
        url: 'http://10.0.0.3:1234',
        host: '10.0.0.3',
        location: 'network',
        status: 'unavailable',
        models: [],
        supports_embeddings: false,
        supports_vision: false,
        supports_audio: false,
        supports_video: false,
        error: 'offline',
      },
    ],
    recommendations: [
      { name: 'mistral', provider: 'ollama', type: 'chat', size_gb: 4.1, performance_score: 85, is_installed: false },
      { name: 'nomic-embed-text', provider: 'ollama', type: 'embedding', size_gb: 0.5, performance_score: 67, is_installed: true, warning: 'Lower multilingual recall' },
      { name: 'llava', provider: 'ollama', type: 'vision', size_gb: 7.1, performance_score: 45, is_installed: false, warning: 'Needs more VRAM' },
    ],
    offline_ready: true,
    has_chat_model: true,
    has_embedding_model: true,
    has_vision_model: true,
    has_audio_model: true,
    has_video_model: true,
    scanned_hosts: ['localhost', '10.0.0.3'],
    custom_endpoints: [{ id: 'endpoint-1', name: 'Local Ollama', url: 'http://localhost:11434', provider_type: 'ollama', enabled: true }],
  })
  apiMocks.localLlmApi.pullModel.mockResolvedValue({ success: true, message: 'Pulled' })
  apiMocks.localLlmApi.testModel.mockResolvedValue({ success: true, message: 'Model ok' })
  apiMocks.localLlmApi.scanNetwork.mockResolvedValue({
    success: true,
    scanned: ['10.0.0.2'],
    scanned_count: 1,
    found_count: 1,
    found: [
      {
        id: 'ollama',
        name: 'VPN Ollama',
        url: 'http://10.0.0.2:11434',
        host: '10.0.0.2',
        location: 'network',
        status: 'available',
        models: [{ name: 'llama3', size_gb: 4.7, type: 'chat' }],
        supports_embeddings: true,
        supports_vision: false,
        supports_audio: false,
        supports_video: false,
      },
    ],
  })

  apiMocks.profilesApi.list.mockResolvedValue(profilesResponse)
  apiMocks.ingestionApi.getStatus.mockResolvedValue({
    status: 'running',
    job_id: 'job-1',
    phase: 'processing',
    total_files: 20,
    processed_files: 8,
    failed_files: 1,
    chunks_created: 40,
    progress_percent: 40,
    errors: ['one failure'],
    can_pause: true,
    can_stop: true,
  })
  apiMocks.ingestionApi.getLogsStreamUrl.mockReturnValue('/api/v1/ingestion/logs/stream')
  apiMocks.ingestionApi.getEventsStreamUrl.mockReturnValue('/api/v1/ingestion/events/stream')
  apiMocks.ingestionApi.pause.mockResolvedValue({ success: true })
  apiMocks.ingestionApi.resume.mockResolvedValue({ success: true })
  apiMocks.ingestionApi.stop.mockResolvedValue({ success: true })
  apiMocks.ingestionQueueApi.getQueue.mockResolvedValue({
    queue: [
      {
        id: 'queue-1',
        profile_key: 'default',
        profile_name: 'Default',
        status: 'pending',
        file_types: ['documents'],
        incremental: true,
        retry_image_only_pdfs: false,
        retry_timeouts: false,
        retry_errors: false,
        retry_no_chunks: false,
        skip_image_only_pdfs: false,
        created_at: '2026-05-18T08:00:00Z',
      },
    ],
    total: 1,
    total_queued: 1,
  })
  apiMocks.ingestionQueueApi.getSchedules.mockResolvedValue({
    schedules: [
      {
        id: 'schedule-1',
        profile_key: 'default',
        profile_name: 'Default',
        frequency: 'daily',
        hour: 2,
        file_types: ['documents'],
        enabled: true,
        next_run: '2026-05-19T02:00:00Z',
      },
    ],
  })
  apiMocks.ingestionQueueApi.addMultipleToQueue.mockResolvedValue({ added: 1 })
  apiMocks.ingestionQueueApi.removeFromQueue.mockResolvedValue({ success: true })
  apiMocks.ingestionQueueApi.createSchedule.mockResolvedValue({ success: true })
  apiMocks.ingestionQueueApi.deleteSchedule.mockResolvedValue({ success: true })
  apiMocks.ingestionQueueApi.toggleSchedule.mockResolvedValue({ success: true })
  apiMocks.ingestionQueueApi.runScheduleNow.mockResolvedValue({ success: true })
  apiMocks.fileRegistryApi.getStats.mockResolvedValue({
    total_files: 12,
    indexed_files: 9,
    by_status: { indexed: 9, changed: 3 },
  })

  apiMocks.backupsApi.list.mockResolvedValue({
    backups: [
      {
        backup_id: 'backup-1',
        profile_key: 'default',
        backup_type: 'full',
        status: 'completed',
        created_at: '2026-05-18T08:00:00Z',
        completed_at: '2026-05-18T08:03:00Z',
        database_name: 'rag_default',
        collections_included: ['documents', 'chunks'],
        document_counts: { documents: 5, chunks: 50 },
        size_bytes: 2048,
        compressed_size_bytes: 1024,
        document_count: 5,
        chunk_count: 50,
        name: 'Nightly full',
        parent_backup_id: 'parent-1',
        ingestion_job_id: 'ingest-1',
      },
    ],
    total: 1,
  })
  apiMocks.backupsApi.getConfig.mockResolvedValue({
    enabled: true,
    backup_dir: '/backups',
    compression_enabled: true,
    retention_days: 7,
    max_backups: 10,
    max_backups_per_profile: 10,
    auto_backup_after_ingestion: true,
    include_embeddings: true,
    schedule_enabled: false,
    schedule_cron: '0 2 * * *',
  })
  apiMocks.backupsApi.getStorageStats.mockResolvedValue({
    total_backups: 1,
    total_size_bytes: 2048,
    compressed_size_bytes: 1024,
    available_space_bytes: 1024 * 1024 * 1024,
    backups_by_type: { full: 1 },
  })
  apiMocks.backupsApi.getStatus.mockResolvedValue({ active: false })
  apiMocks.backupsApi.create.mockResolvedValue({ backup_id: 'backup-2', status: 'completed' })
  apiMocks.backupsApi.restore.mockResolvedValue({
    success: true,
    collections_restored: ['documents'],
    documents_restored: { documents: 5 },
  })
  apiMocks.backupsApi.delete.mockResolvedValue({ success: true })
  apiMocks.backupsApi.updateConfig.mockResolvedValue({ success: true })

  apiMocks.cloudSourcesApi.getConnections.mockResolvedValue({
    connections: [
      {
        id: 'conn-1',
        provider: 'google_drive',
        provider_type: 'google_drive',
        display_name: 'Drive',
        status: 'active',
        auth_type: 'oauth2',
        created_at: '2026-05-18T08:00:00Z',
        last_sync_at: '2026-05-18T08:10:00Z',
      },
    ],
  })
  apiMocks.cloudSourcesApi.getProviders.mockResolvedValue({
    providers: [
      {
        provider_type: 'google_drive',
        display_name: 'Google Drive',
        auth_type: 'oauth2',
        supports_delta_sync: true,
      },
    ],
  })
  apiMocks.cloudSourcesApi.getConnection.mockResolvedValue({
    id: 'conn-1',
    provider: 'google_drive',
    provider_type: 'google_drive',
    display_name: 'Drive',
    status: 'active',
    auth_type: 'oauth2',
    created_at: '2026-05-18T08:00:00Z',
    last_validated_at: '2026-05-18T08:05:00Z',
    last_sync_at: '2026-05-18T08:10:00Z',
    oauth_email: 'drive@example.com',
    oauth_expires_at: '2026-05-19T08:00:00Z',
    server_url: 'https://drive.example.com',
  })
  apiMocks.cloudSourcesApi.getSyncConfigs.mockResolvedValue({
    configs: [
      {
        id: 'sync-1',
        connection_id: 'conn-1',
        name: 'Docs',
        enabled: true,
        source_paths: [{ path: '/docs', recursive: true }],
        schedule: { enabled: true, frequency: 'daily' },
        stats: { total_files: 42 },
      },
    ],
  })
  apiMocks.cloudSourcesApi.testConnection.mockResolvedValue({ success: true, message: 'Connection ok' })
  apiMocks.cloudSourcesApi.deleteConnection.mockResolvedValue({ success: true })
  apiMocks.cloudSourcesApi.refreshOAuthTokens.mockResolvedValue({
    id: 'conn-1',
    provider: 'google_drive',
    provider_type: 'google_drive',
    display_name: 'Drive',
    status: 'active',
    auth_type: 'oauth2',
    created_at: '2026-05-18T08:00:00Z',
    last_validated_at: '2026-05-18T08:15:00Z',
    last_sync_at: '2026-05-18T08:10:00Z',
    oauth_email: 'drive@example.com',
    oauth_expires_at: '2026-05-19T08:00:00Z',
    server_url: 'https://drive.example.com',
  })
  apiMocks.cloudSourcesApi.createSyncConfig.mockResolvedValue({ id: 'sync-2' })

  apiMocks.benchmarkApi.getProviders.mockResolvedValue({
    openai: {
      name: 'OpenAI',
      available: true,
      models: [{ id: 'text-embedding-3-small', name: 'text-embedding-3-small', dimension: 1536 }],
    },
    ollama: {
      name: 'Ollama',
      available: true,
      url: 'http://localhost:11434',
      models: [{ id: 'nomic-embed-text', name: 'nomic-embed-text', dimension: 768 }],
    },
    vllm: { available: false, models: [] },
  })
  apiMocks.benchmarkApi.testProvider.mockResolvedValue({
    success: true,
    provider: 'OpenAI',
    model: 'text-embedding-3-small',
    latency_ms: 40,
    dimension: 1536,
  })
  apiMocks.benchmarkApi.runBenchmark.mockResolvedValue({
    id: 'bench-1',
    timestamp: '2026-05-18T08:00:00Z',
    file_name: 'benchmark.txt',
    file_size_bytes: 64,
    content_preview: 'Benchmark content',
    chunk_config: { chunk_size: 1000, chunk_overlap: 200, max_tokens: 512 },
    results: [
      {
        provider: 'OpenAI',
        model: 'text-embedding-3-small',
        provider_type: 'openai',
        total_time_ms: 80,
        chunking_time_ms: 10,
        embedding_time_ms: 40,
        avg_latency_ms: 40,
        tokens_processed: 120,
        chunks_created: 3,
        embedding_dimension: 1536,
        memory_before_mb: 100,
        memory_after_mb: 120,
        memory_peak_mb: 130,
        cpu_percent: 22,
        cost_estimate_usd: 0.02,
        success: true,
      },
    ],
    winner: 'OpenAI',
  })
  apiMocks.benchmarkApi.getHistory.mockResolvedValue({
    results: [
      {
        id: 'bench-1',
        timestamp: '2026-05-18T08:00:00Z',
        file_name: 'benchmark.txt',
        file_size_bytes: 64,
        content_preview: 'Benchmark content',
        chunk_config: { chunk_size: 1000, chunk_overlap: 200, max_tokens: 512 },
        results: [
          {
            provider: 'OpenAI',
            model: 'text-embedding-3-small',
            provider_type: 'openai',
            total_time_ms: 80,
            chunking_time_ms: 10,
            embedding_time_ms: 40,
            avg_latency_ms: 40,
            tokens_processed: 120,
            chunks_created: 3,
            embedding_dimension: 1536,
            memory_before_mb: 100,
            memory_after_mb: 120,
            memory_peak_mb: 130,
            cpu_percent: 22,
            cost_estimate_usd: 0.02,
            success: true,
          },
        ],
        winner: 'OpenAI',
      },
    ],
    total: 1,
  })

  apiMocks.promptsApi.list.mockResolvedValue({
    templates: [
      {
        id: 'system',
        name: 'System Prompt',
        description: 'Default system prompt',
        category: 'chat',
        active_version: 2,
        versions: [
          {
            version: 1,
            system_prompt: 'Old prompt',
            tools: [{ name: 'search', enabled: false, description: 'Search', parameters: {} }],
            created_at: '2026-05-17T08:00:00Z',
            is_active: false,
          },
          {
            version: 2,
            system_prompt: 'New prompt',
            tools: [{ name: 'search', enabled: true, description: 'Search', parameters: {} }],
            created_at: '2026-05-18T08:00:00Z',
            is_active: true,
          },
        ],
        variables: [{ name: 'query', description: 'User query', required: true }],
        tools: [{ name: 'search', description: 'Search', parameters: {} }],
      },
    ],
  })
  apiMocks.promptsApi.compare.mockResolvedValue({
    prompt_diff: '- Old\n+ New',
    tools_added: ['lookup'],
    tools_removed: [],
    tools_modified: ['search'],
    diff: [{ type: 'changed', old: 'Old', new: 'New' }],
    summary: 'Changed prompt',
  })
  apiMocks.promptsApi.test.mockResolvedValue({
    success: true,
    response: 'Test response',
    tool_calls: [{ name: 'search', arguments: '{"query":"hello"}' }],
    tokens_used: 42,
    duration_ms: 120,
  })
  apiMocks.promptsApi.createVersion.mockResolvedValue({ version: 3 })
  apiMocks.promptsApi.activateVersion.mockResolvedValue({ success: true })

  apiMocks.strategiesApi.list.mockResolvedValue(strategies)
  apiMocks.strategiesApi.getAllMetrics.mockResolvedValue([
    {
      strategy_id: 'balanced',
      execution_count: 10,
      avg_latency_ms: 420,
      avg_confidence: 0.82,
    },
  ])
  apiMocks.strategiesApi.get.mockResolvedValue({
    id: 'balanced',
    name: 'Balanced',
    description: 'Balanced strategy',
    domain: 'general',
    domains: ['general'],
    tags: ['balanced', 'general'],
    version: '1.0.0',
    author: 'RecallHub',
    prompt_template: 'Use balanced reasoning',
    config: {
      max_iterations: 3,
      confidence_threshold: 0.75,
      early_exit_enabled: true,
      cross_search_boost: 1.2,
    },
    prompts_preview: { orchestrator: 'Use balanced reasoning', worker: 'Search carefully' },
  })
  apiMocks.strategiesApi.abCompareResponses.mockResolvedValue({
    strategy_a: 'balanced',
    strategy_b: 'legal',
    overall_winner: 'balanced',
    scores_a: { quality: 8, hallucination: 9, readability: 8, factuality: 8, relevance: 8, overall: 8 },
    scores_b: { quality: 7, hallucination: 8, readability: 7, factuality: 7, relevance: 7, overall: 7 },
    analysis: 'A was clearer',
    recommendation: 'Use Balanced for this query',
  })
  apiMocks.sessionsApi.create.mockResolvedValue({ id: 'ab-session', title: 'A/B Test Session', messages: [] })
  apiMocks.sessionsApi.update.mockResolvedValue({ success: true })
  apiMocks.sessionsApi.sendMessage.mockResolvedValue({
    assistant_message: {
      id: 'assistant-new',
      role: 'assistant',
      content: 'Answer from strategy',
      created_at: '2026-05-18T08:00:00Z',
    },
    user_message: {
      id: 'user-new',
      role: 'user',
      content: 'Question',
      created_at: '2026-05-18T08:00:00Z',
    },
    session: chatSidebarState.currentSession,
  })
  apiMocks.sessionsApi.sendMessageStream.mockImplementation((_sessionId, _message, _options, callbacks) => {
    callbacks?.onStart?.()
    callbacks?.onOrchestratorStep?.({
      phase: 'plan',
      reasoning: 'Streaming plan',
      output: '',
      duration_ms: 100,
      tokens: 12,
      tasks: [{ id: 'task-stream', type: 'search_profile', query: 'strategy' }],
    })
    callbacks?.onWorkerStep?.({
      task_id: 'task-stream',
      task_type: 'search_profile',
      tool: 'vector',
      duration_ms: 80,
      success: true,
      documents: [{ title: 'Plan.md', score: 0.91, excerpt: 'Planning excerpt' }],
    })
    callbacks?.onResponse?.({
      content: 'Streamed answer',
      sources: [{ title: 'Plan.md', source: '/docs/Plan.md', relevance: 0.91, excerpt: 'Planning excerpt' }],
      stats: {
        orchestrator_tokens: 12,
        worker_tokens: 20,
        total_tokens: 32,
        cost_usd: 0.001,
        tokens_per_second: 15,
        latency_ms: 500,
      },
      trace: chatSidebarState.currentSession.messages[1].agent_trace,
    })
    callbacks?.onTitleUpdate?.('Updated streamed session')
    callbacks?.onDone?.()
    return { abort: vi.fn() }
  })
  apiMocks.documentsApi.findBySource.mockResolvedValue({ id: 'doc-1', title: 'Plan.md' })
  apiMocks.debugApi.getSystemState.mockResolvedValue({
    uptime_seconds: 3661,
    active_requests: 1,
    total_requests: 20,
    memory_usage_mb: 256,
    orchestrator_model: 'gpt-5.2',
    orchestrator_provider: 'openai',
    worker_model: 'gpt-5.2-mini',
    worker_provider: 'openai',
    active_profile: 'default',
    database: 'rag_default',
    ollama_url: 'http://localhost:11434',
    model_pool: { active: 2, idle: 1 },
  })
  apiMocks.debugApi.getActiveRequests.mockResolvedValue({
    active: [
      {
        request_id: 'request-1234567890',
        session_id: 'session-1',
        model: 'gpt-5.2',
        status: 'in_progress',
        elapsed_ms: 1200,
        started_at: '2026-05-18T08:00:00Z',
      },
    ],
  })
  apiMocks.debugApi.getLiveActivity.mockResolvedValue({
    activity: [
      {
        request_id: 'request-1234567890',
        status: 'complete',
        model: 'gpt-5.2',
        started_at: '2026-05-18T08:00:00Z',
        duration_ms: 1500,
        total_tokens: 64,
        phases_count: 3,
        entries: [
          { timestamp: '2026-05-18T08:00:01Z', type: 'phase', phase: 'Started', duration_ms: 90 },
        ],
      },
      {
        request_id: 'request-error-1234',
        status: 'error',
        model: 'gpt-5.2-mini',
        started_at: '2026-05-18T08:01:00Z',
        duration_ms: 65000,
        entries: [{ type: 'error', error: 'Provider timeout' }],
      },
    ],
  })
  apiMocks.debugApi.getRequestDetail.mockResolvedValue({
    request_id: 'request-1234567890',
    duration_ms: 65000,
    total_tokens: 96,
    status: 'complete',
    entries: [
      { type: 'llm_call', phase: 'plan', model: 'gpt-5.2', duration_ms: 1200, tokens: 42 },
      { type: 'search', query: 'strategy coverage gaps', results_count: 5, duration_ms: 250 },
      { type: 'error', error: 'Recovered worker error' },
      { type: 'phase', phase: 'finalize', duration_ms: 80 },
    ],
    timeline: [{ timestamp: '2026-05-18T08:00:01Z', event: 'started' }],
    spans: [],
  })
}

function installFetchMock() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'

    if (url.includes('/system/email-providers')) {
      return jsonResponse({
        providers: [
          {
            type: 'email_gmail',
            display_name: 'Gmail',
            icon: 'mail',
            auth_type: 'oauth2',
            description: 'Gmail messages',
            requires_airbyte: true,
            config_fields: [{ name: 'label', label: 'Label', type: 'text', required: false }],
          },
        ],
      })
    }
    if (url.includes('/system/cloud-storage-providers')) {
      return jsonResponse({
        providers: [
          {
            type: 'google_drive',
            display_name: 'Google Drive',
            icon: 'drive',
            auth_type: 'oauth2',
            description: 'Drive files',
            requires_airbyte: true,
            supports_multiple: true,
            config_fields: [{ name: 'folder', label: 'Folder', type: 'text', required: false }],
          },
        ],
      })
    }
    if (url.includes('/system/airbyte/status')) {
      return jsonResponse({ enabled: true, available: true, api_url: 'http://localhost:11021', webapp_url: 'http://localhost:11020' })
    }
    if (url.includes('/system/database/test-connection')) {
      return jsonResponse({ connected: true, server_version: '8.0', current_database: 'rag_default', uri_host: 'localhost' })
    }
    if (url.includes('/system/database/check/')) {
      return jsonResponse({
        database: 'rag_default',
        exists: true,
        has_collections: true,
        collections: ['documents', 'chunks'],
        documents_count: 4,
        chunks_count: 20,
        has_documents_collection: true,
        has_chunks_collection: true,
        can_create: false,
      })
    }
    if (url.includes('/system/database/create')) {
      return jsonResponse({ database: 'rag_default', created: true, errors: [] })
    }
    if (url.includes('/profiles/active')) {
      return jsonResponse({ key: 'default', profile: { database: 'rag_default' } })
    }
    if (url.includes('/profiles/default/cloud-sources') && method === 'GET') {
      return jsonResponse({
        cloud_sources: [
          {
            connection_id: 'source-1',
            provider_type: 'email_gmail',
            display_name: 'Work Gmail',
            enabled: true,
            sync_schedule: 'daily',
            last_sync_at: '2026-05-18T08:00:00Z',
            last_sync_status: 'success',
          },
        ],
        sources: [
          {
            connection_id: 'source-1',
            provider_type: 'email_gmail',
            display_name: 'Work Gmail',
            enabled: true,
            sync_schedule: 'daily',
            last_sync_status: 'success',
          },
        ],
      })
    }
    if (url.includes('/profiles/default/cloud-sources')) {
      return jsonResponse({ success: true })
    }
    if (url.includes('/failed-documents/summary')) {
      return jsonResponse({
        by_error_type: {
          timeout: { count: 1, total_size_bytes: 2048 },
          error: { count: 1, total_size_bytes: 0 },
        },
        total_unresolved: 1,
        total_resolved: 1,
        total: 30,
      })
    }
    if (url.includes('/failed-documents') && method === 'GET') {
      return jsonResponse({
        documents: [
          {
            _id: 'failed-1',
            file_path: '/docs/slow.pdf',
            file_name: 'slow.pdf',
            file_size_bytes: 2048,
            error_type: 'timeout',
            error_message: 'Timed out',
            timeout_seconds: 300,
            processing_time_ms: 300000,
            failed_at: '2026-05-18T08:00:00Z',
            profile_key: 'default',
            resolved: false,
            retry_count: 1,
          },
          {
            _id: 'failed-2',
            file_path: '/docs/bad.txt',
            file_name: 'bad.txt',
            file_size_bytes: 0,
            error_type: 'error',
            error_message: 'Parser failed',
            timeout_seconds: 60,
            processing_time_ms: 500,
            failed_at: '2026-05-18T08:01:00Z',
            profile_key: 'default',
            resolved: true,
            retry_count: 0,
          },
        ],
        total: 30,
      })
    }
    if (url.includes('/failed-documents')) {
      return jsonResponse({ success: true })
    }
    if (url.includes('/analytics/overview')) {
      return jsonResponse({
        total_files_processed: 100,
        successful: 92,
        failed: 8,
        success_rate: 92,
        avg_processing_time_ms: 1200,
        min_processing_time_ms: 100,
        max_processing_time_ms: 300000,
        total_processing_hours: 2.5,
        total_chunks_created: 500,
        total_size_gb: 1.5,
        errors_by_type: { timeout: 3, error: 5 },
      })
    }
    if (url.includes('/analytics/outliers')) {
      return jsonResponse({
        outliers: [
          {
            _id: 'outlier-1',
            file_path: '/docs/huge.pdf',
            file_name: 'huge.pdf',
            file_size_bytes: 10_000_000,
            processing_time_ms: 300000,
            chunks_created: 40,
            started_at: '2026-05-18T08:00:00Z',
          },
        ],
        threshold_ms: 200000,
        avg_ms: 1200,
        std_dev_ms: 4000,
      })
    }
    if (url.includes('/analytics/by-extension')) {
      return jsonResponse({
        by_extension: [
          {
            extension: '.pdf',
            count: 12,
            successful: 10,
            failed: 2,
            success_rate: 83,
            avg_processing_time_ms: 1500,
            max_processing_time_ms: 300000,
            total_chunks: 150,
            avg_size_mb: 2,
            total_size_mb: 24,
          },
        ],
      })
    }
    if (url.includes('/analytics/timeline')) {
      return jsonResponse({
        timeline: [
          {
            hour: '2026-05-18T08:00:00Z',
            files_processed: 5,
            successful: 4,
            failed: 1,
            chunks_created: 30,
            avg_processing_time_ms: 1300,
          },
        ],
      })
    }
    if (url.includes('/analytics/clear')) {
      return jsonResponse({ deleted: 3 })
    }
    if (url.includes('/jobs/stats/summary')) {
      return jsonResponse({
        total_jobs: 2,
        by_status: { completed: 1, failed: 1 },
        overall_stats: {
          total_files_processed: 20,
          successful_files: 18,
          failed_files: 2,
          total_chunks: 100,
          total_size_bytes: 4096,
          avg_processing_time_ms: 1100,
        },
      })
    }
    if (url.match(/\/jobs\/job-1\/logs/)) {
      return jsonResponse({ logs: [{ timestamp: '2026-05-18T08:00:00Z', level: 'INFO', message: 'Started', logger: 'worker' }] })
    }
    if (url.match(/\/jobs\/job-1\/stats/)) {
      return jsonResponse({ stats: [{ _id: 'stat-1', file_path: '/docs/a.pdf', file_name: 'a.pdf', file_size_bytes: 100, processing_time_ms: 1000, chunks_created: 4, success: true, error_type: null, error_message: null }] })
    }
    if (url.match(/\/jobs\/job-1\/failed/)) {
      return jsonResponse({ failed_documents: [{ _id: 'failed-1', file_path: '/docs/b.pdf', file_name: 'b.pdf', error_type: 'error', error_message: 'bad', failed_at: '2026-05-18T08:00:00Z' }] })
    }
    if (url.match(/\/jobs\/job-1$/)) {
      return jsonResponse({
        job: { job_id: 'job-1', status: 'completed', profile: 'default' },
        stats_summary: {
          total_files: 10,
          successful: 9,
          failed: 1,
          total_processing_time_ms: 10000,
          total_chunks: 40,
          total_size_bytes: 1024,
          avg_processing_time_ms: 1000,
        },
        failed_by_type: { error: 1 },
      })
    }
    if (url.includes('/jobs') && method === 'GET') {
      return jsonResponse({
        jobs: [
          {
            job_id: 'job-1',
            profile: 'default',
            status: 'completed',
            phase: 'completed',
            started_at: '2026-05-18T08:00:00Z',
            completed_at: '2026-05-18T08:05:00Z',
            duration_seconds: 300,
            total_files: 10,
            processed_files: 10,
            failed_files: 1,
            chunks_created: 40,
            duplicates_skipped: 2,
            stats_count: 10,
            failed_count: 1,
            logs_count: 3,
            errors: ['one error'],
          },
        ],
        total: 1,
      })
    }
    if (url.includes('/jobs')) {
      return jsonResponse({ success: true })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function clickButtonIfPresent(user: ReturnType<typeof userEvent.setup>, name: RegExp | string) {
  const button = screen.queryAllByRole('button', { name })[0]
  if (button) {
    await user.click(button)
  }
  return button
}

function findSelectWithOption(value: string, currentValue?: string) {
  return screen.getAllByRole('combobox').find((element) => {
    const select = element as HTMLSelectElement
    const hasOption = Array.from(select.options).some((option) => option.value === value)
    return hasOption && (currentValue === undefined || select.value === currentValue)
  }) as HTMLSelectElement | undefined
}

describe('large workflow page coverage smoke tests', () => {
  beforeEach(() => {
    resetApiMocks()
    installFetchMock()
    vi.stubGlobal('EventSource', MockEventSource)
    vi.stubGlobal('confirm', vi.fn(() => true))
    MockEventSource.instances = []
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn() },
      configurable: true,
    })
    Element.prototype.scrollIntoView = vi.fn()
    authState.user = {
      id: 'admin-1',
      email: 'admin@example.com',
      name: 'Admin User',
      full_name: 'Admin User',
      is_admin: true,
      is_active: true,
    }
    authState.isLoading = false
    chatSidebarState.currentSession = defaultChatSession
    chatSidebarState.pendingMessage = ''
    chatSidebarState.pendingAttachments = []
    chatSidebarState.setCurrentSession.mockClear()
    chatSidebarState.setSessions.mockClear()
    chatSidebarState.handleNewChat.mockClear()
    chatSidebarState.setPendingMessage.mockClear()
    localStorage.clear()
    localStorage.setItem('i18nextLng', 'en')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders configuration data, discovery results, and every configuration tab', async () => {
    const user = userEvent.setup()
    renderRoute(<ConfigurationPage />)

    expect(await screen.findByText('config.title')).toBeInTheDocument()
    expect(apiMocks.systemApi.getLLMProviderConfig).toHaveBeenCalled()

    await user.click(screen.getByText('config.discoverLLMs'))
    await waitFor(() => expect(apiMocks.localLlmApi.discover).toHaveBeenCalled())

    for (const tab of [
      'config.tabs.tools',
      'config.tabs.agent',
      'config.tabs.ingestion',
      'config.tabs.offline',
      'config.tabs.network',
      'config.tabs.models',
      'config.tabs.profiles',
      'config.tabs.search',
      'config.tabs.llm',
    ]) {
      await user.click(screen.getByText(tab))
    }

    expect(screen.getByText('config.llm.orchestrator')).toBeInTheDocument()

    const fetchButtons = screen.getAllByRole('button', { name: 'config.llm.fetchModels' })
    await user.click(fetchButtons[0])
    await waitFor(() => expect(apiMocks.systemApi.fetchModelsFromApi).toHaveBeenCalledWith('openai'))
    await user.click(fetchButtons[1])
    await waitFor(() => expect(apiMocks.systemApi.fetchModelsFromApi).toHaveBeenCalledWith('google'))
    await user.click(fetchButtons[2])
    await waitFor(() => expect(apiMocks.systemApi.fetchModelsFromApi).toHaveBeenCalledWith('anthropic'))
    await clickButtonIfPresent(user, 'config.llm.discoverModels')
    await waitFor(() => expect(apiMocks.systemApi.fetchModelsFromApi).toHaveBeenCalledWith('ollama'))

    const providerTestSelect = findSelectWithOption('gpt-5.2', '')
    if (providerTestSelect) {
      await user.selectOptions(providerTestSelect, 'gpt-5.2')
      await user.click(screen.getByRole('button', { name: 'config.llm.testConnection' }))
      expect((await screen.findAllByText(/Hello from model|config.llm.success/i)).length).toBeGreaterThan(0)
    }

    const capabilitySelect = findSelectWithOption('openai/gpt-5.2', '')
    if (capabilitySelect) {
      await user.selectOptions(capabilitySelect, 'openai/gpt-5.2')
      await user.click(screen.getByRole('button', { name: 'config.modelCapabilities.testAsOrchestrator' }))
      await waitFor(() => expect(apiMocks.systemApi.testModelCapability).toHaveBeenCalled())
      await user.click(screen.getByRole('button', { name: 'config.modelCapabilities.forceApprove' }))
      await waitFor(() => expect(apiMocks.systemApi.approveModel).toHaveBeenCalled())
    }

    await user.click(screen.getByRole('button', { name: 'config.llm.saveLLMConfig' }))
    await waitFor(() => expect(apiMocks.systemApi.saveLLMProviderConfig).toHaveBeenCalled())

    await user.click(screen.getByText('config.tabs.tools'))
    await user.click(screen.getByRole('button', { name: 'config.tools.selectToTest' }))
    await user.type(screen.getByPlaceholderText('Search query'), 'strategy')
    await user.click(screen.getByRole('button', { name: 'Run Test' }))
    expect(await screen.findByText('Test Passed')).toBeInTheDocument()

    await user.click(screen.getByText('config.tabs.agent'))
    const agentNumericInputs = screen.getAllByRole('spinbutton')
    await user.clear(agentNumericInputs[0])
    await user.type(agentNumericInputs[0], '0')
    expect(await screen.findByText('Parallel Workers must be at least 1')).toBeInTheDocument()
    await user.clear(agentNumericInputs[0])
    await user.type(agentNumericInputs[0], '4')
    await user.click(screen.getByRole('button', { name: 'Save Agent Configuration' }))
    await waitFor(() => expect(apiMocks.systemApi.saveAgentPerformanceConfig).toHaveBeenCalled())

    await user.click(screen.getByText('config.tabs.ingestion'))
    const ingestionNumericInputs = screen.getAllByRole('spinbutton')
    await user.clear(ingestionNumericInputs[0])
    await user.type(ingestionNumericInputs[0], '0')
    expect(await screen.findByText('Concurrent Files must be at least 1')).toBeInTheDocument()
    await user.clear(ingestionNumericInputs[0])
    await user.type(ingestionNumericInputs[0], '2')
    await user.click(screen.getByRole('button', { name: 'Save Ingestion Configuration' }))
    await waitFor(() => expect(apiMocks.systemApi.saveIngestionPerformanceConfig).toHaveBeenCalled())

    await user.click(screen.getByText('config.tabs.offline'))
    await user.click(screen.getAllByTitle('Test')[0])
    await waitFor(() => expect(apiMocks.localLlmApi.testModel).toHaveBeenCalledWith('ollama', 'llama3', 'chat'))
    await user.click(screen.getAllByTitle('Select')[0])
    expect(screen.getAllByText('llama3').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Save Configuration' }))
    await waitFor(() => expect(apiMocks.localLlmApi.saveOfflineConfig).toHaveBeenCalled())

    await user.click(screen.getByText('config.tabs.models'))
    expect(screen.getByText('Recommended Models')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Pull' })[0])
    await waitFor(() => expect(apiMocks.localLlmApi.pullModel).toHaveBeenCalledWith('ollama', 'mistral'))
    await user.click(screen.getAllByTitle('Select')[0])
    expect(screen.getAllByText('llama3').length).toBeGreaterThan(0)

    await user.click(screen.getByText('config.tabs.network'))
    await user.click(screen.getByText('Docker Host (Windows/Mac)').closest('label') as HTMLElement)
    await user.click(screen.getByText('Common Local Network').closest('label') as HTMLElement)
    await user.click(screen.getByText('Local Network .0.x').closest('label') as HTMLElement)
    await user.type(screen.getByPlaceholderText('192.168.1.100, 10.0.0.50'), '10.0.0.2')
    await user.click(screen.getByRole('button', { name: 'Start Network Scan' }))
    expect((await screen.findAllByText('VPN Ollama')).length).toBeGreaterThan(0)
    await user.click(screen.getAllByRole('button', { name: 'Add' })[0])
    await waitFor(() => expect(apiMocks.localLlmApi.addCustomEndpoint).toHaveBeenCalled())
    const customEndpointDelete = screen.getByText('Local Ollama').parentElement?.parentElement?.querySelector('button') as HTMLButtonElement
    await user.click(customEndpointDelete)
    await waitFor(() => expect(apiMocks.localLlmApi.deleteCustomEndpoint).toHaveBeenCalledWith('endpoint-1'))
    await user.click(screen.getAllByRole('button', { name: 'Add' }).at(-1) as HTMLButtonElement)
    expect(await screen.findByText('Name and URL are required')).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('Endpoint name'), 'Remote LLM')
    await user.type(screen.getByPlaceholderText('http://192.168.1.100:11434'), 'http://10.0.0.9:11434')
    await user.selectOptions(screen.getByDisplayValue('Ollama'), 'openai-compatible')
    await user.click(screen.getAllByRole('button', { name: 'Add' }).at(-1) as HTMLButtonElement)
    await waitFor(() => expect(apiMocks.localLlmApi.addCustomEndpoint).toHaveBeenCalledTimes(2))

    await user.click(screen.getByText('config.tabs.search'))
    const matchCountSelect = findSelectWithOption('20', '10')
    expect(matchCountSelect).toBeDefined()
    await user.selectOptions(matchCountSelect as HTMLSelectElement, '20')
    await user.click(screen.getByRole('button', { name: 'Save Configuration' }))
    await waitFor(() => expect(apiMocks.systemApi.saveConfigToDb).toHaveBeenCalledWith(expect.objectContaining({ default_match_count: 20 })))
  }, 30000)

  it('renders chat session details and opens chat controls', async () => {
    const user = userEvent.setup()
    renderRoute(<ChatPageNew />)

    expect(await screen.findByText('Coverage Session')).toBeInTheDocument()
    expect(screen.getByText(/The plan is ready/)).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.documentsApi.findBySource).toHaveBeenCalledWith('/docs/Plan.md'))

    await user.click(screen.getByText(/chatPage.agentOperations/))
    expect(screen.getByText(/vector_index/)).toBeInTheDocument()
    expect(screen.getByText(/temporary failure/)).toBeInTheDocument()

    const agentPanelHeader = screen.queryByText(/agentPanel.header/)
    if (agentPanelHeader) {
      await user.click(agentPanelHeader)
      expect(screen.getByText('gpt-5.2-mini')).toBeInTheDocument()
      await user.click(screen.getByText(/agentPanel.orchestratorSteps/))
      expect(screen.getByText(/Identify the strategy OS rollout needs/)).toBeInTheDocument()
    }

    await user.click(screen.getByText('chatPage.modes.auto.label'))
    expect(screen.getByText('chatPage.agentMode')).toBeInTheDocument()
    await user.click(screen.getByText('chatPage.modes.fast.label'))

    await waitFor(() => expect(apiMocks.systemApi.getModelCapabilities).toHaveBeenCalled())
    await user.click(screen.getAllByText('chatPage.modelSelector.default')[0])
    await user.click(screen.getByText('gpt-5.2'))
    await waitFor(() => expect(apiMocks.sessionsApi.update).toHaveBeenCalledWith('session-1', { orchestrator_model: 'openai:gpt-5.2' }))

    await user.click(screen.getByText('chatPage.modelSelector.default'))
    const workerDropdown = screen.getByText('chatPage.modelSelector.worker').closest('.p-2') as HTMLElement
    await user.click(within(workerDropdown).getByText('gpt-5.2'))
    await waitFor(() => expect(apiMocks.sessionsApi.update).toHaveBeenCalledWith('session-1', { worker_model: 'openai:gpt-5.2' }))

    await user.click(screen.getByTitle('chatPage.settingsTooltip'))
    expect(screen.getByText('chatPage.settingsTitle')).toBeInTheDocument()

    await user.click(screen.getByText('viewToggle.userView'))
    const researchSummary = screen.queryByText(/userView.researchSummary/)
    if (researchSummary) {
      await user.click(researchSummary)
      expect(screen.getByText(/userView.sourcesFound/)).toBeInTheDocument()
    }

    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText')
    await user.click(screen.getAllByTitle('chatPage.copyMessage')[0])
    await waitFor(() => expect(writeTextSpy).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: 'chatPage.regenerate' }))
    await waitFor(() => expect(apiMocks.sessionsApi.sendMessageStream).toHaveBeenCalled())

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Follow up' } })
    expect(screen.getByRole('textbox')).toHaveValue('Follow up')

    await user.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(['hello'], 'upload.txt', { type: 'text/plain' }))
    expect(await screen.findByText('upload.txt')).toBeInTheDocument()
    await user.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File([new Uint8Array([1, 2, 3])], 'diagram-small.png', { type: 'image/png' }))
    expect(await screen.findByText('diagram-small.png')).toBeInTheDocument()
    await user.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File([new ArrayBuffer(20 * 1024 * 1024 + 1)], 'too-big.pdf', { type: 'application/pdf' }))
    expect(await screen.findByText('File "too-big.pdf" is too large (max 20MB)')).toBeInTheDocument()
    fireEvent.submit(screen.getByRole('textbox').closest('form') as HTMLFormElement)
    await waitFor(() => expect(apiMocks.sessionsApi.sendMessageStream).toHaveBeenCalledTimes(2))
  }, 15000)

  it('renders chat empty state and consumes pending dashboard messages', async () => {
    const user = userEvent.setup()

    chatSidebarState.currentSession = null as unknown as typeof chatSidebarState.currentSession
    renderRoute(<ChatPageNew />)
    expect(await screen.findByText('chatPage.assistantTitle')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'chatPage.startNewChat' }))
    expect(chatSidebarState.handleNewChat).toHaveBeenCalled()

    chatSidebarState.currentSession = defaultChatSession
    chatSidebarState.pendingMessage = 'Pending dashboard prompt'
    chatSidebarState.pendingAttachments = [
      {
        filename: 'pending.txt',
        content_type: 'text/plain',
        size_bytes: 24,
        token_estimate: 6,
      },
    ] as typeof chatSidebarState.pendingAttachments

    renderRoute(<ChatPageNew />)
    await waitFor(() => expect(apiMocks.sessionsApi.sendMessageStream).toHaveBeenCalledWith(
      'session-1',
      'Pending dashboard prompt',
      expect.objectContaining({
        attachments: [expect.objectContaining({ filename: 'pending.txt' })],
      }),
      expect.any(Object)
    ))
    expect(chatSidebarState.setPendingMessage).toHaveBeenCalledWith(null)
  }, 15000)

  it('shows live chat streaming progress, stop controls, and retryable errors', async () => {
    const user = userEvent.setup()
    const abort = vi.fn()
    apiMocks.sessionsApi.sendMessageStream.mockImplementationOnce((_sessionId, _message, _options, callbacks) => {
      callbacks?.onStart?.()
      callbacks?.onOrchestratorStep?.({
        phase: 'plan',
        reasoning: 'Plan the streamed request',
        output: '',
        duration_ms: 120,
        tokens: 11,
        tasks: [{ id: 'task-live', type: 'search_profile', query: 'coverage' }],
      })
      callbacks?.onWorkerStep?.({
        task_id: 'task-live',
        task_type: 'search_profile',
        tool: 'vector',
        duration_ms: 95,
        success: true,
        documents: [{ title: 'Plan.md', score: 0.88, excerpt: 'Plan excerpt' }],
      })
      return { abort }
    })

    renderRoute(<ChatPageNew />)
    expect(await screen.findByText('Coverage Session')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Stream slowly' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', code: 'Enter' })
    expect(await screen.findByText(/chatPage.orchestratorSteps/)).toBeInTheDocument()
    expect(screen.getByText(/chatPage.workerTasks/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /chatPage.stop/ }))
    expect(abort).toHaveBeenCalled()

    apiMocks.sessionsApi.sendMessageStream.mockImplementationOnce((_sessionId, _message, _options, callbacks) => {
      callbacks?.onStart?.()
      callbacks?.onError?.('backend down')
      callbacks?.onDone?.()
      return { abort: vi.fn() }
    })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Retry this' } })
    fireEvent.submit(screen.getByRole('textbox').closest('form') as HTMLFormElement)
    expect(await screen.findByText('Error: backend down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'chatPage.retry' }))
    await waitFor(() => expect(apiMocks.sessionsApi.sendMessageStream).toHaveBeenCalledTimes(3))
  }, 15000)

  it('renders ingestion queue state and exercises controls, schedules, filters, and streams', async () => {
    const user = userEvent.setup()
    renderRoute(<IngestionManagementPage />)

    await waitFor(() => expect(apiMocks.ingestionQueueApi.getQueue).toHaveBeenCalled())
    expect((await screen.findAllByText(/default|running/i)).length).toBeGreaterThan(0)

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    MockEventSource.instances[0].emit({ timestamp: '2026-05-18T08:00:01Z', level: 'INFO', message: 'Streamed log' })
    MockEventSource.instances[1]?.emit({ type: 'progress', phase: 'processing', message: 'Halfway', progress_percent: 50 })
    expect(await screen.findByText('Streamed log')).toBeInTheDocument()
    await clickButtonIfPresent(user, /Copy All/i)

    await user.click(screen.getByRole('button', { name: /Pause/i }))
    await waitFor(() => expect(apiMocks.ingestionApi.pause).toHaveBeenCalled())
    await user.click(screen.getAllByRole('button', { name: /Stop/i })[0])
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(apiMocks.ingestionApi.stop).toHaveBeenCalled())

    await user.click(screen.getByTitle('ingestion.toggle'))
    await waitFor(() => expect(apiMocks.ingestionQueueApi.toggleSchedule).toHaveBeenCalledWith('schedule-1'))
    await user.click(screen.getByTitle('common.delete'))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(apiMocks.ingestionQueueApi.deleteSchedule).toHaveBeenCalledWith('schedule-1'))

    await user.click(screen.getByText('ingestion.addToQueue'))
    const profileButtons = screen.getAllByRole('button', { name: 'Default' })
    await user.click(profileButtons[profileButtons.length - 1])
    await user.click(screen.getByLabelText(/Retry Timeouts/i))
    await user.click(screen.getByLabelText(/Skip Image-Only PDFs/i))
    const addProfileButton = screen.getByRole('button', { name: /ingestion.addProfile/ })
    await waitFor(() => expect(addProfileButton).toBeEnabled())
    await user.click(addProfileButton)
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(apiMocks.ingestionQueueApi.addMultipleToQueue).toHaveBeenCalled())

    expect(screen.getAllByText(/documents|all files/i).length).toBeGreaterThan(0)
  })

  it('renders paused ingestion state and drives resume, queue removal, schedule creation, and run-now actions', async () => {
    const user = userEvent.setup()
    apiMocks.ingestionApi.getStatus.mockResolvedValue({
      status: 'paused',
      job_id: 'job-paused',
      phase: 'filtering',
      total_files: 12,
      processed_files: 6,
      failed_files: 0,
      chunks_created: 20,
      progress_percent: 50,
      can_pause: true,
      can_stop: true,
    })

    renderRoute(<IngestionManagementPage />)
    expect(await screen.findByText(/filtering/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Resume/i }))
    await waitFor(() => expect(apiMocks.ingestionApi.resume).toHaveBeenCalled())

    await user.click(screen.getByTitle('ingestion.runNow'))
    await waitFor(() => expect(apiMocks.ingestionQueueApi.runScheduleNow).toHaveBeenCalledWith('schedule-1'))

    const queueDelete = screen.getAllByText('Default')[0].closest('.flex')?.parentElement?.querySelector('button.text-red-500') as HTMLButtonElement
    await user.click(queueDelete)
    await waitFor(() => expect(apiMocks.ingestionQueueApi.removeFromQueue).toHaveBeenCalledWith('queue-1'))
    await new Promise((resolve) => setTimeout(resolve, 2100))

    await user.click(screen.getByText('ingestion.newSchedule'))
    const scheduleProfileButtons = screen.getAllByRole('button', { name: 'Default' })
    await user.click(scheduleProfileButtons[scheduleProfileButtons.length - 1])
    await user.selectOptions(screen.getByRole('combobox'), 'weekly')
    await user.clear(screen.getByRole('spinbutton'))
    await user.type(screen.getByRole('spinbutton'), '4')
    await user.click(screen.getAllByRole('button', { name: 'ingestion.createSchedule' }).at(-1) as HTMLButtonElement)
    await waitFor(() => expect(apiMocks.ingestionQueueApi.createSchedule).toHaveBeenCalled())
  }, 15000)

  it('renders backup inventory and drives create, settings, restore, and delete actions', async () => {
    const user = userEvent.setup()
    renderRoute(<BackupManagementPage />)

    const backupId = await screen.findByText('backup-1')
    expect(backupId).toBeInTheDocument()
    expect(apiMocks.backupsApi.getStorageStats).toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /Create Backup/i }))
    await waitFor(() => expect(apiMocks.backupsApi.create).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: /Settings/i }))
    await user.click(screen.getByRole('button', { name: /Save Settings/i }))
    await waitFor(() => expect(apiMocks.backupsApi.updateConfig).toHaveBeenCalled())

    const row = backupId.closest('.p-4') as HTMLElement
    await user.click(within(row).getAllByRole('button')[0])
    expect(await screen.findByText('Collections:')).toBeInTheDocument()

    await user.click(screen.getByTitle('Restore'))
    await user.type(screen.getByPlaceholderText('rag_default'), 'rag_test')
    const restoreButtons = screen.getAllByRole('button', { name: 'Restore' })
    await user.click(restoreButtons[restoreButtons.length - 1])
    await waitFor(() => expect(apiMocks.backupsApi.restore).toHaveBeenCalled())

    await user.click(screen.getByTitle('Delete'))
    await waitFor(() => expect(apiMocks.backupsApi.delete).toHaveBeenCalledWith('backup-1'))
  })

  it('renders backup status variations and handles filtering and failed actions', async () => {
    const user = userEvent.setup()
    expect(getStatusColor('completed')).toContain('green')
    expect(getStatusColor('in_progress')).toContain('blue')
    expect(getStatusColor('pending')).toContain('yellow')
    expect(getStatusColor('failed')).toContain('red')
    expect(getStatusColor('unknown' as Parameters<typeof getStatusColor>[0])).toContain('gray')

    apiMocks.backupsApi.list.mockResolvedValue({
      backups: [
        {
          backup_id: 'backup-pending',
          profile_key: 'default',
          backup_type: 'incremental',
          status: 'pending',
          created_at: new Date().toISOString(),
          database_name: 'rag_default',
          collections_included: [],
          document_counts: {},
          size_bytes: 0,
          compressed_size_bytes: 0,
          document_count: 0,
          chunk_count: 0,
        },
        {
          backup_id: 'backup-failed',
          profile_key: 'default',
          backup_type: 'checkpoint',
          status: 'failed',
          created_at: '2026-05-10T08:00:00Z',
          database_name: 'rag_default',
          collections_included: ['documents'],
          document_counts: { documents: 2 },
          size_bytes: 1024,
          compressed_size_bytes: 512,
          document_count: 2,
          chunk_count: 0,
          error_message: 'Disk full',
        },
        {
          backup_id: 'backup-running',
          profile_key: 'default',
          backup_type: 'post_ingestion',
          status: 'in_progress',
          created_at: '2026-05-18T07:30:00Z',
          database_name: 'rag_default',
          collections_included: ['chunks'],
          document_counts: { chunks: 20 },
          size_bytes: 4096,
          compressed_size_bytes: 2048,
          document_count: 0,
          chunk_count: 20,
          ingestion_job_id: 'job-running',
        },
      ],
      total: 3,
    })
    apiMocks.backupsApi.getStorageStats.mockResolvedValue({
      total_backups: 3,
      total_size_bytes: 5120,
      compressed_size_bytes: 2560,
      available_space_bytes: 512 * 1024 * 1024,
      backups_by_type: { incremental: 1, checkpoint: 1, post_ingestion: 1 },
    })
    apiMocks.backupsApi.getStatus.mockResolvedValue({
      status: 'in_progress',
      backup_id: 'backup-running',
      progress_percent: 40,
      collections_completed: 1,
      total_collections: 3,
      current_collection: 'chunks',
    })

    renderRoute(<BackupManagementPage />)
    expect(await screen.findByText('backup-pending')).toBeInTheDocument()
    expect(screen.getAllByText('Incremental').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Checkpoint').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Post-Ingestion').length).toBeGreaterThan(0)

    await user.click(screen.getByText('backup-failed').closest('.p-4')?.querySelector('button') as HTMLButtonElement)
    expect(await screen.findByText('Disk full')).toBeInTheDocument()

    await user.selectOptions(findSelectWithOption('checkpoint', '') as HTMLSelectElement, 'checkpoint')
    await waitFor(() => expect(apiMocks.backupsApi.list).toHaveBeenCalledWith(undefined, 'checkpoint'))

    vi.stubGlobal('confirm', vi.fn(() => false))
    await user.click(screen.getAllByTitle('Delete')[0])
    expect(apiMocks.backupsApi.delete).not.toHaveBeenCalled()

    vi.stubGlobal('confirm', vi.fn(() => true))
    apiMocks.backupsApi.delete.mockRejectedValueOnce(new Error('delete failed'))
    await user.click(screen.getAllByTitle('Delete')[0])
    expect(await screen.findByText('delete failed')).toBeInTheDocument()
  }, 15000)

  it('renders cloud source connections and drives detail actions', async () => {
    const user = userEvent.setup()
    renderWithParamRoute(<CloudSourceConnectionsPage />, '/en/cloud-sources/connections/:connectionId?', '/en/cloud-sources/connections/conn-1')

    expect(await screen.findByText('Drive')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Test$/i }))
    await waitFor(() => expect(apiMocks.cloudSourcesApi.testConnection).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.refreshNow' }))
    await waitFor(() => expect(apiMocks.cloudSourcesApi.refreshOAuthTokens).toHaveBeenCalledWith('conn-1'))

    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.addConfig' }))
    await user.type(screen.getByPlaceholderText('cloudConnectionsPage.configNamePlaceholder'), 'Docs sync')
    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.browseFolders' }))
    await user.click(screen.getByRole('button', { name: 'Pick folder' }))
    expect((await screen.findAllByText('Docs')).length).toBeGreaterThan(1)
    await user.click(screen.getByText('cloudConnectionsPage.automaticSync').parentElement?.querySelector('button') as HTMLButtonElement)
    await user.selectOptions(screen.getAllByRole('combobox')[0], 'weekly')
    await user.clear(screen.getByRole('spinbutton'))
    await user.type(screen.getByRole('spinbutton'), '250')
    await user.click(screen.getByText('cloudConnectionsPage.deleteRemoved').parentElement?.parentElement?.querySelector('button') as HTMLButtonElement)
    fireEvent.submit(screen.getByRole('button', { name: 'cloudConnectionsPage.createConfiguration' }).closest('form') as HTMLFormElement)
    await waitFor(() => expect(apiMocks.cloudSourcesApi.createSyncConfig).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.deleteConnection' }))
    const deleteButtons = screen.getAllByRole('button', { name: 'cloudConnectionsPage.deleteConnection' })
    await user.click(deleteButtons[deleteButtons.length - 1])
    await waitFor(() => expect(apiMocks.cloudSourcesApi.deleteConnection).toHaveBeenCalledWith('conn-1'))
  })

  it('renders cloud source list and failure branches for connection details', async () => {
    const user = userEvent.setup()

    apiMocks.cloudSourcesApi.getConnections.mockResolvedValueOnce({
      connections: [
        {
          id: 'conn-pending',
          provider: 'webdav',
          provider_type: 'webdav',
          display_name: 'Fileshare',
          status: 'pending',
          auth_type: 'basic',
          created_at: '2026-05-18T07:00:00Z',
          server_url: 'https://files.example.com',
        },
        {
          id: 'conn-oauth',
          provider: 'dropbox',
          provider_type: 'dropbox',
          display_name: 'Dropbox',
          status: 'active',
          auth_type: 'oauth2',
          created_at: '2026-05-18T07:10:00Z',
          oauth_email: 'dropbox@example.com',
        },
      ],
    })
    renderRoute(<CloudSourceConnectionsPage />)

    expect(await screen.findByText('Fileshare')).toBeInTheDocument()
    expect(screen.getByText('https://files.example.com')).toBeInTheDocument()
    expect(screen.getByText('dropbox@example.com')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Refresh$/ }))
    await waitFor(() => expect(apiMocks.cloudSourcesApi.getConnections).toHaveBeenCalledTimes(2))

    apiMocks.cloudSourcesApi.getConnection.mockResolvedValueOnce({
      id: 'conn-failed',
      provider: 'dropbox',
      provider_type: 'dropbox',
      display_name: 'Broken Dropbox',
      status: 'failed',
      auth_type: 'oauth2',
      created_at: '2026-05-18T07:00:00Z',
      last_validated_at: undefined,
      oauth_email: 'broken@example.com',
      oauth_expires_at: '2026-05-18T08:00:00Z',
      error_message: 'OAuth expired',
    })
    apiMocks.cloudSourcesApi.getSyncConfigs.mockResolvedValueOnce({ configs: [] })
    apiMocks.cloudSourcesApi.testConnection.mockRejectedValueOnce(new Error('No token'))
    apiMocks.cloudSourcesApi.refreshOAuthTokens.mockRejectedValueOnce(new Error('Refresh failed'))
    apiMocks.cloudSourcesApi.deleteConnection.mockRejectedValueOnce(new Error('Delete failed'))

    renderWithParamRoute(<CloudSourceConnectionsPage />, '/en/cloud-sources/connections/:connectionId?', '/en/cloud-sources/connections/conn-failed')

    expect(await screen.findByText('Broken Dropbox')).toBeInTheDocument()
    expect(screen.getByText('OAuth expired')).toBeInTheDocument()
    expect(screen.getByText('cloudConnectionsPage.noSyncConfigs')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Test$/i }))
    expect(await screen.findByText('No token')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.refreshNow' }))
    expect(await screen.findByText('Refresh failed')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.createFirstConfig' }))
    expect(await screen.findByText('cloudConnectionsPage.createSyncConfig')).toBeInTheDocument()
    await user.click(screen.getByText('Cancel'))

    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.deleteConnection' }))
    await user.click(screen.getByText('Cancel'))
    await user.click(screen.getByRole('button', { name: 'cloudConnectionsPage.deleteConnection' }))
    await user.click(screen.getAllByRole('button', { name: 'cloudConnectionsPage.deleteConnection' }).at(-1) as HTMLButtonElement)
    expect(await screen.findByText('Delete failed')).toBeInTheDocument()
  })

  it('renders email and cloud configuration data from direct fetch calls', async () => {
    const user = userEvent.setup()
    renderRoute(<EmailCloudConfigPage />)

    expect((await screen.findAllByText(/Gmail|Google Drive|Airbyte/i)).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Work Gmail|rag_default/i).length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: 'emailConfigPage.testConnection' }))
    expect(await screen.findByText('MongoDB connection successful!')).toBeInTheDocument()

    await user.click(screen.getByTitle('emailConfigPage.triggerSync'))
    expect(await screen.findByText('Sync triggered! Check Airbyte UI for progress.')).toBeInTheDocument()

    await user.click(screen.getByTitle('emailConfigPage.editConfig'))
    await user.clear(screen.getByPlaceholderText('emailConfigPage.displayNamePlaceholder'))
    await user.click(screen.getByRole('button', { name: /emailConfigPage.update/ }))
    expect(await screen.findByText('Display Name is required')).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('emailConfigPage.displayNamePlaceholder'), 'Edited Gmail')
    await user.click(screen.getByRole('button', { name: /emailConfigPage.update/ }))
    expect(await screen.findByText('Gmail configuration updated!')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: /emailConfigPage.addProvider/ })[0])
    await user.clear(screen.getByPlaceholderText('emailConfigPage.displayNamePlaceholder'))
    await user.type(screen.getByPlaceholderText('emailConfigPage.displayNamePlaceholder'), 'New Gmail')
    await user.click(screen.getByRole('button', { name: /emailConfigPage.save/ }))
    expect(await screen.findByText('Gmail configuration saved!')).toBeInTheDocument()

    await user.click(screen.getByTitle('emailConfigPage.removeSource'))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/profiles/default/cloud-sources/source-1'),
        expect.objectContaining({ method: 'DELETE' }),
      )
    })
  }, 15000)

  it('renders unavailable email/cloud infrastructure and validates provider setup', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method || 'GET'

      if (url.includes('/system/email-providers')) {
        return jsonResponse({
          providers: [
            {
              type: 'gmail',
              display_name: 'Gmail',
              icon: 'mail',
              auth_type: 'oauth2',
              description: 'Gmail messages',
              requires_airbyte: true,
              config_fields: [],
            },
            {
              type: 'imap',
              display_name: 'IMAP',
              icon: 'mail',
              auth_type: 'basic',
              description: 'Mailbox',
              requires_airbyte: false,
              config_fields: [
                { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'user@example.com' },
                { name: 'endpoint', label: 'Endpoint', type: 'url', required: true, placeholder: 'https://mail.example.com' },
                { name: 'archive', label: 'Archive', type: 'checkbox', required: false, default: true },
              ],
            },
          ],
        })
      }
      if (url.includes('/system/cloud-storage-providers')) {
        return jsonResponse({
          providers: [
            {
              type: 'webdav',
              display_name: 'WebDAV',
              icon: 'cloud',
              auth_type: 'basic',
              description: 'Files',
              requires_airbyte: false,
              supports_multiple: true,
              config_fields: [{ name: 'url', label: 'URL', type: 'url', required: true, placeholder: 'https://files.example.com' }],
            },
          ],
        })
      }
      if (url.includes('/system/airbyte/status')) {
        return jsonResponse({ enabled: true, available: false, message: 'Airbyte offline' })
      }
      if (url.includes('/system/database/test-connection')) {
        return jsonResponse({ connected: false, error: 'Mongo down' })
      }
      if (url.includes('/system/database/check/')) {
        return jsonResponse({
          database: 'user_rag_admin_user',
          exists: false,
          has_collections: false,
          collections: [],
          documents_count: 0,
          chunks_count: 0,
          has_documents_collection: false,
          has_chunks_collection: false,
          can_create: true,
        })
      }
      if (url.includes('/system/database/create')) {
        return jsonResponse({ database: 'user_rag_admin_user', created: true, errors: ['index warning'] })
      }
      if (url.includes('/profiles/active')) {
        return jsonResponse({ key: 'default', profile: { database: 'rag_default' } })
      }
      if (url.includes('/profiles/default/cloud-sources') && method === 'GET') {
        return jsonResponse({
          cloud_sources: [
            {
              connection_id: 'imap-source',
              provider_type: 'imap',
              display_name: 'Inbox',
              enabled: true,
              last_sync_status: 'running',
              last_sync_at: '2026-05-18T08:00:00Z',
            },
            {
              connection_id: 'webdav-source',
              provider_type: 'webdav',
              display_name: 'Archive',
              enabled: false,
              last_sync_status: 'failed',
            },
          ],
        })
      }
      if (url.includes('/profiles/default/cloud-sources')) {
        return jsonResponse({ success: true })
      }
      return jsonResponse({ ok: true })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRoute(<EmailCloudConfigPage />)

    expect(await screen.findByText('Airbyte offline')).toBeInTheDocument()
    expect(screen.getAllByText('Mongo down').length).toBeGreaterThan(0)
    expect(screen.getByText('emailConfigPage.dbNotExists')).toBeInTheDocument()
    expect(screen.getByText('Inbox')).toBeInTheDocument()
    expect(screen.getByText('Archive')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'emailConfigPage.createDbCollections' }))
    expect(await screen.findByText('Database created with warnings: index warning')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'emailConfigPage.testConnection' }))
    expect(await screen.findByText('emailConfigPage.errorLabel')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'emailConfigPage.addProvider' })[0])
    await user.click(screen.getByRole('button', { name: /emailConfigPage.save/ }))
    expect(await screen.findByText('Email is required')).toBeInTheDocument()
    expect(screen.getByText('Endpoint is required')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('user@example.com'), 'not-an-email')
    await user.type(screen.getByPlaceholderText('https://mail.example.com'), 'not-a-url')
    await user.click(screen.getByRole('button', { name: /emailConfigPage.save/ }))
    expect(await screen.findByText('Email must be a valid email address')).toBeInTheDocument()
    expect(screen.getByText('Endpoint must be a valid URL')).toBeInTheDocument()

    await user.clear(screen.getByPlaceholderText('user@example.com'))
    await user.type(screen.getByPlaceholderText('user@example.com'), 'admin@example.com')
    await user.clear(screen.getByPlaceholderText('https://mail.example.com'))
    await user.type(screen.getByPlaceholderText('https://mail.example.com'), 'https://mail.example.com')
    await user.click(screen.getByLabelText('emailConfigPage.enableCheckbox'))
    await user.click(screen.getByRole('button', { name: /emailConfigPage.save/ }))
    expect(await screen.findByText('IMAP configuration saved!')).toBeInTheDocument()

    const confirmMock = vi.mocked(confirm)
    confirmMock.mockReturnValueOnce(false)
    await user.click(screen.getAllByTitle('emailConfigPage.removeSource')[0])
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('/profiles/default/cloud-sources/imap-source'),
      expect.objectContaining({ method: 'DELETE' }),
    )
  }, 15000)

  it('renders embedding benchmark providers, runs a benchmark, and shows history', async () => {
    const user = userEvent.setup()
    renderRoute(<EmbeddingBenchmarkPage />)

    expect((await screen.findAllByText(/OpenAI|Ollama/i)).length).toBeGreaterThan(0)
    expect(apiMocks.benchmarkApi.getProviders).toHaveBeenCalled()

    await user.selectOptions(screen.getAllByRole('combobox')[0], 'openai')
    await waitFor(() => expect(screen.getByRole('option', { name: /text-embedding-3-small/i })).toBeInTheDocument())
    await user.selectOptions(screen.getAllByRole('combobox')[1], 'text-embedding-3-small')
    await user.type(screen.getByPlaceholderText('sk-...'), 'sk-test')

    await user.click(screen.getByRole('button', { name: /Test Connection/i }))
    await waitFor(() => expect(apiMocks.benchmarkApi.testProvider).toHaveBeenCalled())

    await user.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(['benchmark'], 'benchmark.txt', { type: 'text/plain' }))
    expect(await screen.findByText('benchmark.txt')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Configuration' }))
    const numericInputs = screen.getAllByRole('spinbutton')
    await user.clear(numericInputs[0])
    await user.type(numericInputs[0], '1200')

    await waitFor(() => expect(screen.getByRole('button', { name: /Run Benchmark/i })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: /Run Benchmark/i }))
    expect(await screen.findByText('Detailed Comparison')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'History' }))
    expect(await screen.findByText('Recent Benchmarks')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run Another Benchmark' }))
  })

  it('renders job history and expands job detail tabs', async () => {
    const user = userEvent.setup()
    renderRoute(<JobHistoryPage />)

    const jobRow = await screen.findByText('job-1...')
    expect(jobRow).toBeInTheDocument()
    await user.click(jobRow)
    expect(await screen.findByText('Total Files')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Stats/ }))
    expect(await screen.findByText('a.pdf')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Logs/ }))
    expect(await screen.findByText('Started')).toBeInTheDocument()
    const jobWriteTextSpy = vi.spyOn(navigator.clipboard, 'writeText')
    await user.click(screen.getByRole('button', { name: /Copy All/ }))
    await waitFor(() => expect(jobWriteTextSpy).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: /Failed/ }))
    expect(await screen.findByText('b.pdf')).toBeInTheDocument()
    expect(screen.getByText('bad')).toBeInTheDocument()

    await user.click(screen.getByTitle('Delete job'))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/ingestion/jobs/job-1'),
        expect.objectContaining({ method: 'DELETE' }),
      )
    })
  })

  it('renders prompt templates and exercises compare, test, save, and activate flows', async () => {
    const user = userEvent.setup()
    renderRoute(<PromptManagementPage />)

    expect((await screen.findAllByText('System Prompt')).length).toBeGreaterThan(0)
    expect(screen.getByText('Default system prompt')).toBeInTheDocument()

    await user.selectOptions(screen.getAllByRole('combobox')[0], '1')
    await user.click(screen.getByRole('button', { name: 'prompts.activateVersion' }))
    await waitFor(() => expect(apiMocks.promptsApi.activateVersion).toHaveBeenCalledWith('system', 1))

    await user.click(screen.getByRole('button', { name: 'prompts.compare' }))
    const compareButtons = screen.getAllByRole('button', { name: 'prompts.compare' })
    await user.click(compareButtons[compareButtons.length - 1])
    expect(await screen.findByText('prompts.toolChanges')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'common.test' }))
    const testMessageBox = screen.getAllByRole('textbox').at(-1) as HTMLTextAreaElement
    await user.type(testMessageBox, 'hello')
    await user.click(screen.getByRole('button', { name: 'prompts.runTest' }))
    expect(await screen.findByText('Test response')).toBeInTheDocument()

    await user.type(screen.getAllByRole('textbox')[0], ' updated')
    await user.click(screen.getByRole('button', { name: /prompts.save/ }))
    await waitFor(() => expect(apiMocks.promptsApi.createVersion).toHaveBeenCalled())
  })

  it('renders strategy metrics and loads selected strategy detail', async () => {
    const user = userEvent.setup()
    renderRoute(<StrategiesPage />)

    await waitFor(() => expect(document.body.textContent).toContain('Balanced'))
    await user.click(screen.getByText('Balanced'))
    await waitFor(() => expect(apiMocks.strategiesApi.get).toHaveBeenCalledWith('balanced'))
  })

  it('renders strategy A/B test setup, runs both strategies, and compares responses', async () => {
    const user = userEvent.setup()
    renderRoute(<StrategyABTestPage />)

    expect((await screen.findAllByText(/Balanced|Legal/i)).length).toBeGreaterThan(0)
    expect(apiMocks.strategiesApi.list).toHaveBeenCalled()

    await user.type(screen.getByPlaceholderText('Enter a question to test both strategies...'), 'Which plan is best?')
    await user.click(screen.getByRole('button', { name: 'Run Test' }))
    await waitFor(() => expect(apiMocks.sessionsApi.sendMessage).toHaveBeenCalledTimes(2))
    expect((await screen.findAllByText('Answer from strategy')).length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: 'Compare Responses with AI' }))
    expect(await screen.findByText('AI Analysis')).toBeInTheDocument()
    expect(screen.getByText('Use Balanced for this query')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run Another Test' }))
  })

  it('renders ingestion analytics tabs backed by fetch responses', async () => {
    const user = userEvent.setup()
    renderRoute(<IngestionAnalyticsPage />)

    expect((await screen.findAllByText(/100|92/i)).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Outliers' }))
    expect(await screen.findByText('huge.pdf')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Extensions' }))
    await waitFor(() => expect(document.body.textContent).toContain('.pdf'))
    await user.click(screen.getByRole('button', { name: 'Timeline' }))
  })

  it('renders live debug system state, expands detail rows, and refreshes', async () => {
    const user = userEvent.setup()
    renderRoute(<LiveDebugPage />)

    expect((await screen.findAllByText(/request-1234|gpt-5.2/i)).length).toBeGreaterThan(0)
    expect(apiMocks.debugApi.getSystemState).toHaveBeenCalled()
    await user.click(screen.getAllByText('request-1234...').at(-1) as HTMLElement)
    expect(await screen.findByText('LLM')).toBeInTheDocument()
    expect(screen.getByText('Search')).toBeInTheDocument()
    expect(screen.getByText('Recovered worker error')).toBeInTheDocument()
    expect(screen.getAllByText(/1m 5s/).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: /Refresh/i }))
    await waitFor(() => expect(apiMocks.debugApi.getSystemState).toHaveBeenCalledTimes(2))
  })

  it('renders failed ingestion documents and drives filters, pagination, resolve, and delete actions', async () => {
    const user = userEvent.setup()
    renderRoute(<FailedDocumentsPage />)

    expect(await screen.findByText('slow.pdf')).toBeInTheDocument()
    expect(screen.getByText('Timed out')).toBeInTheDocument()
    expect(screen.getByText('bad.txt')).toBeInTheDocument()

    await user.selectOptions(screen.getAllByRole('combobox')[0], 'timeout')
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('error_type=timeout')))
    await user.selectOptions(screen.getAllByRole('combobox')[1], 'true')
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('resolved=true')))

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('skip=25')))
    await user.click(screen.getByRole('button', { name: 'Previous' }))

    await user.click(screen.getByRole('button', { name: 'Resolve' }))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/failed-documents/failed-1/resolve'),
        expect.objectContaining({ method: 'POST' }),
      )
    })

    await user.click(screen.getAllByRole('button', { name: /Delete/ })[0])
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/failed-documents/failed-1'),
        expect.objectContaining({ method: 'DELETE' }),
      )
    })

    await user.click(screen.getByRole('button', { name: /Clear Resolved/ }))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('resolved_only=true'),
        expect.objectContaining({ method: 'DELETE' }),
      )
    })
  })
})
