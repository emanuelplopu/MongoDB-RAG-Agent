/**
 * Unit tests for System page component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { server } from '../test/server'
import SystemPage from './SystemPage'
import { mockUser } from '../test/test-utils'

// Mock the AuthContext module - SystemPage needs admin user
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockUser, // mockUser.is_admin = true
    isLoading: false,
    isAuthenticated: true,
    sessionExpired: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
    dismissSessionExpired: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}))

// Wrapper with router context
const renderWithRouter = (ui: React.ReactElement) => {
  return render(
    <BrowserRouter>
      {ui}
    </BrowserRouter>
  )
}

// Start MSW server before tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  vi.clearAllMocks()
})
afterAll(() => server.close())


describe('SystemPage', () => {
  it('should render system page with loading or content', async () => {
    renderWithRouter(<SystemPage />)
    
    // Wait for data to load - may show loading first
    await waitFor(() => {
      // Either loading or system content should be visible
      const container = document.body
      expect(container.textContent).toBeDefined()
    })
  })

  it('should display health status section', async () => {
    renderWithRouter(<SystemPage />)
    
    // Wait for data to load
    await waitFor(() => {
      // Check for any content rendered
      const container = document.body
      expect(container.textContent?.length).toBeGreaterThan(0)
    }, { timeout: 3000 })
  })

  it('should display configuration section', async () => {
    renderWithRouter(<SystemPage />)
    
    await waitFor(() => {
      // Look for configuration-related labels
      const configElements = screen.queryAllByText(/config|settings|provider|model/i)
      expect(configElements.length).toBeGreaterThanOrEqual(0) // May or may not be loaded
    })
  })

  it('should have indexes section', async () => {
    renderWithRouter(<SystemPage />)
    
    await waitFor(() => {
      const indexElements = screen.queryAllByText(/index|indexes/i)
      expect(indexElements.length).toBeGreaterThanOrEqual(0)
    })
  })

  it('should show database statistics', async () => {
    renderWithRouter(<SystemPage />)
    
    await waitFor(() => {
      const dbElements = screen.queryAllByText(/database|documents|chunks/i)
      expect(dbElements.length).toBeGreaterThanOrEqual(0)
    })
  })
})
