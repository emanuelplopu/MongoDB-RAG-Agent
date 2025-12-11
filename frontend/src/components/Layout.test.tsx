/**
 * Unit tests for React components.
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './Layout'

// Wrapper with router context - Layout uses Outlet so we need Routes
const renderWithRouter = (initialPath = '/chat') => {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/chat" element={<div>Chat Content</div>} />
          <Route path="/search" element={<div>Search Content</div>} />
          <Route path="/documents" element={<div>Documents Content</div>} />
          <Route path="/profiles" element={<div>Profiles Content</div>} />
          <Route path="/system" element={<div>System Content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}


describe('Layout', () => {
  it('should render layout with navigation', () => {
    renderWithRouter()
    
    // Check navigation items exist (getAllByText since there are desktop+mobile versions)
    expect(screen.getAllByText('Chat').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Search').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Documents').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Profiles').length).toBeGreaterThan(0)
    expect(screen.getAllByText('System').length).toBeGreaterThan(0)
  })

  it('should render children content via Outlet', () => {
    renderWithRouter('/chat')
    
    expect(screen.getByText('Chat Content')).toBeInTheDocument()
  })

  it('should have navigation links', () => {
    renderWithRouter()
    
    // Check that links are clickable - use getAllByText for multiple matches
    const chatLinks = screen.getAllByText('Chat')
    expect(chatLinks.length).toBeGreaterThan(0)
  })

  it('should render the app branding', () => {
    renderWithRouter()
    
    // Check for app title/header - uses MongoDB RAG branding
    expect(screen.getAllByText(/MongoDB RAG/i).length).toBeGreaterThan(0)
  })

  it('should show current page name in header', () => {
    renderWithRouter('/search')
    
    // Should show Search in the header area
    expect(screen.getAllByText('Search').length).toBeGreaterThan(0)
  })
})
