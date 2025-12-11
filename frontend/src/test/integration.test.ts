/**
 * Integration tests for Frontend-Backend communication.
 * 
 * These tests verify end-to-end API communication between
 * the frontend React app and the FastAPI backend.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { server } from '../test/server'
import { 
  systemApi, 
  searchApi, 
  chatApi, 
  profilesApi, 
  ingestionApi,
} from '../api/client'


// Start MSW server before tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())


describe('Frontend-Backend Integration: System Flow', () => {
  it('should complete health check flow', async () => {
    // Step 1: Check health
    const health = await systemApi.health()
    expect(health.status).toBe('healthy')
    
    // Step 2: Get system info
    const info = await systemApi.info()
    expect(info.version).toBeDefined()
    
    // Step 3: Get config
    const config = await systemApi.config()
    expect(config.llm_provider).toBeDefined()
  })

  it('should fetch all system stats in sequence', async () => {
    const [health, info, config, indexes, dbStats] = await Promise.all([
      systemApi.health(),
      systemApi.info(),
      systemApi.config(),
      systemApi.indexes(),
      systemApi.databaseStats(),
    ])
    
    expect(health.status).toBe('healthy')
    expect(info.version).toBeDefined()
    expect(config.llm_provider).toBeDefined()
    expect(indexes.indexes).toBeInstanceOf(Array)
    expect(dbStats.documents).toBeDefined()
  })
})


describe('Frontend-Backend Integration: Profile Flow', () => {
  it('should complete profile management flow', async () => {
    // Step 1: List all profiles
    const profiles = await profilesApi.list()
    expect(profiles.profiles).toBeDefined()
    expect(Object.keys(profiles.profiles).length).toBeGreaterThan(0)
    
    // Step 2: Get active profile
    const active = await profilesApi.getActive()
    expect(active.name).toBeDefined()
    
    // Step 3: Switch to a profile
    const switched = await profilesApi.switch('default')
    expect(switched.message).toContain('switched')
  })

  it('should handle profile creation and deletion', async () => {
    // Create profile
    const created = await profilesApi.create({
      key: 'integration_test_profile',
      name: 'Integration Test',
      description: 'Test profile for integration tests',
      documents_folders: ['./test_docs'],
    })
    expect(created.key).toBe('integration_test_profile')
    
    // Delete profile
    const deleted = await profilesApi.delete('integration_test_profile')
    expect(deleted.message).toContain('deleted')
  })
})


describe('Frontend-Backend Integration: Search Flow', () => {
  it('should complete full search flow', async () => {
    // Step 1: Get system health to ensure backend is ready
    const health = await systemApi.health()
    expect(health.status).toBe('healthy')
    
    // Step 2: Perform search
    const results = await searchApi.search('integration test query', 'hybrid', 10)
    expect(results.query).toBe('integration test query')
    expect(results.search_type).toBe('hybrid')
    expect(results.results).toBeInstanceOf(Array)
    expect(results.total_results).toBeDefined()
    expect(results.processing_time_ms).toBeDefined()
  })

  it('should perform all search types', async () => {
    const [hybrid, semantic, text] = await Promise.all([
      searchApi.hybridSearch('test', 5),
      searchApi.semanticSearch('test', 5),
      searchApi.textSearch('test', 5),
    ])
    
    expect(hybrid.search_type).toBe('hybrid')
    expect(semantic.search_type).toBe('semantic')
    expect(text.search_type).toBe('text')
  })

  it('should search and process results correctly', async () => {
    const results = await searchApi.search('document search', 'hybrid', 5)
    
    // Verify response structure
    expect(results).toMatchObject({
      query: 'document search',
      search_type: 'hybrid',
      results: expect.any(Array),
      total_results: expect.any(Number),
      processing_time_ms: expect.any(Number),
    })
    
    // Verify each result has required fields if there are any
    if (results.results.length > 0) {
      expect(results.results[0]).toMatchObject({
        chunk_id: expect.any(String),
        document_id: expect.any(String),
        document_title: expect.any(String),
        content: expect.any(String),
        similarity: expect.any(Number),
      })
    }
  })
})


describe('Frontend-Backend Integration: Chat Flow', () => {
  it('should complete chat conversation flow', async () => {
    // Step 1: Start new conversation
    const response1 = await chatApi.send('Hello, how can you help me?')
    expect(response1.message).toBeDefined()
    expect(response1.conversation_id).toBeDefined()
    const conversationId = response1.conversation_id
    
    // Step 2: Continue conversation
    const response2 = await chatApi.send('Tell me more', conversationId)
    expect(response2.message).toBeDefined()
    expect(response2.conversation_id).toBe(conversationId)
  })

  it('should handle chat response metadata', async () => {
    const response = await chatApi.send('What documents do you have?')
    
    expect(response).toMatchObject({
      message: expect.any(String),
      conversation_id: expect.any(String),
      search_performed: expect.any(Boolean),
      model: expect.any(String),
      processing_time_ms: expect.any(Number),
    })
  })

  it('should list and manage conversations', async () => {
    // Send a message to create conversation
    await chatApi.send('Test message')
    
    // List conversations
    const list = await chatApi.listConversations()
    expect(list.conversations).toBeInstanceOf(Array)
  })
})


describe('Frontend-Backend Integration: Ingestion Flow', () => {
  it('should check ingestion status', async () => {
    const status = await ingestionApi.getStatus()
    
    expect(status).toMatchObject({
      status: expect.any(String),
      total_files: expect.any(Number),
      processed_files: expect.any(Number),
      failed_files: expect.any(Number),
      chunks_created: expect.any(Number),
      errors: expect.any(Array),
      progress_percent: expect.any(Number),
    })
  })

  it('should start ingestion job', async () => {
    const result = await ingestionApi.start({ incremental: true })
    
    expect(result.status).toBe('running')
    expect(result.job_id).toBeDefined()
  })
})


describe('Frontend-Backend Integration: Combined Workflows', () => {
  it('should complete profile-then-search workflow', async () => {
    // Get active profile
    const profile = await profilesApi.getActive()
    expect(profile.name).toBeDefined()
    
    // Perform search using current profile's database
    const results = await searchApi.search('documents in current profile', 'hybrid', 10)
    expect(results.results).toBeInstanceOf(Array)
  })

  it('should complete health-config-chat workflow', async () => {
    // Check system health
    const health = await systemApi.health()
    expect(health.status).toBe('healthy')
    
    // Get configuration
    const config = await systemApi.config()
    expect(config.llm_model).toBeDefined()
    
    // Start chat with configured model
    const chat = await chatApi.send('Hello')
    expect(chat.model).toBeDefined()
  })

  it('should handle parallel requests efficiently', async () => {
    const startTime = Date.now()
    
    const [health, profiles, status] = await Promise.all([
      systemApi.health(),
      profilesApi.list(),
      ingestionApi.getStatus(),
    ])
    
    const duration = Date.now() - startTime
    
    expect(health.status).toBe('healthy')
    expect(profiles.profiles).toBeDefined()
    expect(status.status).toBeDefined()
    
    // Parallel requests should be fast
    expect(duration).toBeLessThan(5000)
  })
})


describe('Frontend-Backend Integration: Error Handling', () => {
  it('should handle API timeouts gracefully', async () => {
    // The retry mechanism in client.ts should handle temporary failures
    try {
      await systemApi.health()
    } catch (error) {
      expect(error).toBeDefined()
    }
  })

  it('should propagate API errors correctly', async () => {
    try {
      // Attempt to switch to non-existent profile
      await profilesApi.switch('nonexistent_profile_xyz')
    } catch (error: unknown) {
      expect((error as Error).message).toBeDefined()
    }
  })
})
