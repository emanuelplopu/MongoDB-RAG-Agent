/**
 * UserPreferencesContext - Manages user preferences and settings
 * Persists settings to localStorage with automatic sync.
 *
 * The provider lives in ./UserPreferencesProvider.tsx and the settings form
 * controls in ../components/SettingsControls.tsx.
 */

import { createContext, useContext } from 'react'
import { UserPreferencesSync } from '../api/client'

// User preferences interface
export interface UserPreferences {
  // Model settings
  defaultModel: string

  // Search settings
  defaultSearchType: 'hybrid' | 'semantic' | 'text'
  defaultMatchCount: number
  showSearchHistory: boolean

  // Display settings
  uiDensity: 'compact' | 'comfortable' | 'spacious'
  itemsPerPage: number
  showLineNumbers: boolean
  codeTheme: 'light' | 'dark' | 'system'

  // Chat settings
  streamingEnabled: boolean
  showTimestamps: boolean
  messageGrouping: boolean
  enterToSend: boolean

  // Notifications
  soundEnabled: boolean
  notificationsEnabled: boolean
  showToastDuration: number

  // Advanced
  developerMode: boolean
  experimentalFeatures: boolean
  autoSaveEnabled: boolean
  autoSaveInterval: number
}

// Context type
export interface UserPreferencesContextType {
  preferences: UserPreferences
  setPreference: <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => void
  setPreferences: (updates: Partial<UserPreferences>) => void
  resetPreferences: () => void
  resetPreference: <K extends keyof UserPreferences>(key: K) => void
  isDefault: <K extends keyof UserPreferences>(key: K) => boolean
  /** Apply preferences from DB sync (no DB write back) */
  applyFromSync: (syncPrefs: UserPreferencesSync) => void
}

export const UserPreferencesContext = createContext<UserPreferencesContextType | null>(null)

export function useUserPreferences() {
  const context = useContext(UserPreferencesContext)
  if (!context) {
    throw new Error('useUserPreferences must be used within a UserPreferencesProvider')
  }
  return context
}
