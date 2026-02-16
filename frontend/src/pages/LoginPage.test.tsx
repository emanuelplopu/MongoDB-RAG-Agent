/**
 * Unit tests for LoginPage component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import LoginPage from './LoginPage'

// Mock navigate
const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

// Mock login and register functions
const mockLogin = vi.fn()
const mockRegister = vi.fn()

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    login: mockLogin,
    register: mockRegister,
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
        expect(mockNavigate).toHaveBeenCalledWith('/')
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
        expect(mockRegister).toHaveBeenCalledWith('test@example.com', 'Test User', 'password123')
        expect(mockNavigate).toHaveBeenCalledWith('/')
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
  })

  describe('Error handling', () => {
    it('should handle 401 error specially', async () => {
      const user = userEvent.setup()
      const error = new Error('Unauthorized')
      ;(error as any).status = 401
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
      const error = new Error('Server error')
      ;(error as any).status = 500
      mockLogin.mockRejectedValue(error)
      
      renderLoginPage()
      
      await user.type(screen.getByPlaceholderText('you@example.com'), 'test@example.com')
      await user.type(screen.getByPlaceholderText('••••••••'), 'password')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      
      await waitFor(() => {
        expect(screen.getByText('Unable to connect to the server. Please try again later.')).toBeInTheDocument()
      })
    })
  })
})
