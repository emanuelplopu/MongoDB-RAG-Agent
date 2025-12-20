/**
 * MSW (Mock Service Worker) handlers for API mocking in tests.
 */

import { http, HttpResponse } from 'msw'

const API_BASE = '/api/v1'

// Mock data
const mockProfiles = {
  profiles: {
    default: {
      name: 'Default',
      description: 'Default profile',
      documents_folders: ['./documents'],
      database: 'rag_db',
      collection_documents: 'documents',
      collection_chunks: 'chunks',
      vector_index: 'vector_index',
      text_index: 'text_index',
    },
  },
  active_profile: 'default',
}

const mockSearchResults = {
  query: 'test query',
  search_type: 'hybrid',
  results: [
    {
      chunk_id: 'chunk_1',
      document_id: 'doc_1',
      document_title: 'Test Document',
      document_source: '/path/to/doc.pdf',
      content: 'This is test content from the document.',
      similarity: 0.95,
      metadata: {},
    },
  ],
  total_results: 1,
  processing_time_ms: 150,
}

const mockChatResponse = {
  message: 'Hello! I can help you with questions about your documents.',
  conversation_id: 'conv_abc123',
  sources: [],
  search_performed: true,
  model: 'gpt-4.1-mini',
  processing_time_ms: 500,
}

const mockSystemHealth = {
  status: 'healthy',
  database: { status: 'connected' },
  timestamp: new Date().toISOString(),
}

const mockSystemStats = {
  database: {
    documents: { count: 10, size_bytes: 1024000, avg_doc_size: 102400 },
    chunks: { count: 150, size_bytes: 512000, avg_doc_size: 3413 },
    database: 'rag_db',
  },
  indexes: {
    indexes: [
      { name: 'vector_index', status: 'ready', type: 'vector' },
      { name: 'text_index', status: 'ready', type: 'text' },
    ],
    vector_index: 'vector_index',
    text_index: 'text_index',
  },
  config: {
    llm_provider: 'openai',
    llm_model: 'gpt-4.1-mini',
    embedding_provider: 'openai',
    embedding_model: 'text-embedding-3-small',
    embedding_dimension: 1536,
    default_match_count: 10,
    database: 'rag_db',
  },
}

const mockDocuments = {
  documents: [
    {
      id: 'doc_1',
      title: 'Test Document 1',
      source: '/path/to/doc1.pdf',
      chunks_count: 15,
      created_at: '2025-01-01T00:00:00Z',
      metadata: {},
    },
    {
      id: 'doc_2',
      title: 'Test Document 2',
      source: '/path/to/doc2.pdf',
      chunks_count: 25,
      created_at: '2025-01-02T00:00:00Z',
      metadata: {},
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
  total_pages: 1,
}

const mockIngestionStatus = {
  status: 'idle',
  job_id: null,
  total_files: 0,
  processed_files: 0,
  failed_files: 0,
  chunks_created: 0,
  errors: [],
  progress_percent: 0,
}

// API handlers
export const handlers = [
  // System endpoints
  http.get(`${API_BASE}/system/health`, () => {
    return HttpResponse.json(mockSystemHealth)
  }),

  http.get(`${API_BASE}/system/stats`, () => {
    return HttpResponse.json(mockSystemStats)
  }),

  http.get(`${API_BASE}/system/config`, () => {
    return HttpResponse.json(mockSystemStats.config)
  }),

  http.get(`${API_BASE}/system/info`, () => {
    return HttpResponse.json({ version: '1.0.0', name: 'RecallHub' })
  }),

  http.get(`${API_BASE}/system/indexes`, () => {
    return HttpResponse.json(mockSystemStats.indexes)
  }),

  http.get(`${API_BASE}/system/database-stats`, () => {
    return HttpResponse.json(mockSystemStats.database)
  }),

  // Profile endpoints
  http.get(`${API_BASE}/profiles`, () => {
    return HttpResponse.json(mockProfiles)
  }),

  http.get(`${API_BASE}/profiles/active`, () => {
    return HttpResponse.json(mockProfiles.profiles.default)
  }),

  http.post(`${API_BASE}/profiles/switch`, async ({ request }) => {
    const body = await request.json() as { profile_key: string }
    if (body.profile_key in mockProfiles.profiles) {
      return HttpResponse.json({ message: 'Profile switched', profile: body.profile_key })
    }
    return HttpResponse.json({ error: 'Profile not found' }, { status: 404 })
  }),

  http.post(`${API_BASE}/profiles/create`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>
    return HttpResponse.json({ message: 'Profile created', profile: body }, { status: 201 })
  }),

  http.delete(`${API_BASE}/profiles/:key`, ({ params }) => {
    if (params.key === 'default') {
      return HttpResponse.json({ error: 'Cannot delete default profile' }, { status: 400 })
    }
    return HttpResponse.json({ message: 'Profile deleted' })
  }),

  // Search endpoints
  http.post(`${API_BASE}/search`, async ({ request }) => {
    const body = await request.json() as { query: string }
    return HttpResponse.json({ ...mockSearchResults, query: body.query })
  }),

  http.post(`${API_BASE}/search/semantic`, async ({ request }) => {
    const body = await request.json() as { query: string }
    return HttpResponse.json({ ...mockSearchResults, query: body.query, search_type: 'semantic' })
  }),

  http.post(`${API_BASE}/search/text`, async ({ request }) => {
    const body = await request.json() as { query: string }
    return HttpResponse.json({ ...mockSearchResults, query: body.query, search_type: 'text' })
  }),

  http.post(`${API_BASE}/search/hybrid`, async ({ request }) => {
    const body = await request.json() as { query: string }
    return HttpResponse.json({ ...mockSearchResults, query: body.query, search_type: 'hybrid' })
  }),

  // Chat endpoints
  http.post(`${API_BASE}/chat`, async ({ request }) => {
    const body = await request.json() as { message: string }
    return HttpResponse.json({
      ...mockChatResponse,
      message: `You asked: "${body.message}". Here is my response.`,
    })
  }),

  http.get(`${API_BASE}/chat/conversations`, () => {
    return HttpResponse.json({ conversations: [] })
  }),

  http.get(`${API_BASE}/chat/conversations/:id`, ({ params }) => {
    return HttpResponse.json({ 
      id: params.id, 
      messages: [],
      created_at: new Date().toISOString() 
    })
  }),

  http.delete(`${API_BASE}/chat/conversations/:id`, () => {
    return HttpResponse.json({ message: 'Conversation deleted' })
  }),

  // Document/Ingestion endpoints
  http.get(`${API_BASE}/ingestion/documents`, ({ request }) => {
    const url = new URL(request.url)
    const page = parseInt(url.searchParams.get('page') || '1')
    const pageSize = parseInt(url.searchParams.get('page_size') || '20')
    return HttpResponse.json({
      ...mockDocuments,
      page,
      page_size: pageSize,
    })
  }),

  http.get(`${API_BASE}/ingestion/documents/:id`, ({ params }) => {
    const doc = mockDocuments.documents.find(d => d.id === params.id)
    if (doc) {
      return HttpResponse.json(doc)
    }
    return HttpResponse.json({ error: 'Document not found' }, { status: 404 })
  }),

  http.delete(`${API_BASE}/ingestion/documents/:id`, () => {
    return HttpResponse.json({ message: 'Document deleted' })
  }),

  http.get(`${API_BASE}/ingestion/status`, () => {
    return HttpResponse.json(mockIngestionStatus)
  }),

  http.post(`${API_BASE}/ingestion/start`, () => {
    return HttpResponse.json({
      ...mockIngestionStatus,
      status: 'running',
      job_id: 'job_' + Date.now(),
    })
  }),

  http.post(`${API_BASE}/ingestion/setup-indexes`, () => {
    return HttpResponse.json({ message: 'Indexes created successfully' })
  }),
]

// Error handlers for testing error scenarios
export const errorHandlers = [
  http.get(`${API_BASE}/system/health`, () => {
    return HttpResponse.json({ error: 'Service unavailable' }, { status: 503 })
  }),

  http.post(`${API_BASE}/search`, () => {
    return HttpResponse.json({ error: 'Search failed' }, { status: 500 })
  }),

  http.post(`${API_BASE}/chat`, () => {
    return HttpResponse.json({ error: 'Chat failed' }, { status: 500 })
  }),
]
