import type { ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => `translated:${key}`,
  }),
}))

vi.mock('../components/LocalizedLink', () => ({
  LocalizedLink: ({
    children,
    to,
    className,
  }: {
    children: ReactNode
    to: string
    className?: string
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}))

vi.mock('../components/ThemeSwitcher', () => ({
  default: () => <div>Theme Switcher</div>,
}))

vi.mock('../components/LanguageSwitcher', () => ({
  default: () => <div>Language Switcher</div>,
}))

import NotFoundPage from './NotFoundPage'

describe('NotFoundPage', () => {
  it('renders the translated 404 page shell and home link', () => {
    render(<NotFoundPage />)

    expect(screen.getByText('404')).toBeInTheDocument()
    expect(screen.getByText('Theme Switcher')).toBeInTheDocument()
    expect(screen.getByText('Language Switcher')).toBeInTheDocument()
    expect(screen.getByText('translated:errors.notFound.title')).toBeInTheDocument()
    expect(screen.getByText('translated:errors.notFound.message')).toBeInTheDocument()

    const homeLink = screen.getByRole('link', { name: /translated:errors.notFound.goHome/i })
    expect(homeLink).toHaveAttribute('href', '/')
    expect(homeLink.className).toContain('bg-primary')
  })
})
