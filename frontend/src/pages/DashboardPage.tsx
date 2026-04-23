import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  PaperAirplaneIcon,
  PaperClipIcon,
  XMarkIcon,
  DocumentTextIcon,
  SparklesIcon,
  ArrowPathIcon,
  MagnifyingGlassIcon,
  ChevronDownIcon,
  ClockIcon,
} from '@heroicons/react/24/outline'
import {
  sessionsApi,
  AttachmentInfo,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { useChatSidebar } from '../contexts/ChatSidebarContext'
import { useUserPreferences } from '../contexts/UserPreferencesContext'
import { useLocalizedNavigate } from '../components/LocalizedLink'
import { useLocalStorage, STORAGE_KEYS } from '../hooks/useLocalStorage'

// Agent mode configuration (same as ChatPageNew) – only non-translatable properties
const AGENT_MODES_CONFIG = {
  auto: {
    icon: '🔄',
    color: 'bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300',
  },
  thinking: {
    icon: '🧠',
    color: 'bg-secondary-100 dark:bg-secondary-900/40 text-secondary-700 dark:text-secondary-300',
  },
  fast: {
    icon: '⚡',
    color: 'bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300',
  },
} as const

export default function DashboardPage() {
  const { user, isLoading: authLoading } = useAuth()
  const { setSessions, setCurrentSession, setPendingMessage } = useChatSidebar()
  const { preferences } = useUserPreferences()
  const { t } = useTranslation()
  const navigate = useLocalizedNavigate()

  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState<AttachmentInfo[]>([])
  const [attachmentTokens, setAttachmentTokens] = useState<number>(0)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [showAgentModeSelector, setShowAgentModeSelector] = useState(false)
  const [agentMode, setAgentMode] = useLocalStorage<'auto' | 'thinking' | 'fast'>(STORAGE_KEYS.CHAT_AGENT_MODE, 'auto')

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const agentModeRef = useRef<HTMLDivElement>(null)

  // Focus input on mount
  useEffect(() => {
    if (!authLoading) {
      inputRef.current?.focus()
    }
  }, [authLoading])

  // Close agent mode dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (agentModeRef.current && !agentModeRef.current.contains(e.target as Node)) {
        setShowAgentModeSelector(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Auto-resize textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const ta = e.target
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
  }

  // Handle key down for Enter to submit
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  // Handle file attachment
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return

    const newAttachments: AttachmentInfo[] = []
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      if (file.size > 20 * 1024 * 1024) continue

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

      let tokenEstimate = 0
      if (file.type.startsWith('image/')) tokenEstimate = 765
      else if (file.type.startsWith('text/')) tokenEstimate = Math.floor(file.size / 4)
      else tokenEstimate = Math.floor(file.size / 100)

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
    setAttachmentTokens(allAttachments.reduce((sum, a) => sum + a.token_estimate, 0))
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const removeAttachment = (index: number) => {
    const newAttachments = attachments.filter((_, i) => i !== index)
    setAttachments(newAttachments)
    setAttachmentTokens(newAttachments.reduce((sum, a) => sum + a.token_estimate, 0))
  }

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  // Submit: create session, set pending message, navigate to chat
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isSubmitting) return

    setIsSubmitting(true)
    try {
      // Create a new session with user's preferred model
      const createData: { model?: string } = {}
      if (preferences.defaultModel) {
        createData.model = preferences.defaultModel
      }
      const session = await sessionsApi.create(createData)
      setSessions((prev: any) => [session, ...prev])
      setCurrentSession(session)

      // Set pending message so ChatPageNew picks it up
      setPendingMessage(input.trim(), attachments.length > 0 ? [...attachments] : null)

      // Navigate to chat
      navigate('/chat')
    } catch (err) {
      console.error('Failed to create session:', err)
      setIsSubmitting(false)
    }
  }

  // Get time-based greeting
  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return t('dashboard.greeting.morning')
    if (hour < 17) return t('dashboard.greeting.afternoon')
    return t('dashboard.greeting.evening')
  }

  if (authLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  const currentMode = AGENT_MODES_CONFIG[agentMode]

  return (
    <div className="flex flex-col items-center justify-center h-full px-4">
      {/* Centered content */}
      <div className="w-full max-w-3xl flex flex-col items-center">
        {/* Greeting */}
        <div className="mb-8 text-center">
          <h1 className="text-3xl sm:text-4xl font-display font-semibold text-primary-900 dark:text-gray-100 mb-2">
            {getGreeting()}, {user?.name?.split(' ')[0] || 'there'}
          </h1>
          <p className="text-lg text-primary-700 dark:text-gray-400">
            {t('dashboard.whereToBegin')}
          </p>
        </div>

        {/* Chat Input Area */}
        <div className="w-full">
          <form onSubmit={handleSubmit}>
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
                    {t('chatPage.totalTokens', { tokens: attachmentTokens.toLocaleString() })}
                  </div>
                )}
              </div>
            )}

            {/* Main input container */}
            <div className="relative flex flex-col bg-surface dark:bg-gray-800 rounded-2xl premium-input-surface">
              <div className="flex items-end gap-2 p-3">
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
                  className="flex h-10 w-10 items-center justify-center rounded-xl hover:bg-surface-variant dark:hover:bg-gray-700 text-secondary hover:text-primary transition-colors flex-shrink-0"
                  title={t('chatPage.attachFiles')}
                >
                  <PaperClipIcon className="h-5 w-5" />
                </button>

                {/* Textarea */}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder={t('dashboard.askAnything')}
                  rows={1}
                  className="flex-1 resize-none bg-transparent px-3 py-2 text-primary-900 dark:text-gray-100 placeholder:text-secondary dark:placeholder:text-gray-500 focus:outline-none max-h-52"
                  disabled={isSubmitting}
                />

                {/* Send button */}
                <button
                  type="submit"
                  disabled={!input.trim() || isSubmitting}
                  className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-white transition-all hover:bg-primary-700 disabled:bg-secondary disabled:cursor-not-allowed flex-shrink-0"
                >
                  {isSubmitting ? (
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                  ) : (
                    <PaperAirplaneIcon className="h-5 w-5" />
                  )}
                </button>
              </div>

              {/* Bottom bar with agent mode selector */}
              <div className="flex items-center gap-2 px-3 pb-2 pt-0">
                {/* Agent Mode Selector */}
                <div ref={agentModeRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setShowAgentModeSelector(!showAgentModeSelector)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${currentMode.color} hover:opacity-80`}
                  >
                    <span>{currentMode.icon}</span>
                    <span>{t(`chatPage.modes.${agentMode}.label`)}</span>
                    <ChevronDownIcon className="h-3 w-3" />
                  </button>

                  {showAgentModeSelector && (
                    <div className="absolute bottom-full left-0 mb-2 w-64 bg-surface dark:bg-gray-800 rounded-xl shadow-elevation-3 border border-surface-variant dark:border-gray-700 py-1 z-50">
                      {(Object.keys(AGENT_MODES_CONFIG) as Array<'auto' | 'thinking' | 'fast'>).map((key) => (
                        <button
                          key={key}
                          type="button"
                          onClick={() => {
                            setAgentMode(key)
                            setShowAgentModeSelector(false)
                          }}
                          className={`w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors ${
                            agentMode === key ? 'bg-surface-variant dark:bg-gray-700' : ''
                          }`}
                        >
                          <span className="text-lg">{AGENT_MODES_CONFIG[key].icon}</span>
                          <div>
                            <p className="text-sm font-medium text-primary-900 dark:text-gray-100">{t(`chatPage.modes.${key}.label`)}</p>
                            <p className="text-xs text-secondary dark:text-gray-400">{t(`chatPage.modes.${key}.description`)}</p>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <span className="text-xs text-secondary dark:text-gray-500">
                  Enter ↵ {t('common.send')} · Shift+Enter {t('common.newLine')}
                </span>
              </div>
            </div>
          </form>
        </div>

        {/* Suggestion chips */}
        <div className="flex flex-wrap gap-2 mt-6 justify-center">
          {[
            { icon: <MagnifyingGlassIcon className="h-4 w-4" />, text: t('dashboard.suggestions.summarize') },
            { icon: <DocumentTextIcon className="h-4 w-4" />, text: t('dashboard.suggestions.findDocument') },
            { icon: <SparklesIcon className="h-4 w-4" />, text: t('dashboard.suggestions.compare') },
            { icon: <ClockIcon className="h-4 w-4" />, text: t('dashboard.suggestions.explain') },
          ].map((chip, i) => (
            <button
              key={i}
              onClick={() => {
                setInput(chip.text)
                inputRef.current?.focus()
              }}
              className="flex items-center gap-2 px-4 py-2 rounded-full bg-surface dark:bg-gray-800 text-sm text-primary-700 dark:text-gray-400 hover:text-primary-900 dark:hover:text-gray-100 premium-chip"
            >
              {chip.icon}
              {chip.text}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
