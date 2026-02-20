"""HR Process Strategy - Optimized for HR policies and employee-related queries.

This strategy is designed for:
- Employee policy questions
- Benefits and compensation inquiries
- Onboarding and offboarding procedures
- Compliance with HR regulations
"""

from typing import Dict, Any, List

from backend.agent.strategies.base import (
    BaseStrategy, StrategyMetadata, StrategyConfig, StrategyDomain
)
from backend.agent.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class HRProcessStrategy(BaseStrategy):
    """Strategy optimized for HR processes and employee policy queries."""
    
    metadata = StrategyMetadata(
        id="hr",
        name="HR Process Strategy",
        version="1.0.0",
        description="Optimized for employee policies, benefits, procedures, compliance, and HR processes",
        domains=[StrategyDomain.HR],
        tags=["hr", "policy", "employee", "benefits", "procedures", "compliance"],
        author="system",
        is_default=False,
        is_legacy=False
    )
    
    config = StrategyConfig(
        max_iterations=2,
        confidence_threshold=0.75,
        early_exit_enabled=True,
        cross_search_boost=1.15,
        content_length_penalty=True,
        custom_params={
            "policy_focus": True,
            "procedure_formatting": True,
            "sensitivity_aware": True,
            "compliance_check": True
        }
    )
    
    def get_analyze_prompt(self) -> str:
        """Return HR focused analysis prompt."""
        return '''Analyze this HR-related query with attention to policy and procedure.

User message: {user_message}

Conversation context:
{conversation_history}

Identify:
1. QUERY_TYPE: One of:
   - POLICY_QUESTION: Understanding company policies
   - BENEFITS_INQUIRY: Benefits, compensation, perks
   - PROCEDURE_REQUEST: How to do something (leave, expense, etc.)
   - COMPLIANCE: Regulatory or compliance questions
   - ONBOARDING: New employee questions
   - OFFBOARDING: Departure-related questions
   - PERFORMANCE: Performance reviews, goals, feedback

2. HR_ENTITIES:
   - policies: [policy names mentioned]
   - benefits: [specific benefits: health, 401k, PTO]
   - procedures: [processes: leave request, expense report]
   - roles: [job roles, departments]
   - dates: [effective dates, deadlines, periods]
   - amounts: [salary, bonuses, limits]

3. HR_CONTEXT:
   - employee_type: full-time, part-time, contractor
   - urgency: routine, time-sensitive
   - sensitivity_level: general, confidential

4. SEARCH_QUERIES:
   - primary: main HR search query
   - alternatives: [2-3 alternative phrasings]
   - policy_specific: search for specific policy documents

Return JSON:
{
    "intent_summary": "HR query summary",
    "query_type": "QUERY_TYPE",
    "hr_entities": {...},
    "hr_context": {...},
    "search_queries": {...},
    "sources_needed": ["profile", "personal"],
    "source_priority": "profile",
    "complexity": 1-5,
    "requires_procedure_steps": true/false
}'''

    def get_plan_prompt(self) -> str:
        """Return HR focused planning prompt."""
        return '''Create a search plan for this HR query.

Analysis:
{analysis}

Available sources:
{available_sources}

For HR queries, prioritize:
1. Official company policies and handbooks
2. Procedure documents and guidelines
3. Benefits documentation
4. Forms and templates

Return JSON:
{
    "intent_summary": "HR search objective",
    "reasoning": "search rationale",
    "strategy": "parallel",
    "tasks": [
        {
            "id": "task_id",
            "type": "search_profile" or "search_personal",
            "query": "HR search query",
            "context_hint": "policy or procedure focus",
            "priority": 1,
            "max_results": 10
        }
    ],
    "success_criteria": "clear answer with procedure steps if applicable",
    "max_iterations": 2
}'''

    def get_evaluate_prompt(self) -> str:
        """Return HR focused evaluation prompt."""
        return '''Evaluate HR search results for policy accuracy and completeness.

Original intent: {intent}
Success criteria: {success_criteria}

Results:
{results_summary}

For HR queries, evaluate:
1. Policy Accuracy: Is the information from current, official policies?
2. Completeness: Are all steps/requirements included?
3. Clarity: Is the information clear and actionable?
4. Compliance: Does it align with regulations?

Return JSON:
{
    "findings_summary": "HR information found",
    "gaps_identified": ["missing information"],
    "decision": "sufficient" | "need_refinement" | "need_expansion" | "cannot_answer",
    "confidence": 0.0-1.0,
    "reasoning": "evaluation reasoning",
    "follow_up_tasks": [
        {
            "id": "followup_id",
            "type": "task_type",
            "query": "refined HR query"
        }
    ]
}'''

    def get_synthesize_prompt(self) -> str:
        """Return HR focused synthesis prompt."""
        return '''Generate a clear, helpful HR response.

User question: {user_message}

HR information found:
{all_results}

Guidelines for HR responses:
1. Be clear, professional, and helpful
2. Reference specific policies by name
3. For procedures, provide numbered steps
4. Include relevant deadlines or timeframes
5. Mention who to contact for additional help
6. Note any eligibility requirements
7. Handle sensitive information appropriately

Response structure:
1. **Answer**: Direct answer to the question
2. **Policy Reference**: Which policy this relates to
3. **Steps** (if procedural): Numbered action items
4. **Important Notes**: Deadlines, eligibility, exceptions
5. **Contact**: Who to reach out to for more help

Keep the tone professional but approachable.

Generate a helpful HR response:'''

    def process_analysis(self, raw_result: Dict) -> Dict:
        """Process analysis with HR-specific enhancements."""
        # Set defaults
        raw_result.setdefault("intent_summary", "")
        raw_result.setdefault("query_type", "POLICY_QUESTION")
        raw_result.setdefault("hr_entities", {
            "policies": [],
            "benefits": [],
            "procedures": [],
            "roles": [],
            "dates": [],
            "amounts": []
        })
        raw_result.setdefault("hr_context", {
            "employee_type": "full-time",
            "urgency": "routine",
            "sensitivity_level": "general"
        })
        raw_result.setdefault("search_queries", {"primary": "", "alternatives": []})
        raw_result.setdefault("sources_needed", ["profile", "personal"])
        raw_result.setdefault("source_priority", "profile")
        raw_result.setdefault("complexity", 2)
        raw_result.setdefault("requires_procedure_steps", False)
        
        # Detect if procedure steps are needed
        query_type = raw_result.get("query_type", "")
        if query_type in ["PROCEDURE_REQUEST", "ONBOARDING", "OFFBOARDING"]:
            raw_result["requires_procedure_steps"] = True
        
        # Legacy compatibility
        all_entities = []
        for category in raw_result.get("hr_entities", {}).values():
            if isinstance(category, list):
                all_entities.extend(category)
        raw_result["key_entities"] = list(set(all_entities))
        raw_result["named_entities"] = raw_result.get("hr_entities", {})
        raw_result["is_complex"] = raw_result.get("complexity", 2) >= 3
        raw_result["requires_multiple_searches"] = raw_result.get("complexity", 2) >= 2
        
        return raw_result

    def should_early_exit(
        self,
        results: List,
        evaluation: Dict,
        iteration: int
    ) -> bool:
        """Determine if we should exit early for HR queries."""
        # Handle None or empty evaluation
        if not evaluation:
            return False
        
        decision = evaluation.get("decision", "")
        confidence = float(evaluation.get("confidence", 0.0) or 0.0)
        
        # HR queries can exit relatively early with good confidence
        if decision == "sufficient" and confidence >= self.config.confidence_threshold:
            return True
        
        # For simple policy questions, can exit with moderate confidence
        if decision == "sufficient" and confidence >= 0.65 and iteration >= 1:
            return True
        
        return False

    def calculate_rrf_scores(
        self,
        results: List[Dict],
        limit: int,
        k: int = 60
    ) -> List[Dict]:
        """Calculate RRF scores with HR document bonuses."""
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
        
        # Apply HR-specific adjustments
        for chunk_id in list(rrf_scores.keys()):
            doc = result_map.get(chunk_id)
            if not doc:
                continue
            content = (doc.get("content") or "").lower()
            title = (doc.get("document_title") or "").lower()
            
            # Cross-search boost
            if chunk_id in cross_match_ids:
                rrf_scores[chunk_id] *= self.config.cross_search_boost
            
            # Policy document bonuses
            policy_indicators = [
                "policy", "handbook", "guideline", "procedure",
                "employee", "benefit", "leave", "pto", "vacation"
            ]
            for indicator in policy_indicators:
                if indicator in content or indicator in title:
                    rrf_scores[chunk_id] *= 1.1
                    break
            
            # Procedure/steps bonus
            if "step" in content or "1." in content or "first" in content:
                rrf_scores[chunk_id] *= 1.08
            
            # Forms/templates bonus
            if "form" in content or "template" in content or "submit" in content:
                rrf_scores[chunk_id] *= 1.05
            
            # Contact information bonus
            if "contact" in content or "email" in content or "hr@" in content:
                rrf_scores[chunk_id] *= 1.05
            
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
