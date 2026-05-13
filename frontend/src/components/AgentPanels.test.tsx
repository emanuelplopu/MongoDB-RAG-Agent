import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import FederatedAgentPanel from './FederatedAgentPanel'
import SimplifiedAgentPanel from './SimplifiedAgentPanel'
import SimplifiedLiveTrace from './SimplifiedLiveTrace'
import SimplifiedThinkingPanel from './SimplifiedThinkingPanel'

const tMock = (key: string, options?: Record<string, unknown>) => {
  if (key.startsWith('userView.phases.')) {
    return key
  }
  if (options?.defaultValue) {
    return String(options.defaultValue)
  }
  if (options && 'count' in options) {
    return `${key}:${String(options.count)}`
  }
  return key
}

const copyIconButtonMock = vi.fn(({ text }: { text: string }) => (
  <button type="button">{`copy:${text.slice(0, 20)}`}</button>
))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}))

vi.mock('./CopyButton', () => ({
  CopyIconButton: (props: { text: string }) => copyIconButtonMock(props),
}))

const federatedTrace = {
  mode: 'federated',
  iterations: 2,
  models: {
    orchestrator: 'gpt-orchestrator',
    worker: 'gpt-worker',
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
      reasoning: 'Understand the legal question and identify the likely data sources to search first.',
      output: '',
      duration_ms: 100,
      tokens: 20,
    },
    {
      phase: 'plan',
      reasoning: 'Search the profile corpus and then validate it against web material.',
      output: '',
      duration_ms: 150,
      tokens: 25,
      tasks: [
        { id: 'task-1', type: 'search_profile', query: 'contract dispute' },
        { id: 'task-2', type: 'web_search', query: 'consumer law update' },
      ],
    },
  ],
  worker_steps: [
    {
      task_id: 'task-1',
      task_type: 'search_profile',
      tool: 'vector',
      input: { query: 'contract dispute' },
      duration_ms: 500,
      success: true,
      documents: [
        { title: 'Contract Guide', score: 0.91, excerpt: 'Relevant contract law excerpt.' },
        { title: 'Dispute Memo', score: 0.84, excerpt: 'Useful dispute memo excerpt.' },
        { title: 'Third Result', score: 0.81, excerpt: 'Third excerpt.' },
      ],
      web_links: [{ title: 'Court portal', url: 'https://example.com/court', excerpt: 'External court link.' }],
    },
    {
      task_id: 'task-2',
      task_type: 'custom_unknown',
      tool: 'browser',
      input: { query: 'consumer law update' },
      duration_ms: 200,
      success: false,
      documents: [],
      web_links: [],
    },
  ],
  sources: {
    documents: [
      { title: 'Doc 1', excerpt: 'Excerpt 1', score: 0.92, source_type: 'profile', source_database: 'profile-db' },
      { title: 'Doc 2', excerpt: 'Excerpt 2', score: 0.88, source_type: 'profile', source_database: 'profile-db' },
      { title: 'Doc 3', excerpt: 'Excerpt 3', score: 0.74, source_type: 'cloud', source_database: 'cloud-db' },
      { title: 'Doc 4', excerpt: 'Excerpt 4', score: 0.71, source_type: 'cloud', source_database: 'cloud-db' },
      { title: 'Doc 5', excerpt: 'Excerpt 5', score: 0.63, source_type: 'web', source_database: 'web-db' },
      { title: 'Doc 6', excerpt: 'Excerpt 6', score: 0.59, source_type: 'web', source_database: 'web-db' },
    ],
    web_links: [
      { title: 'Web 1', url: 'https://example.com/1', excerpt: 'Web excerpt 1' },
      { title: 'Web 2', url: 'https://example.com/2', excerpt: 'Web excerpt 2' },
      { title: 'Web 3', url: 'https://example.com/3', excerpt: 'Web excerpt 3' },
      { title: 'Web 4', url: 'https://example.com/4', excerpt: 'Web excerpt 4' },
    ],
  },
}

describe('FederatedAgentPanel', () => {
  it('renders the expanded trace with orchestrator, worker, and source details', () => {
    render(<FederatedAgentPanel trace={federatedTrace as any} />)

    fireEvent.click(screen.getByRole('button', { name: /agentPanel.header/i }))
    fireEvent.click(screen.getByRole('button', { name: /agentPanel.orchestratorSteps/i }))

    expect(screen.getByText('gpt-orchestrator')).toBeInTheDocument()
    expect(screen.getByText('gpt-worker')).toBeInTheDocument()
    expect(screen.getByText(/Understand the legal question/)).toBeInTheDocument()
    expect(screen.getByText('Contract Guide')).toBeInTheDocument()
    expect(screen.getByText('Web 1')).toBeInTheDocument()
    expect(screen.getByText('Doc 1')).toBeInTheDocument()
    expect(screen.getAllByText('agentPanel.moreDocuments:1').length).toBeGreaterThan(0)
    expect(screen.getByText('agentPanel.moreLinks:1')).toBeInTheDocument()
    expect(screen.getByText('agentPanel.copyTrace')).toBeInTheDocument()
    expect(copyIconButtonMock).toHaveBeenCalled()
  })
})

describe('SimplifiedAgentPanel', () => {
  it('filters hidden searches, shows relevance labels, and expands the document list', () => {
    const trace = {
      ...federatedTrace,
      worker_steps: [
        federatedTrace.worker_steps[0],
        {
          task_id: 'task-3',
          task_type: 'summarize',
          tool: 'internal',
          duration_ms: 80,
          success: true,
          documents: [],
          web_links: [],
        },
        {
          task_id: 'task-4',
          task_type: 'search_cloud',
          tool: 'cloud',
          duration_ms: 90,
          success: true,
          documents: [],
          web_links: [],
        },
      ],
      sources: {
        documents: Array.from({ length: 12 }, (_, index) => ({
          title: `Visible Doc ${index + 1}`,
          excerpt: `Excerpt ${index + 1}`,
          score: index === 0 ? 0.91 : index === 1 ? 0.72 : 0.42,
        })),
        web_links: federatedTrace.sources.web_links,
      },
    }

    render(<SimplifiedAgentPanel trace={trace as any} />)

    fireEvent.click(screen.getByRole('button', { name: /userView.researchSummary/i }))

    expect(screen.getByText('search_profile')).toBeInTheDocument()
    expect(screen.queryByText('summarize')).not.toBeInTheDocument()
    expect(screen.getByText('userView.relevanceHigh')).toBeInTheDocument()
    expect(screen.getByText('userView.relevanceGood')).toBeInTheDocument()
    expect(screen.getAllByText('userView.relevancePartial').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /\+7 userView.showMore:7/i }))
    expect(screen.getByText('Visible Doc 12')).toBeInTheDocument()
    expect(screen.getByText('agentPanel.moreLinks:1')).toBeInTheDocument()
  })
})

describe('SimplifiedLiveTrace', () => {
  it('shows user-friendly live progress, pending tasks, and the final synthesis summary', () => {
    const stopMock = vi.fn()
    const liveTrace = {
      orchestrator_steps: [
        {
          phase: 'analyze',
          reasoning:
            'This is a very long analysis sentence that should be truncated because it is much longer than the presentation limit for the live trace panel.',
          output: '',
          duration_ms: 0,
          tokens: 0,
        },
        {
          phase: 'plan',
          reasoning: '',
          output: '',
          duration_ms: 0,
          tokens: 0,
          tasks: [
            { id: 'task-1', type: 'search_profile', query: 'contract dispute' },
            { id: 'task-2', type: 'web_search', query: 'consumer law update' },
            { id: 'task-3', type: 'summarize', query: 'hidden task' },
          ],
        },
      ],
      worker_steps: [
        {
          task_id: 'task-1',
          task_type: 'search_profile',
          tool: 'vector',
          duration_ms: 0,
          success: true,
          documents: [{ title: 'Contract Guide', score: 0.9, excerpt: 'Excerpt' }],
        },
      ],
      stats: {
        total_tokens: 0,
        orchestrator_tokens: 0,
        worker_tokens: 0,
        cost_usd: 0,
      },
      startTime: 0,
      currentPhase: 'executing',
    }

    const { rerender } = render(
      <SimplifiedLiveTrace liveTrace={liveTrace} elapsedTime={4} onStop={stopMock} />
    )

    expect(screen.getByText('userView.phases.executing')).toBeInTheDocument()
    expect(screen.getByText(/This is a very long analysis sentence/)).toBeInTheDocument()
    expect(screen.getByText('search_profile, web_search')).toBeInTheDocument()
    expect(screen.getByText(/userView\.liveTrace\.searchInProgress/)).toBeInTheDocument()

    rerender(
      <SimplifiedLiveTrace
        liveTrace={{ ...liveTrace, currentPhase: 'synthesize' }}
        elapsedTime={6}
        onStop={stopMock}
      />
    )

    expect(screen.getByText('userView.liveTrace.totalFound:1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /chatPage.stop/i }))
    expect(stopMock).toHaveBeenCalledTimes(1)
  })
})

describe('SimplifiedThinkingPanel', () => {
  it('renders nothing when there are no searches or tool calls', () => {
    const { container } = render(
      <SimplifiedThinkingPanel thinking={{ search: { operations: [], total_results: 0 }, tool_calls: [] } as any} />
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('expands simplified search and tool details', () => {
    render(
      <SimplifiedThinkingPanel
        thinking={
          {
            search: {
              total_results: 3,
              operations: [
                {
                  query:
                    'This is a very long query that should be truncated after eighty characters to keep the UI concise for users.',
                  results_count: 3,
                },
                {
                  query: 'no result query',
                  results_count: 0,
                },
              ],
            },
            tool_calls: [
              { tool_name: 'web_search', success: true },
              { tool_name: 'unknown_tool', success: false },
            ],
          } as any
        }
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /userView.searchDetails/i }))

    expect(screen.getAllByText('userView.searched:').length).toBe(2)
    expect(screen.getByText(/This is a very long query/)).toBeInTheDocument()
    expect(screen.getByText('userView.noResults')).toBeInTheDocument()
    expect(screen.getAllByText('userView.toolType.default').length).toBe(2)
    expect(screen.getByText(/userView\.toolSuccess/)).toBeInTheDocument()
    expect(screen.getByText(/userView\.toolFailed/)).toBeInTheDocument()
  })
})
