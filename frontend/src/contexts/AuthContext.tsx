import { createContext, useContext, useState, useEffect, ReactNode, useCallback, useRef } from 'react'
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
  const lastValidationRef = useRef<number>(0)
  const VALIDATION_INTERVAL = 5 * 60 * 1000 // 5 minutes

  // Function to validate auth token
  const validateAuth = useCallback(async (force: boolean = false) => {
    const token = getAuthToken()
    if (!token) {
      setUser(null)
      setIsLoading(false)
      return
    }
    
    // Skip if recently validated (unless forced)
    const now = Date.now()
    if (!force && lastValidationRef.current && (now - lastValidationRef.current) < VALIDATION_INTERVAL) {
      setIsLoading(false)
      return
    }
    
    try {
      const userData = await authApi.getMe()
      setUser(userData)
      lastValidationRef.current = now
    } catch (error) {
      // Token expired or invalid
      clearAuthToken()
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Check for existing session on mount
  useEffect(() => {
    validateAuth(true)
  }, [validateAuth])
  
  // Revalidate when tab becomes visible (handles idle/sleep scenarios)
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && getAuthToken()) {
        validateAuth()
      }
    }
    
    const handleFocus = () => {
      if (getAuthToken()) {
        validateAuth()
      }
    }
    
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('focus', handleFocus)
    
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('focus', handleFocus)
    }
  }, [validateAuth])

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
