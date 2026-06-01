// Strategy OS telemetry types — wire shape returned by
// /api/v1/admin/telemetry/strategy/* endpoints.

export interface NodeOutputRecord {
  node_id: string
  node_type: string
  status: string
  duration_ms: number
  error: string | null
}

export interface StrategyRunRecord {
  trace_id: string
  run_id: string
  strategy_id: string
  strategy_version: string | null
  capability_id: string | null
  tenant_id: string | null
  status: 'completed' | 'failed' | 'timed_out' | 'cancelled'
  started_at: string
  completed_at: string
  duration_ms: number
  node_outputs: NodeOutputRecord[]
  halt_reason: string | null
}

export interface LLMCallRecord {
  call_id: string
  trace_id: string
  node_id: string
  node_type: string
  model: string
  provider: string
  model_role: string | null
  system_prompt: string | null
  user_prompt: string
  assistant_response: string
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  latency_ms: number
  temperature: number | null
  max_tokens: number | null
  cost_eur: number | null
  success: boolean
  error: string | null
  created_at: string
}

export interface AdaptiveDecisionCandidate {
  strategy_id: string
  scores: Record<string, number>
  total: number
  disqualified: boolean
  disqualify_reason: string | null
}

export interface AdaptiveDecisionRecord {
  decision_id: string
  capability_id: string
  decided_at: string
  selected_strategy_id: string
  candidates: AdaptiveDecisionCandidate[]
  fast_path_eligible: boolean
  reason: string
}

export interface StrategyStats {
  total_runs: number
  success_rate: number
  avg_duration_ms: number
  avg_llm_calls: number
  top_strategies: Array<{ strategy_id: string; count: number }>
}
