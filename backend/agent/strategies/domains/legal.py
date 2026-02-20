"""Legal Analysis Strategy - Optimized for legal document review and analysis.

This strategy is designed for:
- Contract review and analysis
- Legal policy interpretation
- Compliance checking
- Legal research and precedent
"""

from typing import Dict, Any, List

from backend.agent.strategies.base import (
    BaseStrategy, StrategyMetadata, StrategyConfig, StrategyDomain
)
from backend.agent.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class LegalAnalysisStrategy(BaseStrategy):
    """Strategy optimized for legal document analysis and research."""
    
    metadata = StrategyMetadata(
        id="legal",
        name="Legal Analysis Strategy",
        version="1.0.0",
        description="Optimized for contract review, legal policy interpretation, compliance analysis, and legal research",
        domains=[StrategyDomain.LEGAL],
        tags=["legal", "contracts", "compliance", "policy", "formal"],
        author="system",
        is_default=False,
        is_legacy=False
    )
    
    config = StrategyConfig(
        max_iterations=3,
        confidence_threshold=0.85,  # Higher threshold for legal accuracy
        early_exit_enabled=True,
        cross_search_boost=1.25,  # Strong boost for cross-verified legal info
        content_length_penalty=True,
        custom_params={
            "citation_required": True,
            "formal_language": True,
            "disclaimer_required": True,
            "cross_reference_check": True
        }
    )
    
    def get_analyze_prompt(self) -> str:
        """Return legal analysis focused prompt."""
        return '''Analyze this legal query with precision and attention to detail.

User message: {user_message}

Conversation context:
{conversation_history}

Identify:
1. QUERY_TYPE: One of:
   - CONTRACT_REVIEW: Analyzing contract terms and clauses
   - POLICY_INTERPRETATION: Understanding legal policies
   - COMPLIANCE_CHECK: Verifying regulatory compliance
   - LEGAL_RESEARCH: General legal research
   - RIGHTS_OBLIGATIONS: Understanding rights and duties
   - RISK_ASSESSMENT: Identifying legal risks

2. LEGAL_ENTITIES:
   - parties: [named parties, organizations, individuals]
   - documents: [contracts, policies, agreements mentioned]
   - legal_terms: [specific legal terminology used]
   - jurisdictions: [applicable jurisdictions/regions]
   - dates: [effective dates, deadlines, periods]
   - clauses: [specific clause types: indemnity, liability, etc.]

3. LEGAL_CONTEXT:
   - document_type: type of legal document
   - is_time_sensitive: urgency indicator
   - requires_cross_reference: needs comparison with other docs

4. SEARCH_QUERIES:
   - primary: main legal search query with key terms
   - alternatives: [2-3 alternative legal phrasings]
   - clause_specific: search for specific clause types

Return JSON:
{
    "intent_summary": "precise legal query summary",
    "query_type": "QUERY_TYPE",
    "legal_entities": {...},
    "legal_context": {...},
    "search_queries": {...},
    "sources_needed": ["profile", "personal"],
    "source_priority": "profile",
    "complexity": 1-5,
    "requires_citation": true/false,
    "jurisdiction_specific": true/false
}'''

    def get_plan_prompt(self) -> str:
        """Return legal analysis focused planning prompt."""
        return '''Create a thorough search plan for this legal query.

Analysis:
{analysis}

Available sources:
{available_sources}

For legal queries, prioritize:
1. Primary legal documents (contracts, policies, agreements)
2. Related precedent documents
3. Policy guidelines and procedures
4. Cross-reference with related documents

Legal searches should be comprehensive and citation-ready.

Return JSON:
{
    "intent_summary": "legal search objective",
    "reasoning": "legal search rationale",
    "strategy": "parallel" or "sequential",
    "tasks": [
        {
            "id": "task_id",
            "type": "search_profile" or "search_personal",
            "query": "precise legal search query",
            "context_hint": "specific clause or section focus",
            "priority": 1,
            "max_results": 15
        }
    ],
    "success_criteria": "legal accuracy and completeness requirements",
    "max_iterations": 3
}'''

    def get_evaluate_prompt(self) -> str:
        """Return legal analysis focused evaluation prompt."""
        return '''Evaluate legal search results with rigorous standards.

Original intent: {intent}
Success criteria: {success_criteria}

Results:
{results_summary}

For legal queries, evaluate:
1. Source Authority: Are results from authoritative legal documents?
2. Relevance: Do results directly address the legal question?
3. Completeness: Are all relevant clauses/sections found?
4. Currency: Is the information current and valid?
5. Cross-Reference: Can findings be verified across documents?

Return JSON:
{
    "findings_summary": "legal findings with source citations",
    "gaps_identified": ["missing legal aspects or documents"],
    "decision": "sufficient" | "need_refinement" | "need_expansion" | "cannot_answer",
    "confidence": 0.0-1.0,
    "reasoning": "legal evaluation reasoning",
    "citation_quality": "assessment of source quality",
    "follow_up_tasks": [
        {
            "id": "followup_id",
            "type": "task_type",
            "query": "refined legal query"
        }
    ]
}'''

    def get_synthesize_prompt(self) -> str:
        """Return legal analysis focused synthesis prompt."""
        return '''Generate a formal legal analysis response.

User question: {user_message}

Legal information found:
{all_results}

Guidelines for legal responses:
1. Use formal, precise legal language
2. Always cite sources with document titles and relevant sections
3. Clearly distinguish between different document sources
4. Note any ambiguities or areas requiring professional review
5. Include relevant dates and parties when applicable
6. Structure response with clear sections
7. Add appropriate disclaimers

Response structure:
1. Summary: Brief answer to the legal question
2. Analysis: Detailed examination of relevant provisions
3. Citations: List all referenced documents and sections
4. Considerations: Any caveats or additional factors
5. Disclaimer: Legal advice disclaimer

**IMPORTANT**: Include the following disclaimer:
"This analysis is for informational purposes only and does not constitute legal advice. Please consult with a qualified legal professional for advice specific to your situation."

Generate a comprehensive legal analysis:'''

    def process_analysis(self, raw_result: Dict) -> Dict:
        """Process analysis with legal-specific enhancements."""
        # Set defaults
        raw_result.setdefault("intent_summary", "")
        raw_result.setdefault("query_type", "LEGAL_RESEARCH")
        raw_result.setdefault("legal_entities", {
            "parties": [],
            "documents": [],
            "legal_terms": [],
            "jurisdictions": [],
            "dates": [],
            "clauses": []
        })
        raw_result.setdefault("legal_context", {
            "document_type": None,
            "is_time_sensitive": False,
            "requires_cross_reference": True
        })
        raw_result.setdefault("search_queries", {"primary": "", "alternatives": []})
        raw_result.setdefault("sources_needed", ["profile", "personal"])
        raw_result.setdefault("source_priority", "profile")
        raw_result.setdefault("complexity", 3)  # Legal queries default higher complexity
        raw_result.setdefault("requires_citation", True)
        raw_result.setdefault("jurisdiction_specific", False)
        
        # Legacy compatibility
        all_entities = []
        for category in raw_result.get("legal_entities", {}).values():
            if isinstance(category, list):
                all_entities.extend(category)
        raw_result["key_entities"] = list(set(all_entities))
        raw_result["named_entities"] = raw_result.get("legal_entities", {})
        raw_result["is_complex"] = True  # Legal queries are always complex
        raw_result["requires_multiple_searches"] = True
        
        return raw_result

    def should_early_exit(
        self,
        results: List,
        evaluation: Dict,
        iteration: int
    ) -> bool:
        """Determine if we should exit early for legal queries.
        
        Legal queries require higher confidence and more thorough search.
        """
        # Handle None or empty evaluation
        if not evaluation:
            return False
        
        decision = evaluation.get("decision", "")
        confidence = float(evaluation.get("confidence", 0.0) or 0.0)
        
        # Legal queries need very high confidence
        if decision == "sufficient" and confidence >= self.config.confidence_threshold:
            return True
        
        # Don't exit early if citation quality is low
        citation_quality = evaluation.get("citation_quality", "unknown")
        if citation_quality in ["poor", "unknown"] and iteration < 2:
            return False
        
        # Allow early exit only with explicit sufficient and good confidence
        if decision == "sufficient" and confidence >= 0.80:
            return True
        
        return False

    def calculate_rrf_scores(
        self,
        results: List[Dict],
        limit: int,
        k: int = 60
    ) -> List[Dict]:
        """Calculate RRF scores with legal document bonuses."""
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
        
        # Apply legal-specific adjustments
        for chunk_id in list(rrf_scores.keys()):
            doc = result_map.get(chunk_id)
            if not doc:
                continue
            content = (doc.get("content") or "").lower()
            title = (doc.get("document_title") or "").lower()
            
            # Strong cross-search boost for legal (verification is critical)
            if chunk_id in cross_match_ids:
                rrf_scores[chunk_id] *= self.config.cross_search_boost
            
            # Legal document type bonuses
            legal_doc_indicators = [
                "contract", "agreement", "policy", "terms", "conditions",
                "clause", "section", "article", "provision"
            ]
            for indicator in legal_doc_indicators:
                if indicator in content or indicator in title:
                    rrf_scores[chunk_id] *= 1.1
                    break
            
            # Party/signature section bonus
            if "party" in content or "parties" in content or "signature" in content:
                rrf_scores[chunk_id] *= 1.05
            
            # Definitions section bonus (often critical in legal docs)
            if "definition" in content or "shall mean" in content:
                rrf_scores[chunk_id] *= 1.08
            
            # Content length - legal docs should have substantial content
            content_len = len(content)
            if content_len < 100:
                rrf_scores[chunk_id] *= 0.6  # Heavy penalty for very short legal content
            elif content_len < 200:
                rrf_scores[chunk_id] *= 0.8
            elif content_len > 500:
                rrf_scores[chunk_id] *= 1.05
        
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
