import { createContext, useContext } from 'react'

export type Theme = 'light' | 'dark' | 'system'

export interface ThemeContextType {
  theme: Theme
  setTheme: (theme: Theme) => void
  /** Apply theme from synced preferences (no DB write back) */
  applyTheme: (theme: Theme) => void
  resolvedTheme: 'light' | 'dark'
}

/** Provided by `ThemeProvider` (see ./ThemeProvider.tsx). */
export const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

export function useTheme() {
  const context = useContext(ThemeContext)
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}
