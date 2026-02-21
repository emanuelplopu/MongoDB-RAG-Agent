"""Enhanced Strategy - Improved prompts and behavior with all optimizations.

This strategy includes:
- Query-type classification (FACTUAL, EXPLORATORY, COMPARATIVE, PROCEDURAL, AGGREGATE)
- Structured entity extraction
- Optimized search query generation
- Confidence-based early exit
- Cross-search boosting in RRF scoring
- Content length penalties

This is the recommended default strategy for most use cases.
"""

from typing import Dict, Any, List, Optional

from backend.agent.strategies.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyConfig,
    StrategyDomain
)
from backend.agent.strategies.registry import register_strategy


# Enhanced prompts with all improvements
ENHANCED_PROMPTS = {
    "analyze": """You are a search strategist analyzing a user's request with deep understanding to determine the optimal search approach.

**User Message:**
{user_message}

**Conversation History:**
{conversation_history}

**Available Data Sources:**
- **profile**: Shared company/organization documents (policies, handbooks, internal docs)
- **cloud**: Cloud storage documents (Google Drive, Dropbox, WebDAV files)
- **personal**: User's private data (emails, personal documents, private files)
- **web**: Internet search via Brave Search + browse specific URLs

**CRITICAL ANALYSIS TASKS:**

1. **Intent Classification** - What type of question is this?
   - FACTUAL: Looking for specific facts ("Who is X?", "What is the address?")
   - EXPLORATORY: Looking for understanding ("How does X work?", "Explain Y")
   - COMPARATIVE: Looking for differences ("Compare A and B")
   - PROCEDURAL: Looking for steps ("How to do X?")
   - AGGREGATE: Looking for summaries ("Summarize all X")

2. **Entity Extraction** - Extract ALL named entities:
   - People: names, roles, titles, relationships
   - Organizations: companies, teams, departments
   - Documents: file names, document types
   - Dates: specific dates, time periods, deadlines
   - Technical terms: jargon, product names, acronyms

3. **Search Query Generation** - Create optimized search queries:
   - Use extracted entities as exact match terms (quotes)
   - Generate synonym variations for key concepts
   - Create both broad and narrow query variants

**Respond with JSON:**
{{
    "intent_summary": "Clear statement of what user wants to know/achieve",
    "query_type": "FACTUAL|EXPLORATORY|COMPARATIVE|PROCEDURAL|AGGREGATE",
    "named_entities": {{
        "people": ["person1", "person2"],
        "organizations": ["org1", "org2"],
        "documents": ["doc type or name"],
        "dates": ["date or period"],
        "technical_terms": ["term1", "term2"]
    }},
    "search_queries": {{
        "primary": "Main search query with key entities",
        "alternatives": ["synonym variation 1", "broader query", "narrower query"]
    }},
    "sources_needed": ["profile", "cloud", "personal", "web"],
    "source_priority": "Which source is most likely to have the answer",
    "must_find": ["Critical piece of information required"],
    "nice_to_have": ["Additional context that would help"],
    "complexity": 1-5,
    "requires_multi_hop": true/false,
    "reasoning": "Your strategic analysis of how to best answer this"
}}

**Source Selection Guidelines:**
- Identity/personal questions ("who am I", "my role", "my emails"): personal first
- Company/organization questions ("our policy", "team handbook"): profile first
- Specific file lookups ("the budget spreadsheet"): cloud first
- Current events, external entities, general knowledge: web search
- Complex questions: use multiple sources in priority order

**Query Optimization Tips:**
- Put names and specific terms in quotes for exact match
- Include role/title when searching for people
- Use document type keywords (policy, handbook, report, email)
- For dates, include both numeric and written formats""",

    "plan": """You are creating an execution plan based on the analysis to find the best answer.

**Analysis Results:**
{analysis}

**Available Sources:**
{available_sources}

**PLANNING STRATEGY:**

1. **Query-Type Specific Approach:**
   - FACTUAL: Use exact entity matches, 2-3 targeted searches
   - EXPLORATORY: Broader searches, multiple perspectives
   - COMPARATIVE: Parallel searches for each item being compared
   - PROCEDURAL: Search for guides, documentation, how-to content
   - AGGREGATE: Multiple searches to gather comprehensive data

2. **Task Types Available:**
   - search_profile: Company docs (policies, handbooks, internal)
   - search_cloud: Cloud storage (Drive, Dropbox files)
   - search_personal: Private data (emails, personal docs)
   - search_all: All accessible sources simultaneously
   - web_search: Internet search for external info
   - browse_web: Fetch specific URL content

3. **Query Optimization Rules:**
   - Use quotes around names and specific terms: "John Smith"
   - Include entity types: "John Smith accountant"
   - Create variations: one exact, one broader, one with synonyms
   - For multi-hop questions, plan dependent searches

**Respond with JSON:**
{{
    "intent_summary": "What the user wants",
    "reasoning": "Why this plan will find the answer",
    "strategy": "parallel" | "sequential" | "iterative",
    "tasks": [
        {{
            "id": "task_1",
            "type": "search_profile|search_cloud|search_personal|search_all|web_search|browse_web",
            "query": "optimized search query with entities",
            "sources": ["specific_source_id"],
            "priority": 1,
            "depends_on": [],
            "max_results": 10,
            "context_hint": "What specifically to look for in results"
        }},
        {{
            "id": "task_2",
            "type": "...",
            "query": "alternative/broader query",
            "priority": 2,
            "depends_on": [],
            "max_results": 5,
            "context_hint": "..."
        }}
    ],
    "success_criteria": "What specific information would make this answer complete",
    "max_iterations": 2
}}

**Planning Best Practices:**
- Create 2-4 focused tasks, not one broad task
- Priority 1 tasks run first; if they succeed, lower priority may be skipped
- Use extracted entities from analysis in queries
- search_all is good for unknown document locations
- web_search first to find URLs, then browse_web for content
- For "who" questions about internal people, search personal/profile first
- For external entities (companies, public figures), use web search
- Set realistic max_iterations (1-2 for simple, 3 for complex)""",

    "evaluate": """You are evaluating search results to decide if we have enough information to answer the user's question.

**Original Intent:**
{intent}

**Success Criteria:**
{success_criteria}

**Results from Workers:**
{results_summary}

**EVALUATION DECISION FRAMEWORK:**

1. **"sufficient" (confidence 0.8-1.0)**: Use when:
   - The main question can be answered with found information
   - Key entities/facts mentioned in intent are ACTUALLY found in results
   - Sources are credible and DIRECTLY relevant to the question
   - Even if not perfect, we have enough to provide value

2. **"need_refinement" (confidence 0.4-0.7)**: Use when:
   - Partial information found but key gaps exist
   - Found related documents but not the specific answer
   - Query terms might need adjustment (synonyms, different phrasing)
   - Only use if refinement is likely to help (not already tried)

3. **"need_expansion" (confidence 0.3-0.6)**: Use when:
   - Few or no results from primary sources
   - Should try different source types (e.g., web if internal failed)
   - Need broader search scope

4. **"cannot_answer" (confidence 0.0-0.3)**: Use when:
   - Results found are UNRELATED to the question (different topics/entities)
   - Multiple searches returned nothing relevant
   - Topic is clearly outside available data
   - Already tried refinements without success
   - **IMPORTANT: If results are about completely different subjects than asked, use this!**

**CRITICAL: Relevance Check**
- Before marking "sufficient", verify results actually answer the question asked
- If results mention entirely different entities/topics than the question, that's NOT sufficient
- Example: User asks about "Company X" but results are about "Invoice Y" = cannot_answer
- Don't synthesize garbage - better to say "not found" than give irrelevant info

**Respond with JSON:**
{{
    "phase": "initial" | "refinement" | "final",
    "findings_summary": "What key information was found",
    "answer_possible": true/false,
    "gaps_identified": ["specific missing info if any"],
    "decision": "sufficient" | "need_refinement" | "need_expansion" | "cannot_answer",
    "follow_up_tasks": [
        // ONLY if decision is need_refinement or need_expansion
        // Max 2 follow-up tasks
        {{
            "id": "followup_1",
            "type": "search type",
            "query": "refined query - different from original",
            "priority": 1,
            "max_results": 5,
            "context_hint": "specific thing to look for"
        }}
    ],
    "reasoning": "Why this decision - be specific about what was found/missing",
    "confidence": 0.0-1.0
}}

**Remember:** 
- Empty results after 2 iterations = stop searching
- Results must be RELEVANT to the question, not just exist
- Don't create follow-up tasks similar to already-executed tasks""",

    "synthesize": """You are synthesizing a comprehensive, well-cited answer from search results.

**User's Question:**
{user_message}

**All Retrieved Information:**
{all_results}

**SYNTHESIS FRAMEWORK:**

1. **Answer Structure** (Follow this order):
   - **Direct Answer**: Start with a clear, direct response to the question (1-2 sentences)
   - **Supporting Details**: Evidence and context from sources
   - **Additional Context**: Related information that adds value
   - **Source Summary**: Brief note on which sources were used

2. **Citation Rules** (MANDATORY):
   - Every factual claim MUST have a citation
   - Citation format: [Source: "Document Title"]
   - For web sources: [Source: URL]
   - Place citation immediately after the claim, not at the end
   - If sources conflict, note the discrepancy: "Document A states X, while Document B indicates Y"

3. **Query-Type Response Guidelines:**

   **For FACTUAL questions** (Who, What, When, Where):
   - Lead with the specific answer
   - Provide the source immediately
   - Add relevant context
   Example: "The project manager is John Smith [Source: "Team Directory"]. He joined the company in 2022..."

   **For EXPLORATORY questions** (How, Why, Explain):
   - Structure with clear sections/headers if complex
   - Build explanation logically
   - Connect concepts with transitions
   - Include examples from documents

   **For COMPARATIVE questions**:
   - Use parallel structure or comparison format
   - Address each item being compared
   - Highlight key similarities and differences
   - Cite sources for each comparison point

   **For PROCEDURAL questions** (How to):
   - Number the steps clearly
   - Include prerequisites and requirements
   - Note any warnings or important considerations
   - Reference detailed documentation

   **For AGGREGATE questions** (Summarize, List all):
   - Organize by category or theme
   - Include counts where relevant
   - Ensure comprehensive coverage
   - Note if information might be incomplete

4. **Quality Checks:**
   - Does the answer directly address the question?
   - Is every claim supported by a citation?
   - Is the response complete or are there acknowledged gaps?
   - Is the language clear and professional?

**If no relevant information was found:**
Say so clearly: "I searched [sources searched] but did not find information about [specific topic]. You may want to [suggest alternative approaches]."

**Generate your response now, following the framework above:**""",

    "fast_response": """Based on the following information, answer the user's question directly.

**User Question:**
{user_message}

**Available Information:**
{context}

**Response Requirements:**
1. **Start with the answer** - Don't begin with "Based on..." or "According to..."
2. **Cite every fact** - Format: [Source: "Document Title"]
3. **Be concise but complete** - Include key details, skip fluff
4. **Acknowledge gaps** - If information is incomplete, say what's missing

**Example Good Response:**
"John Smith is the project manager for the Alpha initiative [Source: "Team Directory"]. He reports to Sarah Jones and has been in this role since January 2024 [Source: "Org Chart Q1"]."

**Example Bad Response:**
"Based on the search results, I found that the project manager appears to be John Smith according to some documents."

**Generate your response now:**"""
}


@register_strategy
class EnhancedStrategy(BaseStrategy):
    """Enhanced strategy with all optimizations.
    
    This strategy includes:
    - Query-type classification (FACTUAL, EXPLORATORY, etc.)
    - Structured entity extraction
    - Optimized search queries
    - Confidence-based early exit
    - Cross-search RRF boosting
    - Content length penalties
    
    Recommended as the default strategy for most use cases.
    """
    
    @property
    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            id="enhanced",
            name="Enhanced Strategy",
            version="2.0.0",
            description="Optimized strategy with query-type classification, entity extraction, and smart early exit",
            domains=[StrategyDomain.GENERAL],
            tags=["optimized", "smart", "citation-heavy", "fast-exit"],
            author="system",
            is_default=True,
            is_legacy=False
        )
    
    def get_analyze_prompt(self) -> str:
        return ENHANCED_PROMPTS["analyze"]
    
    def get_plan_prompt(self) -> str:
        return ENHANCED_PROMPTS["plan"]
    
    def get_evaluate_prompt(self) -> str:
        return ENHANCED_PROMPTS["evaluate"]
    
    def get_synthesize_prompt(self) -> str:
        return ENHANCED_PROMPTS["synthesize"]
    
    def get_fast_response_prompt(self) -> str:
        return ENHANCED_PROMPTS["fast_response"]
    
    def process_analysis(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        """Enhanced analysis processing with structured entity extraction."""
        # Set defaults for the enhanced schema
        raw_result.setdefault("intent_summary", "")
        raw_result.setdefault("query_type", "FACTUAL")
        raw_result.setdefault("named_entities", {
            "people": [],
            "organizations": [],
            "documents": [],
            "dates": [],
            "technical_terms": []
        })
        raw_result.setdefault("search_queries", {
            "primary": "",
            "alternatives": []
        })
        raw_result.setdefault("sources_needed", ["profile", "personal"])
        raw_result.setdefault("source_priority", "profile")
        raw_result.setdefault("must_find", [])
        raw_result.setdefault("nice_to_have", [])
        raw_result.setdefault("complexity", 2)
        raw_result.setdefault("requires_multi_hop", False)
        
        # Legacy field compatibility
        raw_result.setdefault("key_entities", self._flatten_entities(
            raw_result.get("named_entities", {})
        ))
        raw_result.setdefault("is_complex", raw_result.get("complexity", 2) >= 3)
        raw_result.setdefault("requires_multiple_searches", raw_result.get("complexity", 2) >= 2)
        
        return raw_result
    
    def _flatten_entities(self, named_entities: Dict[str, List[str]]) -> List[str]:
        """Flatten named entities dict into a simple list."""
        all_entities = []
        for category, entities in named_entities.items():
            if isinstance(entities, list):
                all_entities.extend(entities)
        return list(set(all_entities))
    
    def should_early_exit(
        self,
        results: List,
        evaluation: Optional[Dict[str, Any]] = None,
        iteration: int = 1
    ) -> bool:
        """Enhanced early exit with confidence and quality checks."""
        if not self.config.early_exit_enabled:
            return False
        
        # Check evaluation confidence
        if evaluation:
            confidence = evaluation.get("confidence", 0)
            decision = evaluation.get("decision", "")
            
            # High confidence early exit
            if decision == "sufficient" and confidence >= self.config.confidence_threshold:
                return True
            
            # Cannot answer - stop
            if decision == "cannot_answer":
                return True
        
        # Check result quality
        if results:
            from backend.agent.schemas import ResultQuality
            
            excellent_count = sum(
                1 for r in results 
                if hasattr(r, 'result_quality') and r.result_quality == ResultQuality.EXCELLENT
            )
            good_count = sum(
                1 for r in results 
                if hasattr(r, 'result_quality') and r.result_quality in [ResultQuality.EXCELLENT, ResultQuality.GOOD]
            )
            
            # Calculate average score
            all_scores = []
            for r in results:
                if hasattr(r, 'documents_found'):
                    for d in r.documents_found:
                        if hasattr(d, 'similarity_score'):
                            all_scores.append(d.similarity_score)
            
            avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
            
            # Early exit on excellent results
            if excellent_count >= 2:
                return True
            
            # Early exit on good results with high scores
            if good_count >= 3 and avg_score > self.get_result_quality_threshold():
                return True
            
            # First iteration with good results
            if iteration == 1 and good_count >= 2:
                return True
        
        return False
    
    def calculate_rrf_scores(
        self,
        results: List[Dict[str, Any]],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Enhanced RRF scoring with cross-search boosting and content penalties."""
        k = 60  # RRF constant
        
        # Separate by search type
        vector_results = [r for r in results if r.get("search_type") == "vector"]
        text_results = [r for r in results if r.get("search_type") == "text"]
        
        # Create ID sets for cross-search detection
        vector_ids = {str(r["chunk_id"]) for r in vector_results}
        text_ids = {str(r["chunk_id"]) for r in text_results}
        cross_match_ids = vector_ids & text_ids
        
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
        
        # Apply quality adjustments
        for chunk_id in rrf_scores:
            doc = result_map[chunk_id]
            content = doc.get("content", "")
            
            # Cross-search boost
            if chunk_id in cross_match_ids:
                rrf_scores[chunk_id] *= self.config.cross_search_boost
            
            # Content length penalties
            if self.config.content_length_penalty:
                content_len = len(content)
                min_len = self.config.min_content_length
                
                if content_len < min_len:
                    rrf_scores[chunk_id] *= 0.5
                elif content_len < min_len * 2:
                    rrf_scores[chunk_id] *= 0.7
                elif content_len < min_len * 4:
                    rrf_scores[chunk_id] *= 0.85
                elif content_len > 500:
                    rrf_scores[chunk_id] *= 1.05
        
        # Normalize scores
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
        
        # Sort and return
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        merged = []
        for chunk_id in sorted_ids[:limit]:
            doc = result_map[chunk_id]
            doc["rrf_score"] = rrf_scores[chunk_id]
            if chunk_id in original_similarity:
                doc["vector_similarity"] = original_similarity[chunk_id]
            doc["cross_match"] = chunk_id in cross_match_ids
            merged.append(doc)
        
        return merged
