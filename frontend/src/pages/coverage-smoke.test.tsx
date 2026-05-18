import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ConfigurationPage from './ConfigurationPage'
import ChatPageNew from './ChatPageNew'
import IngestionManagementPage from './IngestionManagementPage'
import BackupManagementPage from './BackupManagementPage'
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
        created_at: '2026-05-18T08:00:00Z',
        attachments: [],
      },
      {
        id: 'm-assistant',
        role: 'assistant',
        content: 'The plan is ready.',
        created_at: '2026-05-18T08:00:02Z',
        sources: [
          {
            document_id: 'doc-1',
            title: 'Plan.md',
            source: '/docs/Plan.md',
            score: 0.91,
            chunk_id: 'chunk-1',
          },
        ],
        trace: {
          mode: 'auto',
          orchestrator: { model: 'gpt-5.2', tokens: 120 },
          workers: [{ task_type: 'search', success: true, documents: ['doc-1'] }],
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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, optionsOrDefault?: string | { defaultValue?: string; count?: number }) => {
      if (typeof optionsOrDefault === 'string') return optionsOrDefault
      if (optionsOrDefault?.defaultValue) return optionsOrDefault.defaultValue
      return key
    },
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
  useToast: () => ({ showToast: vi.fn(), showSuccess: vi.fn(), showError: vi.fn() }),
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
  default: ({ onChange }: { onChange?: (paths: string[]) => void }) => (
    <button type="button" onClick={() => onChange?.(['/docs'])}>Pick folder</button>
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
  url: string
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    this.url = url
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
        name: 'search',
        description: 'Search documents',
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
    worker_timeout: 60,
    orchestrator_timeout: 120,
    total_timeout: 300,
    default_mode: 'auto',
  })
  apiMocks.systemApi.getIngestionPerformanceConfig.mockResolvedValue({
    max_concurrent_files: 2,
    embedding_batch_size: 32,
    chunk_batch_size: 100,
    conversion_timeout_seconds: 300,
    enable_parallel_conversion: true,
  })
  apiMocks.systemApi.getModelCapabilities.mockResolvedValue({ models: modelCapabilities })
  apiMocks.systemApi.saveLLMProviderConfig.mockResolvedValue({ success: true })
  apiMocks.systemApi.saveAgentPerformanceConfig.mockResolvedValue({ success: true })
  apiMocks.systemApi.saveIngestionPerformanceConfig.mockResolvedValue({ success: true })
  apiMocks.systemApi.testModelCapability.mockResolvedValue({
    success: true,
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
    logs: ['Connected'],
  })
  apiMocks.systemApi.testTool.mockResolvedValue({
    success: true,
    output: { ok: true },
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
      gpu_available: false,
    },
    providers: [
      {
        provider_type: 'ollama',
        available: true,
        base_url: 'http://localhost:11434',
        models: ['llama3'],
      },
    ],
    recommendations: [
      { provider: 'ollama', model: 'llama3', model_type: 'chat', reason: 'local' },
    ],
  })
  apiMocks.localLlmApi.pullModel.mockResolvedValue({ success: true, message: 'Pulled' })
  apiMocks.localLlmApi.testModel.mockResolvedValue({ success: true, message: 'Model ok' })
  apiMocks.localLlmApi.scanNetwork.mockResolvedValue({
    hosts_scanned: 1,
    endpoints_found: [{ name: 'VPN Ollama', url: 'http://10.0.0.2:11434', provider_type: 'ollama' }],
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
        collections: ['documents', 'chunks'],
        size_bytes: 2048,
        compressed_size_bytes: 1024,
        document_count: 5,
        chunk_count: 50,
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
  apiMocks.backupsApi.restore.mockResolvedValue({ restore_id: 'restore-1', status: 'completed' })
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
  apiMocks.cloudSourcesApi.refreshOAuthTokens.mockResolvedValue({ status: 'active' })
  apiMocks.cloudSourcesApi.createSyncConfig.mockResolvedValue({ id: 'sync-2' })

  apiMocks.benchmarkApi.getProviders.mockResolvedValue({
    openai: { available: true, models: ['text-embedding-3-small'] },
    ollama: { available: true, models: ['nomic-embed-text'] },
    vllm: { available: false, models: [] },
  })
  apiMocks.benchmarkApi.testProvider.mockResolvedValue({
    success: true,
    latency_ms: 40,
    dimension: 1536,
  })
  apiMocks.benchmarkApi.runBenchmark.mockResolvedValue({
    benchmark_id: 'bench-1',
    results: [
      {
        provider_name: 'OpenAI',
        model: 'text-embedding-3-small',
        success: true,
        metrics: {
          avg_latency_ms: 40,
          throughput_docs_per_sec: 20,
          cost_per_1k_docs: 0.02,
          dimension: 1536,
        },
      },
    ],
    comparison: { fastest: 'OpenAI', cheapest: 'OpenAI', recommended: 'OpenAI' },
  })
  apiMocks.benchmarkApi.getHistory.mockResolvedValue({
    benchmarks: [
      { benchmark_id: 'bench-1', created_at: '2026-05-18T08:00:00Z', provider_count: 1 },
    ],
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
    diff: [{ type: 'changed', old: 'Old', new: 'New' }],
    summary: 'Changed prompt',
  })
  apiMocks.promptsApi.test.mockResolvedValue({
    rendered_prompt: 'Rendered prompt',
    response: 'Test response',
    variables_used: { query: 'hello' },
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
    prompt_template: 'Use balanced reasoning',
    config: { max_workers: 3 },
  })
  apiMocks.strategiesApi.abCompareResponses.mockResolvedValue({
    winner: 'A',
    scores_a: { relevance: 8, accuracy: 8, completeness: 7, clarity: 8, overall: 8 },
    scores_b: { relevance: 7, accuracy: 7, completeness: 7, clarity: 7, overall: 7 },
    reasoning: 'A was clearer',
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
    callbacks?.onDone?.({
      assistant_message: {
        id: 'assistant-stream',
        role: 'assistant',
        content: 'Streamed answer',
        created_at: '2026-05-18T08:00:00Z',
      },
      user_message: {
        id: 'user-stream',
        role: 'user',
        content: 'Question',
        created_at: '2026-05-18T08:00:00Z',
      },
      session: chatSidebarState.currentSession,
    })
    return vi.fn()
  })
  apiMocks.documentsApi.findBySource.mockResolvedValue({ id: 'doc-1', title: 'Plan.md' })
  apiMocks.debugApi.getSystemState.mockResolvedValue({
    uptime_seconds: 3661,
    active_requests: 1,
    total_requests: 20,
    memory_usage_mb: 256,
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
        entries: [
          { timestamp: '2026-05-18T08:00:01Z', level: 'info', message: 'Started' },
        ],
      },
    ],
  })
  apiMocks.debugApi.getRequestDetail.mockResolvedValue({
    request_id: 'request-1234567890',
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
        by_error_type: { timeout: { count: 1, total_size_bytes: 2048 } },
        total_unresolved: 1,
        total_resolved: 1,
        total: 2,
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
        ],
        total: 1,
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

describe('large workflow page coverage smoke tests', () => {
  beforeEach(() => {
    resetApiMocks()
    installFetchMock()
    vi.stubGlobal('EventSource', MockEventSource)
    vi.stubGlobal('confirm', vi.fn(() => true))
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
  })

  it('renders chat session details and opens chat controls', async () => {
    const user = userEvent.setup()
    renderRoute(<ChatPageNew />)

    expect(await screen.findByText('Coverage Session')).toBeInTheDocument()
    expect(screen.getByText('The plan is ready.')).toBeInTheDocument()

    await user.click(screen.getByText('chatPage.modes.auto.label'))
    expect(screen.getByText('chatPage.agentMode')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Follow up' } })
    expect(screen.getByRole('textbox')).toHaveValue('Follow up')
  })

  it('renders ingestion queue state and opens ingestion forms', async () => {
    const user = userEvent.setup()
    renderRoute(<IngestionManagementPage />)

    await waitFor(() => expect(apiMocks.ingestionQueueApi.getQueue).toHaveBeenCalled())
    expect((await screen.findAllByText(/default|running/i)).length).toBeGreaterThan(0)

    await user.click(screen.getByText('ingestion.addToQueue'))
    expect(screen.getAllByText(/documents|all files/i).length).toBeGreaterThan(0)
  })

  it('renders backup inventory and configuration data', async () => {
    renderRoute(<BackupManagementPage />)

    expect(await screen.findByText(/backup-1|rag_default/i)).toBeInTheDocument()
    expect(apiMocks.backupsApi.getStorageStats).toHaveBeenCalled()
  })

  it('renders cloud source connections and detail data', async () => {
    const user = userEvent.setup()
    renderWithParamRoute(<CloudSourceConnectionsPage />, '/en/cloud-sources/connections/:connectionId?', '/en/cloud-sources/connections/conn-1')

    expect(await screen.findByText('Drive')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Test$/i }))
    await waitFor(() => expect(apiMocks.cloudSourcesApi.testConnection).toHaveBeenCalled())
  })

  it('renders email and cloud configuration data from direct fetch calls', async () => {
    renderRoute(<EmailCloudConfigPage />)

    expect((await screen.findAllByText(/Gmail|Google Drive|Airbyte/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/Work Gmail|rag_default/i)).toBeInTheDocument()
  })

  it('renders embedding benchmark providers and history', async () => {
    renderRoute(<EmbeddingBenchmarkPage />)

    expect((await screen.findAllByText(/OpenAI|Ollama/i)).length).toBeGreaterThan(0)
    expect(apiMocks.benchmarkApi.getProviders).toHaveBeenCalled()
  })

  it('renders job history and expands job detail tabs', async () => {
    const user = userEvent.setup()
    renderRoute(<JobHistoryPage />)

    const jobRow = await screen.findByText('job-1...')
    expect(jobRow).toBeInTheDocument()
    await user.click(jobRow)
    expect(await screen.findByText('Total Files')).toBeInTheDocument()
  })

  it('renders prompt templates and editor surface', async () => {
    renderRoute(<PromptManagementPage />)

    expect(await screen.findByText('System Prompt')).toBeInTheDocument()
    expect(screen.getByText('Default system prompt')).toBeInTheDocument()
  })

  it('renders strategy metrics and loads selected strategy detail', async () => {
    const user = userEvent.setup()
    renderRoute(<StrategiesPage />)

    expect(await screen.findByText('Balanced')).toBeInTheDocument()
    await user.click(screen.getByText('Balanced'))
    await waitFor(() => expect(apiMocks.strategiesApi.get).toHaveBeenCalledWith('balanced'))
  })

  it('renders strategy A/B test setup and strategies', async () => {
    renderRoute(<StrategyABTestPage />)

    expect(await screen.findByText(/Balanced|Legal/i)).toBeInTheDocument()
    expect(apiMocks.strategiesApi.list).toHaveBeenCalled()
  })

  it('renders ingestion analytics tabs backed by fetch responses', async () => {
    const user = userEvent.setup()
    renderRoute(<IngestionAnalyticsPage />)

    expect(await screen.findByText(/100|92/i)).toBeInTheDocument()
    for (const label of [/outliers/i, /extensions/i, /timeline/i, /overview/i]) {
      const target = screen.queryAllByText(label)[0]
      if (target) await user.click(target)
    }
    expect(screen.getByText(/huge.pdf|pdf/i)).toBeInTheDocument()
  })

  it('renders live debug system state and activity', async () => {
    renderRoute(<LiveDebugPage />)

    expect((await screen.findAllByText(/request-1234|gpt-5.2/i)).length).toBeGreaterThan(0)
    expect(apiMocks.debugApi.getSystemState).toHaveBeenCalled()
  })

  it('renders failed ingestion documents and summary', async () => {
    renderRoute(<FailedDocumentsPage />)

    expect(await screen.findByText('slow.pdf')).toBeInTheDocument()
    expect(screen.getByText('Timed out')).toBeInTheDocument()
  })
})
