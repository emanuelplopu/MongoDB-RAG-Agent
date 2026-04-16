/**
 * SettingsSyncContext - Coordinates settings sync between localStorage and DB.
 * 
 * When preferences are pulled from DB (on login), dispatches a 'settings:synced' 
 * custom event. Individual context providers listen for this and apply the relevant settings.
 */

import { createContext, useContext, useCallback, useEffect, useRef, ReactNode } from 'react'
import { useSettingsSync, clearSyncedPreferences } from '../hooks/useSettingsSync'
import { UserPreferencesSync } from '../api/client'

// Custom event for when preferences are pulled from DB
export const SETTINGS_SYNCED_EVENT = 'settings:synced'

interface SettingsSyncContextType {
  /** Sync a partial preference update to both localStorage and DB */
  syncPreference: (updates: Partial<UserPreferencesSync>) => void
  /** Pull all preferences from DB and apply them */
  pullAndApply: () => Promise<UserPreferencesSync | null>
  /** Clear synced data (call on logout) */
  clearSync: () => void
}

const SettingsSyncContext = createContext<SettingsSyncContextType | null>(null)

export function useSettingsSyncContext() {
  const context = useContext(SettingsSyncContext)
  if (!context) {
    throw new Error('useSettingsSyncContext must be used within SettingsSyncProvider')
  }
  return context
}

// Optional access - returns null if not within provider
export function useOptionalSettingsSync() {
  return useContext(SettingsSyncContext)
}

interface SettingsSyncProviderProps {
  children: ReactNode
  isAuthenticated: boolean
}

export function SettingsSyncProvider({ children, isAuthenticated }: SettingsSyncProviderProps) {
  const { pullFromDb, syncPreference, flushToDb } = useSettingsSync()
  const lastAuthState = useRef(false)

  // Pull preferences from DB when user becomes authenticated
  useEffect(() => {
    if (isAuthenticated && !lastAuthState.current) {
      // User just logged in - pull from DB and dispatch event
      pullFromDb().then(prefs => {
        if (prefs) {
          window.dispatchEvent(new CustomEvent(SETTINGS_SYNCED_EVENT, { detail: prefs }))
        }
      })
    } else if (!isAuthenticated && lastAuthState.current) {
      // User logged out - clear synced data
      clearSyncedPreferences()
    }
    lastAuthState.current = isAuthenticated
  }, [isAuthenticated, pullFromDb])

  // Flush pending updates before page unload
  useEffect(() => {
    const handleBeforeUnload = () => {
      flushToDb()
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [flushToDb])

  const pullAndApply = useCallback(async () => {
    const prefs = await pullFromDb()
    if (prefs) {
      window.dispatchEvent(new CustomEvent(SETTINGS_SYNCED_EVENT, { detail: prefs }))
    }
    return prefs
  }, [pullFromDb])

  const clearSync = useCallback(() => {
    clearSyncedPreferences()
  }, [])

  const value: SettingsSyncContextType = {
    syncPreference,
    pullAndApply,
    clearSync,
  }

  return (
    <SettingsSyncContext.Provider value={value}>
      {children}
    </SettingsSyncContext.Provider>
  )
}
