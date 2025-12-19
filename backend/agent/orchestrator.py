"""Orchestrator - High-level thinking model for planning and synthesis.

The Orchestrator is responsible for:
1. Analyzing user intent
2. Creating a search/action plan
3. Evaluating worker results
4. Synthesizing final responses

It uses a "thinking" model (like GPT-5.1 or o1) for complex reasoning.
"""

import json
import logging
import time
from typing import List, Optional, Dict, Any

from backend.agent.schemas import (
    TaskDefinition, TaskType, AgentPlan, EvaluationDecision,
    OrchestratorStep, OrchestratorPhase, WorkerResult, DocumentReference
)
from backend.core.config import settings

logger = logging.getLogger(__name__)


# Prompts for each orchestrator phase
ANALYZE_PROMPT = """You are analyzing a user's request to determine what information they need.

**User Message:**
{user_message}

**Conversation History:**
{conversation_history}

**Available Data Sources:**
- profile: Shared company/organization documents
- cloud: Cloud storage (Google Drive, Dropbox, etc.)
- personal: User's private data (emails, personal documents)
- web: Internet search

**Analyze and respond with JSON:**
{{
    "intent_summary": "Brief description of what the user wants",
    "key_entities": ["list", "of", "important", "terms"],
    "sources_needed": ["list of source types needed"],
    "is_complex": true/false,
    "requires_multiple_searches": true/false,
    "reasoning": "Your reasoning about how to approach this"
}}"""

PLAN_PROMPT = """You are creating a search plan based on the analysis.

**Analysis:**
{analysis}

**Available Sources:**
{available_sources}

**Create a plan with tasks. Each task should:**
- Have a clear, focused query
- Target specific sources when possible
- Be parallelizable when independent

**Respond with JSON:**
{{
    "intent_summary": "What the user wants",
    "reasoning": "Your strategy",
    "strategy": "parallel" | "sequential" | "iterative",
    "tasks": [
        {{
            "id": "unique_id",
            "type": "search_profile" | "search_cloud" | "search_personal" | "search_all" | "web_search",
            "query": "the search query",
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

Important:
- For "who am I" or identity questions, search personal data first
- For company/organization questions, search profile first
- Use web_search for external information only
- Create 2-4 focused tasks rather than one broad task"""

EVALUATE_PROMPT = """You are evaluating search results to decide if more searching is needed.

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
- "cannot_answer" only if exhausted all options"""

SYNTHESIZE_PROMPT = """You are synthesizing a final answer from search results.

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

If the search found no relevant information, say so clearly and explain what you searched for."""


class Orchestrator:
    """High-level thinking model that plans and coordinates searches."""
    
    def __init__(self, model: str = None):
        """Initialize orchestrator.
        
        Args:
            model: LLM model to use for orchestration
        """
        self.model = model or settings.orchestrator_model
        self.steps: List[OrchestratorStep] = []
        self._client = None
    
    def reset(self):
        """Reset steps for a new session."""
        self.steps = []
    
    async def _call_llm(
        self,
        prompt: str,
        phase: OrchestratorPhase,
        expect_json: bool = True
    ) -> Dict[str, Any]:
        """Call the LLM and record the step.
        
        Args:
            prompt: The prompt to send
            phase: Which phase this is
            expect_json: Whether to parse response as JSON
        
        Returns:
            Parsed response dict (or {"response": text} if not JSON)
        """
        from litellm import acompletion
        
        start_time = time.time()
        
        try:
            response = await acompletion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2 if phase != OrchestratorPhase.SYNTHESIZE else 0.7,
                max_tokens=2000,
            )
            
            content = response.choices[0].message.content
            tokens_used = response.usage.total_tokens if response.usage else 0
            duration_ms = (time.time() - start_time) * 1000
            
            # Try to parse JSON
            result = None
            if expect_json:
                try:
                    # Handle markdown code blocks
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0]
                    result = json.loads(content.strip())
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON from orchestrator: {content[:200]}")
                    result = {"response": content, "parse_error": True}
            else:
                result = {"response": content}
            
            # Record step
            step = OrchestratorStep(
                phase=phase,
                model=self.model,
                input_summary=prompt[:200] + "..." if len(prompt) > 200 else prompt,
                reasoning=result.get("reasoning", content[:500]),
                output_summary=json.dumps(result)[:500] if isinstance(result, dict) else str(result)[:500],
                tokens_used=tokens_used,
                duration_ms=duration_ms
            )
            self.steps.append(step)
            
            return result
            
        except Exception as e:
            logger.error(f"Orchestrator LLM call failed: {e}")
            duration_ms = (time.time() - start_time) * 1000
            
            # Record failed step
            step = OrchestratorStep(
                phase=phase,
                model=self.model,
                input_summary=prompt[:200] + "...",
                reasoning=f"Error: {str(e)}",
                output_summary="Failed",
                tokens_used=0,
                duration_ms=duration_ms
            )
            self.steps.append(step)
            
            raise
    
    async def analyze(
        self,
        user_message: str,
        conversation_history: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Phase 1: Analyze user intent.
        
        Args:
            user_message: The user's message
            conversation_history: Previous messages in conversation
        
        Returns:
            Analysis dict with intent, entities, sources needed
        """
        history_str = ""
        if conversation_history:
            history_str = "\n".join([
                f"{m.get('role', 'user')}: {m.get('content', '')[:200]}"
                for m in conversation_history[-5:]  # Last 5 messages
            ])
        else:
            history_str = "(No previous messages)"
        
        prompt = ANALYZE_PROMPT.format(
            user_message=user_message,
            conversation_history=history_str
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.ANALYZE)
        
        # Set defaults for missing fields
        result.setdefault("intent_summary", user_message[:100])
        result.setdefault("key_entities", [])
        result.setdefault("sources_needed", ["profile", "personal"])
        result.setdefault("is_complex", False)
        result.setdefault("requires_multiple_searches", True)
        
        return result
    
    async def plan(
        self,
        analysis: Dict[str, Any],
        available_sources: List[Dict[str, Any]]
    ) -> AgentPlan:
        """Phase 2: Create execution plan.
        
        Args:
            analysis: Result from analyze phase
            available_sources: List of available data sources
        
        Returns:
            AgentPlan with tasks to execute
        """
        sources_str = "\n".join([
            f"- {s.get('id', 'unknown')}: {s.get('display_name', '')} (type: {s.get('type', '')})"
            for s in available_sources
        ])
        
        prompt = PLAN_PROMPT.format(
            analysis=json.dumps(analysis, indent=2),
            available_sources=sources_str
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.PLAN)
        
        # Parse tasks
        tasks = []
        for task_data in result.get("tasks", []):
            try:
                task = TaskDefinition(
                    id=task_data.get("id", f"task_{len(tasks)}"),
                    type=TaskType(task_data.get("type", "search_all")),
                    query=task_data.get("query", analysis.get("intent_summary", "")),
                    sources=task_data.get("sources", []),
                    priority=task_data.get("priority", 1),
                    depends_on=task_data.get("depends_on", []),
                    max_results=task_data.get("max_results", 10),
                    context_hint=task_data.get("context_hint")
                )
                tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to parse task: {e}")
        
        # If no tasks were created, create a default search task
        if not tasks:
            tasks = [
                TaskDefinition(
                    id="default_search",
                    type=TaskType.SEARCH_ALL,
                    query=analysis.get("intent_summary", ""),
                    max_results=10
                )
            ]
        
        return AgentPlan(
            intent_summary=result.get("intent_summary", analysis.get("intent_summary", "")),
            reasoning=result.get("reasoning", ""),
            strategy=result.get("strategy", "parallel"),
            tasks=tasks,
            success_criteria=result.get("success_criteria", "Find relevant information"),
            max_iterations=result.get("max_iterations", 3)
        )
    
    async def evaluate(
        self,
        plan: AgentPlan,
        results: List[WorkerResult],
        iteration: int = 1
    ) -> EvaluationDecision:
        """Phase 3: Evaluate results and decide next steps.
        
        Args:
            plan: The execution plan
            results: Results from worker executions
            iteration: Current iteration number
        
        Returns:
            EvaluationDecision with next steps
        """
        # Summarize results
        results_summary = []
        for r in results:
            summary = {
                "task_id": r.task_id,
                "task_type": r.task_type,
                "query": r.query,
                "success": r.success,
                "documents_found": len(r.documents_found),
                "web_links_found": len(r.web_links_found),
                "quality": r.result_quality,
            }
            # Add document excerpts
            if r.documents_found:
                summary["top_documents"] = [
                    {"title": d.title, "excerpt": d.excerpt[:200]}
                    for d in r.documents_found[:3]
                ]
            if r.web_links_found:
                summary["top_links"] = [
                    {"title": l.title, "url": l.url}
                    for l in r.web_links_found[:3]
                ]
            results_summary.append(summary)
        
        prompt = EVALUATE_PROMPT.format(
            intent=plan.intent_summary,
            success_criteria=plan.success_criteria,
            results_summary=json.dumps(results_summary, indent=2)
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.EVALUATE)
        
        # Parse follow-up tasks
        follow_up_tasks = []
        for task_data in result.get("follow_up_tasks", []):
            try:
                task = TaskDefinition(
                    id=task_data.get("id", f"followup_{len(follow_up_tasks)}"),
                    type=TaskType(task_data.get("type", "search_all")),
                    query=task_data.get("query", ""),
                    sources=task_data.get("sources", []),
                    priority=task_data.get("priority", 1),
                    depends_on=task_data.get("depends_on", []),
                    max_results=task_data.get("max_results", 10),
                    context_hint=task_data.get("context_hint")
                )
                follow_up_tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to parse follow-up task: {e}")
        
        # Determine phase
        phase = "initial" if iteration == 1 else ("refinement" if iteration == 2 else "final")
        
        return EvaluationDecision(
            phase=phase,
            findings_summary=result.get("findings_summary", ""),
            gaps_identified=result.get("gaps_identified", []),
            decision=result.get("decision", "sufficient"),
            follow_up_tasks=follow_up_tasks,
            reasoning=result.get("reasoning", ""),
            confidence=result.get("confidence", 0.5)
        )
    
    async def synthesize(
        self,
        user_message: str,
        all_results: List[WorkerResult]
    ) -> str:
        """Phase 4: Generate final answer.
        
        Args:
            user_message: Original user message
            all_results: All results from all iterations
        
        Returns:
            Final answer string
        """
        # Compile all results
        all_info = []
        
        for r in all_results:
            if r.documents_found:
                for doc in r.documents_found:
                    all_info.append({
                        "type": "document",
                        "title": doc.title,
                        "source": doc.source_type,
                        "content": doc.full_content or doc.excerpt,
                        "score": doc.similarity_score
                    })
            if r.web_links_found:
                for link in r.web_links_found:
                    all_info.append({
                        "type": "web",
                        "title": link.title,
                        "url": link.url,
                        "content": link.fetched_content or link.excerpt
                    })
        
        # Sort by relevance
        all_info.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        # Format for prompt
        if all_info:
            results_str = json.dumps(all_info[:20], indent=2)  # Top 20 results
        else:
            results_str = "No relevant information was found in the searches."
        
        prompt = SYNTHESIZE_PROMPT.format(
            user_message=user_message,
            all_results=results_str
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.SYNTHESIZE, expect_json=False)
        
        return result.get("response", "I was unable to generate a response.")
    
    def _format_results_for_prompt(self, results: List[WorkerResult]) -> str:
        """Format worker results for inclusion in prompts.
        
        Args:
            results: List of worker results
        
        Returns:
            Formatted string
        """
        lines = []
        for r in results:
            lines.append(f"Task {r.task_id} ({r.task_type}):")
            lines.append(f"  Query: {r.query}")
            lines.append(f"  Documents: {len(r.documents_found)}")
            if r.documents_found:
                for d in r.documents_found[:3]:
                    lines.append(f"    - {d.title}: {d.excerpt[:100]}...")
            lines.append(f"  Web links: {len(r.web_links_found)}")
            if r.web_links_found:
                for l in r.web_links_found[:3]:
                    lines.append(f"    - {l.title}: {l.url}")
        return "\n".join(lines)
