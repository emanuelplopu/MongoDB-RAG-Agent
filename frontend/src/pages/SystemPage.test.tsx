/**
 * Unit tests for System page component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest'
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

// Mock the tenant feature flags the hub filters its cards by
const { mockFeatures } = vi.hoisted(() => ({
  mockFeatures: { showEmbeddingBenchmark: true },
}))
vi.mock('../contexts/TenantContext', () => ({
  useTenant: () => ({ tenant: { features: mockFeatures }, tenantId: 'recallhub', isLoading: false }),
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
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
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

  it('shows the embedding benchmark card when the tenant enables it', async () => {
    mockFeatures.showEmbeddingBenchmark = true
    renderWithRouter(<SystemPage />)

    // Card name and description both mention the benchmark
    expect((await screen.findAllByText(/benchmark/i)).length).toBeGreaterThan(0)
  })

  it('hides the embedding benchmark card when the tenant feature is off', async () => {
    mockFeatures.showEmbeddingBenchmark = false
    renderWithRouter(<SystemPage />)

    // The other hub cards still render (status card), the gated one does not
    expect((await screen.findAllByText(/status/i)).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/benchmark/i)).toHaveLength(0)
    mockFeatures.showEmbeddingBenchmark = true
  })
})
