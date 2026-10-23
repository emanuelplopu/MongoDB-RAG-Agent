import { useCallback, useEffect, useRef, ReactNode } from 'react'
import { useSettingsSync, clearSyncedPreferences } from '../hooks/useSettingsSync'
import { SettingsSyncContext, SettingsSyncContextType, SETTINGS_SYNCED_EVENT } from './SettingsSyncContext'

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
