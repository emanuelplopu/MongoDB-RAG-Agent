"""Legacy Strategy - Baseline implementation preserving original behavior.

This strategy provides the original, simpler prompts and behavior
before the enhanced improvements. It serves as:
- A baseline for A/B testing
- A fallback for stability
- A reference implementation
"""

from typing import Dict, Any, List, Optional

from backend.agent.strategies.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyConfig,
    StrategyDomain
)
from backend.agent.strategies.registry import register_strategy


# Original prompts before enhancements
LEGACY_PROMPTS = {
    "analyze": """You are analyzing a user's request to determine what information they need and which sources to search.

**User Message:**
{user_message}

**Conversation History:**
{conversation_history}

**Available Data Sources:**
- **profile**: Shared company/organization documents (policies, handbooks, internal docs)
- **cloud**: Cloud storage documents (Google Drive, Dropbox, WebDAV files)
- **personal**: User's private data (emails, personal documents, private files)
- **web**: Internet search via Brave Search + browse specific URLs

**Analyze and respond with JSON:**
{{
    "intent_summary": "Brief description of what the user wants",
    "key_entities": ["list", "of", "important", "terms"],
    "sources_needed": ["profile", "cloud", "personal", "web"],
    "is_complex": true/false,
    "requires_multiple_searches": true/false,
    "requires_web_browsing": true/false,
    "reasoning": "Your reasoning about how to approach this"
}}

**Analysis Guidelines:**
- For identity questions ("who am I", "my role"): prioritize personal data
- For company questions: prioritize profile documents
- For cloud files: prioritize cloud storage
- For current events or external info: use web search
- Complex questions may need multiple sources""",

    "plan": """You are creating a search plan based on the analysis.

**Analysis:**
{analysis}

**Available Sources:**
{available_sources}

**Create a plan with tasks. Each task should:**
- Have a clear, focused query
- Target specific sources when possible
- Be parallelizable when independent

**Task Types Available:**
- search_profile: Search shared company/organization documents
- search_cloud: Search cloud storage (Google Drive, Dropbox, etc.)
- search_personal: Search user's private data (emails, personal documents)
- search_all: Search across all accessible sources
- web_search: Search the internet using Brave Search
- browse_web: Fetch content from a specific URL

**Respond with JSON:**
{{
    "intent_summary": "What the user wants",
    "reasoning": "Your strategy",
    "strategy": "parallel" | "sequential" | "iterative",
    "tasks": [
        {{
            "id": "unique_id",
            "type": "search_profile" | "search_cloud" | "search_personal" | "search_all" | "web_search" | "browse_web",
            "query": "the search query OR URL for browse_web",
            "sources": ["source_ids if specific"],
            "priority": 1,
            "depends_on": [],
            "max_results": 10,
            "context_hint": "what to look for"
        }}
    ],
    "success_criteria": "What would make this answer complete",
    "max_iterations": 3
}}

**Planning Guidelines:**
- For "who am I" or identity questions, search personal data first
- For company/organization questions, search profile first
- For cloud documents (Google Drive, Dropbox), use search_cloud
- Use web_search to find URLs, then browse_web to fetch specific pages
- Create 2-4 focused tasks rather than one broad task
- Use parallel strategy when tasks are independent
- Use sequential when later tasks depend on earlier results""",

    "evaluate": """You are evaluating search results to decide if more searching is needed.

**Original Intent:**
{intent}

**Success Criteria:**
{success_criteria}

**Results from Workers:**
{results_summary}

**Evaluate and respond with JSON:**
{{
    "phase": "initial" | "refinement" | "final",
    "findings_summary": "What was found",
    "gaps_identified": ["list of missing information"],
    "decision": "sufficient" | "need_refinement" | "need_expansion" | "cannot_answer",
    "follow_up_tasks": [
        // Only if decision is need_refinement or need_expansion
        {{
            "id": "unique_id",
            "type": "search type",
            "query": "refined query",
            "sources": [],
            "priority": 1,
            "depends_on": [],
            "max_results": 10,
            "context_hint": "what to look for"
        }}
    ],
    "reasoning": "Why this decision",
    "confidence": 0.0-1.0
}}

Guidelines:
- If key information is found, decision should be "sufficient"
- If results are empty but there are other search strategies, try "need_refinement"
- If results are partial, consider "need_expansion" for broader search
- "cannot_answer" only if exhausted all options""",

    "synthesize": """You are synthesizing a final answer from search results.

**User's Question:**
{user_message}

**All Retrieved Information:**
{all_results}

**Instructions:**
1. Answer the question directly and completely
2. Cite sources inline: [Source: document_title] or [Source: url]
3. If information is incomplete, acknowledge what's missing
4. Be specific - quote relevant excerpts
5. Organize the answer logically

If the search found no relevant information, say so clearly and explain what you searched for.""",

    "fast_response": """Based on the following information, answer the user's question.

**User Question:**
{user_message}

**Available Information:**
{context}

**Instructions:**
- Answer directly and concisely
- Cite sources when using specific information: [Source: document/page title]
- If information is not found, say so clearly"""
}


@register_strategy
class LegacyStrategy(BaseStrategy):
    """Legacy strategy preserving original behavior.
    
    This strategy uses the simpler, original prompts without:
    - Query-type classification
    - Structured entity extraction
    - Aggressive early exit
    - Cross-search boosting
    
    Use this as a baseline for A/B testing or when stability is preferred.
    """
    
    @property
    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            id="legacy",
            name="Legacy Strategy",
            version="1.0.0",
            description="Original baseline strategy with simpler prompts and behavior",
            domains=[StrategyDomain.GENERAL],
            tags=["baseline", "stable", "simple"],
            author="system",
            is_default=False,
            is_legacy=True
        )
    
    def get_analyze_prompt(self) -> str:
        return LEGACY_PROMPTS["analyze"]
    
    def get_plan_prompt(self) -> str:
        return LEGACY_PROMPTS["plan"]
    
    def get_evaluate_prompt(self) -> str:
        return LEGACY_PROMPTS["evaluate"]
    
    def get_synthesize_prompt(self) -> str:
        return LEGACY_PROMPTS["synthesize"]
    
    def get_fast_response_prompt(self) -> str:
        return LEGACY_PROMPTS["fast_response"]
    
    def process_analysis(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        """Simple analysis processing with basic defaults."""
        # Set simple defaults for missing fields
        raw_result.setdefault("intent_summary", "")
        raw_result.setdefault("key_entities", [])
        raw_result.setdefault("sources_needed", ["profile", "personal"])
        raw_result.setdefault("is_complex", False)
        raw_result.setdefault("requires_multiple_searches", True)
        raw_result.setdefault("requires_web_browsing", False)
        
        return raw_result
    
    def should_early_exit(
        self,
        results: List,
        evaluation: Optional[Dict[str, Any]] = None,
        iteration: int = 1
    ) -> bool:
        """Legacy strategy uses simple exit logic - only on explicit sufficient."""
        if evaluation is None:
            return False
        
        decision = evaluation.get("decision", "")
        return decision in ["sufficient", "cannot_answer"]
    
    def calculate_rrf_scores(
        self,
        results: List[Dict[str, Any]],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Standard RRF scoring without enhancements.
        
        Uses basic RRF formula without:
        - Cross-search boosting
        - Content length penalties
        """
        k = 60  # Standard RRF constant
        
        # Separate by search type
        vector_results = [r for r in results if r.get("search_type") == "vector"]
        text_results = [r for r in results if r.get("search_type") == "text"]
        
        rrf_scores = {}
        result_map = {}
        original_similarity = {}
        
        for rank, doc in enumerate(vector_results):
            chunk_id = str(doc["chunk_id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            result_map[chunk_id] = doc
            if "similarity" in doc:
                original_similarity[chunk_id] = doc["similarity"]
        
        for rank, doc in enumerate(text_results):
            chunk_id = str(doc["chunk_id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            if chunk_id not in result_map:
                result_map[chunk_id] = doc
        
        # Simple min-max normalization
        if rrf_scores:
            min_score = min(rrf_scores.values())
            max_score = max(rrf_scores.values())
            score_range = max_score - min_score
            
            if score_range > 0:
                for chunk_id in rrf_scores:
                    normalized = (rrf_scores[chunk_id] - min_score) / score_range
                    rrf_scores[chunk_id] = 0.5 + (normalized * 0.5)
            else:
                for chunk_id in rrf_scores:
                    rrf_scores[chunk_id] = original_similarity.get(chunk_id, 0.75)
        
        # Sort by RRF score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        merged = []
        for chunk_id in sorted_ids[:limit]:
            doc = result_map[chunk_id]
            doc["rrf_score"] = rrf_scores[chunk_id]
            if chunk_id in original_similarity:
                doc["vector_similarity"] = original_similarity[chunk_id]
            merged.append(doc)
        
        return merged
