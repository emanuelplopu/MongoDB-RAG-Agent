import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { LocalizedLink, useLocalizedNavigate, useLocalizedPath } from './LocalizedLink'

const navigateMock = vi.fn()

vi.mock('../contexts/LanguageContext', () => ({
  useLanguage: () => ({ language: 'de' }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

function PathHarness({ path }: { path: string }) {
  const getLocalizedPath = useLocalizedPath()
  return <span>{getLocalizedPath(path)}</span>
}

function NavigateHarness() {
  const localizedNavigate = useLocalizedNavigate()
  return (
    <button onClick={() => localizedNavigate('/settings', { replace: true })}>
      navigate
    </button>
  )
}

describe('LocalizedLink helpers', () => {
  beforeEach(() => {
    navigateMock.mockReset()
  })

  it('prefixes plain paths with the current language', () => {
    render(
      <MemoryRouter>
        <PathHarness path="/search" />
      </MemoryRouter>
    )

    expect(screen.getByText('/de/search')).toBeInTheDocument()
  })

  it('replaces an existing language prefix', () => {
    render(
      <MemoryRouter>
        <PathHarness path="/en/search" />
      </MemoryRouter>
    )

    expect(screen.getByText('/de/search')).toBeInTheDocument()
  })

  it('renders links with localized destinations', () => {
    render(
      <MemoryRouter>
        <LocalizedLink to="/settings">settings</LocalizedLink>
      </MemoryRouter>
    )

    expect(screen.getByRole('link', { name: 'settings' })).toHaveAttribute('href', '/de/settings')
  })

  it('navigates with a localized path', () => {
    render(
      <MemoryRouter>
        <NavigateHarness />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('button', { name: 'navigate' }))

    expect(navigateMock).toHaveBeenCalledWith('/de/settings', { replace: true })
  })
})
