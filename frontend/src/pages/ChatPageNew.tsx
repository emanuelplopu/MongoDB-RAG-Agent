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
  ChevronRightIcon,
  ExclamationCircleIcon,
  PaperClipIcon,
  XMarkIcon,
  MagnifyingGlassIcon,
  CpuChipIcon,
  GlobeAltIcon,
  CheckCircleIcon,
  XCircleIcon,
  InformationCircleIcon,
  Cog6ToothIcon,
} from '@heroicons/react/24/outline'
import {
  sessionsApi,
  documentsApi,
  SessionMessage,
  ApiError,
  AttachmentInfo,
} from '../api/client'
import { useChatSidebar } from '../contexts/ChatSidebarContext'
import { useAuth } from '../contexts/AuthContext'
import FederatedAgentPanel from '../components/FederatedAgentPanel'

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

// Agent mode configuration with descriptions
const AGENT_MODES = {
  auto: {
    label: 'Auto',
    icon: '🔄',
    description: 'Automatically chooses mode based on query complexity',
    details: 'Uses FAST mode for short/simple queries (<50 chars). Uses THINKING mode for complex queries with words like "analyze", "step by step", "compare", "explain", "why", "how does".',
    color: 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300',
  },
  thinking: {
    label: 'Thinking',
    icon: '🧠',
    description: 'Full orchestrator-worker pipeline for complex questions',
    details: 'Always uses the Orchestrator (GPT-5.2) to analyze intent, create a search plan, execute parallel searches via Workers, evaluate results, and synthesize a comprehensive answer. Best for multi-step questions, research tasks, and when you need thorough answers.',
    color: 'bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300',
  },
  fast: {
    label: 'Fast',
    icon: '⚡',
    description: 'Direct search without orchestration for quick answers',
    details: 'Skips the Orchestrator entirely and performs a direct hybrid search. Faster but less thorough. Good for simple factual questions or when you know the information exists in documents.',
    color: 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300',
  },
} as const

export default function ChatPage() {
  const {
    currentSession,
    setCurrentSession,
    setSessions,
    handleNewChat,
    models,
    getPricing,
  } = useChatSidebar()
  
  // Get current user for error message handling
  const { user } = useAuth()

  // Local state
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showModelSelector, setShowModelSelector] = useState(false)
  const [showAgentModeSelector, setShowAgentModeSelector] = useState(false)
  const [agentMode, setAgentMode] = useState<'auto' | 'thinking' | 'fast'>('auto')
  const [showSettingsInfo, setShowSettingsInfo] = useState(false)
  const [attachments, setAttachments] = useState<AttachmentInfo[]>([])
  const [attachmentTokens, setAttachmentTokens] = useState<number>(0)
  const [useStreaming] = useState(true)  // Enable streaming by default
  const [liveTrace, setLiveTrace] = useState<{
    orchestrator_steps: Array<{ phase: string; reasoning: string; output: string; duration_ms: number; tokens: number }>
    worker_steps: Array<{ task_id: string; task_type: string; tool: string; duration_ms: number; success: boolean; documents: Array<{ title: string; score: number; excerpt: string }> }>
    stats: { total_tokens: number; orchestrator_tokens: number; worker_tokens: number; cost_usd: number }
    startTime: number
    currentPhase: string
  } | null>(null)
  const [elapsedTime, setElapsedTime] = useState(0)
  const streamAbortRef = useRef<{ abort: () => void } | null>(null)
  
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  // Track elapsed time during streaming
  useEffect(() => {
    if (liveTrace) {
      const interval = setInterval(() => {
        setElapsedTime(Math.floor((Date.now() - liveTrace.startTime) / 1000))
      }, 1000)
      return () => clearInterval(interval)
    } else {
      setElapsedTime(0)
    }
  }, [liveTrace?.startTime])

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

  // Handle file attachment
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return

    const newAttachments: AttachmentInfo[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      
      // Check file size (max 20MB)
      if (file.size > 20 * 1024 * 1024) {
        setError(`File "${file.name}" is too large (max 20MB)`)
        continue
      }

      // Read file as data URL for images
      const dataUrl = await new Promise<string | null>((resolve) => {
        if (file.type.startsWith('image/')) {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => resolve(null)
          reader.readAsDataURL(file)
        } else {
          resolve(null)
        }
      })

      // Estimate tokens for the attachment
      let tokenEstimate = 0
      if (file.type.startsWith('image/')) {
        // Default estimate for images (will be refined by backend)
        tokenEstimate = 765 // Default for 1024x1024 image
      } else if (file.type.startsWith('text/')) {
        tokenEstimate = Math.floor(file.size / 4)
      } else {
        tokenEstimate = Math.floor(file.size / 100)
      }

      newAttachments.push({
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
        size_bytes: file.size,
        data_url: dataUrl || undefined,
        token_estimate: tokenEstimate,
      })
    }

    const allAttachments = [...attachments, ...newAttachments]
    setAttachments(allAttachments)
    
    // Calculate total token estimate
    const totalTokens = allAttachments.reduce((sum, a) => sum + a.token_estimate, 0)
    setAttachmentTokens(totalTokens)

    // Clear the file input
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // Remove attachment
  const removeAttachment = (index: number) => {
    const newAttachments = attachments.filter((_, i) => i !== index)
    setAttachments(newAttachments)
    const totalTokens = newAttachments.reduce((sum, a) => sum + a.token_estimate, 0)
    setAttachmentTokens(totalTokens)
  }

  // Format file size
  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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
    const messageAttachments = attachments.length > 0 ? [...attachments] : undefined
    setInput('')
    setAttachments([])
    setAttachmentTokens(0)
    setError(null)
    localStorage.removeItem(STORAGE_KEYS.DRAFT + session.id)
    setIsLoading(true)
    setLiveTrace(null)  // Reset live trace

    // Optimistically add user message
    const tempUserMessage: SessionMessage = {
      id: 'temp-' + Date.now(),
      role: 'user',
      content: messageContent,
      timestamp: new Date().toISOString(),
      attachments: messageAttachments,
    }
    
    // Add a temp assistant message for showing live trace
    const tempAssistantMessage: SessionMessage = {
      id: 'temp-assistant-' + Date.now(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
    }
    
    setCurrentSession(prev => prev ? {
      ...prev,
      messages: [...prev.messages, tempUserMessage, tempAssistantMessage]
    } : null)

    if (useStreaming) {
      // Use streaming API
      const sessionId = session.id
      const startTime = Date.now()
      const accumulated = {
        orchestrator_steps: [] as Array<{ phase: string; reasoning: string; output: string; duration_ms: number; tokens: number }>,
        worker_steps: [] as Array<{ task_id: string; task_type: string; tool: string; duration_ms: number; success: boolean; documents: Array<{ title: string; score: number; excerpt: string }> }>,
        stats: { total_tokens: 0, orchestrator_tokens: 0, worker_tokens: 0, cost_usd: 0 },
        startTime,
        currentPhase: 'starting'
      }
      
      streamAbortRef.current = sessionsApi.sendMessageStream(
        sessionId,
        messageContent,
        { attachments: messageAttachments, agent_mode: agentMode },
        {
          onStart: () => {
            // Reset accumulated data
            accumulated.orchestrator_steps = []
            accumulated.worker_steps = []
            accumulated.currentPhase = 'starting'
            setLiveTrace({ ...accumulated })
          },
          onOrchestratorStep: (step) => {
            accumulated.orchestrator_steps.push(step)
            const stepTokens = step.tokens || 0
            accumulated.stats.orchestrator_tokens += stepTokens
            accumulated.stats.total_tokens += stepTokens
            accumulated.currentPhase = step.phase
            setLiveTrace({ ...accumulated })
          },
          onWorkerStep: (step) => {
            accumulated.worker_steps.push(step as typeof accumulated.worker_steps[0])
            accumulated.currentPhase = 'executing'
            setLiveTrace({ ...accumulated })
          },
          onResponse: (response) => {
            // Debug log to see what's coming from the backend
            console.log('Streaming response received:', response)
            
            // Update with final response
            const assistantMessage: SessionMessage = {
              id: 'msg-' + Date.now(),
              role: 'assistant',
              content: response.content || '(No response content received)',
              timestamp: new Date().toISOString(),
              sources: response.sources,
              stats: {
                input_tokens: response.stats?.orchestrator_tokens || 0,
                output_tokens: response.stats?.worker_tokens || 0,
                total_tokens: response.stats?.total_tokens || 0,
                cost_usd: response.stats?.cost_usd || 0,
                tokens_per_second: response.stats?.tokens_per_second || 0,
                latency_ms: response.stats?.latency_ms || 0,
              },
              agent_trace: response.trace,
            }
            
            setCurrentSession(prev => {
              if (!prev) return null
              const messages = prev.messages.filter(m => !m.id.startsWith('temp-'))
              return {
                ...prev,
                messages: [...messages, { ...tempUserMessage, id: 'msg-user-' + Date.now() }, assistantMessage],
              }
            })
            
            setLiveTrace(null)
          },
          onError: (error) => {
            setError(`Error: ${error}`)
            setCurrentSession(prev => prev ? {
              ...prev,
              messages: prev.messages.filter(m => !m.id.startsWith('temp-'))
            } : null)
            setLiveTrace(null)
          },
          onDone: () => {
            setIsLoading(false)
            streamAbortRef.current = null
          },
        }
      )
    } else {
      // Use regular API (non-streaming)
      try {
        const response = await sessionsApi.sendMessage(session.id, messageContent, {
          attachments: messageAttachments,
          agent_mode: agentMode,
        })
        
        // Update session with real messages and title if provided
        setCurrentSession(prev => {
          if (!prev) return null
          const messages = prev.messages.filter(m => !m.id.startsWith('temp-'))
          return {
            ...prev,
            messages: [...messages, response.user_message, response.assistant_message],
            stats: response.session_stats,
            title: response.title || prev.title,
          }
        })

        // Update sessions list with title if changed
        setSessions(prev => prev.map(s => 
          s.id === session!.id 
            ? { 
                ...s, 
                updated_at: new Date().toISOString(), 
                stats: response.session_stats,
                title: response.title || s.title
              }
            : s
        ))
      } catch (err: unknown) {
        console.error('Failed to send message:', err)
        
        let errorMessage: string
        if (err instanceof ApiError) {
          errorMessage = err.getUserMessage(user?.is_admin ?? false)
        } else if (err instanceof Error) {
          errorMessage = user?.is_admin 
            ? `Error: ${err.message}`
            : 'Failed to send message. Please try again.'
        } else {
          errorMessage = 'An unexpected error occurred. Please try again.'
        }
        
        setError(errorMessage)
        setCurrentSession(prev => prev ? {
          ...prev,
          messages: prev.messages.filter(m => !m.id.startsWith('temp-'))
        } : null)
      } finally {
        setIsLoading(false)
      }
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

              {/* Agent Mode Selector */}
              <div className="relative">
                <button
                  onClick={() => setShowAgentModeSelector(!showAgentModeSelector)}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg ${AGENT_MODES[agentMode].color} hover:opacity-80 text-sm transition-all`}
                >
                  <span>{AGENT_MODES[agentMode].icon}</span>
                  <span>{AGENT_MODES[agentMode].label}</span>
                  <ChevronDownIcon className="h-4 w-4" />
                </button>
                {showAgentModeSelector && (
                  <div className="absolute right-0 mt-1 w-80 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-surface-variant dark:border-gray-600 z-50">
                    <div className="p-2">
                      <div className="text-xs font-medium text-secondary dark:text-gray-400 px-2 py-1 uppercase flex items-center gap-1">
                        Agent Mode
                        <div className="group relative inline-block">
                          <InformationCircleIcon className="h-3.5 w-3.5 cursor-help" />
                          <div className="absolute left-0 bottom-full mb-2 w-64 p-2 bg-gray-900 text-white text-[10px] rounded-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                            Controls how the AI processes your question. Different modes trade off between speed and thoroughness.
                          </div>
                        </div>
                      </div>
                      {(Object.entries(AGENT_MODES) as [keyof typeof AGENT_MODES, typeof AGENT_MODES[keyof typeof AGENT_MODES]][]).map(([mode, config]) => (
                        <button
                          key={mode}
                          onClick={() => {
                            setAgentMode(mode)
                            setShowAgentModeSelector(false)
                          }}
                          className={`w-full flex flex-col px-3 py-2 rounded-lg text-left hover:bg-surface-variant dark:hover:bg-gray-700 ${
                            agentMode === mode ? 'bg-primary-50 dark:bg-primary-900/30' : ''
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <span>{config.icon}</span>
                            <span className="text-sm font-medium dark:text-gray-200">{config.label}</span>
                            {agentMode === mode && (
                              <CheckCircleIcon className="h-4 w-4 text-primary ml-auto" />
                            )}
                          </div>
                          <p className="text-xs text-secondary dark:text-gray-400 mt-1 ml-6">
                            {config.description}
                          </p>
                        </button>
                      ))}
                      {/* Detailed explanation */}
                      <div className="mt-2 mx-2 p-2 bg-surface-variant dark:bg-gray-700 rounded-lg">
                        <div className="flex items-start gap-2">
                          <InformationCircleIcon className="h-4 w-4 text-primary flex-shrink-0 mt-0.5" />
                          <div className="text-[10px] text-secondary dark:text-gray-400">
                            <strong className="text-primary-700 dark:text-primary-300">Current: {AGENT_MODES[agentMode].label}</strong>
                            <p className="mt-1">{AGENT_MODES[agentMode].details}</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Settings Info Button */}
              <div className="relative">
                <button
                  onClick={() => setShowSettingsInfo(!showSettingsInfo)}
                  className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 text-secondary hover:text-primary transition-colors"
                  title="View hidden settings that affect behavior"
                >
                  <Cog6ToothIcon className="h-5 w-5" />
                </button>
                {showSettingsInfo && (
                  <div className="absolute right-0 mt-1 w-96 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-surface-variant dark:border-gray-600 z-50">
                    <div className="p-3">
                      <div className="flex items-center gap-2 mb-3">
                        <Cog6ToothIcon className="h-4 w-4 text-primary" />
                        <span className="text-sm font-medium dark:text-gray-200">Settings That Affect Behavior</span>
                      </div>
                      <div className="space-y-3 text-xs">
                        {/* Agent Mode */}
                        <div className="p-2 bg-surface-variant dark:bg-gray-700 rounded-lg">
                          <div className="flex items-center gap-2 font-medium text-primary-700 dark:text-primary-300">
                            <span>{AGENT_MODES[agentMode].icon}</span>
                            Agent Mode: {AGENT_MODES[agentMode].label}
                          </div>
                          <p className="text-secondary dark:text-gray-400 mt-1">
                            {AGENT_MODES[agentMode].description}
                          </p>
                        </div>
                        
                        {/* Auto Mode Threshold */}
                        <div className="p-2 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg border border-yellow-200 dark:border-yellow-800">
                          <div className="flex items-center gap-2 font-medium text-yellow-700 dark:text-yellow-300">
                            <InformationCircleIcon className="h-4 w-4" />
                            Auto Mode Threshold
                          </div>
                          <p className="text-yellow-600 dark:text-yellow-400 mt-1">
                            In AUTO mode, queries &lt;20 characters use FAST mode (no orchestrator).
                            Short questions like "what is X?" skip the planning phase.
                          </p>
                        </div>
                        
                        {/* Search Type */}
                        <div className="p-2 bg-surface-variant dark:bg-gray-700 rounded-lg">
                          <div className="flex items-center gap-2 font-medium text-primary-700 dark:text-primary-300">
                            <MagnifyingGlassIcon className="h-4 w-4" />
                            Search Type: Hybrid
                          </div>
                          <p className="text-secondary dark:text-gray-400 mt-1">
                            Combines vector (semantic) + text (keyword) search with RRF fusion.
                          </p>
                        </div>
                        
                        {/* Match Count */}
                        <div className="p-2 bg-surface-variant dark:bg-gray-700 rounded-lg">
                          <div className="flex items-center gap-2 font-medium text-primary-700 dark:text-primary-300">
                            <DocumentTextIcon className="h-4 w-4" />
                            Max Results: 10
                          </div>
                          <p className="text-secondary dark:text-gray-400 mt-1">
                            Maximum documents retrieved per search operation.
                          </p>
                        </div>
                        
                        {/* Model Info */}
                        <div className="p-2 bg-surface-variant dark:bg-gray-700 rounded-lg">
                          <div className="flex items-center gap-2 font-medium text-primary-700 dark:text-primary-300">
                            <CpuChipIcon className="h-4 w-4" />
                            Models
                          </div>
                          <div className="text-secondary dark:text-gray-400 mt-1 space-y-1">
                            <p><span className="font-medium">Chat:</span> {currentSession.model}</p>
                            <p><span className="font-medium">Orchestrator:</span> gpt-5.2 (planning/synthesis)</p>
                            <p><span className="font-medium">Worker:</span> gpt-4o-mini (search execution)</p>
                          </div>
                        </div>
                        
                        <p className="text-[10px] text-center text-secondary dark:text-gray-500 pt-2 border-t border-gray-200 dark:border-gray-700">
                          These settings affect how the AI processes your questions.
                          Use THINKING mode for complex queries to ensure thorough processing.
                        </p>
                      </div>
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
                    {liveTrace ? (
                      <div className="bg-surface dark:bg-gray-700/50 rounded-xl p-3 space-y-2">
                        {/* Live Agent Progress Header */}
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 text-xs text-primary-700 dark:text-primary-300">
                            <CpuChipIcon className="h-4 w-4 animate-pulse" />
                            <span className="font-medium capitalize">
                              {liveTrace.currentPhase === 'synthesize' ? 'Generating response...' : 
                               liveTrace.currentPhase === 'executing' ? 'Executing tasks...' :
                               liveTrace.currentPhase === 'evaluate' ? 'Evaluating results...' :
                               liveTrace.currentPhase === 'plan' ? 'Planning...' :
                               liveTrace.currentPhase === 'analyze' ? 'Analyzing...' :
                               'Starting...'}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-[10px] text-secondary dark:text-gray-400">
                            <span className="font-mono">{elapsedTime}s</span>
                            <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                          </div>
                        </div>
                        
                        {/* Orchestrator Steps */}
                        {liveTrace.orchestrator_steps.length > 0 && (
                          <div className="space-y-1">
                            <div className="text-[10px] text-secondary dark:text-gray-400">
                              🧠 Orchestrator: {liveTrace.orchestrator_steps.length} step(s)
                            </div>
                            {liveTrace.orchestrator_steps.slice(-2).map((step, idx) => (
                              <div key={idx} className="text-[10px] text-gray-600 dark:text-gray-400 pl-4 border-l-2 border-purple-300 dark:border-purple-700">
                                <span className="capitalize font-medium">{step.phase}</span>
                                <span className="text-secondary dark:text-gray-500 ml-2">{step.duration_ms.toFixed(0)}ms</span>
                                {step.tokens > 0 && <span className="text-secondary dark:text-gray-500 ml-2">{step.tokens} tok</span>}
                                {step.reasoning && (
                                  <p className="line-clamp-1 text-gray-500 dark:text-gray-500">{step.reasoning}</p>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        
                        {/* Worker Steps */}
                        {liveTrace.worker_steps.length > 0 && (
                          <div className="space-y-1">
                            <div className="text-[10px] text-secondary dark:text-gray-400">
                              ⚡ Workers: {liveTrace.worker_steps.length} task(s)
                            </div>
                            {liveTrace.worker_steps.slice(-3).map((step, idx) => (
                              <div key={idx} className="text-[10px] text-gray-600 dark:text-gray-400 pl-4 border-l-2 border-blue-300 dark:border-blue-700">
                                <span className="font-medium">{step.task_type}</span>
                                <span className={`ml-2 ${step.success ? 'text-green-600' : 'text-red-600'}`}>
                                  {step.documents.length} docs
                                </span>
                                <span className="text-secondary dark:text-gray-500 ml-2">{step.duration_ms.toFixed(0)}ms</span>
                              </div>
                            ))}
                          </div>
                        )}
                        
                        {/* Live Stats */}
                        <div className="flex items-center gap-3 text-[10px] text-secondary dark:text-gray-500 pt-1 border-t border-gray-200 dark:border-gray-600">
                          <span>{liveTrace.stats.total_tokens.toLocaleString()} tokens</span>
                          <span>🧠 {liveTrace.stats.orchestrator_tokens.toLocaleString()}</span>
                          <span>⚡ {liveTrace.stats.worker_tokens.toLocaleString()}</span>
                        </div>
                      </div>
                    ) : (
                      <div className="flex space-x-2 py-4">
                        <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '0ms' }} />
                        <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '150ms' }} />
                        <div className="h-2 w-2 animate-bounce rounded-full bg-primary" style={{ animationDelay: '300ms' }} />
                      </div>
                    )}
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
              {/* Attachment Preview */}
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-2 p-2 bg-surface-variant dark:bg-gray-700 rounded-xl">
                  {attachments.map((attachment, index) => (
                    <div
                      key={index}
                      className="relative group flex items-center gap-2 px-2 py-1 bg-white dark:bg-gray-600 rounded-lg border border-surface dark:border-gray-500"
                    >
                      {attachment.data_url && attachment.content_type.startsWith('image/') ? (
                        <img
                          src={attachment.data_url}
                          alt={attachment.filename}
                          className="w-10 h-10 object-cover rounded"
                        />
                      ) : (
                        <div className="w-10 h-10 flex items-center justify-center bg-surface-variant dark:bg-gray-500 rounded">
                          <DocumentTextIcon className="h-5 w-5 text-secondary" />
                        </div>
                      )}
                      <div className="flex flex-col">
                        <span className="text-xs text-primary-900 dark:text-gray-200 truncate max-w-[100px]">
                          {attachment.filename}
                        </span>
                        <span className="text-[10px] text-secondary dark:text-gray-400">
                          {formatFileSize(attachment.size_bytes)} • ~{attachment.token_estimate.toLocaleString()} tokens
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeAttachment(index)}
                        className="absolute -top-1 -right-1 w-4 h-4 flex items-center justify-center bg-red-500 text-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <XMarkIcon className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                  {attachmentTokens > 0 && (
                    <div className="flex items-center px-2 text-xs text-secondary dark:text-gray-400">
                      Total: ~{attachmentTokens.toLocaleString()} tokens
                    </div>
                  )}
                </div>
              )}
              <div className="relative flex items-end gap-2 bg-surface-variant dark:bg-gray-700 rounded-2xl p-2">
                {/* File input (hidden) */}
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept="image/*,.pdf,.txt,.md,.doc,.docx"
                  onChange={handleFileSelect}
                  className="hidden"
                />
                {/* Attachment button */}
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex h-10 w-10 items-center justify-center rounded-xl hover:bg-surface dark:hover:bg-gray-600 text-secondary hover:text-primary transition-colors flex-shrink-0"
                  title="Attach files"
                >
                  <PaperClipIcon className="h-5 w-5" />
                </button>
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
                Press Enter to send, Shift+Enter for new line{attachments.length > 0 ? ` • ${attachments.length} file(s) attached` : ''}
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
            RecallHub Assistant
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
  const [loadingDocs, setLoadingDocs] = useState(false)
  const [showThinking, setShowThinking] = useState(false)

  // Lookup document IDs for sources - load all at once
  useEffect(() => {
    if (message.sources && message.sources.length > 0 && !loadingDocs) {
      setLoadingDocs(true)
      const loadDocIds = async () => {
        const newIds: Record<string, string> = {}
        await Promise.all(
          message.sources!.map(async (source) => {
            try {
              const doc = await documentsApi.findBySource(source.source)
              if (doc) {
                newIds[source.source] = doc.id
              }
            } catch (err) {
              // Ignore lookup errors
            }
          })
        )
        setDocumentIds(prev => ({ ...prev, ...newIds }))
        setLoadingDocs(false)
      }
      loadDocIds()
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
          {/* Attachments */}
          {message.attachments && message.attachments.length > 0 && (
            <div className={`flex flex-wrap gap-2 mb-2 ${isUser ? 'justify-end' : ''}`}>
              {message.attachments.map((attachment, idx) => (
                <div key={idx} className="relative">
                  {attachment.data_url && attachment.content_type?.startsWith('image/') ? (
                    <img
                      src={attachment.data_url}
                      alt={attachment.filename}
                      className="max-w-[200px] max-h-[150px] object-cover rounded-lg border border-white/20"
                    />
                  ) : (
                    <div className="flex items-center gap-2 px-2 py-1 bg-white/10 rounded-lg">
                      <DocumentTextIcon className="h-4 w-4" />
                      <span className="text-xs truncate max-w-[120px]">{attachment.filename}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="prose prose-sm max-w-none dark:prose-invert">
              {message.content ? (
                <ReactMarkdown>{message.content}</ReactMarkdown>
              ) : (
                <p className="text-gray-500 italic">Loading response...</p>
              )}
            </div>
          )}
        </div>
        
        {/* Thinking Panel - Shows search and tool operations */}
        {message.thinking && ((message.thinking.search?.operations?.length ?? 0) > 0 || (message.thinking.tool_calls?.length ?? 0) > 0) && (
          <div className="mt-2">
            <button
              onClick={() => setShowThinking(!showThinking)}
              className="flex items-center gap-1 text-xs text-secondary dark:text-gray-400 hover:text-primary dark:hover:text-primary-300 transition-colors"
            >
              {showThinking ? (
                <ChevronDownIcon className="h-3 w-3" />
              ) : (
                <ChevronRightIcon className="h-3 w-3" />
              )}
              <CpuChipIcon className="h-3 w-3" />
              <span>
                Agent Operations
                {(message.thinking.search?.operations?.length ?? 0) > 0 && ` (${message.thinking.search?.operations?.length} search)`}
                {(message.thinking.tool_calls?.length ?? 0) > 0 && ` (${message.thinking.tool_calls?.length} tool)`}
              </span>
              <span className="text-[10px] opacity-60">
                {message.thinking.total_duration_ms.toFixed(0)}ms
              </span>
            </button>
            
            {showThinking && (
              <div className="mt-2 space-y-2 pl-4 border-l-2 border-primary-200 dark:border-primary-800">
                {/* Search Operations */}
                {message.thinking.search?.operations?.map((op, idx) => (
                  <div
                    key={`search-${idx}`}
                    className="p-2 rounded-lg bg-surface dark:bg-gray-800/50 text-xs space-y-2"
                  >
                    {/* Header with type and index */}
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        op.index_type === 'vector' 
                          ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300' 
                          : 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300'
                      }`}>
                        {op.index_type === 'vector' ? 'Vector Search' : 'Text Search'}
                      </span>
                      <span className="text-secondary dark:text-gray-500">
                        {op.index_name}
                      </span>
                    </div>
                    
                    {/* Query sent to search */}
                    <div className="bg-gray-100 dark:bg-gray-700/50 rounded px-2 py-1">
                      <span className="text-[10px] text-secondary dark:text-gray-500 font-medium">Query: </span>
                      <span className="text-primary-900 dark:text-gray-200">
                        {op.query.length > 100 ? op.query.substring(0, 100) + '...' : op.query}
                      </span>
                    </div>
                    
                    {/* Stats row */}
                    <div className="flex items-center gap-3 text-secondary dark:text-gray-400">
                      <span className="flex items-center gap-1">
                        <MagnifyingGlassIcon className="h-3 w-3" />
                        {op.results_count} results
                      </span>
                      <span className="flex items-center gap-1">
                        <ClockIcon className="h-3 w-3" />
                        {op.duration_ms.toFixed(1)}ms
                      </span>
                      {op.top_score !== null && (
                        <span className="flex items-center gap-1">
                          <BoltIcon className="h-3 w-3" />
                          {(op.top_score * 100).toFixed(1)}% top score
                        </span>
                      )}
                    </div>
                    
                    {/* Top results excerpts */}
                    {op.top_results && op.top_results.length > 0 && (
                      <div className="space-y-1 border-t border-gray-200 dark:border-gray-700 pt-1 mt-1">
                        <span className="text-[10px] text-secondary dark:text-gray-500 font-medium">Top Results:</span>
                        {op.top_results.slice(0, 3).map((result, ridx) => (
                          <div key={ridx} className="bg-gray-50 dark:bg-gray-800 rounded px-2 py-1">
                            <div className="flex items-center justify-between">
                              <span className="text-[10px] font-medium text-primary-700 dark:text-primary-300 truncate max-w-[150px]">
                                {result.title}
                              </span>
                              {result.score !== null && (
                                <span className="text-[9px] text-secondary dark:text-gray-500">
                                  {(result.score * 100).toFixed(0)}%
                                </span>
                              )}
                            </div>
                            <p className="text-[10px] text-gray-600 dark:text-gray-400 line-clamp-2 mt-0.5">
                              {result.excerpt}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                
                {/* Tool Operations */}
                {message.thinking.tool_calls?.map((tool, idx) => (
                  <div
                    key={`tool-${idx}`}
                    className="p-2 rounded-lg bg-surface dark:bg-gray-800/50 text-xs space-y-2"
                  >
                    {/* Header with tool name and status */}
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        tool.success
                          ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300'
                          : 'bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300'
                      }`}>
                        <span className="flex items-center gap-1">
                          {tool.tool_name === 'browse_web' ? (
                            <GlobeAltIcon className="h-3 w-3" />
                          ) : (
                            <CpuChipIcon className="h-3 w-3" />
                          )}
                          {tool.tool_name}
                        </span>
                      </span>
                      {tool.success ? (
                        <CheckCircleIcon className="h-3 w-3 text-green-500" />
                      ) : (
                        <XCircleIcon className="h-3 w-3 text-red-500" />
                      )}
                      <span className="flex items-center gap-1 text-secondary dark:text-gray-500 ml-auto">
                        <ClockIcon className="h-3 w-3" />
                        {tool.duration_ms.toFixed(0)}ms
                      </span>
                    </div>
                    
                    {/* Tool Input Parameters */}
                    {tool.tool_input && Object.keys(tool.tool_input).length > 0 && (
                      <div className="bg-gray-100 dark:bg-gray-700/50 rounded px-2 py-1">
                        <span className="text-[10px] text-secondary dark:text-gray-500 font-medium">Input: </span>
                        <span className="text-primary-900 dark:text-gray-200 text-[10px]">
                          {(() => {
                            const inputStr = JSON.stringify(tool.tool_input)
                            // Show first 200 chars or full if under limit
                            return inputStr.length > 200 ? inputStr.substring(0, 200) + '...' : inputStr
                          })()}
                        </span>
                      </div>
                    )}
                    
                    {/* Result summary or error */}
                    {(tool.result_summary || tool.error) && (
                      <div className={`rounded px-2 py-1 ${
                        tool.error 
                          ? 'bg-red-50 dark:bg-red-900/20' 
                          : 'bg-green-50 dark:bg-green-900/20'
                      }`}>
                        <span className="text-[10px] text-secondary dark:text-gray-500 font-medium">
                          {tool.error ? 'Error: ' : 'Result: '}
                        </span>
                        <span className={`text-[10px] ${
                          tool.error 
                            ? 'text-red-700 dark:text-red-300' 
                            : 'text-green-700 dark:text-green-300'
                        }`}>
                          {tool.error || (tool.result_summary.length > 200 
                            ? tool.result_summary.substring(0, 200) + '...' 
                            : tool.result_summary)}
                        </span>
                      </div>
                    )}
                  </div>
                ))}
                
                <div className="text-[10px] text-secondary dark:text-gray-500">
                  Total: {message.thinking.search?.total_results ?? 0} search results in {message.thinking.total_duration_ms.toFixed(0)}ms
                </div>
              </div>
            )}
          </div>
        )}
        
        {/* Federated Agent Panel - New orchestrator-worker trace */}
        {message.agent_trace && (
          <FederatedAgentPanel trace={message.agent_trace} />
        )}
        
        {/* Sources */}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <span className="text-[9px] text-secondary dark:text-gray-500 font-medium">
              {message.sources.length} matches:
            </span>
            {message.sources.map((source, idx) => {
              const docId = documentIds[source.source]
              const badge = (
                <span
                  key={idx}
                  className={`inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] bg-primary-50 dark:bg-gray-600 ${docId ? 'hover:bg-primary-100 dark:hover:bg-gray-500 cursor-pointer' : 'opacity-60'} transition-colors`}
                  title={source.excerpt}
                >
                  <DocumentTextIcon className="h-2 w-2 text-primary-500 flex-shrink-0" />
                  <span className="text-primary-600 dark:text-primary-300 truncate max-w-[80px]">{source.title}</span>
                  <span className="text-secondary dark:text-gray-400 ml-0.5">{Math.round(source.relevance * 100)}%</span>
                </span>
              )
              
              return docId ? (
                <Link key={idx} to={`/documents/${docId}`} className="no-underline">
                  {badge}
                </Link>
              ) : (
                badge
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
