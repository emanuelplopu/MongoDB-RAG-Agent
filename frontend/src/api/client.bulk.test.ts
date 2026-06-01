import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  apiKeysApi,
  authApi,
  backupsApi,
  benchmarkApi,
  chatApi,
  clearAuthToken,
  cloudSourcesApi,
  debugApi,
  documentsApi,
  fileRegistryApi,
  getAuthToken,
  indexesApi,
  ingestionApi,
  ingestionQueueApi,
  localLlmApi,
  profilesApi,
  promptsApi,
  searchApi,
  sessionsApi,
  setAuthToken,
  statusApi,
  strategiesApi,
  supportApi,
  systemApi,
} from './client'

const axiosMock = vi.hoisted(() => {
  const dataFor = (method: string, args: unknown[]) => ({ ok: true, method, args })
  const mockApi = {
    defaults: { baseURL: '/api/v1', headers: { common: {} } },
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn(async (...args: unknown[]) => ({ data: dataFor('get', args) })),
    post: vi.fn(async (...args: unknown[]) => ({ data: dataFor('post', args) })),
    put: vi.fn(async (...args: unknown[]) => ({ data: dataFor('put', args) })),
    patch: vi.fn(async (...args: unknown[]) => ({ data: dataFor('patch', args) })),
    delete: vi.fn(async (...args: unknown[]) => ({ data: dataFor('delete', args) })),
    request: vi.fn(async (...args: unknown[]) => ({ data: dataFor('request', args) })),
  }

  return {
    create: vi.fn(() => mockApi),
    mockApi,
  }
})

vi.mock('axios', () => ({
  default: {
    create: axiosMock.create,
  },
  AxiosError: class AxiosError extends Error {},
  AxiosInstance: class AxiosInstance {},
  AxiosResponse: class AxiosResponse {},
}))

describe('api client broad wrapper coverage', () => {
  const waitForAssertion = async (assertion: () => void) => {
    let lastError: unknown
    for (let i = 0; i < 25; i += 1) {
      try {
        assertion()
        return
      } catch (error) {
        lastError = error
        await new Promise((resolve) => setTimeout(resolve, 0))
      }
    }
    throw lastError
  }

  beforeEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
    axiosMock.mockApi.get.mockReset()
    axiosMock.mockApi.post.mockReset()
    axiosMock.mockApi.put.mockReset()
    axiosMock.mockApi.patch.mockReset()
    axiosMock.mockApi.delete.mockReset()
    axiosMock.mockApi.request.mockReset()
    axiosMock.mockApi.get.mockImplementation(async (...args: unknown[]) => ({ data: { ok: true, method: 'get', args } }))
    axiosMock.mockApi.post.mockImplementation(async (...args: unknown[]) => ({ data: { ok: true, method: 'post', args } }))
    axiosMock.mockApi.put.mockImplementation(async (...args: unknown[]) => ({ data: { ok: true, method: 'put', args } }))
    axiosMock.mockApi.patch.mockImplementation(async (...args: unknown[]) => ({ data: { ok: true, method: 'patch', args } }))
    axiosMock.mockApi.delete.mockImplementation(async (...args: unknown[]) => ({ data: { ok: true, method: 'delete', args } }))
  })

  it('covers auth token helpers and ApiError user messages', () => {
    setAuthToken('token-1')
    expect(getAuthToken()).toBe('token-1')
    clearAuthToken()
    expect(getAuthToken()).toBeNull()

    expect(new ApiError('', 0, 'NETWORK_ERROR').getUserMessage()).toContain('Unable to connect')
    expect(new ApiError('', 429, 'HTTP_429').getUserMessage()).toContain('busy')
    expect(new ApiError('Invalid email or password', 401, 'HTTP_401').getUserMessage()).toBe('Invalid email or password')
    expect(new ApiError('', 401, 'HTTP_401').getUserMessage()).toContain('expired')
    expect(new ApiError('', 403, 'HTTP_403').getUserMessage()).toContain('permission')
    expect(new ApiError('', 404, 'HTTP_404').getUserMessage()).toContain('not found')
    expect(new ApiError('', 422, 'HTTP_422').getUserMessage()).toContain('input')
    expect(new ApiError('failed', 500, 'HTTP_500', {}, 'err-1').getUserMessage()).toContain('err-1')
    expect(
      new ApiError('failed', 500, 'HTTP_500', {}, 'err-2', {
        exception_type: 'ValueError',
        exception_message: 'bad value',
      }).getUserMessage(true)
    ).toContain('ValueError')
    expect(new ApiError('Specific problem', 418, 'HTTP_418').getUserMessage()).toBe('Specific problem')
  })

  it('routes broad API wrapper calls through the configured axios instance', async () => {
    const profile = { key: 'p', name: 'Profile', documents_folders: ['/docs'] }
    const config = { enabled: true }
    const endpoint = { id: 'ep-1', name: 'Local', url: 'http://localhost:11434', provider_type: 'ollama', enabled: true }
    const cloudConnection = { provider: 'owncloud' as const, display_name: 'Files', server_url: 'https://files', username: 'u', password: 'p' }
    const syncConfig = {
      connection_id: 'conn-1',
      profile_key: 'default',
      name: 'Sync',
      source_paths: [{ path: '/', remote_id: 'root', include_subfolders: true }],
    }
    const promptPayload = { name: 'Prompt', system_prompt: 'Answer carefully' }
    const backupRequest = { backup_type: 'full' as const, profile_key: 'default' }
    const benchmarkRequest = {
      providers: [{ provider_type: 'openai', model: 'text-embedding-3-small' }],
      file_content: 'Zm9v',
      file_name: 'a.txt',
    }

    const calls: Array<() => Promise<unknown> | unknown> = [
      () => chatApi.send('hello', 'conv-1'),
      () => chatApi.getConversation('conv-1'),
      () => chatApi.listConversations(),
      () => chatApi.deleteConversation('conv-1'),
      () => searchApi.search('query', 'hybrid', 7),
      () => searchApi.semanticSearch('query', 3),
      () => searchApi.textSearch('query', 4),
      () => searchApi.hybridSearch('query', 5),
      () => profilesApi.list(),
      () => profilesApi.getActive(),
      () => profilesApi.switch('default'),
      () => profilesApi.create(profile),
      () => profilesApi.update('default', { name: 'Updated' }),
      () => profilesApi.delete('old'),
      () => documentsApi.list(2, 25, '/docs', 'term', true, 'name', 'asc'),
      () => documentsApi.getFolders(),
      () => documentsApi.get('doc-1'),
      () => documentsApi.getFullInfo('doc-1'),
      () => documentsApi.delete('doc-1'),
      () => documentsApi.openInExplorer('doc-1'),
      () => ingestionApi.start({ profile: 'default', incremental: true }),
      () => ingestionApi.getStatus(),
      () => ingestionApi.getJobStatus('job-1'),
      () => ingestionApi.cancel('job-1'),
      () => ingestionApi.pause(),
      () => ingestionApi.resume(),
      () => ingestionApi.stop(),
      () => ingestionApi.getRuns(3, 9),
      () => ingestionApi.setupIndexes(),
      () => ingestionApi.getLogs(10, 20),
      () => ingestionApi.clearLogs(),
      () => ingestionApi.getPendingFiles(25),
      () => ingestionApi.startMetadataRebuild(),
      () => ingestionApi.getMetadataRebuildStatus(),
      () => ingestionApi.cancelMetadataRebuild(),
      () => systemApi.health(),
      () => systemApi.stats(),
      () => systemApi.config(),
      () => systemApi.info(),
      () => systemApi.indexes(),
      () => systemApi.createIndexes(),
      () => systemApi.databaseStats(),
      () => systemApi.listLLMModels(),
      () => systemApi.listEmbeddingModels(),
      () => systemApi.getConfigOptions(),
      () => systemApi.updateConfig({ llm_model: 'gpt-5.2' }),
      () => systemApi.saveConfigToDb({ embedding_model: 'text-embedding-3-small' }),
      () => systemApi.getSavedConfig(),
      () => systemApi.getLLMProviderConfig(),
      () => systemApi.saveLLMProviderConfig({ orchestrator_provider: 'openai' }),
      () => systemApi.getProviderModelsDetailed('openai'),
      () => systemApi.fetchModelsFromApi('openai', 'sk-test'),
      () => systemApi.testProviderConnection({ provider_id: 'openai', api_key: 'sk-test' }),
      () => systemApi.listTools(),
      () => systemApi.getToolDetails('search'),
      () => systemApi.testTool({ tool_name: 'search', parameters: { query: 'hello' } }),
      () => systemApi.switchModelVersions({ orchestrator_model: 'gpt-5.2' }),
      () => systemApi.getAgentPerformanceConfig(),
      () => systemApi.saveAgentPerformanceConfig({} as any),
      () => systemApi.getIngestionPerformanceConfig(),
      () => systemApi.saveIngestionPerformanceConfig({} as any),
      () => systemApi.testModelCapability('openai/gpt-5.2', 'worker'),
      () => systemApi.getModelCapabilities(),
      () => systemApi.approveModel('openai/gpt-5.2', 'orchestrator', true),
      () => sessionsApi.list('folder-1'),
      () => sessionsApi.create({ title: 'Session', folder_id: 'folder-1', model: 'gpt-5.2' }),
      () => sessionsApi.get('session-1'),
      () => sessionsApi.update('session-1', { title: 'Updated', is_pinned: true }),
      () => sessionsApi.delete('session-1'),
      () => sessionsApi.sendMessage('session-1', 'hello', { agent_mode: 'auto', strategy_id: 'balanced', language: 'en' }),
      () => sessionsApi.estimateTokens([{ filename: 'a.txt', content_type: 'text/plain', size_bytes: 3, content: 'foo' } as any]),
      () => sessionsApi.clearMessages('session-1'),
      () => sessionsApi.listFolders(),
      () => sessionsApi.createFolder('Project', '#fff'),
      () => sessionsApi.updateFolder('folder-1', { name: 'Renamed', is_expanded: false }),
      () => sessionsApi.deleteFolder('folder-1'),
      () => sessionsApi.getModelPricing(),
      () => sessionsApi.listArchived(),
      () => sessionsApi.archiveSessions(['session-1']),
      () => sessionsApi.restoreSessions(['session-1']),
      () => sessionsApi.deletePermanently(['session-1']),
      () => sessionsApi.moveToFolder(['session-1'], null),
      () => sessionsApi.exportSession('session-1'),
      () => authApi.register({ email: 'a@example.com', name: 'A', password: 'secret' }),
      () => authApi.login({ email: 'a@example.com', password: 'secret' }),
      () => authApi.logout(),
      () => authApi.getMe(),
      () => authApi.updateMe({ name: 'A' }),
      () => authApi.changePassword('old', 'new'),
      () => authApi.listUsers(),
      () => authApi.getAccessMatrix(),
      () => authApi.setProfileAccess({ user_id: 'u1', profile_key: 'default', has_access: true }),
      () => authApi.createUser({ email: 'b@example.com', name: 'B', password: 'secret' }),
      () => authApi.updateUser('u1', { name: 'B' }),
      () => authApi.setUserStatus('u1', false),
      () => authApi.deleteUser('u1'),
      () => authApi.getPreferences(),
      () => authApi.updatePreferences({ language: 'en' }),
      () => apiKeysApi.list(),
      () => apiKeysApi.create({ name: 'Key', scopes: ['read'] }),
      () => apiKeysApi.revoke('key-1'),
      () => apiKeysApi.toggle('key-1'),
      () => apiKeysApi.listAll(),
      () => statusApi.getDashboard(),
      () => statusApi.getProfileMetrics('default'),
      () => statusApi.getDetailedHealth(),
      () => indexesApi.getDashboard(),
      () => indexesApi.createIndexes(),
      () => indexesApi.getPerformanceHistory(48),
      () => ingestionQueueApi.getQueue(),
      () => ingestionQueueApi.addToQueue({ profile_key: 'default' }),
      () => ingestionQueueApi.addMultipleToQueue([{ profile_key: 'default' }]),
      () => ingestionQueueApi.removeFromQueue('job-1'),
      () => ingestionQueueApi.clearQueue(),
      () => ingestionQueueApi.reorderQueue(['job-2', 'job-1']),
      () => ingestionQueueApi.getSchedules(),
      () => ingestionQueueApi.createSchedule({ profile_key: 'default', frequency: 'daily' }),
      () => ingestionQueueApi.updateSchedule('schedule-1', { profile_key: 'default', frequency: 'weekly' }),
      () => ingestionQueueApi.deleteSchedule('schedule-1'),
      () => ingestionQueueApi.toggleSchedule('schedule-1'),
      () => ingestionQueueApi.runScheduleNow('schedule-1'),
      () => fileRegistryApi.getStats('default'),
      () => fileRegistryApi.listFiles({ profile_key: 'default', classification: 'timeout', limit: 10 }),
      () => fileRegistryApi.reclassifyFile('/docs/a.pdf', 'normal'),
      () => fileRegistryApi.clearRegistry({ profile_key: 'default' }),
      () => fileRegistryApi.retryCategory('default', 'timeout'),
      () => localLlmApi.discover(),
      () => localLlmApi.pullModel('ollama', 'llama3', 'http://localhost:11434'),
      () => localLlmApi.getPullStatus('ollama'),
      () => localLlmApi.getOfflineConfig(),
      () => localLlmApi.saveOfflineConfig(config as any),
      () => localLlmApi.testModel('ollama', 'llama3', 'chat', 'http://localhost:11434'),
      () => localLlmApi.compareModels(),
      () => localLlmApi.scanNetwork({ custom_ips: ['127.0.0.1'], ports: [11434] }),
      () => localLlmApi.getCustomEndpoints(),
      () => localLlmApi.addCustomEndpoint(endpoint),
      () => localLlmApi.deleteCustomEndpoint('ep-1'),
      () => localLlmApi.testCustomEndpoint('ep-1'),
      () => cloudSourcesApi.getProviders(),
      () => cloudSourcesApi.getProvider('owncloud'),
      () => cloudSourcesApi.getConnections({ provider: 'owncloud', status: 'active' }),
      () => cloudSourcesApi.createConnection(cloudConnection),
      () => cloudSourcesApi.getConnection('conn-1'),
      () => cloudSourcesApi.updateConnection('conn-1', { display_name: 'New' }),
      () => cloudSourcesApi.deleteConnection('conn-1'),
      () => cloudSourcesApi.testConnection('conn-1'),
      () => cloudSourcesApi.browseFolder('conn-1', { path: '/' }),
      () => cloudSourcesApi.initiateOAuth('google_drive', 'Drive'),
      () => cloudSourcesApi.refreshOAuthTokens('conn-1'),
      () => cloudSourcesApi.revokeOAuthTokens('conn-1'),
      () => cloudSourcesApi.getSyncConfigs({ connection_id: 'conn-1' }),
      () => cloudSourcesApi.createSyncConfig(syncConfig),
      () => cloudSourcesApi.getSyncConfig('sync-1'),
      () => cloudSourcesApi.updateSyncConfig('sync-1', { name: 'Updated' }),
      () => cloudSourcesApi.deleteSyncConfig('sync-1'),
      () => cloudSourcesApi.runSync('sync-1', { type: 'manual', force_full: true }),
      () => cloudSourcesApi.getSyncStatus('sync-1'),
      () => cloudSourcesApi.pauseSync('sync-1'),
      () => cloudSourcesApi.resumeSync('sync-1'),
      () => cloudSourcesApi.cancelSync('sync-1'),
      () => cloudSourcesApi.getSyncHistory('sync-1', { page: 2, page_size: 10 }),
      () => cloudSourcesApi.getDashboard(),
      () => cloudSourcesApi.getJob('job-1'),
      () => cloudSourcesApi.getCloudSourceInfo('doc-1'),
      () => cloudSourcesApi.getCachedFile('doc-1', 'conn-1'),
      () => cloudSourcesApi.getCacheStats('conn-1'),
      () => cloudSourcesApi.clearCache('conn-1'),
      () => promptsApi.list('chat'),
      () => promptsApi.get('prompt-1'),
      () => promptsApi.create(promptPayload),
      () => promptsApi.update('prompt-1', { description: 'Updated' }),
      () => promptsApi.delete('prompt-1'),
      () => promptsApi.createVersion('prompt-1', { system_prompt: 'New' }),
      () => promptsApi.activateVersion('prompt-1', 2),
      () => promptsApi.getVersion('prompt-1', 2),
      () => promptsApi.test({ template_id: 'prompt-1', test_message: 'hello' }),
      () => promptsApi.compare({ template_id: 'prompt-1', version_a: 1, version_b: 2 }),
      () => strategiesApi.list('general'),
      () => strategiesApi.getDefault(),
      () => strategiesApi.get('balanced'),
      () => strategiesApi.getMetrics('balanced', 24, 'general'),
      () => strategiesApi.getAllMetrics(24, 'general'),
      () => strategiesApi.getForDomain('legal'),
      () => strategiesApi.autoDetect('contract review'),
      () => strategiesApi.compare('balanced', 'legal', 24, 'general'),
      () => strategiesApi.recordFeedback('balanced', 'session-1', 5, 'good'),
      () => strategiesApi.abCompareResponses({
        query: 'q',
        response_a: 'a',
        response_b: 'b',
        strategy_a: 'balanced',
        strategy_b: 'legal',
        latency_a_ms: 10,
        latency_b_ms: 20,
      }),
      () => backupsApi.create(backupRequest),
      () => backupsApi.createCheckpoint({ name: 'checkpoint', profile_key: 'default' }),
      () => backupsApi.list('default', 'full', 10, 5),
      () => backupsApi.listCheckpoints('default', 10),
      () => backupsApi.getDetails('backup-1'),
      () => backupsApi.getChain('backup-1'),
      () => backupsApi.restore('backup-1', { backup_id: 'backup-1', restore_mode: 'full' }),
      () => backupsApi.delete('backup-1'),
      () => backupsApi.getConfig(),
      () => backupsApi.updateConfig({ retention_days: 7 }),
      () => backupsApi.getStatus(),
      () => backupsApi.getStorageStats(),
      () => benchmarkApi.runBenchmark(benchmarkRequest),
      () => benchmarkApi.getProviders(),
      () => benchmarkApi.testProvider({ provider_type: 'openai', model: 'text-embedding-3-small' }),
      () => benchmarkApi.getHistory(5),
      () => benchmarkApi.getResult('bench-1'),
      () => benchmarkApi.deleteResult('bench-1'),
      () => supportApi.getSessionDiagnostic('session-1'),
      () => supportApi.submitSupportRequest({ diagnostic_text: 'diag', session_id: 'session-1' }),
      () => debugApi.getRequestDetail('req-1'),
    ]

    for (const call of calls) {
      await call()
    }

    expect(documentsApi.getFileUrl('doc-1')).toBe('/api/v1/ingestion/documents/doc-1/file')
    expect(ingestionApi.getLogsStreamUrl()).toBe('/api/v1/ingestion/logs/stream')
    expect(ingestionApi.getEventsStreamUrl()).toBe('/api/v1/ingestion/events/stream')
    expect(cloudSourcesApi.getJobLogsUrl('job-1')).toBe('/api/v1/cloud-sources/jobs/job-1/logs')
    expect(cloudSourcesApi.getCachedFileUrl('conn-1', 'doc-1')).toBe('/api/v1/cloud-sources/cache/serve/conn-1/doc-1')
    expect(axiosMock.mockApi.post).toHaveBeenCalledWith('/profiles/switch', { profile_key: 'default' })
    expect(axiosMock.mockApi.get).toHaveBeenCalledWith('/system/models/llm')
    expect(axiosMock.mockApi.post).toHaveBeenCalledWith('/system/models/openai%2Fgpt-5.2/test?role=worker')
  })

  it('normalizes lookup and debug response helpers', async () => {
    axiosMock.mockApi.get.mockImplementation(async (url: string) => {
      if (url === '/ingestion/documents/lookup') {
        return {
          data: {
            found: true,
            document: { id: 'doc-1', title: 'Doc', source: '/docs/doc.md' },
          },
        }
      }
      if (url === '/debug/activity/live') {
        return {
          data: {
            activity: [
              {
                request_id: 'req-1',
                session_id: 'session-1',
                started_at: '2026-05-18T08:00:00Z',
                completed_at: '2026-05-18T08:00:01Z',
                duration_ms: 1000,
                summary: { total_tokens: 42, total_errors: 0, phases_completed: ['plan'], models_used: ['gpt-5.2'] },
              },
            ],
          },
        }
      }
      if (url === '/debug/activity/active') {
        return {
          data: {
            active: [
              {
                request_id: 'req-2',
                session_id: 'session-1',
                model: 'gpt-5.2',
                started_at: '2026-05-18T08:00:00Z',
                elapsed_seconds: 1.25,
              },
            ],
          },
        }
      }
      if (url === '/debug/system-state') {
        return {
          data: {
            orchestrator: 'openai/gpt-5.2',
            worker: 'openai/gpt-5.2-mini',
            active_profile: 'default',
            active_requests_count: 1,
          },
        }
      }
      return { data: { ok: true } }
    })

    await expect(documentsApi.findBySource('/docs/doc.md')).resolves.toEqual({
      id: 'doc-1',
      title: 'Doc',
      source: '/docs/doc.md',
      chunks_count: 0,
      metadata: {},
    })
    await expect(debugApi.getLiveActivity()).resolves.toMatchObject({
      count: 1,
      activity: [{ request_id: 'req-1', status: 'complete', phases_count: 1 }],
    })
    await expect(debugApi.getActiveRequests()).resolves.toMatchObject({
      count: 1,
      active: [{ request_id: 'req-2', elapsed_ms: 1250 }],
    })
    await expect(debugApi.getSystemState()).resolves.toMatchObject({
      orchestrator_provider: 'openai',
      orchestrator_model: 'gpt-5.2',
      worker_provider: 'openai',
      worker_model: 'gpt-5.2-mini',
      active_requests: 1,
    })

    axiosMock.mockApi.get.mockRejectedValueOnce(new Error('lookup failed'))
    await expect(documentsApi.findBySource('/missing.md')).resolves.toBeNull()
  })

  it('streams session messages through SSE callbacks and handles stream errors', async () => {
    const encoder = new TextEncoder()
    const makeStreamResponse = (events: string[]) =>
      ({
        ok: true,
        body: new ReadableStream({
          start(controller) {
            for (const event of events) {
              controller.enqueue(encoder.encode(event))
            }
            controller.close()
          },
        }),
      }) as unknown as Response

    const fetchMock = vi.fn(async () =>
      makeStreamResponse([
        `data: ${JSON.stringify({ type: 'start', mode: 'federated', models: { orchestrator: 'gpt-5.2', worker: 'gpt-5.2-mini' } })}\n`,
        `data: ${JSON.stringify({ type: 'orchestrator_step', phase: 'plan', reasoning: 'thinking', output: 'tasks', duration_ms: 12, tokens: 4 })}\n`,
        `data: ${JSON.stringify({ type: 'worker_step', task_id: 'task-1', task_type: 'search', tool: 'vector', input: { query: 'q' }, documents_count: 1, links_count: 0, duration_ms: 20, success: true, documents: [] })}\n`,
        `data: ${JSON.stringify({ type: 'response', content: 'answer', sources: [], stats: { total_tokens: 8, orchestrator_tokens: 4, worker_tokens: 4, cost_usd: 0.001, tokens_per_second: 2, latency_ms: 100 }, trace: { mode: 'federated' } })}\n`,
        `data: ${JSON.stringify({ type: 'error', message: 'recoverable warning' })}\n`,
        `data: ${JSON.stringify({ type: 'title_update', title: 'New title' })}\n`,
        'data: {not-json}\n',
        `data: ${JSON.stringify({ type: 'done' })}\n`,
      ])
    )
    vi.stubGlobal('fetch', fetchMock)

    const callbacks = {
      onStart: vi.fn(),
      onOrchestratorStep: vi.fn(),
      onWorkerStep: vi.fn(),
      onResponse: vi.fn(),
      onError: vi.fn(),
      onDone: vi.fn(),
      onTitleUpdate: vi.fn(),
    }

    const stream = sessionsApi.sendMessageStream(
      'session-1',
      'hello',
      {
        attachments: [{ filename: 'a.txt', content_type: 'text/plain', size_bytes: 3, content: 'foo' } as any],
        agent_mode: 'auto',
        strategy_id: 'balanced',
        language: 'en',
      },
      callbacks
    )

    await waitForAssertion(() => expect(callbacks.onDone).toHaveBeenCalled())
    expect(callbacks.onStart).toHaveBeenCalledWith(expect.objectContaining({ mode: 'federated' }))
    expect(callbacks.onOrchestratorStep).toHaveBeenCalledWith(expect.objectContaining({ phase: 'plan' }))
    expect(callbacks.onWorkerStep).toHaveBeenCalledWith(expect.objectContaining({ task_id: 'task-1' }))
    expect(callbacks.onResponse).toHaveBeenCalledWith(expect.objectContaining({ content: 'answer' }))
    expect(callbacks.onError).toHaveBeenCalledWith('recoverable warning')
    expect(callbacks.onTitleUpdate).toHaveBeenCalledWith('New title')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sessions/session-1/messages/stream',
      expect.objectContaining({ method: 'POST', signal: expect.any(AbortSignal) })
    )
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toMatchObject({
      content: 'hello',
      agent_mode: 'auto',
      strategy_id: 'balanced',
      language: 'en',
    })
    stream.abort()

    const httpError = vi.fn()
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503, body: null } as unknown as Response)
    sessionsApi.sendMessageStream('session-1', 'hello', undefined, { onError: httpError })
    await waitForAssertion(() => expect(httpError).toHaveBeenCalledWith('HTTP error 503'))

    const bodyError = vi.fn()
    fetchMock.mockResolvedValueOnce({ ok: true, body: null } as unknown as Response)
    sessionsApi.sendMessageStream('session-1', 'hello', undefined, { onError: bodyError })
    await waitForAssertion(() => expect(bodyError).toHaveBeenCalledWith('No response body'))

    const abortError = vi.fn()
    const aborted = Object.assign(new Error('aborted'), { name: 'AbortError' })
    fetchMock.mockRejectedValueOnce(aborted)
    sessionsApi.sendMessageStream('session-1', 'hello', undefined, { onError: abortError })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(abortError).not.toHaveBeenCalled()
  })
})
