export interface LLMCall {
  call_id: string
  phase: string
  model: string
  provider: string
  temperature: number
  max_tokens: number
  prompt_text: string
  response_text: string
  prompt_tokens: number
  response_tokens: number
  total_tokens: number
  latency_ms: number
  finish_reason: string
  success: boolean
  error?: string
  is_cold_start: boolean
  repetition_detected: boolean
}

export interface SearchOperation {
  query: string
  search_type: string
  sources_queried: string[]
  results_per_source: Record<string, number>
  total_results: number
  deduplicated_results: number
  duration_ms: number
  embedding_duration_ms: number
  rrf_applied: boolean
  top_scores: number[]
  chunks_returned: string[]
}

export interface ToolExecution {
  task_id: string
  task_type: string
  input_query: string
  output_text: string
  duration_ms: number
  tokens_used: number
  success: boolean
  error?: string
  results_count: number
  sources_searched: string[]
}

export interface PhaseMetrics {
  phase: string
  duration_ms: number
  tokens_used: number
  input_size: number
  output_size: number
  success: boolean
  error?: string
}

export interface ToolCall {
  tool_name: string
  arguments: string
  result_summary: string
  duration_ms: number
}

export interface SearchHit {
  source: string
  score: number
  snippet: string
}

export interface TelemetryRecord {
  record_id: string
  timestamp: string
  session_id: string
  user_id: string
  tenant: string
  language: string
  app_version: string

  // Request
  prompt_pseudonymized: string
  prompt_tokens: number

  // Agent state
  agent_strategy: string
  agent_thinking: string
  tools_called: ToolCall[]
  search_queries: string[]
  search_results_summary: SearchHit[]

  // Response
  response_pseudonymized: string
  response_tokens: number
  response_latency_ms: number

  // Quality signals
  hybrid_search_scores: Record<string, any>
  sources_cited: string[]
  confidence_score?: number

  // Full capture
  llm_calls: LLMCall[]
  search_operations: SearchOperation[]
  tool_executions: ToolExecution[]
  phase_metrics: PhaseMetrics[]

  // Execution context
  agent_mode: string
  orchestrator_model: string
  worker_model: string
  orchestrator_provider: string
  worker_provider: string

  // Timing
  orchestrator_duration_ms: number
  worker_duration_ms: number
  total_duration_ms: number

  // Token breakdown
  tokens_per_model: Record<string, number>

  // Quality
  early_exit_triggered: boolean
  early_exit_confidence?: number
  total_sources_found: number
  deduplicated_sources: number

  // Entity mapping (PII)
  entity_mapping?: Record<string, string>
}
