import { useState, useRef, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  PaperAirplaneIcon,
  PlusIcon,
  ChatBubbleLeftRightIcon,
  SparklesIcon,
  ClockIcon,
  CurrencyDollarIcon,
  BoltIcon,
  DocumentTextIcon,
  ChevronDownIcon,
  ExclamationCircleIcon,
} from '@heroicons/react/24/outline'
import {
  sessionsApi,
  documentsApi,
  SessionMessage,
} from '../api/client'
import { useChatSidebar } from '../contexts/ChatSidebarContext'

// Local storage keys
const STORAGE_KEYS = {
  DRAFT: 'chat_draft_',
}

// Format cost for display
const formatCost = (cost: number): string => {
  if (cost < 0.0001) return '<$0.0001'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(3)}`
}

// Format tokens per second (with safety check)
const formatTps = (tps: number | undefined | null): string => {
  if (tps === undefined || tps === null || !isFinite(tps)) return '0 tok/s'
  return `${tps.toFixed(1)} tok/s`
}

export default function ChatPage() {
  const {
    currentSession,
    setCurrentSession,
    setSessions,
    handleNewChat,
    models,
    getPricing,
  } = useChatSidebar()

  // Local state
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showModelSelector, setShowModelSelector] = useState(false)
  
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Load draft from localStorage when session changes
  useEffect(() => {
    if (currentSession) {
      const draft = localStorage.getItem(STORAGE_KEYS.DRAFT + currentSession.id)
      if (draft) {
        setInput(draft)
      } else {
        setInput('')
      }
    }
  }, [currentSession?.id])

  // Save draft to localStorage on input change
  useEffect(() => {
    if (currentSession && input) {
      localStorage.setItem(STORAGE_KEYS.DRAFT + currentSession.id, input)
    } else if (currentSession) {
      localStorage.removeItem(STORAGE_KEYS.DRAFT + currentSession.id)
    }
  }, [input, currentSession?.id])

  // Scroll to bottom when messages change
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [currentSession?.messages, scrollToBottom])

  // Focus input when session changes
  useEffect(() => {
    if (currentSession) {
      inputRef.current?.focus()
    }
  }, [currentSession?.id])

  // Change model for current session
  const handleChangeModel = async (modelId: string) => {
    if (!currentSession) return
    try {
      await sessionsApi.update(currentSession.id, { model: modelId })
      setCurrentSession(prev => prev ? { ...prev, model: modelId } : null)
      setSessions(prev => prev.map(s => 
        s.id === currentSession.id ? { ...s, model: modelId } : s
      ))
    } catch (err) {
      console.error('Failed to change model:', err)
    }
    setShowModelSelector(false)
  }

  // Send message
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isLoading) return

    // Create session if needed
    let session = currentSession
    if (!session) {
      session = await sessionsApi.create()
      setSessions(prev => [session!, ...prev])
      setCurrentSession(session)
    }

    const messageContent = input.trim()
    setInput('')
    setError(null)
    localStorage.removeItem(STORAGE_KEYS.DRAFT + session.id)
    setIsLoading(true)

    // Optimistically add user message
    const tempUserMessage: SessionMessage = {
      id: 'temp-' + Date.now(),
      role: 'user',
      content: messageContent,
      timestamp: new Date().toISOString(),
    }
    setCurrentSession(prev => prev ? {
      ...prev,
      messages: [...prev.messages, tempUserMessage]
    } : null)

    try {
      const response = await sessionsApi.sendMessage(session.id, messageContent)
      
      // Update session with real messages
      setCurrentSession(prev => {
        if (!prev) return null
        const messages = prev.messages.filter(m => !m.id.startsWith('temp-'))
        return {
          ...prev,
          messages: [...messages, response.user_message, response.assistant_message],
          stats: response.session_stats,
        }
      })

      // Update sessions list
      setSessions(prev => prev.map(s => 
        s.id === session!.id 
          ? { ...s, updated_at: new Date().toISOString(), stats: response.session_stats }
          : s
      ))
    } catch (err: any) {
      console.error('Failed to send message:', err)
      setError(err.message || 'Failed to send message. Please try again.')
      // Remove temp message on error
      setCurrentSession(prev => prev ? {
        ...prev,
        messages: prev.messages.filter(m => !m.id.startsWith('temp-'))
      } : null)
    } finally {
      setIsLoading(false)
    }
  }

  // Handle textarea auto-resize
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
  }

  // Handle Enter to send (Shift+Enter for newline)
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
    }
  }

  return (
    <div className="flex h-[calc(100vh-0px)] lg:h-screen flex-col bg-white dark:bg-gray-800">
      {currentSession ? (
        <>
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-3 border-b border-surface-variant dark:border-gray-700 flex-shrink-0">
            <h2 className="font-semibold text-primary-900 dark:text-gray-100 truncate max-w-md">
              {currentSession.title || 'New Chat'}
            </h2>
            <div className="flex items-center gap-3">
              {/* Model Selector */}
              <div className="relative">
                <button
                  onClick={() => setShowModelSelector(!showModelSelector)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-variant dark:bg-gray-700 hover:bg-surface dark:hover:bg-gray-600 text-sm transition-colors"
                >
                  <SparklesIcon className="h-4 w-4 text-primary" />
                  <span className="dark:text-gray-200">{currentSession.model}</span>
                  <ChevronDownIcon className="h-4 w-4 text-secondary" />
                </button>
                {showModelSelector && (
                  <div className="absolute right-0 mt-1 w-80 max-h-96 overflow-y-auto bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-surface-variant dark:border-gray-600 z-50">
                    <div className="p-2">
                      <div className="text-xs font-medium text-secondary dark:text-gray-400 px-2 py-1 uppercase">
                        Select Model
                      </div>
                      {models.slice(0, 20).map(model => {
                        const pricing = getPricing(model.id)
                        return (
                          <button
                            key={model.id}
                            onClick={() => handleChangeModel(model.id)}
                            className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-left hover:bg-surface-variant dark:hover:bg-gray-700 ${
                              currentSession.model === model.id ? 'bg-primary-50 dark:bg-primary-900/30' : ''
                            }`}
                          >
                            <span className="text-sm dark:text-gray-200">{model.id}</span>
                            <span className="text-xs text-secondary dark:text-gray-400">
                              ${pricing.output}/1M out
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>

              {/* Session Stats */}
              {(currentSession.stats?.total_tokens ?? 0) > 0 && (
                <div className="flex items-center gap-3 text-xs text-secondary dark:text-gray-400">
                  <span className="flex items-center gap-1">
                    <DocumentTextIcon className="h-4 w-4" />
                    {(currentSession.stats?.total_tokens ?? 0).toLocaleString()} tokens
                  </span>
                  <span className="flex items-center gap-1">
                    <CurrencyDollarIcon className="h-4 w-4" />
                    {formatCost(currentSession.stats?.total_cost_usd ?? 0)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-6 py-4">
            <div className="max-w-3xl mx-auto space-y-6">
              {currentSession.messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                />
              ))}
              {isLoading && (
                <div className="flex gap-4">
                  <div className="w-8 h-8 rounded-full bg-primary-100 dark:bg-primary-900 flex items-center justify-center flex-shrink-0">
                    <SparklesIcon className="h-5 w-5 text-primary" />
                  </div>
                  <div className="flex-1">
                    <div className="flex space-x-2 py-4">
                      <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '0ms' }} />
                      <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '150ms' }} />
                      <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '300ms' }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input */}
          <div className="border-t border-surface-variant dark:border-gray-700 px-6 py-4 flex-shrink-0">
            {/* Error Message */}
            {error && (
              <div className="max-w-3xl mx-auto mb-3">
                <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 text-sm">
                  <ExclamationCircleIcon className="h-5 w-5 flex-shrink-0" />
                  <span>{error}</span>
                  <button
                    onClick={() => setError(null)}
                    className="ml-auto text-red-500 hover:text-red-700 dark:hover:text-red-300"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}
            <form onSubmit={handleSubmit} className="max-w-3xl mx-auto">
              <div className="relative flex items-end gap-2 bg-surface-variant dark:bg-gray-700 rounded-2xl p-2">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Message..."
                  rows={1}
                  className="flex-1 resize-none bg-transparent px-3 py-2 text-primary-900 dark:text-gray-100 placeholder:text-secondary dark:placeholder:text-gray-500 focus:outline-none max-h-52"
                  disabled={isLoading}
                />
                <button
                  type="submit"
                  disabled={!input.trim() || isLoading}
                  className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-white transition-all hover:bg-primary-700 disabled:bg-secondary disabled:cursor-not-allowed flex-shrink-0"
                >
                  <PaperAirplaneIcon className="h-5 w-5" />
                </button>
              </div>
              <p className="text-xs text-center text-secondary dark:text-gray-500 mt-2">
                Press Enter to send, Shift+Enter for new line
              </p>
            </form>
          </div>
        </>
      ) : (
        /* Empty State */
        <div className="flex-1 flex flex-col items-center justify-center text-center px-6">
          <div className="rounded-3xl bg-primary-100 dark:bg-primary-900/50 p-8 mb-6">
            <ChatBubbleLeftRightIcon className="h-16 w-16 text-primary" />
          </div>
          <h2 className="text-2xl font-semibold text-primary-900 dark:text-gray-100 mb-3">
            MongoDB RAG Assistant
          </h2>
          <p className="text-secondary dark:text-gray-400 max-w-md mb-6">
            Ask questions about your documents. I'll search through your knowledge base to find relevant information.
          </p>
          <button
            onClick={() => handleNewChat()}
            className="flex items-center gap-2 px-6 py-3 rounded-xl bg-primary text-white hover:bg-primary-700 transition-colors font-medium"
          >
            <PlusIcon className="h-5 w-5" />
            Start New Chat
          </button>
        </div>
      )}
    </div>
  )
}

// Message Bubble Component
function MessageBubble({ message }: { message: SessionMessage }) {
  const isUser = message.role === 'user'
  const [documentIds, setDocumentIds] = useState<Record<string, string>>({})

  // Lookup document IDs for sources
  useEffect(() => {
    if (message.sources && message.sources.length > 0) {
      message.sources.forEach(async (source) => {
        if (!documentIds[source.source]) {
          try {
            const doc = await documentsApi.findBySource(source.source)
            if (doc) {
              setDocumentIds(prev => ({ ...prev, [source.source]: doc.id }))
            }
          } catch (err) {
            // Ignore lookup errors
          }
        }
      })
    }
  }, [message.sources])

  return (
    <div className={`flex gap-4 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
        isUser 
          ? 'bg-primary text-white' 
          : 'bg-primary-100 dark:bg-primary-900'
      }`}>
        {isUser ? (
          <span className="text-sm font-medium">U</span>
        ) : (
          <SparklesIcon className="h-5 w-5 text-primary" />
        )}
      </div>
      <div className={`flex-1 max-w-[85%] ${isUser ? 'text-right' : ''}`}>
        <div className={`inline-block rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-primary text-white'
            : 'bg-surface-variant dark:bg-gray-700 text-primary-900 dark:text-gray-100'
        }`}>
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="prose prose-sm max-w-none dark:prose-invert">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          )}
        </div>
        
        {/* Sources */}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {message.sources.map((source, idx) => {
              const docId = documentIds[source.source]
              const content = (
                <>
                  <DocumentTextIcon className="h-3 w-3" />
                  <span className="text-primary-700 dark:text-primary-300">{source.title}</span>
                  <span className="text-secondary dark:text-gray-400">{Math.round(source.relevance * 100)}%</span>
                </>
              )
              return docId ? (
                <Link
                  key={idx}
                  to={`/documents/${docId}`}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-surface-variant dark:bg-gray-700 text-xs hover:bg-surface dark:hover:bg-gray-600 transition-colors"
                  title={source.excerpt}
                >
                  {content}
                </Link>
              ) : (
                <span
                  key={idx}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-surface-variant dark:bg-gray-700 text-xs"
                  title={source.excerpt}
                >
                  {content}
                </span>
              )
            })}
          </div>
        )}
        
        {/* Stats */}
        {message.stats && (
          <div className="mt-2 flex items-center gap-3 text-xs text-secondary dark:text-gray-400">
            <span className="flex items-center gap-1">
              <DocumentTextIcon className="h-3 w-3" />
              {message.stats.total_tokens?.toLocaleString() ?? 0} tokens
            </span>
            <span className="flex items-center gap-1">
              <BoltIcon className="h-3 w-3" />
              {formatTps(message.stats.tokens_per_second)}
            </span>
            <span className="flex items-center gap-1">
              <ClockIcon className="h-3 w-3" />
              {((message.stats.latency_ms ?? 0) / 1000).toFixed(1)}s
            </span>
            <span className="flex items-center gap-1">
              <CurrencyDollarIcon className="h-3 w-3" />
              {formatCost(message.stats.cost_usd ?? 0)}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
