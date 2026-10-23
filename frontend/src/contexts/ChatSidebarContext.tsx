import { createContext, useContext } from 'react'
import {
  AttachmentInfo,
  ChatSession,
  ChatFolder,
  LLMModel,
  ModelPricingInfo,
} from '../api/client'

// Local storage keys
export const STORAGE_KEYS = {
  DRAFT: 'chat_draft_',
  COLLAPSED_FOLDERS: 'chat_collapsed_folders',
}

export interface ChatSidebarContextType {
  // State
  sessions: ChatSession[]
  folders: ChatFolder[]
  currentSession: ChatSession | null
  isSidebarLoading: boolean
  models: LLMModel[]
  modelPricing: ModelPricingInfo[]
  collapsedFolders: Set<string>
  editingTitle: string | null
  editingTitleValue: string
  showNewFolder: boolean
  newFolderName: string
  contextMenu: { sessionId: string; x: number; y: number } | null
  // Multi-select
  isSelectMode: boolean
  selectedSessions: Set<string>
  // Pending message (for dashboard-to-chat transition)
  pendingMessage: string | null
  pendingAttachments: AttachmentInfo[] | null

  // Actions
  loadSessions: () => Promise<void>
  handleNewChat: (folderId?: string) => Promise<void>
  handleSelectSession: (sessionId: string) => Promise<void>
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleTogglePin: (sessionId: string, isPinned: boolean) => Promise<void>
  handleUpdateTitle: (sessionId: string) => Promise<void>
  handleCreateFolder: () => Promise<void>
  handleDeleteFolder: (folderId: string) => Promise<void>
  toggleFolder: (folderId: string) => void
  setEditingTitle: (id: string | null) => void
  setEditingTitleValue: (value: string) => void
  setShowNewFolder: (show: boolean) => void
  setNewFolderName: (name: string) => void
  setContextMenu: (menu: { sessionId: string; x: number; y: number } | null) => void
  setCurrentSession: React.Dispatch<React.SetStateAction<ChatSession | null>>
  setSessions: React.Dispatch<React.SetStateAction<ChatSession[]>>
  getPricing: (modelId: string) => { input: number; output: number }
  // Multi-select actions
  toggleSelectMode: () => void
  toggleSessionSelection: (sessionId: string) => void
  selectAllSessions: () => void
  clearSelection: () => void
  archiveSelected: () => Promise<void>
  deleteSelected: () => Promise<void>
  moveSelectedToFolder: (folderId: string | null) => Promise<void>
  setPendingMessage: (message: string | null, attachments?: AttachmentInfo[] | null) => void
}

/** Provided by `ChatSidebarProvider` (see ./ChatSidebarProvider.tsx). */
export const ChatSidebarContext = createContext<ChatSidebarContextType | null>(null)

export function useChatSidebar() {
  const context = useContext(ChatSidebarContext)
  if (!context) {
    throw new Error('useChatSidebar must be used within ChatSidebarProvider')
  }
  return context
}
