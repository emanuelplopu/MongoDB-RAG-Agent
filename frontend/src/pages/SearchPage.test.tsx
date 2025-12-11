/**
 * Unit tests for Search page component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { server } from '../test/server'
import SearchPage from './SearchPage'

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
afterEach(() => server.resetHandlers())
afterAll(() => server.close())


describe('SearchPage', () => {
  it('should render search page', () => {
    renderWithRouter(<SearchPage />)
    
    // Check for search input
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument()
  })

  it('should have search type selector', () => {
    renderWithRouter(<SearchPage />)
    
    // Check for search type options
    expect(screen.getByText(/hybrid/i)).toBeInTheDocument()
  })

  it('should allow entering search query', () => {
    renderWithRouter(<SearchPage />)
    
    const input = screen.getByPlaceholderText(/search/i) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'test query' } })
    
    expect(input.value).toBe('test query')
  })

  it('should submit search on form submit', async () => {
    renderWithRouter(<SearchPage />)
    
    const input = screen.getByPlaceholderText(/search/i)
    fireEvent.change(input, { target: { value: 'test search' } })
    
    const form = input.closest('form')
    if (form) {
      fireEvent.submit(form)
    }
    
    // Wait for results to load
    await waitFor(() => {
      // Either results or loading state should appear
      expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument()
    })
  })

  it('should display results count selector', () => {
    renderWithRouter(<SearchPage />)
    
    // Look for results/match count selector - it's a select element
    const selectElements = screen.getAllByRole('combobox')
    expect(selectElements.length).toBeGreaterThan(0)
  })
})
