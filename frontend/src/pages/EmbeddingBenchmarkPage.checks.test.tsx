import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import EmbeddingBenchmarkPage from './EmbeddingBenchmarkPage'
import type { EmbeddingChecksResult } from '../api/client'

const authState = vi.hoisted(() => ({
  user: { id: 'admin-1', email: 'admin@example.com', name: 'Admin', is_admin: true, is_active: true },
  isLoading: false,
}))

const apiMocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
  runChecks: vi.fn(),
  getChecksHistory: vi.fn(),
  getHistory: vi.fn(),
  runBenchmark: vi.fn(),
  testProvider: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : key),
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../contexts/LanguageContext', () => ({
  useLanguage: () => ({ language: 'en', setLanguage: vi.fn() }),
}))

vi.mock('../api/client', () => ({
  benchmarkApi: apiMocks,
}))

const checksResult: EmbeddingChecksResult = {
  id: 'chk-1',
  timestamp: '2026-10-10T12:00:00Z',
  kind: 'checks',
  sample: {
    source: 'index',
    count: 60,
    avg_chars: 540,
    database: 'rag_parhelion',
    collection: 'chunks',
    reference_model: 'text-embedding-3-large',
    reference_dimension: 3072,
  },
  options: {
    sample_size: 60,
    batch_sizes: [1, 8],
    single_batch_limit: 32,
    truncate_dims: [512, 256],
    latency_runs: 5,
    top_k: 10,
    pseudo_query_every: 10,
    check_images: true,
    query_text: 'q',
  },
  results: [
    {
      provider: 'Provider 1',
      model: 'embeddinggemma-2',
      provider_type: 'llamacpp',
      base_url: 'http://embedder:8080',
      success: true,
      error: null,
      warnings: [],
      query_prefix: 'task: search result | query: ',
      document_prefix: 'title: none | text: ',
      truncate_dim: null,
      capabilities: { build: 'b11541', model_alias: 'embeddinggemma-2', modalities: { vision: true }, context_length: 8192, slots: 4 },
      hardware: { hostname: 'gpu-box', cpu_count: 12, ram_gb: 64, gpus: [{ name: 'NVIDIA RTX 4000', memory_total_mb: 20480 }], note: '' },
      latency: { cold_ms: 75, p50_ms: 68, p95_ms: 84, runs: 5 },
      throughput: [
        { batch_size: 1, texts: 32, wall_ms: 2600, texts_per_s: 12.2, tokens: 4300, tokens_per_s: 1650, token_source: 'server' },
        { batch_size: 8, texts: 60, wall_ms: 3000, texts_per_s: 20, tokens: 9000, tokens_per_s: 3000, token_source: 'server' },
      ],
      vectors: { dimension: 768, expected_dimension: 768, dimension_matches: true, norm_mean: 1, norm_min: 1, norm_max: 1, normalized: true },
      truncation: [
        { dim: 512, topk_overlap: 0.915 },
        { dim: 256, topk_overlap: 0.83 },
      ],
      retrieval: { queries: 6, top1_hits: 5, top5_hits: 6, top1_rate: 0.833, top5_rate: 1 },
      image: { supported: true, tokens: 468, latency_ms: 5100, vector_changed: true, error: null },
      agreement_with_index: 0.6,
      total_ms: 12000,
    },
  ],
  agreement: [],
}

describe('EmbeddingBenchmarkPage — validation checks mode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getProviders.mockResolvedValue({
      openai: { name: 'OpenAI', available: false, models: [] },
      ollama: { name: 'Ollama', available: false, url: null, models: [] },
      llamacpp: {
        name: 'llama.cpp',
        available: true,
        url: 'http://embedder:8080',
        models: [{ id: 'embeddinggemma-2', name: 'embeddinggemma-2', dimension: 768, multimodal: true }],
      },
      vllm: { name: 'vLLM', available: false, models: [] },
    })
    apiMocks.runChecks.mockResolvedValue(checksResult)
    apiMocks.getChecksHistory.mockResolvedValue({ results: [checksResult], total: 1 })
    apiMocks.getHistory.mockResolvedValue({ results: [], total: 0 })
  })

  const renderPage = () =>
    render(
      <MemoryRouter>
        <EmbeddingBenchmarkPage />
      </MemoryRouter>
    )

  it('runs the checks against the index sample and renders the results', async () => {
    renderPage()
    await waitFor(() => expect(apiMocks.getProviders).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('tab', { name: 'Validation checks' }))
    expect(screen.getByText('Validation options')).toBeInTheDocument()
    // No file upload needed when sampling from the index
    expect(screen.queryByText('File Upload')).not.toBeInTheDocument()

    const providerSelects = screen.getAllByRole('combobox')
    fireEvent.change(providerSelects[0], { target: { value: 'llamacpp' } })
    const modelSelect = await screen.findByDisplayValue('Select model...')
    fireEvent.change(modelSelect, { target: { value: 'embeddinggemma-2' } })

    const runButton = await screen.findByRole('button', { name: /Run Checks/ })
    await waitFor(() => expect(runButton).not.toBeDisabled())
    fireEvent.click(runButton)

    await waitFor(() => expect(apiMocks.runChecks).toHaveBeenCalledTimes(1))
    const request = apiMocks.runChecks.mock.calls[0][0]
    expect(request.providers).toHaveLength(1)
    expect(request.providers[0]).toMatchObject({ provider_type: 'llamacpp', model: 'embeddinggemma-2', base_url: 'http://embedder:8080' })
    expect(request.sample).toEqual({ source: 'index', limit: 200, compare_with_index: true })
    expect(request.options).toMatchObject({ batch_sizes: [1, 8, 32, 64], truncate_dims: [512, 256], latency_runs: 10, check_images: true })

    expect(await screen.findByTestId('embedding-check-results')).toBeInTheDocument()
    expect(screen.getByText('68 / 84 ms')).toBeInTheDocument()
    expect(screen.getByText('3000 tok/s')).toBeInTheDocument()
    expect(screen.getByText('768d')).toBeInTheDocument()
    expect(screen.getByText('512d: 92% · 256d: 83%')).toBeInTheDocument()
    expect(screen.getByText(/supported \(468 tok, 5100 ms\)/)).toBeInTheDocument()
    expect(screen.getByText(/NVIDIA RTX 4000 20 GB/)).toBeInTheDocument()
    expect(screen.getByText(/index vectors: text-embedding-3-large \(3072d\)/)).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()
  })

  it('shows the checks history for the checks mode', async () => {
    renderPage()
    await waitFor(() => expect(apiMocks.getProviders).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('tab', { name: 'Validation checks' }))
    fireEvent.click(screen.getByRole('button', { name: /History/ }))

    await waitFor(() => expect(apiMocks.getChecksHistory).toHaveBeenCalledWith(10))
    expect(await screen.findByText(/60 texts \(index\)/)).toBeInTheDocument()
    expect(apiMocks.getHistory).not.toHaveBeenCalled()
  })

  it('renders a failed provider with its error', async () => {
    apiMocks.runChecks.mockResolvedValue({
      ...checksResult,
      results: [{ ...checksResult.results[0], success: false, error: 'connection refused', throughput: [], truncation: [] }],
    })
    renderPage()
    await waitFor(() => expect(apiMocks.getProviders).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('tab', { name: 'Validation checks' }))
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'llamacpp' } })
    fireEvent.change(await screen.findByDisplayValue('Select model...'), { target: { value: 'embeddinggemma-2' } })
    const runButton = await screen.findByRole('button', { name: /Run Checks/ })
    await waitFor(() => expect(runButton).not.toBeDisabled())
    fireEvent.click(runButton)

    expect(await screen.findByText('connection refused')).toBeInTheDocument()
  })
})
