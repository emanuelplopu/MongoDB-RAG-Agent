import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { navigateMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'nav.dashboard': 'Dashboard',
        'nav.chat': 'Chat',
        'nav.search': 'Search',
        'nav.documents': 'Documents',
        'nav.cloudSources': 'Cloud Sources',
        'nav.status': 'Status',
        'nav.prompts': 'Prompts',
        'nav.profiles': 'Profiles',
        'nav.configuration': 'Configuration',
      })[key] ?? key,
  }),
}))

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    MagnifyingGlassIcon: makeIcon('MagnifyingGlassIcon'),
    ChatBubbleLeftRightIcon: makeIcon('ChatBubbleLeftRightIcon'),
    DocumentTextIcon: makeIcon('DocumentTextIcon'),
    Cog6ToothIcon: makeIcon('Cog6ToothIcon'),
    ArrowRightIcon: makeIcon('ArrowRightIcon'),
    ClockIcon: makeIcon('ClockIcon'),
    HomeIcon: makeIcon('HomeIcon'),
    CloudIcon: makeIcon('CloudIcon'),
    CommandLineIcon: makeIcon('CommandLineIcon'),
    UserCircleIcon: makeIcon('UserCircleIcon'),
    ChartBarIcon: makeIcon('ChartBarIcon'),
  }
})

vi.mock('../hooks/useKeyboardShortcuts', () => ({
  useEscapeKey: vi.fn(),
  useFocusTrap: vi.fn(),
}))

vi.mock('../hooks/useLocalStorage', () => ({
  useLocalStorage: () => [['/recent-chat', '/recent-search']],
}))

import CommandPalette, { useCommandPalette } from './CommandPalette'

describe('CommandPalette', () => {
  beforeEach(() => {
    navigateMock.mockReset()
    Element.prototype.scrollIntoView = vi.fn()
  })

  it('renders recent and navigation items, filters by query, and navigates on selection', () => {
    const onClose = vi.fn()

    render(<CommandPalette isOpen onClose={onClose} />)

    expect(screen.getByText('Recent')).toBeInTheDocument()
    expect(screen.getByText('Navigation')).toBeInTheDocument()
    expect(screen.getByText('Recently visited: /recent-chat')).toBeInTheDocument()
    expect(screen.getByText('Dashboard')).toBeInTheDocument()

    const input = screen.getByPlaceholderText('Search pages, actions...')
    fireEvent.change(input, { target: { value: 'cloud' } })
    expect(screen.getByText('Cloud Sources')).toBeInTheDocument()
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument()

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(navigateMock).toHaveBeenCalledWith('/cloud-sources')
    expect(onClose).toHaveBeenCalled()
  })

  it('supports keyboard navigation, empty state, and backdrop close', () => {
    const onClose = vi.fn()

    const { container } = render(<CommandPalette isOpen onClose={onClose} />)
    const input = screen.getByPlaceholderText('Search pages, actions...')

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(navigateMock).toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'missing' } })
    expect(screen.getByText('No results found')).toBeInTheDocument()

    const backdrop = container.querySelector('.bg-black\\/50') as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalled()
  })

  it('returns null when closed', () => {
    const { container } = render(<CommandPalette isOpen={false} onClose={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('useCommandPalette', () => {
  it('opens, closes, toggles, and responds to Ctrl+K', () => {
    const { result } = renderHook(() => useCommandPalette())

    expect(result.current.isOpen).toBe(false)

    act(() => {
      result.current.open()
    })
    expect(result.current.isOpen).toBe(true)

    act(() => {
      result.current.close()
    })
    expect(result.current.isOpen).toBe(false)

    act(() => {
      result.current.toggle()
    })
    expect(result.current.isOpen).toBe(true)

    act(() => {
      fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    })
    expect(result.current.isOpen).toBe(false)
  })
})
