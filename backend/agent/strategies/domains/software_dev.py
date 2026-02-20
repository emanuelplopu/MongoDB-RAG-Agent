"""Software Development Strategy - Optimized for technical and code-related queries.

This strategy is designed for:
- Code documentation and API references
- Technical architecture questions
- Error troubleshooting and debugging
- Software engineering best practices
"""

from typing import Dict, Any, List

from backend.agent.strategies.base import (
    BaseStrategy, StrategyMetadata, StrategyConfig, StrategyDomain
)
from backend.agent.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class SoftwareDevStrategy(BaseStrategy):
    """Strategy optimized for software development and technical queries."""
    
    metadata = StrategyMetadata(
        id="software_dev",
        name="Software Development Strategy",
        version="1.0.0",
        description="Optimized for code documentation, technical architecture, API references, and debugging queries",
        domains=[StrategyDomain.SOFTWARE_DEV],
        tags=["technical", "code", "api", "debugging", "architecture"],
        author="system",
        is_default=False,
        is_legacy=False
    )
    
    config = StrategyConfig(
        max_iterations=3,
        confidence_threshold=0.75,
        early_exit_enabled=True,
        cross_search_boost=1.2,  # Higher boost for cross-matches in technical docs
        content_length_penalty=True,
        custom_params={
            "code_snippet_bonus": 1.15,  # Bonus for results containing code
            "api_doc_priority": True,
            "prefer_official_docs": True
        }
    )
    
    def get_analyze_prompt(self) -> str:
        """Return software development focused analysis prompt."""
        return '''Analyze this software development query with technical precision.

User message: {user_message}

Conversation context:
{conversation_history}

Identify:
1. QUERY_TYPE: One of:
   - CODE_EXPLANATION: Understanding existing code
   - API_USAGE: How to use an API/library
   - DEBUGGING: Fixing errors or bugs
   - ARCHITECTURE: System design questions
   - BEST_PRACTICES: Software engineering patterns
   - CONFIGURATION: Setup and config questions

2. TECHNICAL_ENTITIES:
   - programming_languages: [list any mentioned: Python, JavaScript, etc.]
   - frameworks: [React, Django, Spring, etc.]
   - libraries: [specific packages/modules]
   - apis: [REST endpoints, GraphQL, etc.]
   - error_codes: [specific error messages]
   - file_paths: [any mentioned paths]

3. CODE_CONTEXT:
   - has_code_snippet: true/false
   - language_detected: detected programming language
   - error_type: if debugging, the error category

4. SEARCH_QUERIES:
   - primary: main technical search query
   - alternatives: [2-3 alternative phrasings]
   - code_specific: query optimized for code search

Return JSON:
{
    "intent_summary": "brief technical summary",
    "query_type": "QUERY_TYPE",
    "technical_entities": {...},
    "code_context": {...},
    "search_queries": {...},
    "sources_needed": ["profile", "personal", "web"],
    "source_priority": "profile" or "web",
    "complexity": 1-5,
    "requires_code_examples": true/false
}'''

    def get_plan_prompt(self) -> str:
        """Return software development focused planning prompt."""
        return '''Create a search plan for this technical query.

Analysis:
{analysis}

Available sources:
{available_sources}

For technical queries, prioritize:
1. Official documentation
2. Code repositories and examples
3. Technical blogs and tutorials
4. Stack Overflow-style Q&A (via web search)

Create targeted search tasks. For each task specify:
- type: search_profile, search_personal, search_all, or web_search
- query: Technical query with specific terms
- context_hint: What technical aspect to focus on
- priority: 1 (highest) to 5 (lowest)

Return JSON:
{
    "intent_summary": "technical goal summary",
    "reasoning": "why this search approach",
    "strategy": "parallel" or "sequential",
    "tasks": [
        {
            "id": "task_id",
            "type": "task_type",
            "query": "technical search query",
            "context_hint": "focus area",
            "priority": 1,
            "max_results": 10
        }
    ],
    "success_criteria": "what constitutes a good technical answer",
    "max_iterations": 2
}'''

    def get_evaluate_prompt(self) -> str:
        """Return software development focused evaluation prompt."""
        return '''Evaluate technical search results for quality and relevance.

Original intent: {intent}
Success criteria: {success_criteria}

Results:
{results_summary}

For technical queries, evaluate:
1. Code relevance: Do results contain relevant code examples?
2. Version accuracy: Is the information for the right version/framework?
3. Completeness: Are all technical aspects addressed?
4. Actionability: Can the user implement based on these results?

Return JSON:
{
    "findings_summary": "what technical information was found",
    "gaps_identified": ["missing technical aspects"],
    "decision": "sufficient" | "need_refinement" | "need_expansion" | "cannot_answer",
    "confidence": 0.0-1.0,
    "reasoning": "technical evaluation reasoning",
    "follow_up_tasks": [
        {
            "id": "followup_id",
            "type": "task_type",
            "query": "refined technical query"
        }
    ]
}'''

    def get_synthesize_prompt(self) -> str:
        """Return software development focused synthesis prompt."""
        return '''Generate a technical response for a software development query.

User question: {user_message}

Technical information found:
{all_results}

Guidelines for technical responses:
1. Start with a clear, direct answer
2. Include code examples when relevant (use markdown code blocks)
3. Specify versions/compatibility when applicable
4. Use proper technical terminology
5. Include file paths in code reference format
6. Add warnings for common pitfalls
7. Cite documentation sources

Format code blocks with language identifiers:
```python
# Example code
```

If multiple approaches exist, list them with pros/cons.
Include links to official documentation when available.

Generate a comprehensive technical response:'''

    def process_analysis(self, raw_result: Dict) -> Dict:
        """Process analysis with software development specific enhancements."""
        # Set defaults
        raw_result.setdefault("intent_summary", "")
        raw_result.setdefault("query_type", "CODE_EXPLANATION")
        raw_result.setdefault("technical_entities", {
            "programming_languages": [],
            "frameworks": [],
            "libraries": [],
            "apis": [],
            "error_codes": [],
            "file_paths": []
        })
        raw_result.setdefault("code_context", {
            "has_code_snippet": False,
            "language_detected": None,
            "error_type": None
        })
        raw_result.setdefault("search_queries", {"primary": "", "alternatives": []})
        raw_result.setdefault("sources_needed", ["profile", "web"])
        raw_result.setdefault("source_priority", "profile")
        raw_result.setdefault("complexity", 2)
        raw_result.setdefault("requires_code_examples", True)
        
        # Legacy compatibility
        all_entities = []
        for category in raw_result.get("technical_entities", {}).values():
            if isinstance(category, list):
                all_entities.extend(category)
        raw_result["key_entities"] = list(set(all_entities))
        raw_result["named_entities"] = raw_result.get("technical_entities", {})
        raw_result["is_complex"] = raw_result.get("complexity", 2) >= 3
        raw_result["requires_multiple_searches"] = raw_result.get("complexity", 2) >= 2
        
        return raw_result

    def should_early_exit(
        self,
        results: List,
        evaluation: Dict,
        iteration: int
    ) -> bool:
        """Determine if we should exit early for technical queries."""
        # Handle None or empty evaluation
        if not evaluation:
            return False
        
        decision = evaluation.get("decision", "")
        confidence = float(evaluation.get("confidence", 0.0) or 0.0)
        
        # For technical queries, we want higher confidence
        if decision == "sufficient" and confidence >= self.config.confidence_threshold:
            return True
        
        # Check if we have code examples
        has_code = False
        if results:
            for r in results:
                r_str = str(r) if r else ""
                if "```" in r_str or "code" in r_str.lower():
                    has_code = True
                    break
        
        # Early exit if we have good results with code
        if decision == "sufficient" and has_code and confidence >= 0.65:
            return True
        
        return False

    def calculate_rrf_scores(
        self,
        results: List[Dict],
        limit: int,
        k: int = 60
    ) -> List[Dict]:
        """Calculate RRF scores with software development bonuses."""
        if not results:
            return []
        
        # Safely filter results by search type
        vector_results = [r for r in results if r and r.get("search_type") == "vector"]
        text_results = [r for r in results if r and r.get("search_type") == "text"]
        
        # Build ID sets safely
        vector_ids = set()
        for r in vector_results:
            chunk_id = r.get("chunk_id")
            if chunk_id is not None:
                vector_ids.add(str(chunk_id))
        
        text_ids = set()
        for r in text_results:
            chunk_id = r.get("chunk_id")
            if chunk_id is not None:
                text_ids.add(str(chunk_id))
        
        cross_match_ids = vector_ids & text_ids
        
        rrf_scores = {}
        result_map = {}
        
        for rank, doc in enumerate(vector_results):
            chunk_id = doc.get("chunk_id")
            if chunk_id is None:
                continue
            chunk_id = str(chunk_id)
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            result_map[chunk_id] = doc
        
        for rank, doc in enumerate(text_results):
            chunk_id = doc.get("chunk_id")
            if chunk_id is None:
                continue
            chunk_id = str(chunk_id)
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            if chunk_id not in result_map:
                result_map[chunk_id] = doc
        
        # Apply software development specific adjustments
        for chunk_id in list(rrf_scores.keys()):  # Use list() to avoid modification during iteration
            doc = result_map.get(chunk_id)
            if not doc:
                continue
            content = (doc.get("content") or "").lower()
            
            # Cross-search boost
            if chunk_id in cross_match_ids:
                rrf_scores[chunk_id] *= self.config.cross_search_boost
            
            # Code snippet bonus
            if "```" in content or "def " in content or "function " in content or "class " in content:
                rrf_scores[chunk_id] *= self.config.custom_params.get("code_snippet_bonus", 1.15)
            
            # API documentation bonus
            if "api" in content or "endpoint" in content or "request" in content:
                rrf_scores[chunk_id] *= 1.1
            
            # Official documentation bonus (if detectable)
            if "official" in content or "documentation" in content:
                rrf_scores[chunk_id] *= 1.1
            
            # Content length penalty
            content_len = len(content)
            if content_len < 50:
                rrf_scores[chunk_id] *= 0.5
            elif content_len < 100:
                rrf_scores[chunk_id] *= 0.7
        
        # Normalize scores
        if rrf_scores:
            max_score = max(rrf_scores.values())
            min_score = min(rrf_scores.values())
            score_range = max_score - min_score
            
            if score_range > 0:
                for chunk_id in rrf_scores:
                    normalized = (rrf_scores[chunk_id] - min_score) / score_range
                    rrf_scores[chunk_id] = 0.5 + (normalized * 0.5)
        
        # Sort and return
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        merged = []
        for chunk_id in sorted_ids[:limit]:
            doc = result_map[chunk_id]
            doc["rrf_score"] = rrf_scores[chunk_id]
            doc["cross_match"] = chunk_id in cross_match_ids
            merged.append(doc)
        
        return merged
