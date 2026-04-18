import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const themeState = {
  theme: 'system' as 'light' | 'dark' | 'system',
  resolvedTheme: 'dark' as 'light' | 'dark',
  setTheme: vi.fn(),
}

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    SunIcon: makeIcon('SunIcon'),
    MoonIcon: makeIcon('MoonIcon'),
    ComputerDesktopIcon: makeIcon('ComputerDesktopIcon'),
  }
})

vi.mock('../contexts/ThemeContext', () => ({
  useTheme: () => themeState,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, string>) => (params?.mode ? `${key}:${params.mode}` : key),
  }),
}))

import ThemeSwitcher from './ThemeSwitcher'
import ThemeToggle from './ThemeToggle'

describe('ThemeSwitcher', () => {
  beforeEach(() => {
    themeState.theme = 'system'
    themeState.resolvedTheme = 'dark'
    themeState.setTheme.mockReset()
  })

  it('renders the resolved current icon and opens the theme menu', () => {
    render(<ThemeSwitcher compact={false} />)

    expect(screen.getByLabelText('theme.switchTo:system')).toBeInTheDocument()
    expect(screen.getByText('theme.system')).toBeInTheDocument()
    expect(screen.getByTestId('MoonIcon')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('theme.switchTo:system'))

    expect(screen.getByText('theme.light')).toBeInTheDocument()
    expect(screen.getByText('theme.dark')).toBeInTheDocument()
    expect(screen.getAllByText('theme.system')).toHaveLength(2)
  })

  it('changes theme and closes when an option or outside area is clicked', () => {
    render(<ThemeSwitcher />)

    fireEvent.click(screen.getByLabelText('theme.switchTo:system'))
    fireEvent.click(screen.getByText('theme.light'))
    expect(themeState.setTheme).toHaveBeenCalledWith('light')

    fireEvent.click(screen.getByLabelText('theme.switchTo:system'))
    expect(screen.getByText('theme.dark')).toBeInTheDocument()

    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('theme.dark')).not.toBeInTheDocument()
  })

  it('uses the explicit dark theme icon when not in system mode', () => {
    themeState.theme = 'dark'
    themeState.resolvedTheme = 'light'

    render(<ThemeSwitcher />)

    expect(screen.getByTestId('MoonIcon')).toBeInTheDocument()
    expect(screen.getByLabelText('theme.switchTo:dark')).toHaveAttribute('title', 'theme.dark')
  })
})

describe('ThemeToggle', () => {
  beforeEach(() => {
    themeState.theme = 'dark'
    themeState.resolvedTheme = 'dark'
    themeState.setTheme.mockReset()
  })

  it('renders all theme buttons and highlights the active one', () => {
    render(<ThemeToggle />)

    const darkButton = screen.getByLabelText('Switch to Dark mode')
    expect(darkButton.className).toContain('bg-primary')
    expect(screen.getByLabelText('Switch to Light mode')).toBeInTheDocument()
    expect(screen.getByLabelText('Switch to System mode')).toBeInTheDocument()
  })

  it('calls setTheme for the selected option', () => {
    render(<ThemeToggle />)

    fireEvent.click(screen.getByLabelText('Switch to Light mode'))
    fireEvent.click(screen.getByLabelText('Switch to System mode'))

    expect(themeState.setTheme).toHaveBeenNthCalledWith(1, 'light')
    expect(themeState.setTheme).toHaveBeenNthCalledWith(2, 'system')
  })
})
