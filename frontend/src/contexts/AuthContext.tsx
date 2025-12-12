import { createContext, useContext, useState, useEffect, ReactNode, useCallback } from 'react'
import { authApi, User, setAuthToken, clearAuthToken, getAuthToken } from '../api/client'

interface AuthContextType {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, name: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Check for existing session on mount
  useEffect(() => {
    const checkAuth = async () => {
      const token = getAuthToken()
      if (token) {
        try {
          const userData = await authApi.getMe()
          setUser(userData)
        } catch (error) {
          // Token expired or invalid
          clearAuthToken()
          setUser(null)
        }
      }
      setIsLoading(false)
    }
    checkAuth()
  }, [])

  // Listen for 401 unauthorized events from API client
  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null)
    }
    window.addEventListener('auth:unauthorized', handleUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const response = await authApi.login({ email, password })
    setAuthToken(response.access_token)
    setUser(response.user)
  }, [])

  const register = useCallback(async (email: string, name: string, password: string) => {
    const response = await authApi.register({ email, name, password })
    setAuthToken(response.access_token)
    setUser(response.user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch (error) {
      // Ignore errors on logout
    }
    clearAuthToken()
    setUser(null)
  }, [])

  const refreshUser = useCallback(async () => {
    try {
      const userData = await authApi.getMe()
      setUser(userData)
    } catch (error) {
      clearAuthToken()
      setUser(null)
    }
  }, [])

  const value: AuthContextType = {
    user,
    isLoading,
    isAuthenticated: !!user,
    login,
    register,
    logout,
    refreshUser,
  }

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}
