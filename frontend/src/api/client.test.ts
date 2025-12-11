/**
 * Unit tests for API Client.
 * 
 * Tests the API client functions and error handling.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { server } from '../test/server'
import { 
  systemApi, 
  searchApi, 
  chatApi, 
  profilesApi, 
  documentsApi, 
  ingestionApi,
  ApiError,
} from './client'

// Start MSW server before tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())


describe('systemApi', () => {
  it('should fetch health status', async () => {
    const result = await systemApi.health()
    expect(result).toBeDefined()
    expect(result.status).toBe('healthy')
  })

  it('should fetch system stats', async () => {
    const result = await systemApi.stats()
    expect(result).toBeDefined()
    expect(result.database).toBeDefined()
    expect(result.indexes).toBeDefined()
  })

  it('should fetch system config', async () => {
    const result = await systemApi.config()
    expect(result).toBeDefined()
    expect(result.llm_provider).toBeDefined()
  })

  it('should fetch system info', async () => {
    const result = await systemApi.info()
    expect(result).toBeDefined()
    expect(result.version).toBeDefined()
  })

  it('should fetch indexes status', async () => {
    const result = await systemApi.indexes()
    expect(result).toBeDefined()
    expect(result.indexes).toBeInstanceOf(Array)
  })

  it('should fetch database stats', async () => {
    const result = await systemApi.databaseStats()
    expect(result).toBeDefined()
    expect(result.documents).toBeDefined()
  })
})


describe('searchApi', () => {
  it('should perform hybrid search', async () => {
    const result = await searchApi.search('test query', 'hybrid', 10)
    expect(result).toBeDefined()
    expect(result.query).toBe('test query')
    expect(result.search_type).toBe('hybrid')
    expect(result.results).toBeInstanceOf(Array)
  })

  it('should perform semantic search', async () => {
    const result = await searchApi.semanticSearch('semantic test', 5)
    expect(result).toBeDefined()
    expect(result.search_type).toBe('semantic')
  })

  it('should perform text search', async () => {
    const result = await searchApi.textSearch('text test', 5)
    expect(result).toBeDefined()
    expect(result.search_type).toBe('text')
  })

  it('should perform hybrid search via specific endpoint', async () => {
    const result = await searchApi.hybridSearch('hybrid test', 5)
    expect(result).toBeDefined()
    expect(result.search_type).toBe('hybrid')
  })

  it('should include total results and timing', async () => {
    const result = await searchApi.search('test', 'hybrid', 5)
    expect(result.total_results).toBeDefined()
    expect(result.processing_time_ms).toBeDefined()
  })
})


describe('chatApi', () => {
  it('should send chat message', async () => {
    const result = await chatApi.send('Hello world')
    expect(result).toBeDefined()
    expect(result.message).toBeDefined()
    expect(result.conversation_id).toBeDefined()
  })

  it('should send chat with conversation ID', async () => {
    const result = await chatApi.send('Follow up', 'existing_conv_123')
    expect(result).toBeDefined()
    expect(result.message).toBeDefined()
  })

  it('should list conversations', async () => {
    const result = await chatApi.listConversations()
    expect(result).toBeDefined()
    expect(result.conversations).toBeInstanceOf(Array)
  })

  it('should get conversation by ID', async () => {
    const result = await chatApi.getConversation('conv_123')
    expect(result).toBeDefined()
    expect(result.id).toBe('conv_123')
  })

  it('should delete conversation', async () => {
    const result = await chatApi.deleteConversation('conv_123')
    expect(result).toBeDefined()
  })
})


describe('profilesApi', () => {
  it('should list all profiles', async () => {
    const result = await profilesApi.list()
    expect(result).toBeDefined()
    expect(result.profiles).toBeDefined()
    expect(result.active_profile).toBeDefined()
  })

  it('should get active profile', async () => {
    const result = await profilesApi.getActive()
    expect(result).toBeDefined()
    expect(result.name).toBeDefined()
  })

  it('should switch profile', async () => {
    const result = await profilesApi.switch('default')
    expect(result).toBeDefined()
    expect(result.message).toContain('switched')
  })

  it('should create profile', async () => {
    const result = await profilesApi.create({
      key: 'test_profile',
      name: 'Test Profile',
      description: 'A test profile',
      documents_folders: ['./test_docs'],
    })
    expect(result).toBeDefined()
    expect(result.key).toBe('test_profile')
  })

  it('should delete profile', async () => {
    const result = await profilesApi.delete('test_profile')
    expect(result).toBeDefined()
  })
})


describe('documentsApi', () => {
  it('should list documents', async () => {
    const result = await documentsApi.list()
    expect(result).toBeDefined()
    expect(result.documents).toBeInstanceOf(Array)
    expect(result.total).toBeDefined()
  })

  it('should list documents with pagination', async () => {
    const result = await documentsApi.list(2, 10)
    expect(result).toBeDefined()
    expect(result.page).toBe(2)
    expect(result.page_size).toBe(10)
  })

  it('should get document by ID', async () => {
    const result = await documentsApi.get('doc_1')
    expect(result).toBeDefined()
    expect(result.id).toBe('doc_1')
  })

  it('should delete document', async () => {
    const result = await documentsApi.delete('doc_1')
    expect(result).toBeDefined()
  })
})


describe('ingestionApi', () => {
  it('should get ingestion status', async () => {
    const result = await ingestionApi.getStatus()
    expect(result).toBeDefined()
    expect(result.status).toBeDefined()
  })

  it('should start ingestion', async () => {
    const result = await ingestionApi.start({ incremental: true })
    expect(result).toBeDefined()
    expect(result.status).toBe('running')
    expect(result.job_id).toBeDefined()
  })

  it('should setup indexes', async () => {
    const result = await ingestionApi.setupIndexes()
    expect(result).toBeDefined()
  })
})


describe('ApiError', () => {
  it('should create ApiError with correct properties', () => {
    const error = new ApiError('Test error', 500, 'TEST_ERROR', { extra: 'data' })
    expect(error.message).toBe('Test error')
    expect(error.status).toBe(500)
    expect(error.code).toBe('TEST_ERROR')
    expect(error.details).toEqual({ extra: 'data' })
    expect(error.name).toBe('ApiError')
  })

  it('should be an instance of Error', () => {
    const error = new ApiError('Test', 400, 'BAD_REQUEST')
    expect(error).toBeInstanceOf(Error)
    expect(error).toBeInstanceOf(ApiError)
  })
})
