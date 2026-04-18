import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    ClockIcon: makeIcon('ClockIcon'),
    MagnifyingGlassIcon: makeIcon('MagnifyingGlassIcon'),
    XMarkIcon: makeIcon('XMarkIcon'),
    ArrowTrendingUpIcon: makeIcon('ArrowTrendingUpIcon'),
    LightBulbIcon: makeIcon('LightBulbIcon'),
  }
})

vi.mock('../hooks/useLocalStorage', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return {
    STORAGE_KEYS: { RECENT_SEARCHES: 'recent_searches' },
    useLocalStorage: <T,>(_key: string, initialValue: T) => React.useState<T>(initialValue),
  }
})

import SearchSuggestions, { useSearchSuggestions } from './SearchSuggestions'

describe('SearchSuggestions', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn()
  })

  it('renders trending and generated suggestions and handles selection', () => {
    const onSelect = vi.fn()
    const onQueryChange = vi.fn()
    const onClose = vi.fn()
    const onOpen = vi.fn()

    render(
      <SearchSuggestions
        query="atlas"
        onSelect={onSelect}
        onQueryChange={onQueryChange}
        isOpen
        onClose={onClose}
        onOpen={onOpen}
        popularSuggestions={['atlas search', 'vector search']}
      />
    )

    expect(screen.getByPlaceholderText('Search...')).toBeInTheDocument()
    expect(screen.getByText('Trending')).toBeInTheDocument()
    expect(screen.getByText('Suggestions')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /atlas documents/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /atlas search/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /atlas documents/i }))
    expect(onSelect).toHaveBeenCalledWith('atlas documents')
    expect(onClose).toHaveBeenCalled()
  })

  it('supports typing, clearing, removal, and keyboard navigation', () => {
    const onSelect = vi.fn()
    const onQueryChange = vi.fn()
    const onClose = vi.fn()
    const onOpen = vi.fn()

    const { rerender } = render(
      <SearchSuggestions
        query=""
        onSelect={onSelect}
        onQueryChange={onQueryChange}
        isOpen={false}
        onClose={onClose}
        onOpen={onOpen}
        popularSuggestions={['atlas search']}
      />
    )

    const input = screen.getByPlaceholderText('Search...')
    fireEvent.focus(input)
    expect(onOpen).toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'at' } })
    expect(onQueryChange).toHaveBeenCalledWith('at')

    rerender(
      <SearchSuggestions
        query="at"
        onSelect={onSelect}
        onQueryChange={onQueryChange}
        isOpen
        onClose={onClose}
        onOpen={onOpen}
        popularSuggestions={['atlas search']}
      />
    )

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalled()

    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()

    fireEvent.click(
      screen.getAllByRole('button').find((button) => button.querySelector('[data-testid="XMarkIcon"]'))!
    )
  })

  it('opens on arrow down when closed and clears the query', () => {
    const onSelect = vi.fn()
    const onQueryChange = vi.fn()
    const onClose = vi.fn()
    const onOpen = vi.fn()

    render(
      <SearchSuggestions
        query="atlas"
        onSelect={onSelect}
        onQueryChange={onQueryChange}
        isOpen={false}
        onClose={onClose}
        onOpen={onOpen}
      />
    )

    const input = screen.getByPlaceholderText('Search...')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(onOpen).toHaveBeenCalled()

    fireEvent.click(
      screen.getAllByRole('button').find((button) => button.querySelector('[data-testid="XMarkIcon"]'))!
    )
    expect(onQueryChange).toHaveBeenCalledWith('')
  })
})

describe('useSearchSuggestions', () => {
  it('manages open/close/toggle and query state', () => {
    const { result } = renderHook(() => useSearchSuggestions())

    expect(result.current.isOpen).toBe(false)
    expect(result.current.query).toBe('')

    act(() => {
      result.current.open()
    })
    expect(result.current.isOpen).toBe(true)

    act(() => {
      result.current.setQuery('atlas')
    })
    expect(result.current.query).toBe('atlas')

    act(() => {
      result.current.toggle()
    })
    expect(result.current.isOpen).toBe(false)

    act(() => {
      result.current.close()
    })
    expect(result.current.isOpen).toBe(false)
  })
})
