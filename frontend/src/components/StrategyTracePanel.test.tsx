import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StrategyTracePanel from './StrategyTracePanel'
import type { StrategyNodeEvent, StrategyTraceResponse } from '../api/client'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, options?: { defaultValue?: string; count?: number }) => options?.defaultValue ?? _key,
  }),
}))

const droppedItems = Array.from({ length: 7 }, (_, index) => ({
  kind: index % 2 === 0 ? 'document' : 'tool',
  tokens: 100 + index,
  reason: `budget reason ${index + 1}`,
}))

const completeTrace: StrategyTraceResponse = {
  active: true,
  spec_selected: 'balanced',
  spec_version: '2.1',
  routing_reason: 'Matched strategy OS policy',
  adaptive_scores: [
    {
      strategy_id: 'balanced',
      latency_fit: 0.91,
      quality: 0.88,
      resource_fit: 0.84,
      residency: 'local',
      fast_path_bias: 0.3,
      total: 0.97,
    },
    {
      strategy_id: 'fallback',
      latency_fit: 0.4,
      quality: null,
      resource_fit: undefined,
      residency: { region: 'remote' },
      fast_path_bias: 'blocked',
      total: 0.41,
    },
  ],
  fast_path_eligible: true,
  nodes_executed: [
    {
      node_id: 'route',
      node_type: 'router',
      status: 'success',
      duration_ms: 120,
      tokens_used: 0,
      llm_call_ids: [],
      error: null,
    },
    {
      node_id: 'compose',
      node_type: 'composer',
      status: 'completed',
      duration_ms: 320,
      tokens_used: 1500,
      llm_call_ids: ['llm-1'],
      error: null,
    },
    {
      node_id: 'lookup',
      node_type: 'retriever',
      status: 'skipped',
      duration_ms: 12,
      tokens_used: 0,
      llm_call_ids: [],
      error: null,
    },
    {
      node_id: 'critic',
      node_type: 'critic',
      status: 'failed',
      duration_ms: 75,
      tokens_used: 42,
      llm_call_ids: ['llm-2'],
      error: 'critic unavailable',
    },
    {
      node_id: 'timeout',
      node_type: 'validator',
      status: 'timed_out',
      duration_ms: 44,
      tokens_used: 0,
      llm_call_ids: [],
      error: 'validation timed out',
    },
  ],
  total_duration_ms: 551,
  context_budget: {
    total_budget_tokens: 2000,
    tokens_used: 1350,
    included_count: 4,
    dropped_count: 7,
    dropped_items: droppedItems,
  },
  trace_id: 'trace-coverage-123',
  llm_call_count: 2,
  total_llm_tokens: 1542,
  fallback_reason: null,
}

const fallbackTrace: StrategyTraceResponse = {
  active: false,
  spec_selected: null,
  spec_version: null,
  routing_reason: '',
  adaptive_scores: null,
  fast_path_eligible: false,
  nodes_executed: [],
  total_duration_ms: 0,
  context_budget: null,
  trace_id: null,
  llm_call_count: 0,
  total_llm_tokens: 0,
  fallback_reason: 'Strategy OS disabled',
}

describe('StrategyTracePanel', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn(),
      configurable: true,
    })
  })

  it('renders a completed strategy trace with scores, nodes, budget, and copy action', async () => {
    const user = userEvent.setup()
    render(<StrategyTracePanel trace={completeTrace} />)

    await user.click(screen.getByRole('button', { name: /Strategy OS/ }))
    expect(screen.getByText('Fast-path eligible')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Adaptive scores/ })).toBeInTheDocument()
    expect(screen.getByText('0.970')).toBeInTheDocument()
    expect(screen.getByText('[object Object]')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: /Nodes/ })).toBeInTheDocument()
    expect(screen.getByText('compose')).toBeInTheDocument()
    expect(screen.getByText(/1,500/)).toBeInTheDocument()
    expect(screen.getByText('critic unavailable')).toBeInTheDocument()
    expect(screen.getByText('validation timed out')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: /Context budget/ })).toBeInTheDocument()
    expect(screen.getByText(/budget reason 1/)).toBeInTheDocument()
    expect(screen.getByText('+{{count}} more dropped items')).toBeInTheDocument()
    expect(screen.getByText('trace-coverage-123')).toBeInTheDocument()

    await user.click(screen.getByTitle('Copy'))
    expect(await screen.findByTitle('Copied!')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Adaptive scores/ }))
    expect(screen.queryByText('0.970')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Nodes/ }))
    expect(screen.queryByText('compose')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Context budget/ }))
    expect(screen.queryByText(/budget reason 1/)).not.toBeInTheDocument()
  })

  it('renders live streaming nodes and fallback metadata', async () => {
    const user = userEvent.setup()
    const liveNodes: StrategyNodeEvent[] = [
      { node_id: 'live-route', node_type: 'router', status: 'running', duration_ms: 16 },
      { node_id: 'live-error', node_type: 'worker', status: 'error', duration_ms: 41 },
      { node_id: 'live-empty', node_type: 'retriever', status: 'empty', duration_ms: 1 },
    ]

    render(<StrategyTracePanel trace={fallbackTrace} liveNodes={liveNodes} />)

    await user.click(screen.getByRole('button', { name: /Legacy Fallback/ }))
    expect(screen.getByText(/Strategy OS disabled/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Live nodes/ })).toBeInTheDocument()
    expect(screen.getByText('streaming')).toBeInTheDocument()
    expect(screen.getByText('live-route')).toBeInTheDocument()
    expect(screen.getByText('live-error')).toBeInTheDocument()
    expect(screen.getByText('live-empty')).toBeInTheDocument()
    expect(screen.getAllByText('0 tokens').length).toBeGreaterThan(0)

    const liveSection = screen.getByText('live-error').closest('.p-2') as HTMLElement
    expect(within(liveSection).getByText('error')).toBeInTheDocument()
  })

  it('renders the empty-node and zero-budget branches', async () => {
    const user = userEvent.setup()
    render(
      <StrategyTracePanel
        trace={{
          ...fallbackTrace,
          context_budget: {
            total_budget_tokens: 0,
            tokens_used: 0,
            included_count: 0,
            dropped_count: 0,
            dropped_items: [],
          },
        }}
      />
    )

    await user.click(screen.getByRole('button', { name: /Legacy Fallback/ }))
    expect(screen.getByText('No nodes executed yet')).toBeInTheDocument()
    expect(screen.getByText('Context budget')).toBeInTheDocument()
  })
})
