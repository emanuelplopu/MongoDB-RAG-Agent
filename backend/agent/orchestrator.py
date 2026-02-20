"""Orchestrator - High-level thinking model for planning and synthesis.

The Orchestrator is responsible for:
1. Analyzing user intent
2. Creating a search/action plan
3. Evaluating worker results
4. Synthesizing final responses

It uses a "thinking" model (like GPT-5.1 or o1) for complex reasoning.
Prompts are loaded from the database for easy customization,
or from the active strategy if one is provided.
"""

import json
import logging
import time
from typing import List, Optional, Dict, Any, TYPE_CHECKING

from backend.agent.schemas import (
    TaskDefinition, TaskType, AgentPlan, EvaluationDecision,
    OrchestratorStep, OrchestratorPhase, WorkerResult, DocumentReference
)
from backend.core.config import settings
from backend.routers.prompts import get_agent_prompt_sync, DEFAULT_AGENT_PROMPTS

if TYPE_CHECKING:
    from backend.agent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


# Default prompts (used when database is not available)
# These are loaded from prompts.py and can be overridden in the database
def _get_default_prompt(key: str) -> str:
    """Get default prompt, falling back to hardcoded if needed."""
    return get_agent_prompt_sync(key)


class Orchestrator:
    """High-level thinking model that plans and coordinates searches."""
    
    def __init__(
        self,
        model: str = None,
        provider: str = None,
        strategy: "BaseStrategy" = None
    ):
        """Initialize orchestrator.
        
        Args:
            model: LLM model to use for orchestration
            provider: LLM provider (openai, google, anthropic)
            strategy: Strategy instance to use for prompts and processing
        """
        self.model = model or settings.orchestrator_model
        self.provider = provider or settings.orchestrator_provider
        self.strategy = strategy
        self.steps: List[OrchestratorStep] = []
        self._client = None
    
    def _get_model_string(self) -> str:
        """Get the model string in LiteLLM format with provider prefix."""
        provider = self.provider.lower()
        model = self.model
        
        if provider == "openai":
            return model  # No prefix needed
        elif provider == "google" or provider == "gemini":
            if not model.startswith("gemini/"):
                return f"gemini/{model}"
            return model
        elif provider == "anthropic" or provider == "claude":
            if not model.startswith("anthropic/"):
                return f"anthropic/{model}"
            return model
        elif provider == "ollama":
            if not model.startswith("ollama/"):
                return f"ollama/{model}"
            return model
        return model
    
    def reset(self):
        """Reset steps for a new session."""
        self.steps = []
    
    def _get_prompt(self, phase: str) -> str:
        """Get prompt for a phase, using strategy if available.
        
        Args:
            phase: One of 'analyze', 'plan', 'evaluate', 'synthesize'
        
        Returns:
            Prompt template string
        """
        if self.strategy:
            prompt_methods = {
                'analyze': self.strategy.get_analyze_prompt,
                'plan': self.strategy.get_plan_prompt,
                'evaluate': self.strategy.get_evaluate_prompt,
                'synthesize': self.strategy.get_synthesize_prompt,
            }
            method = prompt_methods.get(phase)
            if method:
                return method()
        
        # Fallback to default prompts
        prompt_keys = {
            'analyze': 'agent_analyze',
            'plan': 'agent_plan',
            'evaluate': 'agent_evaluate',
            'synthesize': 'agent_synthesize',
        }
        return _get_default_prompt(prompt_keys.get(phase, f'agent_{phase}'))
    
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
        from backend.core.config import settings
        
        start_time = time.time()
        
        try:
            # Get the model string with provider prefix
            model_string = self._get_model_string()
            api_key = settings.get_orchestrator_api_key()
            
            # Handle newer OpenAI models that require max_completion_tokens
            llm_params = {
                "model": model_string,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2 if phase != OrchestratorPhase.SYNTHESIZE else 0.7,
                "api_key": api_key,
            }
            
            # Check if this is a newer OpenAI model that requires max_completion_tokens
            if "gpt-5" in model_string.lower() or "gpt-4o" in model_string.lower():
                llm_params["max_completion_tokens"] = 2000
            else:
                llm_params["max_tokens"] = 2000
            
            response = await acompletion(**llm_params)
            
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
        """Phase 1: Analyze user intent with enhanced context extraction.
        
        Args:
            user_message: The user's message
            conversation_history: Previous messages in conversation
        
        Returns:
            Analysis dict with intent, entities, sources needed
        """
        history_str = self._extract_relevant_context(user_message, conversation_history)
        
        prompt = self._get_prompt("analyze").format(
            user_message=user_message,
            conversation_history=history_str
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.ANALYZE)
        
        # Use strategy's process_analysis if available
        if self.strategy:
            result = self.strategy.process_analysis(result)
        else:
            # Set defaults for missing fields with new schema compatibility
            result.setdefault("intent_summary", user_message[:100])
            result.setdefault("query_type", "FACTUAL")
            result.setdefault("named_entities", {"people": [], "organizations": [], "documents": [], "dates": [], "technical_terms": []})
            result.setdefault("search_queries", {"primary": user_message, "alternatives": []})
            result.setdefault("sources_needed", ["profile", "personal"])
            result.setdefault("source_priority", "profile")
            result.setdefault("must_find", [])
            result.setdefault("nice_to_have", [])
            result.setdefault("complexity", 2)
            result.setdefault("requires_multi_hop", False)
            
            # Legacy field compatibility
            result.setdefault("key_entities", self._flatten_entities(result.get("named_entities", {})))
            result.setdefault("is_complex", result.get("complexity", 2) >= 3)
            result.setdefault("requires_multiple_searches", result.get("complexity", 2) >= 2)
        
        return result
    
    def _extract_relevant_context(self, current_query: str, history: List[Dict]) -> str:
        """Extract relevant context from conversation history.
        
        Uses smarter selection to include:
        - Recent messages (last 3)
        - Earlier messages with entity overlap
        - Messages that seem to be part of the same topic
        
        Args:
            current_query: The current user message
            history: Full conversation history
        
        Returns:
            Formatted context string
        """
        if not history:
            return "(No previous messages)"
        
        relevant_parts = []
        
        # Extract terms from current query for relevance matching
        query_terms = set(word.lower() for word in current_query.split() if len(word) > 3)
        
        # Always include last 3 messages for immediate context
        recent_count = min(3, len(history))
        recent = history[-recent_count:]
        for msg in recent:
            role = msg.get('role', 'user')
            content = msg.get('content', '')[:400]
            relevant_parts.append(f"{role}: {content}")
        
        # Look for earlier messages with entity/term overlap
        earlier_relevant = []
        for msg in history[:-recent_count]:
            content = msg.get('content', '').lower()
            content_terms = set(word for word in content.split() if len(word) > 3)
            
            # Calculate overlap score
            overlap = len(query_terms & content_terms)
            
            # Include if significant overlap
            if overlap >= 2:
                earlier_relevant.append({
                    'role': msg.get('role', 'user'),
                    'content': msg.get('content', '')[:250],
                    'overlap': overlap
                })
        
        # Sort by relevance and take top 3
        earlier_relevant.sort(key=lambda x: x['overlap'], reverse=True)
        for msg in earlier_relevant[:3]:
            relevant_parts.insert(0, f"[Earlier relevant] {msg['role']}: {msg['content']}")
        
        return "\n".join(relevant_parts[:10])  # Max 10 context entries
    
    def _flatten_entities(self, named_entities: Dict[str, List[str]]) -> List[str]:
        """Flatten named entities dict into a simple list for legacy compatibility.
        
        Args:
            named_entities: Dict with entity categories as keys
        
        Returns:
            Flat list of all entities
        """
        all_entities = []
        for category, entities in named_entities.items():
            if isinstance(entities, list):
                all_entities.extend(entities)
        return list(set(all_entities))  # Deduplicate
    
    async def plan(
        self,
        analysis: Dict[str, Any],
        available_sources: List[Dict[str, Any]]
    ) -> AgentPlan:
        """Phase 2: Create execution plan with optimized queries.
        
        Uses the enhanced analysis to create targeted search tasks
        with entity-optimized queries.
        
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
        
        prompt = self._get_prompt("plan").format(
            analysis=json.dumps(analysis, indent=2),
            available_sources=sources_str
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.PLAN)
        
        # Parse tasks
        tasks = []
        for task_data in result.get("tasks", []):
            try:
                # Get query - prefer optimized queries from analysis if task query is generic
                query = task_data.get("query", "")
                if not query or query == analysis.get("intent_summary", ""):
                    # Use the primary search query from analysis if available
                    search_queries = analysis.get("search_queries", {})
                    if search_queries.get("primary"):
                        query = search_queries["primary"]
                    else:
                        query = analysis.get("intent_summary", "")
                
                task = TaskDefinition(
                    id=task_data.get("id", f"task_{len(tasks)}"),
                    type=TaskType(task_data.get("type", "search_all")),
                    query=query,
                    sources=task_data.get("sources", []),
                    priority=task_data.get("priority", 1),
                    depends_on=task_data.get("depends_on", []),
                    max_results=task_data.get("max_results", 10),
                    context_hint=task_data.get("context_hint")
                )
                tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to parse task: {e}")
        
        # If no tasks were created, create smart default tasks based on analysis
        if not tasks:
            tasks = self._create_default_tasks(analysis)
        
        return AgentPlan(
            intent_summary=result.get("intent_summary", analysis.get("intent_summary", "")),
            reasoning=result.get("reasoning", ""),
            strategy=result.get("strategy", "parallel"),
            tasks=tasks,
            success_criteria=result.get("success_criteria", "Find relevant information"),
            max_iterations=result.get("max_iterations", 2)
        )
    
    def _create_default_tasks(self, analysis: Dict[str, Any]) -> List[TaskDefinition]:
        """Create default search tasks based on analysis when planning fails.
        
        Args:
            analysis: The analysis result
        
        Returns:
            List of default TaskDefinition objects
        """
        tasks = []
        search_queries = analysis.get("search_queries", {})
        primary_query = search_queries.get("primary", analysis.get("intent_summary", ""))
        alternatives = search_queries.get("alternatives", [])
        source_priority = analysis.get("source_priority", "profile")
        
        # Primary search based on priority source
        source_type_map = {
            "profile": TaskType.SEARCH_PROFILE,
            "personal": TaskType.SEARCH_PERSONAL,
            "cloud": TaskType.SEARCH_CLOUD,
            "web": TaskType.WEB_SEARCH
        }
        
        primary_type = source_type_map.get(source_priority, TaskType.SEARCH_ALL)
        
        tasks.append(TaskDefinition(
            id="default_primary",
            type=primary_type,
            query=primary_query,
            priority=1,
            max_results=10
        ))
        
        # Add alternative query search if available
        if alternatives and len(alternatives) > 0:
            tasks.append(TaskDefinition(
                id="default_alternative",
                type=TaskType.SEARCH_ALL,
                query=alternatives[0],
                priority=2,
                max_results=5
            ))
        
        # Add web search for complex queries or external entities
        if analysis.get("requires_multi_hop") or source_priority == "web":
            tasks.append(TaskDefinition(
                id="default_web",
                type=TaskType.WEB_SEARCH,
                query=primary_query,
                priority=3,
                max_results=5
            ))
        
        return tasks
    
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
        
        prompt = self._get_prompt("evaluate").format(
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
        
        # Format for prompt - limit total content size to avoid context overflow
        # Estimate ~4 chars per token, aim for max ~10k tokens for results
        max_content_chars = 40000
        current_chars = 0
        limited_info = []
        
        for info in all_info:
            content = info.get("content", "")
            # Truncate individual content if needed
            if len(content) > 2000:
                content = content[:2000] + "..."
                info = {**info, "content": content}
            
            entry_chars = len(json.dumps(info))
            if current_chars + entry_chars > max_content_chars:
                logger.info(f"Truncating synthesis context: {len(limited_info)} of {len(all_info)} items used")
                break
            limited_info.append(info)
            current_chars += entry_chars
        
        # Format for prompt
        if limited_info:
            results_str = json.dumps(limited_info, indent=2)
        else:
            results_str = "No relevant information was found in the searches."
        
        prompt = self._get_prompt("synthesize").format(
            user_message=user_message,
            all_results=results_str
        )
        
        try:
            result = await self._call_llm(prompt, OrchestratorPhase.SYNTHESIZE, expect_json=False)
            response_text = result.get("response")
            
            # Check for empty or whitespace-only response
            if response_text and response_text.strip():
                return response_text
            
            logger.warning("LLM returned empty response during synthesis, using fallback")
            
        except Exception as e:
            logger.error(f"Synthesis LLM call failed: {e}")
        
        # Fallback: Generate a basic response from the found documents
        if limited_info:
            fallback_parts = ["Based on the search results, here's what I found:\n"]
            for i, info in enumerate(limited_info[:5], 1):
                title = info.get("title", "Untitled")
                content = info.get("content", "")[:500]
                source = info.get("source", info.get("type", "unknown"))
                fallback_parts.append(f"**{i}. {title}** (Source: {source})\n{content}\n")
            return "\n".join(fallback_parts)
        
        return "I was unable to generate a response. The search found relevant documents but synthesis failed. Please try again."
    
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
