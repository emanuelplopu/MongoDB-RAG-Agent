import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import QuellexLandingPage from './QuellexLandingPage'

const tMock = (key: string) => key

let authState: {
  isAuthenticated: boolean
}

type ObserverRecord = {
  callback: IntersectionObserverCallback
  observe: ReturnType<typeof vi.fn>
  disconnect: ReturnType<typeof vi.fn>
}

const observerRecords: ObserverRecord[] = []

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../components/LocalizedLink', () => ({
  LocalizedLink: ({
    to,
    children,
    ...props
  }: {
    to: string
    children: React.ReactNode
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('../components/ThemeSwitcher', () => ({
  default: () => <div>theme-switcher</div>,
}))

vi.mock('../components/LanguageSwitcher', () => ({
  default: () => <div>language-switcher</div>,
}))

describe('QuellexLandingPage', () => {
  beforeEach(() => {
    authState = {
      isAuthenticated: false,
    }
    observerRecords.length = 0
    window.scrollTo = vi.fn()

    class MockIntersectionObserver {
      callback: IntersectionObserverCallback
      observe = vi.fn()
      disconnect = vi.fn()
      unobserve = vi.fn()

      constructor(callback: IntersectionObserverCallback) {
        this.callback = callback
        observerRecords.push({
          callback,
          observe: this.observe,
          disconnect: this.disconnect,
        })
      }
    }

    ;(globalThis as typeof globalThis & { IntersectionObserver: typeof MockIntersectionObserver }).IntersectionObserver =
      MockIntersectionObserver
  })

  it('renders the marketing sections and routes guests to the login CTA', () => {
    render(<QuellexLandingPage />)

    expect(screen.getByText('quellexLanding.heroSubtitle')).toBeInTheDocument()
    expect(screen.getByText('quellexLanding.whyQuellex')).toBeInTheDocument()
    expect(screen.getByText('quellexLanding.teamHeading')).toBeInTheDocument()
    expect(screen.getByText('quellexLanding.ctaHeading')).toBeInTheDocument()
    expect(
      screen
        .getAllByRole('link', { name: 'quellexLanding.bookDemo' })
        .every((link) => link.getAttribute('href') === '/login')
    ).toBe(true)
  })

  it('updates observed state and supports mobile section navigation', () => {
    const { container } = render(<QuellexLandingPage />)

    const revealElement = container.querySelector('.quellex-reveal')
    expect(revealElement).not.toBeNull()

    observerRecords[0]?.callback(
      [{ isIntersecting: true, target: revealElement! } as IntersectionObserverEntry],
      {} as IntersectionObserver
    )
    expect(revealElement).toHaveClass('revealed')

    const teamSection = document.getElementById('team')
    expect(teamSection).not.toBeNull()
    act(() => {
      observerRecords[1]?.callback(
        [{ isIntersecting: true, target: teamSection! } as IntersectionObserverEntry],
        {} as IntersectionObserver
      )
    })

    const desktopTeamButton = screen.getAllByRole('button', { name: 'quellexLanding.navTeam' })[0]
    expect(desktopTeamButton.className).toContain('text-quellex-gold')

    const allButtons = screen.getAllByRole('button')
    const mobileMenuButton = allButtons.find((button) => button.className.includes('lg:hidden'))
    expect(mobileMenuButton).toBeDefined()

    fireEvent.click(mobileMenuButton!)
    expect(screen.getByText('quellexLanding.navOverview')).toBeInTheDocument()

    const featuresSection = document.getElementById('features')
    expect(featuresSection).not.toBeNull()
    Object.defineProperty(featuresSection!, 'offsetTop', {
      configurable: true,
      value: 420,
    })

    fireEvent.click(screen.getAllByRole('button', { name: 'quellexLanding.navFeatures' })[1])
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 348, behavior: 'smooth' })
  })

  it('routes authenticated users to the dashboard hero CTA', () => {
    authState = {
      isAuthenticated: true,
    }

    render(<QuellexLandingPage />)

    const ctaLinks = screen.getAllByRole('link', { name: 'quellexLanding.bookDemo' })
    expect(ctaLinks.some((link) => link.getAttribute('href') === '/dashboard')).toBe(true)
  })
})
