import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import FeatureGuard from './FeatureGuard'
import { useTenant } from '../contexts/TenantContext'

vi.mock('../contexts/TenantContext', () => ({
  useTenant: vi.fn(),
}))

const mockedUseTenant = vi.mocked(useTenant)

describe('FeatureGuard', () => {
  it('renders children when the feature is enabled', () => {
    mockedUseTenant.mockReturnValue({
      tenant: { features: { prompts: true } },
    } as never)

    render(
      <MemoryRouter initialEntries={['/feature']}>
        <Routes>
          <Route
            path="/feature"
            element={
              <FeatureGuard feature="prompts">
                <div>visible content</div>
              </FeatureGuard>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByText('visible content')).toBeInTheDocument()
  })

  it('redirects to the dashboard when the feature is disabled', () => {
    mockedUseTenant.mockReturnValue({
      tenant: { features: { prompts: false } },
    } as never)

    render(
      <MemoryRouter initialEntries={['/feature']}>
        <Routes>
          <Route
            path="/feature"
            element={
              <FeatureGuard feature="prompts">
                <div>hidden content</div>
              </FeatureGuard>
            }
          />
          <Route path="/dashboard" element={<div>dashboard page</div>} />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByText('dashboard page')).toBeInTheDocument()
    expect(screen.queryByText('hidden content')).not.toBeInTheDocument()
  })
})
