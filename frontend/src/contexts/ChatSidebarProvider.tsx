import { useState, useCallback, useEffect, ReactNode } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  sessionsApi,
  systemApi,
  AttachmentInfo,
  ChatSession,
  ChatFolder,
  LLMModel,
  ModelPricingInfo,
  ApiError,
} from '../api/client'
import { useAuth } from './AuthContext'
import { useUserPreferences } from './UserPreferencesContext'
import { ChatSidebarContext, ChatSidebarContextType, STORAGE_KEYS } from './ChatSidebarContext'

export function ChatSidebarProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, isLoading: isAuthLoading } = useAuth()
  const { preferences } = useUserPreferences()

  // State
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [folders, setFolders] = useState<ChatFolder[]>([])
  const [currentSession, setCurrentSession] = useState<ChatSession | null>(null)
  const [isSidebarLoading, setIsSidebarLoading] = useState(true)
  const [models, setModels] = useState<LLMModel[]>([])
  const [modelPricing, setModelPricing] = useState<ModelPricingInfo[]>([])
  // Collapsed folders start from the persisted set (lazy initializer instead of a mount effect)
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(() => {
    const saved = localStorage.getItem(STORAGE_KEYS.COLLAPSED_FOLDERS)
    return saved ? new Set<string>(JSON.parse(saved)) : new Set<string>()
  })
  const [editingTitle, setEditingTitle] = useState<string | null>(null)
  const [editingTitleValue, setEditingTitleValue] = useState('')
  const [showNewFolder, setShowNewFolder] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [contextMenu, setContextMenu] = useState<{ sessionId: string; x: number; y: number } | null>(null)
  // Multi-select state
  const [isSelectMode, setIsSelectMode] = useState(false)
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set())
  // Pending message for dashboard-to-chat transition
  const [pendingMessage, setPendingMessageState] = useState<string | null>(null)
  const [pendingAttachments, setPendingAttachmentsState] = useState<AttachmentInfo[] | null>(null)

  const setPendingMessage = useCallback((message: string | null, attachments?: AttachmentInfo[] | null) => {
    setPendingMessageState(message)
    setPendingAttachmentsState(attachments ?? null)
  }, [])

  // Save collapsed folders to localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.COLLAPSED_FOLDERS, JSON.stringify([...collapsedFolders]))
  }, [collapsedFolders])

  // Load sessions and folders
  const loadSessions = useCallback(async () => {
    try {
      setIsSidebarLoading(true)
      const response = await sessionsApi.list()
      setSessions(response.sessions)
      setFolders(response.folders)
    } catch (err) {
      // Log error details for debugging
      if (err instanceof ApiError) {
        console.error('Failed to load sessions:', err.getUserMessage(user?.is_admin ?? false))
        // Only log technical details in development
        if (import.meta.env.DEV && err.technicalDetails) {
          console.debug('Technical details:', err.technicalDetails)
        }
      } else {
        console.error('Failed to load sessions:', err)
      }
    } finally {
      setIsSidebarLoading(false)
    }
  }, [user?.is_admin])

  // Load models and pricing
  const loadModels = useCallback(async () => {
    try {
      const [modelsRes, pricingRes] = await Promise.all([
        systemApi.listLLMModels(),
        sessionsApi.getModelPricing(),
      ])
      setModels(modelsRes.models)
      setModelPricing(pricingRes.models)
    } catch (err) {
      // Log error details for debugging
      if (err instanceof ApiError) {
        console.error('Failed to load models:', err.getUserMessage(user?.is_admin ?? false))
      } else {
        console.error('Failed to load models:', err)
      }
      // Models are not critical - continue with empty list
    }
  }, [user?.is_admin])

  useEffect(() => {
    // Only load sessions after auth is done loading
    if (!isAuthLoading) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sidebar load once auth is known; loadSessions flips the loading flag before awaiting the API
      loadSessions()
      loadModels()
    }
  }, [loadSessions, loadModels, isAuthLoading, user?.id])

  // Session handlers compare against the id only, so they do not change identity on every session update
  const currentSessionId = currentSession?.id

  // Create new session
  const handleNewChat = useCallback(async (folderId?: string) => {
    try {
      const createData: { folder_id?: string; model?: string } = { folder_id: folderId }
      // Use user's preferred default model if set
      if (preferences.defaultModel) {
        createData.model = preferences.defaultModel
      }
      const session = await sessionsApi.create(createData)
      setSessions(prev => [session, ...prev])
      setCurrentSession(session)
      // Navigate to chat if not already there
      if (!location.pathname.startsWith('/chat')) {
        navigate('/chat')
      }
    } catch (err) {
      console.error('Failed to create session:', err)
    }
  }, [navigate, location.pathname, preferences.defaultModel])

  // Select session
  const handleSelectSession = useCallback(async (sessionId: string) => {
    try {
      const session = await sessionsApi.get(sessionId)
      setCurrentSession(session)
      // Navigate to chat if not already there
      if (!location.pathname.startsWith('/chat')) {
        navigate('/chat')
      }
    } catch (err) {
      console.error('Failed to load session:', err)
    }
  }, [navigate, location.pathname])

  // Delete session
  const handleDeleteSession = useCallback(async (sessionId: string) => {
    try {
      await sessionsApi.delete(sessionId)
      setSessions(prev => prev.filter(s => s.id !== sessionId))
      if (currentSessionId === sessionId) {
        setCurrentSession(null)
      }
      localStorage.removeItem(STORAGE_KEYS.DRAFT + sessionId)
    } catch (err) {
      console.error('Failed to delete session:', err)
    }
    setContextMenu(null)
  }, [currentSessionId])

  // Pin/unpin session
  const handleTogglePin = useCallback(async (sessionId: string, isPinned: boolean) => {
    try {
      await sessionsApi.update(sessionId, { is_pinned: !isPinned })
      setSessions(prev => prev.map(s =>
        s.id === sessionId ? { ...s, is_pinned: !isPinned } : s
      ))
      if (currentSessionId === sessionId) {
        setCurrentSession(prev => prev ? { ...prev, is_pinned: !isPinned } : null)
      }
    } catch (err) {
      console.error('Failed to toggle pin:', err)
    }
    setContextMenu(null)
  }, [currentSessionId])

  // Update session title
  const handleUpdateTitle = useCallback(async (sessionId: string) => {
    if (!editingTitleValue.trim()) {
      setEditingTitle(null)
      return
    }
    try {
      await sessionsApi.update(sessionId, { title: editingTitleValue.trim() })
      setSessions(prev => prev.map(s =>
        s.id === sessionId ? { ...s, title: editingTitleValue.trim() } : s
      ))
      if (currentSessionId === sessionId) {
        setCurrentSession(prev => prev ? { ...prev, title: editingTitleValue.trim() } : null)
      }
    } catch (err) {
      console.error('Failed to update title:', err)
    }
    setEditingTitle(null)
  }, [editingTitleValue, currentSessionId])

  // Create folder
  const handleCreateFolder = useCallback(async () => {
    if (!newFolderName.trim()) return
    try {
      const folder = await sessionsApi.createFolder(newFolderName.trim())
      setFolders(prev => [...prev, folder])
      setNewFolderName('')
      setShowNewFolder(false)
    } catch (err) {
      console.error('Failed to create folder:', err)
    }
  }, [newFolderName])

  // Delete folder
  const handleDeleteFolder = useCallback(async (folderId: string) => {
    try {
      await sessionsApi.deleteFolder(folderId)
      setFolders(prev => prev.filter(f => f.id !== folderId))
      // Sessions in the folder will be moved to no folder
      loadSessions()
    } catch (err) {
      console.error('Failed to delete folder:', err)
    }
  }, [loadSessions])

  // Toggle folder collapse
  const toggleFolder = useCallback((folderId: string) => {
    setCollapsedFolders(prev => {
      const next = new Set(prev)
      if (next.has(folderId)) {
        next.delete(folderId)
      } else {
        next.add(folderId)
      }
      return next
    })
  }, [])

  // Get pricing for a model
  const getPricing = useCallback((modelId: string) => {
    const pricing = modelPricing.find(p => modelId.startsWith(p.id))
    return pricing?.pricing || { input: 2.50, output: 10.00 }
  }, [modelPricing])

  // Multi-select actions
  const toggleSelectMode = useCallback(() => {
    setIsSelectMode(prev => {
      if (prev) {
        // Exiting select mode - clear selection
        setSelectedSessions(new Set())
      }
      return !prev
    })
  }, [])

  const toggleSessionSelection = useCallback((sessionId: string) => {
    setSelectedSessions(prev => {
      const next = new Set(prev)
      if (next.has(sessionId)) {
        next.delete(sessionId)
      } else {
        next.add(sessionId)
      }
      return next
    })
  }, [])

  const selectAllSessions = useCallback(() => {
    setSelectedSessions(new Set(sessions.map(s => s.id)))
  }, [sessions])

  const clearSelection = useCallback(() => {
    setSelectedSessions(new Set())
  }, [])

  const archiveSelected = useCallback(async () => {
    if (selectedSessions.size === 0) return
    try {
      const ids = Array.from(selectedSessions)
      await sessionsApi.archiveSessions(ids)
      // Remove archived sessions from list
      setSessions(prev => prev.filter(s => !selectedSessions.has(s.id)))
      // If current session was archived, clear it
      if (currentSession && selectedSessions.has(currentSession.id)) {
        setCurrentSession(null)
      }
      setSelectedSessions(new Set())
      setIsSelectMode(false)
    } catch (err) {
      console.error('Failed to archive sessions:', err)
    }
  }, [selectedSessions, currentSession])

  const deleteSelected = useCallback(async () => {
    if (selectedSessions.size === 0) return
    try {
      const ids = Array.from(selectedSessions)
      await sessionsApi.deletePermanently(ids)
      // Remove deleted sessions from list
      setSessions(prev => prev.filter(s => !selectedSessions.has(s.id)))
      // If current session was deleted, clear it
      if (currentSession && selectedSessions.has(currentSession.id)) {
        setCurrentSession(null)
      }
      // Clean up drafts
      ids.forEach(id => localStorage.removeItem(STORAGE_KEYS.DRAFT + id))
      setSelectedSessions(new Set())
      setIsSelectMode(false)
    } catch (err) {
      console.error('Failed to delete sessions:', err)
    }
  }, [selectedSessions, currentSession])

  const moveSelectedToFolder = useCallback(async (folderId: string | null) => {
    if (selectedSessions.size === 0) return
    try {
      const ids = Array.from(selectedSessions)
      await sessionsApi.moveToFolder(ids, folderId)
      // Update folder_id on sessions in state
      setSessions(prev => prev.map(s =>
        selectedSessions.has(s.id) ? { ...s, folder_id: folderId ?? undefined } : s
      ))
      if (currentSession && selectedSessions.has(currentSession.id)) {
        setCurrentSession(prev => prev ? { ...prev, folder_id: folderId ?? undefined } : null)
      }
      setSelectedSessions(new Set())
      setIsSelectMode(false)
    } catch (err) {
      console.error('Failed to move sessions to folder:', err)
    }
  }, [selectedSessions, currentSession])

  const value: ChatSidebarContextType = {
    sessions,
    folders,
    currentSession,
    isSidebarLoading,
    models,
    modelPricing,
    collapsedFolders,
    editingTitle,
    editingTitleValue,
    showNewFolder,
    newFolderName,
    contextMenu,
    isSelectMode,
    selectedSessions,
    loadSessions,
    handleNewChat,
    handleSelectSession,
    handleDeleteSession,
    handleTogglePin,
    handleUpdateTitle,
    handleCreateFolder,
    handleDeleteFolder,
    toggleFolder,
    setEditingTitle,
    setEditingTitleValue,
    setShowNewFolder,
    setNewFolderName,
    setContextMenu,
    setCurrentSession,
    setSessions,
    getPricing,
    toggleSelectMode,
    toggleSessionSelection,
    selectAllSessions,
    clearSelection,
    archiveSelected,
    deleteSelected,
    moveSelectedToFolder,
    pendingMessage,
    pendingAttachments,
    setPendingMessage,
  }

  return (
    <ChatSidebarContext.Provider value={value}>
      {children}
    </ChatSidebarContext.Provider>
  )
}
