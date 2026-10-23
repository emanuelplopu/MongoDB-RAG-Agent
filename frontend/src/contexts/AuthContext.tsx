import { createContext, useContext } from 'react'
import { User } from '../api/client'

export interface AuthContextType {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  sessionExpired: boolean
  showSessionModal: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, name: string, password: string, titlePrefix?: string, titleSuffix?: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  dismissSessionExpired: () => void
  closeSessionModal: () => void
  continueAsGuest: () => void
}

/** Provided by `AuthProvider` (see ./AuthProvider.tsx). */
export const AuthContext = createContext<AuthContextType | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
