import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { listModelsMock } = vi.hoisted(() => ({
  listModelsMock: vi.fn(),
}))

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    MagnifyingGlassIcon: makeIcon('MagnifyingGlassIcon'),
    ArrowsRightLeftIcon: makeIcon('ArrowsRightLeftIcon'),
    InformationCircleIcon: makeIcon('InformationCircleIcon'),
    CurrencyDollarIcon: makeIcon('CurrencyDollarIcon'),
    ClockIcon: makeIcon('ClockIcon'),
    ExclamationTriangleIcon: makeIcon('ExclamationTriangleIcon'),
    CheckCircleIcon: makeIcon('CheckCircleIcon'),
    XMarkIcon: makeIcon('XMarkIcon'),
  }
})

vi.mock('../api/modelVersions', () => ({
  modelVersionsApi: {
    listModels: listModelsMock,
  },
}))

import ModelVersionSelector from './ModelVersionSelector'

const models = [
  {
    id: 'gpt-4.1',
    name: 'GPT 4.1',
    provider: 'openai',
    type: 'chat',
    version: '2025.01',
    release_date: '2025-01-01',
    context_window: 128000,
    max_output_tokens: 4096,
    capabilities: ['reasoning', 'function_calling'],
    pricing_input: 0.01,
    pricing_output: 0.02,
    is_deprecated: false,
    is_experimental: false,
    parameter_mapping: {},
    default_parameters: {},
  },
  {
    id: 'text-embedding-3-large',
    name: 'Embedding Large',
    provider: 'openai',
    type: 'embedding',
    version: '2024.11',
    release_date: '2024-11-01',
    context_window: 8192,
    max_output_tokens: 0,
    capabilities: ['multimodal'],
    pricing_input: 0.005,
    pricing_output: 0,
    is_deprecated: false,
    is_experimental: true,
    parameter_mapping: {},
    default_parameters: {},
  },
  {
    id: 'claude-2',
    name: 'Claude 2',
    provider: 'anthropic',
    type: 'chat',
    version: '2024.01',
    release_date: '2024-01-01',
    context_window: 100000,
    max_output_tokens: 4096,
    capabilities: ['code_generation'],
    pricing_input: 0.03,
    pricing_output: 0.04,
    is_deprecated: true,
    is_experimental: false,
    parameter_mapping: {},
    default_parameters: {},
  },
]

describe('ModelVersionSelector', () => {
  beforeEach(() => {
    listModelsMock.mockReset()
  })

  it('loads models, filters them, and switches models successfully', async () => {
    const onModelChange = vi.fn()
    listModelsMock.mockResolvedValue({
      models,
      total: models.length,
      provider_filter: null,
      capability_filter: null,
      type_filter: null,
    })

    render(
      <ModelVersionSelector
        currentOrchestrator="gpt-4.1"
        currentEmbedding="text-embedding-3-large"
        onModelChange={onModelChange}
      />
    )

    expect(screen.getByText('Loading models...')).toBeInTheDocument()
    expect(await screen.findByText('GPT 4.1')).toBeInTheDocument()
    expect(screen.queryByText('Claude 2')).not.toBeInTheDocument()
    expect(listModelsMock).toHaveBeenCalledWith({
      show_deprecated: false,
      sort_by: 'release_date',
      limit: 100,
    })

    fireEvent.change(screen.getByPlaceholderText('Search models...'), {
      target: { value: 'embedding' },
    })
    expect(screen.getByText('Embedding Large')).toBeInTheDocument()
    expect(screen.queryByText('GPT 4.1')).not.toBeInTheDocument()

    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'openai' } })
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'embedding' } })
    fireEvent.change(screen.getAllByRole('combobox')[2], { target: { value: 'name' } })
    expect(screen.getByText('Showing 1 of 3 models')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Search models...'), {
      target: { value: '' },
    })
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'all' } })
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'all' } })
    fireEvent.click(screen.getByLabelText('Show Deprecated'))
    expect(screen.getByText('Claude 2')).toBeInTheDocument()

    const claudeCard = screen.getByText('Claude 2').closest('.p-4') as HTMLElement
    fireEvent.click(within(claudeCard).getByRole('button', { name: 'Set Worker' }))
    expect(onModelChange).toHaveBeenCalledWith('worker', 'claude-2')
    expect(screen.getByText('Successfully switched worker to claude-2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Current Orchestrator' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Current Embedding' })).toBeDisabled()
  })

  it('shows empty state and switch errors from the callback', async () => {
    listModelsMock.mockResolvedValue({
      models: [models[0]],
      total: 1,
      provider_filter: null,
      capability_filter: null,
      type_filter: null,
    })
    const onModelChange = vi.fn(() => {
      throw new Error('switch failed')
    })

    render(<ModelVersionSelector onModelChange={onModelChange} />)

    await screen.findByText('GPT 4.1')

    fireEvent.change(screen.getByPlaceholderText('Search models...'), {
      target: { value: 'missing' },
    })
    expect(screen.getByText('No models found matching your criteria')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Search models...'), {
      target: { value: '' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Set Orchestrator' }))
    expect(screen.getByText('switch failed')).toBeInTheDocument()
  })

  it('renders network errors and retries loading', async () => {
    listModelsMock
      .mockRejectedValueOnce({ message: 'Network Error', code: 'ERR_NETWORK' })
      .mockResolvedValueOnce({
        models: [models[1]],
        total: 1,
        provider_filter: null,
        capability_filter: null,
        type_filter: null,
      })

    render(<ModelVersionSelector />)

    expect(
      await screen.findByText(
        'Error: Cannot connect to the backend server. Please ensure the backend is running and try again.'
      )
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Embedding Large')).toBeInTheDocument()
    expect(listModelsMock).toHaveBeenCalledTimes(2)
  })

  it('renders backend error messages when available', async () => {
    listModelsMock.mockRejectedValueOnce({
      response: {
        data: {
          detail: 'Backend rejected request',
        },
      },
    })

    render(<ModelVersionSelector />)

    await waitFor(() => {
      expect(screen.getByText('Error: Backend rejected request')).toBeInTheDocument()
    })
  })
})
