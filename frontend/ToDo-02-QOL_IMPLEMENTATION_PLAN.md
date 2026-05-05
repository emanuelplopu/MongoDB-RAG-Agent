# RecallHub Quality of Life (QoL) Implementation Plan

## Overview
This document outlines a comprehensive 10-phase implementation plan for 78+ quality of life improvements to make RecallHub more intuitive, less stressful, and more value-producing.

## Implementation Phases

---

## Phase 1: Copy & Clipboard Infrastructure (Foundation)
**Priority: Critical | Estimated: 2-3 hours**

### Components to Create
- [ ] 1.1 `CopyButton.tsx` - Reusable copy button component with tooltip feedback
- [ ] 1.2 `ToastProvider.tsx` - Global toast notification system
- [ ] 1.3 `useClipboard.ts` - Custom hook for clipboard operations
- [ ] 1.4 `useToast.ts` - Hook for triggering toast notifications

### Features to Implement
| ID | Feature | Target Component |
|----|---------|------------------|
| 1.1 | Copy AI responses | MessageBubble in ChatPageNew.tsx |
| 1.2 | Copy user prompts | MessageBubble in ChatPageNew.tsx |
| 1.3 | Copy code blocks | Markdown renderer |
| 1.4 | Copy search results | SearchPage.tsx |
| 1.5 | Copy document excerpts | FederatedAgentPanel.tsx |
| 1.6 | Copy system prompts | PromptManagementPage.tsx |
| 1.7 | Copy agent trace | FederatedAgentPanel.tsx |
| 1.8 | Export conversation | ChatPageNew.tsx |

### Validation Criteria
- [ ] All copy buttons show visual feedback
- [ ] Toast notifications appear on copy
- [ ] Works across all browsers
- [ ] Accessible via keyboard

---

## Phase 2: Input Persistence & Recovery
**Priority: High | Estimated: 2 hours**

### Components to Create
- [ ] 2.1 `useLocalStorage.ts` - Generic localStorage hook with serialization
- [ ] 2.2 `usePersistentState.ts` - State that syncs to localStorage
- [ ] 2.3 `useBeforeUnload.ts` - Unsaved changes warning hook
- [ ] 2.4 `DraftManager.ts` - Centralized draft management utility

### Features to Implement
| ID | Feature | Target Component |
|----|---------|------------------|
| 2.1 | Global draft persistence | ChatPageNew.tsx (enhance existing) |
| 2.2 | Search query persistence | SearchPage.tsx |
| 2.3 | Form state recovery | IngestionManagementPage.tsx |
| 2.4 | Config form autosave | ConfigurationPage.tsx |
| 2.5 | Prompt editor autosave | PromptManagementPage.tsx |
| 2.6 | Unsaved changes warning | All form pages |
| 2.7 | Session restore on crash | ChatSidebarContext.tsx |

### Validation Criteria
- [ ] Input persists across tab reload
- [ ] Input persists after browser crash
- [ ] Warning shown before losing unsaved changes
- [ ] Drafts clear after successful submit

---

## Phase 3: Chat Experience Enhancements
**Priority: High | Estimated: 3-4 hours**

### Components to Create
- [ ] 3.1 `MessageActions.tsx` - Action buttons for messages (copy, edit, regenerate)
- [ ] 3.2 `EditableMessage.tsx` - Inline message editing component
- [ ] 3.3 `RetryButton.tsx` - Retry failed message component
- [ ] 3.4 `StopButton.tsx` - Stop generation button

### Features to Implement
| ID | Feature | Target Component |
|----|---------|------------------|
| 3.1 | Edit sent messages | ChatPageNew.tsx |
| 3.2 | Regenerate response | ChatPageNew.tsx |
| 3.3 | Retry failed messages | ChatPageNew.tsx |
| 3.4 | Stop generation | ChatPageNew.tsx |
| 3.5 | Message reactions | ChatPageNew.tsx |
| 3.6 | Conversation search | ChatPageNew.tsx |
| 3.7 | Voice input | ChatPageNew.tsx |
| 3.8 | Prompt templates | ChatPageNew.tsx |

### Validation Criteria
- [ ] Can edit any user message
- [ ] Regenerate produces new response
- [ ] Stop button cancels streaming
- [ ] Retry recovers from errors

---

## Phase 4: Keyboard Shortcuts & Accessibility
**Priority: Medium | Estimated: 2 hours**

### Components to Create
- [ ] 4.1 `KeyboardShortcuts.tsx` - Global shortcut handler
- [ ] 4.2 `ShortcutOverlay.tsx` - Keyboard shortcut help overlay
- [ ] 4.3 `useKeyboardShortcut.ts` - Hook for registering shortcuts
- [ ] 4.4 `FocusTrap.tsx` - Focus trap for modals

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 4.1 | Cmd+K quick search | Global |
| 4.2 | Cmd+N new chat | Global |
| 4.3 | Escape to close | All modals/menus |
| 4.4 | Arrow navigation | Chat history |
| 4.5 | Tab navigation | All components |
| 4.6 | Focus trap in modals | All modals |
| 4.7 | ? for shortcut help | Global |

### Validation Criteria
- [ ] All shortcuts work on Mac and Windows
- [ ] Shortcuts don't conflict with browser defaults
- [ ] Focus management is correct
- [ ] Screen reader compatible

---

## Phase 5: Visual Feedback & Status
**Priority: Medium | Estimated: 2-3 hours**

### Components to Create
- [ ] 5.1 `ConnectionStatus.tsx` - API connection indicator
- [ ] 5.2 `ProgressBar.tsx` - Streaming progress component
- [ ] 5.3 `SkeletonLoader.tsx` - Content placeholder component
- [ ] 5.4 `AnimatedTransition.tsx` - Success animation wrapper

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 5.1 | Connection status indicator | Layout.tsx |
| 5.2 | Streaming progress bar | ChatPageNew.tsx |
| 5.3 | Skeleton loading states | All pages |
| 5.4 | Success animations | All actions |
| 5.5 | Unread message indicator | Sidebar |
| 5.6 | Enhanced typing indicators | ChatPageNew.tsx |

### Validation Criteria
- [ ] Connection status updates in real-time
- [ ] Loading states are smooth
- [ ] Animations are subtle and not distracting
- [ ] Works with reduced motion preferences

---

## Phase 6: Navigation & Organization
**Priority: Medium | Estimated: 2-3 hours**

### Components to Create
- [ ] 6.1 `QuickJump.tsx` - Cmd+P style fuzzy finder
- [ ] 6.2 `RecentItems.tsx` - Recently viewed items list
- [ ] 6.3 `DraggableList.tsx` - Drag-and-drop reorderable list

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 6.1 | Quick jump (Cmd+P) | Global |
| 6.2 | Recent searches | SearchPage.tsx |
| 6.3 | Recently viewed docs | DocumentsPage.tsx |
| 6.4 | Favorites/bookmarks | DocumentsPage.tsx |
| 6.5 | Drag-drop chat organization | Layout.tsx |
| 6.6 | Collapsible sections | Layout.tsx |

### Validation Criteria
- [ ] Quick jump is fast and accurate
- [ ] Recent items persist across sessions
- [ ] Drag-drop is smooth
- [ ] State persists after refresh

---

## Phase 7: Bulk Operations & Selection
**Priority: Medium | Estimated: 2 hours**

### Components to Create
- [ ] 7.1 `SelectionToolbar.tsx` - Bulk action toolbar
- [ ] 7.2 `useSelection.ts` - Selection state management hook
- [ ] 7.3 `SelectableItem.tsx` - Selectable item wrapper

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 7.1 | Multi-select documents | DocumentsPage.tsx |
| 7.2 | Select all/none toggle | DocumentsPage.tsx |
| 7.3 | Shift+Click range select | DocumentsPage.tsx |
| 7.4 | Bulk delete | DocumentsPage.tsx |
| 7.5 | Bulk export | DocumentsPage.tsx |
| 7.6 | Bulk reingestion | DocumentsPage.tsx |

### Validation Criteria
- [ ] Selection state is clear
- [ ] Bulk actions work correctly
- [ ] Range select works
- [ ] Clear visual feedback

---

## Phase 8: Search & Filtering
**Priority: Medium | Estimated: 2 hours**

### Components to Create
- [ ] 8.1 `SearchFilters.tsx` - Advanced filter panel
- [ ] 8.2 `FilterPresets.tsx` - Saved filter presets
- [ ] 8.3 `SearchSuggestions.tsx` - Autocomplete suggestions

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 8.1 | Search filter presets | SearchPage.tsx |
| 8.2 | Date range filter | DocumentsPage.tsx |
| 8.3 | File type quick filters | DocumentsPage.tsx |
| 8.4 | Search suggestions | SearchPage.tsx |
| 8.5 | Highlight search terms | SearchPage.tsx |
| 8.6 | Search history | SearchPage.tsx |

### Validation Criteria
- [ ] Filters work correctly
- [ ] Presets persist
- [ ] Suggestions are relevant
- [ ] Highlighting is accurate

---

## Phase 9: Settings & Preferences
**Priority: Low | Estimated: 2 hours**

### Components to Create
- [ ] 9.1 `UserPreferences.tsx` - User settings panel
- [ ] 9.2 `usePreferences.ts` - Preferences management hook

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 9.1 | Remember view preference | DocumentsPage.tsx |
| 9.2 | Font size adjustment | ChatPageNew.tsx |
| 9.3 | Message density | ChatPageNew.tsx |
| 9.4 | Default agent mode | ChatPageNew.tsx |
| 9.5 | Sidebar width | Layout.tsx |
| 9.6 | Code theme selection | ChatPageNew.tsx |

### Validation Criteria
- [ ] Preferences persist
- [ ] Settings apply immediately
- [ ] Reset to defaults works
- [ ] Syncs across tabs

---

## Phase 10: Final Polish
**Priority: Low | Estimated: 2-3 hours**

### Components to Create
- [ ] 10.1 `ErrorBoundary.tsx` - Global error boundary
- [ ] 10.2 `OnboardingTour.tsx` - First-time user guide
- [ ] 10.3 `UndoManager.tsx` - Undo/redo infrastructure

### Features to Implement
| ID | Feature | Target |
|----|---------|--------|
| 10.1 | Undo delete | All delete actions |
| 10.2 | Error boundary | Global |
| 10.3 | Offline indicator | Layout.tsx |
| 10.4 | Auto-retry on failure | API client |
| 10.5 | First-time walkthrough | Global |
| 10.6 | Tooltips on icons | All icon buttons |
| 10.7 | Empty state guidance | All list pages |

### Validation Criteria
- [ ] Errors are handled gracefully
- [ ] Undo works within time window
- [ ] Onboarding is helpful
- [ ] App recovers from errors

---

## Implementation Timeline

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 1 | 2-3 hours | None |
| Phase 2 | 2 hours | Phase 1 (Toast) |
| Phase 3 | 3-4 hours | Phase 1, 2 |
| Phase 4 | 2 hours | Phase 1 |
| Phase 5 | 2-3 hours | Phase 1 |
| Phase 6 | 2-3 hours | Phase 1, 4 |
| Phase 7 | 2 hours | Phase 1 |
| Phase 8 | 2 hours | Phase 1, 2 |
| Phase 9 | 2 hours | Phase 2 |
| Phase 10 | 2-3 hours | All phases |

**Total Estimated Time: 21-27 hours**

---

## Testing Strategy

### Unit Tests
- Test each utility hook independently
- Test component rendering
- Test clipboard operations

### Integration Tests
- Test toast system with copy actions
- Test persistence across page reloads
- Test keyboard shortcuts

### E2E Tests
- Test complete user flows
- Test error recovery
- Test accessibility

---

## Success Metrics
- Copy operations: < 100ms response time
- Toast display: Immediate
- Input persistence: Survives page reload
- Keyboard shortcuts: Work on first press
- Loading states: No content flash
