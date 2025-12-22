/**
 * Test utilities with common wrappers and mock providers.
 */

import React, { ReactNode, ReactElement, createContext, useContext } from 'react'
import { render, RenderOptions } from '@testing-library/react'
import { BrowserRouter, MemoryRouter, Routes, Route } from 'react-router-dom'

// Mock user for authenticated tests
export const mockUser = {
  id: 'test-user-id',
  email: 'test@example.com',
  name: 'Test User',
  is_admin: true,
  created_at: '2025-01-01T00:00:00Z',
}

export const mockNonAdminUser = {
  id: 'test-user-id',
  email: 'user@example.com',
  name: 'Regular User',
  is_admin: false,
  created_at: '2025-01-01T00:00:00Z',
}

// Mock AuthContext value for tests
export interface MockAuthContextValue {
  user: typeof mockUser | null
  isLoading: boolean
  isAuthenticated: boolean
  sessionExpired: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, name: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  dismissSessionExpired: () => void
}

// Create mock auth context with default values
export const createMockAuthContext = (overrides?: Partial<MockAuthContextValue>): MockAuthContextValue => ({
  user: mockUser,
  isLoading: false,
  isAuthenticated: true,
  sessionExpired: false,
  login: async () => {},
  register: async () => {},
  logout: async () => {},
  refreshUser: async () => {},
  dismissSessionExpired: () => {},
  ...overrides,
})

// Create the same AuthContext structure as the real one
const TestAuthContext = createContext<MockAuthContextValue | null>(null)

// Mock AuthProvider that uses our test context
interface MockAuthProviderProps {
  children: ReactNode
  value?: Partial<MockAuthContextValue>
}

export function MockAuthProvider({ children, value }: MockAuthProviderProps) {
  const contextValue = createMockAuthContext(value)
  
  return (
    <TestAuthContext.Provider value={contextValue}>
      {children}
    </TestAuthContext.Provider>
  )
}

// Test hook that mirrors useAuth
export function useTestAuth() {
  const context = useContext(TestAuthContext)
  if (!context) {
    throw new Error('useTestAuth must be used within MockAuthProvider')
  }
  return context
}

// Export the context for direct use in vi.mock
export { TestAuthContext }

// Simple router wrapper without auth (for components that don't need auth)
export function RouterWrapper({ children }: { children: ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>
}

// Memory router wrapper for specific routes
export function MemoryRouterWrapper({ 
  children, 
  initialRoute = '/' 
}: { 
  children: ReactNode
  initialRoute?: string 
}) {
  return (
    <MemoryRouter initialEntries={[initialRoute]}>
      {children}
    </MemoryRouter>
  )
}

// All-in-one wrapper with auth and router
interface AllProvidersProps {
  children: ReactNode
  authValue?: Partial<MockAuthContextValue>
  initialRoute?: string
}

export function AllProviders({ children, authValue, initialRoute = '/' }: AllProvidersProps) {
  return (
    <MemoryRouter initialEntries={[initialRoute]}>
      <MockAuthProvider value={authValue}>
        {children}
      </MockAuthProvider>
    </MemoryRouter>
  )
}

// Custom render function that wraps with all providers
interface CustomRenderOptions extends Omit<RenderOptions, 'wrapper'> {
  authValue?: Partial<MockAuthContextValue>
  initialRoute?: string
}

export function renderWithProviders(
  ui: ReactElement,
  options?: CustomRenderOptions
) {
  const { authValue, initialRoute, ...renderOptions } = options || {}
  
  return render(ui, {
    wrapper: ({ children }) => (
      <AllProviders authValue={authValue} initialRoute={initialRoute}>
        {children}
      </AllProviders>
    ),
    ...renderOptions,
  })
}

// Export mock data for reuse
export { mockUser as testUser, mockNonAdminUser as testNonAdminUser }
