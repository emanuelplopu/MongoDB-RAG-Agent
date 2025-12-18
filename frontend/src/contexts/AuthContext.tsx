import { createContext, useContext, useState, useEffect, ReactNode, useCallback, useRef } from 'react'
import { authApi, User, setAuthToken, clearAuthToken, getAuthToken } from '../api/client'
import SessionExpiredModal from '../components/SessionExpiredModal'

interface AuthContextType {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  sessionExpired: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, name: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  dismissSessionExpired: () => void
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
  const [sessionExpired, setSessionExpired] = useState(false)
  const [showSessionModal, setShowSessionModal] = useState(false)
  const hadUserRef = useRef(false) // Track if user was previously logged in
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
      // Only show modal if user was previously logged in
      if (hadUserRef.current) {
        setSessionExpired(true)
        setShowSessionModal(true)
      }
      setUser(null)
      clearAuthToken()
    }
    window.addEventListener('auth:unauthorized', handleUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized)
  }, [])

  // Track when user becomes logged in
  useEffect(() => {
    if (user) {
      hadUserRef.current = true
    }
  }, [user])

  const login = useCallback(async (email: string, password: string) => {
    const response = await authApi.login({ email, password })
    setAuthToken(response.access_token)
    setUser(response.user)
    setSessionExpired(false)
    setShowSessionModal(false)
  }, [])

  const register = useCallback(async (email: string, name: string, password: string) => {
    const response = await authApi.register({ email, name, password })
    setAuthToken(response.access_token)
    setUser(response.user)
    setSessionExpired(false)
    setShowSessionModal(false)
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch (error) {
      // Ignore errors on logout
    }
    clearAuthToken()
    setUser(null)
    setSessionExpired(false)
    setShowSessionModal(false)
    hadUserRef.current = false
  }, [])

  const refreshUser = useCallback(async () => {
    try {
      const userData = await authApi.getMe()
      setUser(userData)
    } catch (error) {
      // Check if this was a session expiration
      if (hadUserRef.current) {
        setSessionExpired(true)
        setShowSessionModal(true)
      }
      clearAuthToken()
      setUser(null)
    }
  }, [])

  const dismissSessionExpired = useCallback(() => {
    setSessionExpired(false)
    setShowSessionModal(false)
    hadUserRef.current = false
  }, [])

  const handleContinueAsGuest = useCallback(() => {
    setSessionExpired(false)
    setShowSessionModal(false)
    hadUserRef.current = false
  }, [])

  const value: AuthContextType = {
    user,
    isLoading,
    isAuthenticated: !!user,
    sessionExpired,
    login,
    register,
    logout,
    refreshUser,
    dismissSessionExpired,
  }

  return (
    <AuthContext.Provider value={value}>
      {children}
      <SessionExpiredModal
        isOpen={showSessionModal}
        onClose={() => setShowSessionModal(false)}
        onContinueAsGuest={handleContinueAsGuest}
      />
    </AuthContext.Provider>
  )
}
