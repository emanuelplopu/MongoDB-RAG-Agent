import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const tenantState = {
  tenant: {
    branding: {
      appName: 'RecallHub',
      logoUrl: '/tenants/recallhub/logo.svg',
    },
  },
}

vi.mock('../contexts/TenantContext', () => ({
  useTenant: () => tenantState,
}))

import TenantLogo from './TenantLogo'

describe('TenantLogo', () => {
  beforeEach(() => {
    tenantState.tenant = {
      branding: {
        appName: 'RecallHub',
        logoUrl: '/tenants/recallhub/logo.svg',
      },
    }
  })

  it('renders SVG logos inside the branded container', () => {
    const { container } = render(<TenantLogo size="sm" className="brand-shell" />)

    const image = screen.getByAltText('RecallHub')
    expect(image).toHaveAttribute('src', '/tenants/recallhub/logo.svg')
    expect(image.className).toContain('h-5')
    expect(container.firstChild).toHaveClass('bg-gradient-brand')
    expect(container.firstChild).toHaveClass('brand-shell')
  })

  it('renders PNG logos directly and falls back when loading fails', () => {
    tenantState.tenant = {
      branding: {
        appName: 'Quellex',
        logoUrl: '/tenants/quellex/logo.png',
      },
    }

    const { rerender } = render(<TenantLogo size="lg" />)

    const image = screen.getByAltText('Quellex')
    expect(image).toHaveAttribute('src', '/tenants/quellex/logo.png')
    expect(image.className).toContain('h-20')

    fireEvent.error(image)
    rerender(<TenantLogo size="lg" />)

    expect(screen.queryByAltText('Quellex')).not.toBeInTheDocument()
    expect(screen.getByText('Q')).toBeInTheDocument()
  })

  it('uses the text fallback when no logo URL exists', () => {
    tenantState.tenant = {
      branding: {
        appName: 'Atlas',
      },
    }

    render(<TenantLogo size="md" className="fallback-shell" />)

    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('A').parentElement).toHaveClass('fallback-shell')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
