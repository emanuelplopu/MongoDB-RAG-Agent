import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import LandingPage from './LandingPage'

const tMock = (key: string, options?: Record<string, unknown>) =>
  options?.name ? `${key}:${options.name}` : key

let authState: {
  isAuthenticated: boolean
  user: { name: string } | null
}

let tenantState: {
  tenant: {
    features: {
      landingPageVariant: 'default' | 'professional' | 'minimal' | 'quellex'
    }
  }
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../contexts/TenantContext', () => ({
  useTenant: () => tenantState,
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

vi.mock('../components/TenantLogo', () => ({
  default: ({ size = 'default' }: { size?: string }) => (
    <div data-testid={`tenant-logo-${size}`}>tenant-logo-{size}</div>
  ),
}))

vi.mock('../components/ThemeSwitcher', () => ({
  default: () => <div>theme-switcher</div>,
}))

vi.mock('../components/LanguageSwitcher', () => ({
  default: () => <div>language-switcher</div>,
}))

vi.mock('./QuellexLandingPage', () => ({
  default: () => <div>quellex-landing-page</div>,
}))

describe('LandingPage', () => {
  beforeEach(() => {
    authState = {
      isAuthenticated: false,
      user: null,
    }
    tenantState = {
      tenant: {
        features: {
          landingPageVariant: 'default',
        },
      },
    }
  })

  it('renders the professional/default landing variant for guests', () => {
    tenantState.tenant.features.landingPageVariant = 'professional'

    render(<LandingPage />)

    expect(screen.getByText('landing.welcomeTo')).toBeInTheDocument()
    expect(screen.getAllByText('common.appName').length).toBeGreaterThan(0)
    expect(screen.getByText('landing.featuresTitle')).toBeInTheDocument()
    expect(screen.getByText('landing.benefitsTitle')).toBeInTheDocument()
    expect(screen.getByText('landing.ctaTitle')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'landing.getStarted' })).toHaveAttribute('href', '/login')
    expect(screen.getByRole('link', { name: 'nav.signIn' })).toHaveAttribute('href', '/login')
    expect(screen.getByRole('link', { name: 'landing.getStartedFree' })).toHaveAttribute('href', '/login')
  })

  it('renders the minimal landing variant for authenticated users', () => {
    tenantState.tenant.features.landingPageVariant = 'minimal'
    authState = {
      isAuthenticated: true,
      user: { name: 'Alex Roe' },
    }

    render(<LandingPage />)

    expect(screen.getByText('landing.subtitle')).toBeInTheDocument()
    expect(screen.queryByText('landing.featuresTitle')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'landing.goToDashboard' })).toHaveAttribute('href', '/dashboard')
  })

  it('delegates to the Quellex landing page when that tenant variant is active', () => {
    tenantState.tenant.features.landingPageVariant = 'quellex'

    render(<LandingPage />)

    expect(screen.getByText('quellex-landing-page')).toBeInTheDocument()
  })
})
