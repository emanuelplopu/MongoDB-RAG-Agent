import { useState, useRef, useEffect } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { PaperAirplaneIcon, DocumentTextIcon } from '@heroicons/react/24/solid'
import { chatApi, ChatMessage, ChatResponse, documentsApi, Document } from '../api/client'

interface SourceWithId {
  title: string
  source: string
  relevance: number
  excerpt: string
  documentId?: string
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | undefined>()
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null)
  const [sourcesWithIds, setSourcesWithIds] = useState<SourceWithId[]>([])
  const [documentsCache, setDocumentsCache] = useState<Document[]>([])
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // Fetch documents list for matching sources to IDs
  useEffect(() => {
    const fetchDocuments = async () => {
      try {
        const response = await documentsApi.list(1, 200)
        setDocumentsCache(response.documents)
      } catch (err) {
        console.error('Failed to fetch documents for source linking:', err)
      }
    }
    fetchDocuments()
  }, [])

  // Match sources with document IDs when response changes
  useEffect(() => {
    if (!lastResponse?.sources || documentsCache.length === 0) {
      setSourcesWithIds([])
      return
    }

    const enrichedSources: SourceWithId[] = lastResponse.sources.map(source => {
      // Try to find matching document by title or source path
      const matchingDoc = documentsCache.find(
        doc => doc.title === source.title || 
               doc.source === source.source ||
               doc.title.toLowerCase() === source.title.toLowerCase()
      )
      return {
        ...source,
        documentId: matchingDoc?.id
      }
    })

    setSourcesWithIds(enrichedSources)
  }, [lastResponse, documentsCache])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isLoading) return

    const userMessage: ChatMessage = {
      role: 'user',
      content: input.trim(),
      timestamp: new Date().toISOString(),
    }

    setMessages((prev) => [...prev, userMessage])
    setInput('')
    setIsLoading(true)

    try {
      const response = await chatApi.send(input.trim(), conversationId)
      setConversationId(response.conversation_id)
      setLastResponse(response)

      const assistantMessage: ChatMessage = {
        role: 'assistant',
        content: response.message,
        timestamp: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, assistantMessage])
    } catch (error) {
      console.error('Chat error:', error)
      const errorMessage: ChatMessage = {
        role: 'assistant',
        content: 'Sorry, an error occurred while processing your request.',
        timestamp: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, errorMessage])
    } finally {
      setIsLoading(false)
    }
  }

  const startNewChat = () => {
    setMessages([])
    setConversationId(undefined)
    setLastResponse(null)
    setSourcesWithIds([])
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      {/* Chat messages area */}
      <div className="flex-1 overflow-y-auto pb-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div className="rounded-3xl bg-primary-100 dark:bg-primary-900/50 p-6 mb-6">
              <DocumentTextIcon className="h-12 w-12 text-primary" />
            </div>
            <h2 className="text-2xl font-semibold text-primary-900 dark:text-primary-200 mb-2">
              MongoDB RAG Assistant
            </h2>
            <p className="text-secondary dark:text-gray-400 max-w-md">
              Ask questions about your documents. I'll search through your knowledge base to find relevant information.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((message, index) => (
              <div
                key={index}
                className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                    message.role === 'user'
                      ? 'bg-primary text-white'
                      : 'bg-surface-variant dark:bg-gray-800 text-primary-900 dark:text-gray-200'
                  }`}
                >
                  {message.role === 'assistant' ? (
                    <div className="prose prose-sm max-w-none dark:prose-invert">
                      <ReactMarkdown>{message.content}</ReactMarkdown>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap">{message.content}</p>
                  )}
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="flex justify-start">
                <div className="bg-surface-variant dark:bg-gray-800 rounded-2xl px-4 py-3">
                  <div className="flex space-x-2">
                    <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '0ms' }} />
                    <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '150ms' }} />
                    <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Sources panel */}
      {sourcesWithIds.length > 0 && (
        <div className="mb-4 rounded-2xl bg-surface-variant dark:bg-gray-800 p-4">
          <h3 className="text-sm font-medium text-primary-900 dark:text-primary-200 mb-2">Sources</h3>
          <div className="flex flex-wrap gap-2">
            {sourcesWithIds.map((source, index) => (
              source.documentId ? (
                <Link
                  key={index}
                  to={`/documents/${source.documentId}`}
                  className="rounded-xl bg-white dark:bg-gray-700 px-3 py-2 text-xs shadow-sm hover:shadow-md hover:bg-primary-50 dark:hover:bg-gray-600 transition-all cursor-pointer"
                  title={`${source.excerpt}\n\nClick to view document details`}
                >
                  <span className="font-medium text-primary-700 dark:text-primary-300 hover:underline">
                    {source.title}
                  </span>
                  <span className="text-secondary dark:text-gray-400 ml-2">
                    {Math.round(source.relevance * 100)}%
                  </span>
                </Link>
              ) : (
                <div
                  key={index}
                  className="rounded-xl bg-white dark:bg-gray-700 px-3 py-2 text-xs shadow-sm"
                  title={source.excerpt}
                >
                  <span className="font-medium text-primary-700 dark:text-primary-300">
                    {source.title}
                  </span>
                  <span className="text-secondary dark:text-gray-400 ml-2">
                    {Math.round(source.relevance * 100)}%
                  </span>
                </div>
              )
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-surface-variant dark:border-gray-700 pt-4">
        <form onSubmit={handleSubmit} className="flex gap-3">
          <div className="relative flex-1">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question about your documents..."
              className="w-full rounded-2xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-800 px-4 py-3 pr-12 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              disabled={isLoading}
            />
          </div>
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-white transition-all hover:bg-primary-700 disabled:bg-secondary disabled:cursor-not-allowed"
          >
            <PaperAirplaneIcon className="h-5 w-5" />
          </button>
        </form>
        {conversationId && (
          <div className="mt-2 flex items-center justify-between text-xs text-secondary dark:text-gray-400">
            <span>Conversation: {conversationId.slice(0, 8)}...</span>
            <button
              onClick={startNewChat}
              className="text-primary hover:underline"
            >
              New Chat
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
