/**
 * Unit tests for AuthContext - authentication state management.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthProvider, useAuth } from './AuthContext'
import { BrowserRouter } from 'react-router-dom'

// Mock the api client
vi.mock('../api/client', () => ({
  authApi: {
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    getMe: vi.fn(),
  },
  setAuthToken: vi.fn(),
  clearAuthToken: vi.fn(),
  getAuthToken: vi.fn(),
}))

// Mock SessionExpiredModal
vi.mock('../components/SessionExpiredModal', () => ({
  default: ({ isOpen, onClose, onContinueAsGuest }: { isOpen: boolean; onClose: () => void; onContinueAsGuest: () => void }) => 
    isOpen ? (
      <div data-testid="session-expired-modal">
        <button onClick={onClose}>Close</button>
        <button onClick={onContinueAsGuest}>Continue as Guest</button>
      </div>
    ) : null,
}))

import { authApi, setAuthToken, clearAuthToken, getAuthToken } from '../api/client'

// Test component that uses useAuth - catches login errors
function TestConsumer({ onAuthChange }: { onAuthChange?: (auth: ReturnType<typeof useAuth>) => void }) {
  const auth = useAuth()
  
  if (onAuthChange) {
    onAuthChange(auth)
  }
  
  const handleLogin = async () => {
    try {
      await auth.login('test@example.com', 'password')
    } catch {
      // Error is expected in some tests
    }
  }
  
  const handleRegister = async () => {
    try {
      await auth.register('test@example.com', 'Test User', 'password')
    } catch {
      // Error is expected in some tests
    }
  }
  
  return (
    <div>
      <div data-testid="user-status">{auth.isAuthenticated ? 'authenticated' : 'guest'}</div>
      <div data-testid="user-name">{auth.user?.name || 'no-user'}</div>
      <div data-testid="loading">{auth.isLoading ? 'loading' : 'ready'}</div>
      <button data-testid="login-btn" onClick={handleLogin}>
        Login
      </button>
      <button data-testid="logout-btn" onClick={() => auth.logout()}>
        Logout
      </button>
      <button data-testid="register-btn" onClick={handleRegister}>
        Register
      </button>
    </div>
  )
}

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <BrowserRouter>
      <AuthProvider>
        {ui}
      </AuthProvider>
    </BrowserRouter>
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Default: no token
    vi.mocked(getAuthToken).mockReturnValue(null)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  describe('useAuth hook', () => {
    it('should throw error when used outside AuthProvider', () => {
      // Suppress console.error for this test
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      
      expect(() => {
        render(<TestConsumer />)
      }).toThrow('useAuth must be used within AuthProvider')
      
      consoleSpy.mockRestore()
    })
  })

  describe('Initial state', () => {
    it('should start with loading state', async () => {
      vi.mocked(getAuthToken).mockReturnValue(null)
      
      renderWithProviders(<TestConsumer />)
      
      // After mount, should finish loading
      await waitFor(() => {
        expect(screen.getByTestId('loading')).toHaveTextContent('ready')
      })
    })

    it('should be unauthenticated when no token exists', async () => {
      vi.mocked(getAuthToken).mockReturnValue(null)
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('user-status')).toHaveTextContent('guest')
        expect(screen.getByTestId('user-name')).toHaveTextContent('no-user')
      })
    })

    it('should validate token on mount if it exists', async () => {
      const mockUser = { id: '1', email: 'test@test.com', name: 'Test', is_admin: false, created_at: '' }
      vi.mocked(getAuthToken).mockReturnValue('valid-token')
      vi.mocked(authApi.getMe).mockResolvedValue(mockUser)
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(authApi.getMe).toHaveBeenCalled()
        expect(screen.getByTestId('user-status')).toHaveTextContent('authenticated')
        expect(screen.getByTestId('user-name')).toHaveTextContent('Test')
      })
    })

    it('should clear token if validation fails', async () => {
      vi.mocked(getAuthToken).mockReturnValue('invalid-token')
      vi.mocked(authApi.getMe).mockRejectedValue(new Error('Unauthorized'))
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(clearAuthToken).toHaveBeenCalled()
        expect(screen.getByTestId('user-status')).toHaveTextContent('guest')
      })
    })
  })

  describe('Login', () => {
    it('should login successfully', async () => {
      const user = userEvent.setup()
      const mockResponse = {
        access_token: 'new-token',
        user: { id: '1', email: 'test@example.com', name: 'Test User', is_admin: false, created_at: '' }
      }
      vi.mocked(authApi.login).mockResolvedValue(mockResponse)
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('loading')).toHaveTextContent('ready')
      })
      
      await user.click(screen.getByTestId('login-btn'))
      
      await waitFor(() => {
        expect(authApi.login).toHaveBeenCalledWith({ email: 'test@example.com', password: 'password' })
        expect(setAuthToken).toHaveBeenCalledWith('new-token')
        expect(screen.getByTestId('user-status')).toHaveTextContent('authenticated')
      })
    })

    it('should handle login failure', async () => {
      const user = userEvent.setup()
      vi.mocked(authApi.login).mockRejectedValue(new Error('Invalid credentials'))
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('loading')).toHaveTextContent('ready')
      })
      
      // Click login - the error will be caught by the context
      // but user state should remain unchanged (guest)
      await user.click(screen.getByTestId('login-btn'))
      
      // Allow error to be processed
      await new Promise(resolve => setTimeout(resolve, 100))
      
      // User should still be guest after failed login
      expect(screen.getByTestId('user-status')).toHaveTextContent('guest')
    })
  })

  describe('Logout', () => {
    it('should logout successfully', async () => {
      const user = userEvent.setup()
      const mockUser = { id: '1', email: 'test@test.com', name: 'Test', is_admin: false, created_at: '' }
      vi.mocked(getAuthToken).mockReturnValue('valid-token')
      vi.mocked(authApi.getMe).mockResolvedValue(mockUser)
      vi.mocked(authApi.logout).mockResolvedValue(undefined)
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('user-status')).toHaveTextContent('authenticated')
      })
      
      await user.click(screen.getByTestId('logout-btn'))
      
      await waitFor(() => {
        expect(authApi.logout).toHaveBeenCalled()
        expect(clearAuthToken).toHaveBeenCalled()
        expect(screen.getByTestId('user-status')).toHaveTextContent('guest')
      })
    })

    it('should handle logout error gracefully', async () => {
      const user = userEvent.setup()
      const mockUser = { id: '1', email: 'test@test.com', name: 'Test', is_admin: false, created_at: '' }
      vi.mocked(getAuthToken).mockReturnValue('valid-token')
      vi.mocked(authApi.getMe).mockResolvedValue(mockUser)
      vi.mocked(authApi.logout).mockRejectedValue(new Error('Network error'))
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('user-status')).toHaveTextContent('authenticated')
      })
      
      await user.click(screen.getByTestId('logout-btn'))
      
      // Should still clear auth state even if API fails
      await waitFor(() => {
        expect(clearAuthToken).toHaveBeenCalled()
        expect(screen.getByTestId('user-status')).toHaveTextContent('guest')
      })
    })
  })

  describe('Register', () => {
    it('should register successfully', async () => {
      const user = userEvent.setup()
      const mockResponse = {
        access_token: 'new-token',
        user: { id: '1', email: 'test@example.com', name: 'Test User', is_admin: false, created_at: '' }
      }
      vi.mocked(authApi.register).mockResolvedValue(mockResponse)
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('loading')).toHaveTextContent('ready')
      })
      
      await user.click(screen.getByTestId('register-btn'))
      
      await waitFor(() => {
        expect(authApi.register).toHaveBeenCalledWith({
          email: 'test@example.com',
          name: 'Test User',
          password: 'password'
        })
        expect(setAuthToken).toHaveBeenCalledWith('new-token')
        expect(screen.getByTestId('user-status')).toHaveTextContent('authenticated')
      })
    })
  })

  describe('Session expired handling', () => {
    it('should handle unauthorized events', async () => {
      const mockUser = { id: '1', email: 'test@test.com', name: 'Test', is_admin: false, created_at: '' }
      vi.mocked(getAuthToken).mockReturnValue('valid-token')
      vi.mocked(authApi.getMe).mockResolvedValue(mockUser)
      
      renderWithProviders(<TestConsumer />)
      
      await waitFor(() => {
        expect(screen.getByTestId('user-status')).toHaveTextContent('authenticated')
      })
      
      // Dispatch unauthorized event
      await act(async () => {
        window.dispatchEvent(new Event('auth:unauthorized'))
      })
      
      await waitFor(() => {
        expect(clearAuthToken).toHaveBeenCalled()
        expect(screen.getByTestId('user-status')).toHaveTextContent('guest')
      })
    })
  })
})
