/**
 * Unit tests for Chat page component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import ChatPage from './ChatPage'

// Mock scrollIntoView which doesn't exist in jsdom
window.HTMLElement.prototype.scrollIntoView = vi.fn()

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
  describe('Initial rendering', () => {
    it('should render chat page with welcome message', () => {
      renderWithRouter(<ChatPage />)
      
      expect(screen.getByText('RecallHub Assistant')).toBeInTheDocument()
      expect(screen.getByText(/Ask questions about your documents/)).toBeInTheDocument()
    })

    it('should render message input', () => {
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      expect(input).toBeInTheDocument()
    })

    it('should render send button', () => {
      renderWithRouter(<ChatPage />)
      
      const buttons = screen.getAllByRole('button')
      expect(buttons.length).toBeGreaterThan(0)
    })

    it('should have send button disabled when input is empty', () => {
      renderWithRouter(<ChatPage />)
      
      const sendButton = screen.getByRole('button', { name: '' }) // Submit button with icon
      expect(sendButton).toBeDisabled()
    })
  })

  describe('Message input', () => {
    it('should allow typing in the input', async () => {
      const user = userEvent.setup()
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hello, AI!')
      
      expect(input).toHaveValue('Hello, AI!')
    })

    it('should enable send button when input has text', async () => {
      const user = userEvent.setup()
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hello')
      
      const submitButton = document.querySelector('button[type="submit"]')
      expect(submitButton).not.toBeDisabled()
    })

    it('should clear input after form submission', async () => {
      const user = userEvent.setup()
      
      // Mock the chat API response
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'Hello! How can I help?',
            conversation_id: 'conv-123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hello')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      // Input should clear after submission
      await waitFor(() => {
        expect(input).toHaveValue('')
      })
    })
  })

  describe('Chat flow', () => {
    it('should display user message after sending', async () => {
      const user = userEvent.setup()
      
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'I can help with that!',
            conversation_id: 'conv-123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'What is React?')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      // Wait for user message to appear
      await waitFor(() => {
        expect(screen.getByText('What is React?')).toBeInTheDocument()
      })
    })

    it('should display assistant response', async () => {
      const user = userEvent.setup()
      
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'React is a JavaScript library for building UIs.',
            conversation_id: 'conv-123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'What is React?')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      await waitFor(() => {
        expect(screen.getByText('React is a JavaScript library for building UIs.')).toBeInTheDocument()
      })
    })

    it('should show conversation ID after first message', async () => {
      const user = userEvent.setup()
      
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'Hello!',
            conversation_id: 'conv-abc123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hi')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      await waitFor(() => {
        expect(screen.getByText(/Conversation: conv-abc/)).toBeInTheDocument()
      })
    })

    it('should show New Chat button after first message', async () => {
      const user = userEvent.setup()
      
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'Hello!',
            conversation_id: 'conv-123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hi')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      await waitFor(() => {
        expect(screen.getByText('New Chat')).toBeInTheDocument()
      })
    })
  })

  describe('New Chat functionality', () => {
    it('should clear messages when New Chat is clicked', async () => {
      const user = userEvent.setup()
      
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'Response message',
            conversation_id: 'conv-123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      // Send a message
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hello')
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      // Wait for response
      await waitFor(() => {
        expect(screen.getByText('New Chat')).toBeInTheDocument()
      })
      
      // Click New Chat
      await user.click(screen.getByText('New Chat'))
      
      // Welcome message should appear again
      await waitFor(() => {
        expect(screen.getByText('RecallHub Assistant')).toBeInTheDocument()
      })
    })
  })

  describe('Error handling', () => {
    it('should display error message when chat fails', async () => {
      const user = userEvent.setup()
      
      // Mock API to return error
      server.use(
        http.post('*/chat', () => {
          return HttpResponse.json(
            { detail: 'Server error' },
            { status: 500 }
          )
        })
      )
      
      // Suppress console.error for this test
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hello')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      await waitFor(() => {
        expect(screen.getByText(/Sorry, an error occurred/)).toBeInTheDocument()
      })
      
      consoleSpy.mockRestore()
    })
  })

  describe('Sources panel', () => {
    it('should display sources when response includes them', async () => {
      const user = userEvent.setup()
      
      // First set up the documents API response
      server.use(
        http.get('*/ingestion/documents', () => {
          return HttpResponse.json({
            documents: [
              { id: 'doc-1', title: 'React Guide', source: '/docs/react.md' },
            ],
            total: 1,
            page: 1,
            limit: 200,
          })
        }),
        http.post('*/chat', () => {
          return HttpResponse.json({
            message: 'Here is the answer.',
            conversation_id: 'conv-123',
            sources: [
              { title: 'React Guide', source: '/docs/react.md', relevance: 0.95, excerpt: 'React is...' },
            ],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      // Wait for documents to load, then send message
      await waitFor(() => {
        // Documents should load first
      }, { timeout: 100 })
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Tell me about React')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      await waitFor(() => {
        expect(screen.getByText('Sources')).toBeInTheDocument()
        expect(screen.getByText('React Guide')).toBeInTheDocument()
        expect(screen.getByText('95%')).toBeInTheDocument()
      })
    })
  })

  describe('Loading state', () => {
    it('should disable input while loading', async () => {
      const user = userEvent.setup()
      
      // Use a delayed response
      server.use(
        http.post('*/chat', async () => {
          await new Promise(resolve => setTimeout(resolve, 100))
          return HttpResponse.json({
            message: 'Response',
            conversation_id: 'conv-123',
            sources: [],
          })
        })
      )
      
      renderWithRouter(<ChatPage />)
      
      const input = screen.getByPlaceholderText('Ask a question about your documents...')
      await user.type(input, 'Hello')
      
      const form = input.closest('form')!
      fireEvent.submit(form)
      
      // Input should be disabled while loading
      await waitFor(() => {
        expect(input).toBeDisabled()
      })
      
      // Input should be enabled after response
      await waitFor(() => {
        expect(input).not.toBeDisabled()
      })
    })
  })
})
