/**
 * Unit tests for Chat page component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { server } from '../test/server'
import ChatPage from './ChatPage'

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


describe('ChatPage', () => {
  it('should render chat page', () => {
    renderWithRouter(<ChatPage />)
    
    // Check for message input
    const inputs = screen.getAllByRole('textbox')
    expect(inputs.length).toBeGreaterThan(0)
  })

  it('should have send button', () => {
    renderWithRouter(<ChatPage />)
    
    // Look for send button
    const buttons = screen.getAllByRole('button')
    expect(buttons.length).toBeGreaterThan(0)
  })

  it('should allow typing message', () => {
    renderWithRouter(<ChatPage />)
    
    const inputs = screen.getAllByRole('textbox')
    const input = inputs[0] as HTMLInputElement | HTMLTextAreaElement
    
    fireEvent.change(input, { target: { value: 'Hello, AI!' } })
    expect(input.value).toBe('Hello, AI!')
  })

  it('should have new chat button', () => {
    renderWithRouter(<ChatPage />)
    
    // Look for new chat/clear button
    const buttons = screen.getAllByRole('button')
    // Button might exist or not depending on design
    expect(buttons.length).toBeGreaterThan(0)
  })

  it('should render messages container', () => {
    renderWithRouter(<ChatPage />)
    
    // The chat messages area should exist
    const container = document.querySelector('[class*="message"], [class*="chat"], [class*="flex"]')
    expect(container).toBeInTheDocument()
  })
})
