/**
 * Unit tests for LoginPage component.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import LoginPage from './LoginPage'

const translations: Record<string, string> = {
  'common.appName': 'RecallHub',
  'login.signInTitle': 'Sign in to your account',
  'login.signUpTitle': 'Create a new account',
  'login.email': 'Email',
  'login.emailPlaceholder': 'you@example.com',
  'login.password': 'Password',
  'login.passwordPlaceholder': '••••••••',
  'login.signIn': 'Sign in',
  'login.signingIn': 'Signing in...',
  'login.createAccount': 'Create account',
  'login.creatingAccount': 'Creating account...',
  'login.name': 'Name',
  'login.namePlaceholder': 'Your name',
  'login.nameRequired': 'Name is required',
  'login.noAccount': "Don't have an account? Sign up",
  'login.hasAccount': 'Already have an account? Sign in',
  'login.continueWithout': 'Continue without account',
  'login.invalidCredentials': 'Invalid email or password. Please try again.',
  'login.serverError': 'Server error. Please try again later.',
  'login.authFailed': 'Authentication failed',
  'login.errorId': 'Error ID',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => translations[key] ?? fallback ?? key,
  }),
}))

// Mock navigate
const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
  }
})

vi.mock('../components/LocalizedLink', () => ({
  useLocalizedNavigate: () => mockNavigate,
  useLocalizedPath: () => (path: string) => path,
}))

// Mutable tenant state: tests flip requireAuth to check the guest-only controls
let tenantState: { tenant: { features: { requireAuth: boolean; startPage: 'landing' | 'login' } } }
vi.mock('../contexts/TenantContext', () => ({
  useTenant: () => tenantState,
}))

vi.mock('../components/TenantLogo', () => ({
  default: () => <div>Tenant Logo</div>,
}))

vi.mock('../components/ThemeSwitcher', () => ({
  default: () => <div>Theme Switcher</div>,
}))

vi.mock('../components/LanguageSwitcher', () => ({
  default: () => <div>Language Switcher</div>,
}))

// Mock login and register functions
const mockLogin = vi.fn()
const mockRegister = vi.fn()

// Mutable auth state: tests set an existing session or an expired one
let authState: { isAuthenticated: boolean; isLoading: boolean; sessionExpired: boolean }
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    login: mockLogin,
    register: mockRegister,
    ...authState,
  }),
}))

// Re-export ApiError for mocking
vi.mock('../api/client', async () => {
  const actual = await vi.importActual('../api/client')
  return {
    ...actual,
    ApiError: class ApiError extends Error {
      errorId: string | null = null
      getUserMessage() { return this.message }
    },
  }
})

const renderLoginPage = () => {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authState = { isAuthenticated: false, isLoading: false, sessionExpired: false }
    tenantState = { tenant: { features: { requireAuth: false, startPage: 'landing' } } }
  })

  afterEach(() => {
    cleanup()
  })

  describe('Initial rendering', () => {
    it('should render login form by default', () => {
      renderLoginPage()
      
      expect(screen.getByText('RecallHub')).toBeInTheDocument()
      expect(screen.getByText('Sign in to your account')).toBeInTheDocument()
      expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
      expect(screen.getByPlaceholderText('••••••••')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    })

    it('should have name field hidden in login mode', () => {
      renderLoginPage()
      
      expect(screen.queryByPlaceholderText('Your name')).not.toBeInTheDocument()
    })
  })

  describe('Mode switching', () => {
    it('should switch to register mode', async () => {
      const user = userEvent.setup()
      renderLoginPage()
      
      await user.click(screen.getByText("Don't have an account? Sign up"))
      
      expect(screen.getByText('Create a new account')).toBeInTheDocument()
      expect(screen.getByPlaceholderText('Your name')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Create account' })).toBeInTheDocument()
    })

    it('should switch back to login mode', async () => {
      const user = userEvent.setup()
      renderLoginPage()
      
      await user.click(screen.getByText("Don't have an account? Sign up"))
      await user.click(screen.getByText('Already have an account? Sign in'))
      
      expect(screen.getByText('Sign in to your account')).toBeInTheDocument()
      expect(screen.queryByPlaceholderText('Your name')).not.toBeInTheDocument()
    })

    it('should clear error when switching modes', async () => {
      const user = userEvent.setup()
      mockLogin.mockRejectedValue(new Error('Login failed'))
      
      renderLoginPage()
      
      // Fill form and submit to get error
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@test.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      await waitFor(() => {
        expect(screen.getByText('Login failed')).toBeInTheDocument()
      })
      
      // Switch mode
      await user.click(screen.getByText("Don't have an account? Sign up"))
      
      // Error should be cleared
      expect(screen.queryByText('Login failed')).not.toBeInTheDocument()
    })
  })

  describe('Login flow', () => {
    it('should call login on form submit', async () => {
      const user = userEvent.setup()
      mockLogin.mockResolvedValue(undefined)
      
      renderLoginPage()
      
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      await waitFor(() => {
        expect(mockLogin).toHaveBeenCalledWith('test@example.com', 'password123')
        expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true })
      })
    })

    it('returns the user to the page that required the login', async () => {
      const user = userEvent.setup()
      mockLogin.mockResolvedValue(undefined)

      render(
        <MemoryRouter initialEntries={[{ pathname: '/en/login', state: { from: '/en/chat?tab=1' } }]}>
          <LoginPage />
        </MemoryRouter>
      )

      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/en/chat?tab=1', { replace: true })
      })
    })

    it('ignores unsafe or self-referencing redirect targets', async () => {
      const user = userEvent.setup()
      mockLogin.mockResolvedValue(undefined)

      render(
        <MemoryRouter initialEntries={[{ pathname: '/en/login', state: { from: '//evil.example' } }]}>
          <LoginPage />
        </MemoryRouter>
      )

      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true })
      })
    })

    it('should show loading state during login', async () => {
      const user = userEvent.setup()
      mockLogin.mockImplementation(() => new Promise(r => setTimeout(r, 100)))
      
      renderLoginPage()
      
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      expect(screen.getByText('Signing in...')).toBeInTheDocument()
    })

    it('should show error on login failure', async () => {
      const user = userEvent.setup()
      mockLogin.mockRejectedValue(new Error('Invalid credentials'))
      
      renderLoginPage()
      
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'wrongpassword')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      await waitFor(() => {
        expect(screen.getByText('Invalid credentials')).toBeInTheDocument()
      })
    })
  })

  describe('Register flow', () => {
    it('should call register on form submit in register mode', async () => {
      const user = userEvent.setup()
      mockRegister.mockResolvedValue(undefined)
      
      renderLoginPage()
      
      // Switch to register mode
      await user.click(screen.getByText("Don't have an account? Sign up"))
      
      await user.type(screen.getByPlaceholderText('Your name'), 'Test User')
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Create account' }))
      
      await waitFor(() => {
        expect(mockRegister).toHaveBeenCalledWith('test@example.com', 'Test User', 'password123', undefined, undefined)
        expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true })
      })
    })

    it('should show error when name is only whitespace in register mode', async () => {
      const user = userEvent.setup()
      
      renderLoginPage()
      
      // Switch to register mode
      await user.click(screen.getByText("Don't have an account? Sign up"))
      
      // Enter whitespace-only name (passes required but fails validation)
      await user.type(screen.getByPlaceholderText('Your name'), '   ')
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Create account' }))
      
      await waitFor(() => {
        expect(screen.getByText('Name is required')).toBeInTheDocument()
      })
      expect(mockRegister).not.toHaveBeenCalled()
    })

    it('should show loading state during registration', async () => {
      const user = userEvent.setup()
      mockRegister.mockImplementation(() => new Promise(r => setTimeout(r, 100)))
      
      renderLoginPage()
      
      await user.click(screen.getByText("Don't have an account? Sign up"))
      
      await user.type(screen.getByPlaceholderText('Your name'), 'Test User')
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Create account' }))
      
      expect(screen.getByText('Creating account...')).toBeInTheDocument()
    })
  })

  describe('Skip login', () => {
    it('should navigate to home when skipping login', async () => {
      const user = userEvent.setup()

      renderLoginPage()

      await user.click(screen.getByText('Continue without account'))

      expect(mockNavigate).toHaveBeenCalledWith('/')
    })

    it('offers no guest access when the tenant requires a login', () => {
      tenantState.tenant.features.requireAuth = true

      renderLoginPage()

      expect(screen.queryByText('Continue without account')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    })
  })

  describe('Existing session', () => {
    const renderWithRoutes = (initialEntry: string | { pathname: string; state?: unknown }) =>
      render(
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/en/login" element={<LoginPage />} />
            <Route path="/dashboard" element={<div>dashboard-page</div>} />
            <Route path="/en/chat" element={<div>chat-page</div>} />
          </Routes>
        </MemoryRouter>
      )

    it('sends a signed-in user straight to the dashboard', () => {
      authState = { isAuthenticated: true, isLoading: false, sessionExpired: false }

      renderWithRoutes('/login')

      expect(screen.getByText('dashboard-page')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument()
    })

    it('sends a signed-in user back to the page they came from', () => {
      authState = { isAuthenticated: true, isLoading: false, sessionExpired: false }

      renderWithRoutes({ pathname: '/en/login', state: { from: '/en/chat' } })

      expect(screen.getByText('chat-page')).toBeInTheDocument()
    })

    it('keeps showing the form while the stored session is still being validated', () => {
      authState = { isAuthenticated: true, isLoading: true, sessionExpired: false }

      renderWithRoutes('/login')

      expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    })

    it('explains why the user landed here after a session expiry', () => {
      renderWithRoutes({ pathname: '/en/login', state: { from: '/en/chat', sessionExpired: true } })

      expect(screen.getByRole('status')).toHaveTextContent('errors.sessionExpired.message')
    })
  })

  describe('Error handling', () => {
    it('should handle 401 error specially', async () => {
      const user = userEvent.setup()
      const error = Object.assign(new Error('Unauthorized'), { status: 401 })
      mockLogin.mockRejectedValue(error)
      
      renderLoginPage()
      
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'wrongpassword')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      await waitFor(() => {
        expect(screen.getByText('Invalid email or password. Please try again.')).toBeInTheDocument()
      })
    })

    it('should handle 500 error specially', async () => {
      const user = userEvent.setup()
      const error = Object.assign(new Error('Server error'), { status: 500 })
      mockLogin.mockRejectedValue(error)
      
      renderLoginPage()
      
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      await waitFor(() => {
        expect(screen.getByText('Server error. Please try again later.')).toBeInTheDocument()
      })
    })
  })
})
