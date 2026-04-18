import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import SearchFilters, {
  ActiveFilters,
  exampleFilterGroups,
  useSearchFilters,
  type FilterGroup,
} from './SearchFilters'

const groups: FilterGroup[] = [
  {
    id: 'type',
    label: 'Type',
    type: 'checkbox',
    options: [
      { id: 'pdf', label: 'PDF', count: 3 },
      { id: 'doc', label: 'Doc', count: 2 },
    ],
  },
  {
    id: 'sort',
    label: 'Sort',
    type: 'radio',
    options: [
      { id: 'newest', label: 'Newest' },
      { id: 'oldest', label: 'Oldest' },
    ],
  },
  {
    id: 'range',
    label: 'Date range',
    type: 'date-range',
  },
]

describe('SearchFilters', () => {
  it('renders groups, tracks active filters, and updates checkbox/radio/date values', () => {
    const onChange = vi.fn()
    const onClear = vi.fn()

    render(
      <SearchFilters
        groups={groups}
        values={{
          type: ['pdf'],
          sort: ['newest'],
          range: { from: '2024-01-01' },
        }}
        onChange={onChange}
        onClear={onClear}
      />
    )

    expect(screen.getByText('Filters')).toBeInTheDocument()
    expect(screen.getByText('3 active')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked()
    expect(screen.getAllByRole('radio')[0]).toBeChecked()

    fireEvent.click(screen.getAllByRole('checkbox')[1])
    expect(onChange).toHaveBeenCalledWith({
      type: ['pdf', 'doc'],
      sort: ['newest'],
      range: { from: '2024-01-01' },
    })

    fireEvent.click(screen.getAllByRole('radio')[1])
    expect(onChange).toHaveBeenCalledWith({
      type: ['pdf'],
      sort: ['oldest'],
      range: { from: '2024-01-01' },
    })

    fireEvent.change(screen.getAllByDisplayValue('')[0], { target: { value: '2024-02-01' } })
    expect(onChange).toHaveBeenCalledWith({
      type: ['pdf'],
      sort: ['newest'],
      range: { from: '2024-01-01', to: '2024-02-01' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(onClear).toHaveBeenCalled()
  })

  it('supports compact open/close and collapsing individual groups', () => {
    const onChange = vi.fn()

    render(
      <SearchFilters
        groups={groups}
        values={{ type: ['pdf', 'doc'] }}
        onChange={onChange}
        onClear={vi.fn()}
        compact
      />
    )

    expect(screen.getByRole('button', { name: /filters/i })).toHaveTextContent('2')
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    expect(screen.getByText('Type')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /type/i }))
    expect(screen.queryByLabelText('PDF')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /sort/i }))
    expect(screen.queryByLabelText('Newest')).not.toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button').find((button) => button.querySelector('svg'))!)
    expect(screen.getByRole('button', { name: /filters/i })).toBeInTheDocument()
  })
})

describe('ActiveFilters', () => {
  it('renders chips for active values and supports removing and clearing', () => {
    const onRemove = vi.fn()
    const onClear = vi.fn()

    render(
      <ActiveFilters
        groups={exampleFilterGroups}
        values={{ file_type: ['pdf', 'docx'], source: ['upload'] }}
        onRemove={onRemove}
        onClear={onClear}
      />
    )

    expect(screen.getByText('Active filters:')).toBeInTheDocument()
    expect(screen.getByText('PDF')).toBeInTheDocument()
    expect(screen.getByText('Word Document')).toBeInTheDocument()
    expect(screen.getByText('Uploaded')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button')[0])
    expect(onRemove).toHaveBeenCalledWith('file_type', 'pdf')

    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(onClear).toHaveBeenCalled()
  })

  it('returns null when there are no active filter chips', () => {
    const { container } = render(
      <ActiveFilters groups={exampleFilterGroups} values={{ date_range: { from: '2024-01-01' } }} onRemove={vi.fn()} onClear={vi.fn()} />
    )

    expect(container).toBeEmptyDOMElement()
  })
})

describe('useSearchFilters', () => {
  it('updates, removes, and clears filter state', () => {
    const { result } = renderHook(() => useSearchFilters({ source: ['upload', 'dropbox'] }))

    expect(result.current.values).toEqual({ source: ['upload', 'dropbox'] })

    act(() => {
      result.current.setValues({ type: ['pdf'] })
    })
    expect(result.current.values).toEqual({ type: ['pdf'] })

    act(() => {
      result.current.remove('source', 'upload')
    })
    expect(result.current.values).toEqual({ type: ['pdf'], source: [] })

    act(() => {
      result.current.clear()
    })
    expect(result.current.values).toEqual({})
  })
})
