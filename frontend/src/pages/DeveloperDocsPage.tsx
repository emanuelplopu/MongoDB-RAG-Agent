import { useState, useEffect } from 'react'

interface ApiEndpoint {
  path: string
  method: string
  summary: string
  tag: string
}

interface ApiTag {
  name: string
  endpoints: ApiEndpoint[]
}

const API_BASE_URL = import.meta.env.VITE_API_URL || ''

export default function DeveloperDocsPage() {
  const [apiTags, setApiTags] = useState<ApiTag[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    fetchOpenApiSpec()
  }, [])

  const fetchOpenApiSpec = async () => {
    try {
      setError(null)
      const response = await fetch(`${API_BASE_URL}/openapi.json`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
      const spec = await response.json()
      
      if (!spec.openapi && !spec.swagger) {
        throw new Error('Invalid OpenAPI specification')
      }
      
      // Parse endpoints by tag
      const tagMap: Record<string, ApiEndpoint[]> = {}
      
      for (const [path, methods] of Object.entries(spec.paths || {})) {
        for (const [method, details] of Object.entries(methods as Record<string, any>)) {
          if (method === 'parameters') continue
          
          const tags = details.tags || ['Other']
          const endpoint: ApiEndpoint = {
            path,
            method: method.toUpperCase(),
            summary: details.summary || details.description || 'No description',
            tag: tags[0]
          }
          
          for (const tag of tags) {
            if (!tagMap[tag]) tagMap[tag] = []
            tagMap[tag].push(endpoint)
          }
        }
      }
      
      const tags: ApiTag[] = Object.entries(tagMap)
        .map(([name, endpoints]) => ({ name, endpoints }))
        .sort((a, b) => a.name.localeCompare(b.name))
      
      setApiTags(tags)
      if (tags.length > 0) setSelectedTag(tags[0].name)
    } catch (err) {
      console.error('Failed to fetch OpenAPI spec:', err)
      setError(err instanceof Error ? err.message : 'Failed to load API specification')
    } finally {
      setLoading(false)
    }
  }

  const getMethodColor = (method: string) => {
    switch (method) {
      case 'GET': return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
      case 'POST': return 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200'
      case 'PUT': return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200'
      case 'PATCH': return 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200'
      case 'DELETE': return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
      default: return 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200'
    }
  }

  const filteredEndpoints = selectedTag
    ? apiTags.find(t => t.name === selectedTag)?.endpoints.filter(e => 
        searchQuery === '' || 
        e.path.toLowerCase().includes(searchQuery.toLowerCase()) ||
        e.summary.toLowerCase().includes(searchQuery.toLowerCase())
      ) || []
    : []

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <div className="bg-white dark:bg-gray-800 shadow">
        <div className="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
            Developer API Documentation
          </h1>
          <p className="mt-2 text-gray-600 dark:text-gray-400">
            Complete API reference for the MongoDB RAG Agent system
          </p>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
        {/* Quick Links */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <a
            href={`${API_BASE_URL}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center p-4 bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow"
          >
            <div className="flex-shrink-0 p-3 bg-blue-100 dark:bg-blue-900 rounded-lg">
              <svg className="w-6 h-6 text-blue-600 dark:text-blue-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div className="ml-4">
              <h3 className="font-semibold text-gray-900 dark:text-white">Swagger UI</h3>
              <p className="text-sm text-gray-500 dark:text-gray-400">Interactive API testing</p>
            </div>
          </a>
          
          <a
            href={`${API_BASE_URL}/redoc`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center p-4 bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow"
          >
            <div className="flex-shrink-0 p-3 bg-purple-100 dark:bg-purple-900 rounded-lg">
              <svg className="w-6 h-6 text-purple-600 dark:text-purple-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </div>
            <div className="ml-4">
              <h3 className="font-semibold text-gray-900 dark:text-white">ReDoc</h3>
              <p className="text-sm text-gray-500 dark:text-gray-400">Beautiful API docs</p>
            </div>
          </a>
          
          <a
            href={`${API_BASE_URL}/openapi.json`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center p-4 bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow"
          >
            <div className="flex-shrink-0 p-3 bg-green-100 dark:bg-green-900 rounded-lg">
              <svg className="w-6 h-6 text-green-600 dark:text-green-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
            </div>
            <div className="ml-4">
              <h3 className="font-semibold text-gray-900 dark:text-white">OpenAPI Spec</h3>
              <p className="text-sm text-gray-500 dark:text-gray-400">Download JSON spec</p>
            </div>
          </a>
        </div>

        {/* Quick Start Guide */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow mb-8 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Quick Start</h2>
          </div>
          <div className="p-6 space-y-6">
            <div>
              <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">Base URL</h3>
              <code className="block bg-gray-100 dark:bg-gray-700 p-3 rounded text-sm font-mono">
                {window.location.origin}/api/v1
              </code>
            </div>

            <div>
              <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">Authentication</h3>
              <p className="text-gray-600 dark:text-gray-400 mb-2">
                Most endpoints require a JWT token. Obtain one via login:
              </p>
              <pre className="bg-gray-100 dark:bg-gray-700 p-3 rounded text-sm font-mono overflow-x-auto">
{`# Login to get a token
curl -X POST "${window.location.origin}/api/v1/auth/login" \\
  -H "Content-Type: application/json" \\
  -d '{"email": "user@example.com", "password": "password"}'

# Use token in requests
curl "${window.location.origin}/api/v1/search/hybrid" \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "search term"}'`}
              </pre>
            </div>

            <div>
              <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">Common Operations</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-gray-50 dark:bg-gray-700 p-4 rounded">
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">Search Knowledge Base</h4>
                  <pre className="text-xs font-mono overflow-x-auto">
{`POST /api/v1/search/hybrid
{
  "query": "your search query",
  "match_count": 10,
  "min_score": 0.5
}`}
                  </pre>
                </div>
                
                <div className="bg-gray-50 dark:bg-gray-700 p-4 rounded">
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">Chat with Agent</h4>
                  <pre className="text-xs font-mono overflow-x-auto">
{`POST /api/v1/sessions/{id}/messages
{
  "content": "your message",
  "search_type": "hybrid",
  "match_count": 10
}`}
                  </pre>
                </div>
                
                <div className="bg-gray-50 dark:bg-gray-700 p-4 rounded">
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">Start Document Ingestion</h4>
                  <pre className="text-xs font-mono overflow-x-auto">
{`POST /api/v1/ingestion/start
{
  "source_type": "directory",
  "source_path": "/path/to/docs",
  "chunk_size": 512
}`}
                  </pre>
                </div>
                
                <div className="bg-gray-50 dark:bg-gray-700 p-4 rounded">
                  <h4 className="font-medium text-gray-900 dark:text-white mb-2">Get System Status</h4>
                  <pre className="text-xs font-mono overflow-x-auto">
{`GET /api/v1/system/health
GET /api/v1/status/dashboard`}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* API Reference */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">API Reference</h2>
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Search endpoints..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-sm"
              />
              <span className="text-sm text-gray-500 dark:text-gray-400">
                {apiTags.reduce((sum, t) => sum + t.endpoints.length, 0)} endpoints
              </span>
            </div>
          </div>

          {loading ? (
            <div className="p-8 text-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mx-auto"></div>
              <p className="mt-2 text-gray-500 dark:text-gray-400">Loading API spec...</p>
            </div>
          ) : error ? (
            <div className="p-8 text-center">
              <div className="text-red-500 dark:text-red-400 mb-4">
                <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <p className="text-red-600 dark:text-red-400 font-medium mb-2">Failed to load API specification</p>
              <p className="text-gray-500 dark:text-gray-400 text-sm mb-4">{error}</p>
              <button
                onClick={() => { setLoading(true); fetchOpenApiSpec(); }}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
              >
                Retry
              </button>
            </div>
          ) : (
            <div className="flex">
              {/* Tag sidebar */}
              <div className="w-48 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                <nav className="p-2 space-y-1 max-h-[600px] overflow-y-auto">
                  {apiTags.map((tag) => (
                    <button
                      key={tag.name}
                      onClick={() => setSelectedTag(tag.name)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                        selectedTag === tag.name
                          ? 'bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 font-medium'
                          : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
                      }`}
                    >
                      {tag.name}
                      <span className="ml-1 text-xs text-gray-500">({tag.endpoints.length})</span>
                    </button>
                  ))}
                </nav>
              </div>

              {/* Endpoints list */}
              <div className="flex-1 p-4 max-h-[600px] overflow-y-auto">
                <div className="space-y-2">
                  {filteredEndpoints.map((endpoint, idx) => (
                    <div
                      key={`${endpoint.method}-${endpoint.path}-${idx}`}
                      className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    >
                      <span className={`px-2 py-1 text-xs font-bold rounded ${getMethodColor(endpoint.method)}`}>
                        {endpoint.method}
                      </span>
                      <div className="flex-1 min-w-0">
                        <code className="text-sm font-mono text-gray-900 dark:text-white break-all">
                          {endpoint.path}
                        </code>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5 truncate">
                          {endpoint.summary}
                        </p>
                      </div>
                      <a
                        href={`${API_BASE_URL}/docs#/${endpoint.tag}/${endpoint.method.toLowerCase()}_${endpoint.path.replace(/\//g, '_').replace(/[{}]/g, '')}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-shrink-0 px-2 py-1 text-xs bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded hover:bg-indigo-200 dark:hover:bg-indigo-800"
                      >
                        Try it
                      </a>
                    </div>
                  ))}
                  {filteredEndpoints.length === 0 && (
                    <p className="text-center text-gray-500 dark:text-gray-400 py-8">
                      No endpoints found
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* SDK/Client Libraries */}
        <div className="mt-8 bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Client Libraries</h2>
          </div>
          <div className="p-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div>
                <h3 className="font-medium text-gray-900 dark:text-white mb-2">Python</h3>
                <pre className="bg-gray-100 dark:bg-gray-700 p-3 rounded text-xs font-mono overflow-x-auto">
{`import requests

API_URL = "${window.location.origin}/api/v1"
TOKEN = "your_jwt_token"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# Search
response = requests.post(
    f"{API_URL}/search/hybrid",
    headers=headers,
    json={"query": "search term"}
)
results = response.json()`}
                </pre>
              </div>
              
              <div>
                <h3 className="font-medium text-gray-900 dark:text-white mb-2">JavaScript/TypeScript</h3>
                <pre className="bg-gray-100 dark:bg-gray-700 p-3 rounded text-xs font-mono overflow-x-auto">
{`const API_URL = "${window.location.origin}/api/v1";
const TOKEN = "your_jwt_token";

// Search
const response = await fetch(
  \`\${API_URL}/search/hybrid\`,
  {
    method: "POST",
    headers: {
      "Authorization": \`Bearer \${TOKEN}\`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query: "search term"
    })
  }
);
const results = await response.json();`}
                </pre>
              </div>
              
              <div>
                <h3 className="font-medium text-gray-900 dark:text-white mb-2">cURL</h3>
                <pre className="bg-gray-100 dark:bg-gray-700 p-3 rounded text-xs font-mono overflow-x-auto">
{`# Search
curl -X POST \\
  "${window.location.origin}/api/v1/search/hybrid" \\
  -H "Authorization: Bearer TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "search term"}'

# Chat
curl -X POST \\
  "${window.location.origin}/api/v1/sessions/ID/messages" \\
  -H "Authorization: Bearer TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"content": "Hello"}'`}
                </pre>
              </div>
            </div>
          </div>
        </div>

        {/* Rate Limits & Best Practices */}
        <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-4">Rate Limits</h2>
            <ul className="space-y-2 text-gray-600 dark:text-gray-400">
              <li className="flex items-center gap-2">
                <span className="w-2 h-2 bg-green-500 rounded-full"></span>
                Search endpoints: 60 requests/minute
              </li>
              <li className="flex items-center gap-2">
                <span className="w-2 h-2 bg-yellow-500 rounded-full"></span>
                Chat endpoints: 30 requests/minute
              </li>
              <li className="flex items-center gap-2">
                <span className="w-2 h-2 bg-blue-500 rounded-full"></span>
                Ingestion: 5 concurrent jobs
              </li>
              <li className="flex items-center gap-2">
                <span className="w-2 h-2 bg-gray-500 rounded-full"></span>
                Request timeout: 30 seconds
              </li>
            </ul>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-4">Best Practices</h2>
            <ul className="space-y-2 text-gray-600 dark:text-gray-400">
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Use hybrid search for best results
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Store tokens securely, never in code
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Handle rate limit errors with backoff
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Use streaming for long responses
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}
