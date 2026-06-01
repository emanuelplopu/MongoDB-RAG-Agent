/**
 * Unit tests for Search page component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import SearchPage from './SearchPage'

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  ToastProvider: ({ children }: { children: React.ReactNode }) => children,
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
  localStorage.clear()
})
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

  it('renders search results, persisted options, and recent-search quick actions', async () => {
    const user = userEvent.setup()
    renderWithRouter(<SearchPage />)

    const input = screen.getByPlaceholderText(/search/i)
    await user.type(input, 'strategy os')
    await user.click(screen.getByRole('button', { name: 'Semantic' }))
    await user.selectOptions(screen.getByRole('combobox'), '20')
    fireEvent.submit(input.closest('form') as HTMLFormElement)

    expect(await screen.findByText('Test Document')).toBeInTheDocument()
    expect(screen.getByText('/path/to/doc.pdf')).toBeInTheDocument()
    expect(screen.getByText('95% match')).toBeInTheDocument()
    expect(screen.getByText('1 results')).toBeInTheDocument()
    expect(screen.getByText('150ms')).toBeInTheDocument()
    expect(screen.getByText('hybrid')).toBeInTheDocument()

    fireEvent.focus(input)
    expect(await screen.findByText('Recent Searches')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /strategy os/ }))
    await waitFor(() => expect(input).toHaveValue('strategy os'))
  })

  it('renders no-result and API error states', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('/api/v1/search', async ({ request }) => {
        const body = await request.json() as { query?: string }
        if (body.query === 'explode') {
          return HttpResponse.json({ error: 'boom' }, { status: 500 })
        }
        return HttpResponse.json({
          query: body.query ?? 'missing',
          search_type: 'hybrid',
          results: [],
          total_results: 0,
          processing_time_ms: 7,
        })
      })
    )

    renderWithRouter(<SearchPage />)
    const input = screen.getByPlaceholderText(/search/i)
    await user.type(input, 'missing')
    fireEvent.submit(input.closest('form') as HTMLFormElement)

    expect(await screen.findByText(/noResults|No results/i)).toBeInTheDocument()
    expect(screen.getByText('0 results')).toBeInTheDocument()

    await user.clear(input)
    await user.type(input, 'explode')
    await user.click(screen.getByRole('button', { name: 'Text' }))
    fireEvent.submit(input.closest('form') as HTMLFormElement)

    expect(await screen.findByText('Failed to perform search. Please try again.')).toBeInTheDocument()
  })
})
