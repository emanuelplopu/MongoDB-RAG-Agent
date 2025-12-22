/**
 * Unit tests for ThemeContext - theme state management.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, useTheme } from './ThemeContext'

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
}
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

// Mock matchMedia - create a function that creates the mock object
let matchMediaListeners: ((e: MediaQueryListEvent) => void)[] = []
let currentMatches = false // controls whether dark mode is detected

const createMatchMediaMock = (query: string) => ({
  matches: currentMatches,
  media: query,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: (_event: string, cb: (e: MediaQueryListEvent) => void) => {
    matchMediaListeners.push(cb)
  },
  removeEventListener: (_event: string, _cb: (e: MediaQueryListEvent) => void) => {
    matchMediaListeners = matchMediaListeners.filter(l => l !== _cb)
  },
  dispatchEvent: vi.fn(),
})

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(createMatchMediaMock),
})

// Test consumer component
function TestConsumer() {
  const { theme, setTheme, resolvedTheme } = useTheme()
  
  return (
    <div>
      <div data-testid="theme">{theme}</div>
      <div data-testid="resolved-theme">{resolvedTheme}</div>
      <button data-testid="set-light" onClick={() => setTheme('light')}>Light</button>
      <button data-testid="set-dark" onClick={() => setTheme('dark')}>Dark</button>
      <button data-testid="set-system" onClick={() => setTheme('system')}>System</button>
    </div>
  )
}

describe('ThemeContext', () => {
  beforeEach(() => {
    // Reset mock states
    matchMediaListeners = []
    currentMatches = false
    localStorageMock.getItem.mockReturnValue(null)
    localStorageMock.setItem.mockClear()
    document.documentElement.classList.remove('dark')
    
    // Reset matchMedia to default implementation
    ;(window.matchMedia as ReturnType<typeof vi.fn>).mockImplementation(createMatchMediaMock)
  })

  afterEach(() => {
    document.documentElement.classList.remove('dark')
  })

  describe('useTheme hook', () => {
    it('should throw error when used outside ThemeProvider', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      
      expect(() => {
        render(<TestConsumer />)
      }).toThrow('useTheme must be used within a ThemeProvider')
      
      consoleSpy.mockRestore()
    })
  })

  describe('Initial state', () => {
    it('should default to system theme', () => {
      localStorageMock.getItem.mockReturnValue(null)
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      expect(screen.getByTestId('theme')).toHaveTextContent('system')
    })

    it('should load stored theme from localStorage', () => {
      localStorageMock.getItem.mockReturnValue('dark')
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      expect(screen.getByTestId('theme')).toHaveTextContent('dark')
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('dark')
    })

    it('should resolve system theme to light when prefers-color-scheme is light', () => {
      currentMatches = false // light mode
      localStorageMock.getItem.mockReturnValue(null)
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('light')
    })

    it('should resolve system theme to dark when prefers-color-scheme is dark', () => {
      currentMatches = true // dark mode
      localStorageMock.getItem.mockReturnValue(null)
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('dark')
    })
  })

  describe('Theme switching', () => {
    it('should switch to light theme', async () => {
      const user = userEvent.setup()
      localStorageMock.getItem.mockReturnValue(null)
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      await user.click(screen.getByTestId('set-light'))
      
      expect(screen.getByTestId('theme')).toHaveTextContent('light')
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('light')
      expect(localStorageMock.setItem).toHaveBeenCalledWith('theme-preference', 'light')
    })

    it('should switch to dark theme', async () => {
      const user = userEvent.setup()
      localStorageMock.getItem.mockReturnValue(null)
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      await user.click(screen.getByTestId('set-dark'))
      
      expect(screen.getByTestId('theme')).toHaveTextContent('dark')
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('dark')
      expect(localStorageMock.setItem).toHaveBeenCalledWith('theme-preference', 'dark')
    })

    it('should switch to system theme', async () => {
      const user = userEvent.setup()
      localStorageMock.getItem.mockReturnValue('dark')
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      await user.click(screen.getByTestId('set-system'))
      
      expect(screen.getByTestId('theme')).toHaveTextContent('system')
      expect(localStorageMock.setItem).toHaveBeenCalledWith('theme-preference', 'system')
    })
  })

  describe('Dark class on document', () => {
    it('should add dark class when theme is dark', async () => {
      const user = userEvent.setup()
      localStorageMock.getItem.mockReturnValue(null)
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      await user.click(screen.getByTestId('set-dark'))
      
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    })

    it('should remove dark class when theme is light', async () => {
      const user = userEvent.setup()
      localStorageMock.getItem.mockReturnValue('dark')
      document.documentElement.classList.add('dark')
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      await user.click(screen.getByTestId('set-light'))
      
      expect(document.documentElement.classList.contains('dark')).toBe(false)
    })
  })

  describe('System theme change listener', () => {
    it('should update resolved theme when system preference changes', async () => {
      currentMatches = false // start with light
      localStorageMock.getItem.mockReturnValue('system')
      
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      )
      
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('light')
      
      // Simulate system theme change to dark
      await act(async () => {
        currentMatches = true // now dark
        // Call all registered listeners
        matchMediaListeners.forEach(cb => {
          cb({ matches: true } as MediaQueryListEvent)
        })
      })
      
      expect(screen.getByTestId('resolved-theme')).toHaveTextContent('dark')
    })
  })
})
