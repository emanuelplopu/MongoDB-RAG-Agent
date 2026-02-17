"""Federated Agent Coordinator - Main entry point for the agent system.

The FederatedAgent coordinates the Orchestrator and WorkerPool to:
1. Process user messages
2. Execute multi-step search and reasoning
3. Generate comprehensive responses
4. Maintain full trace for transparency
"""

import logging
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from backend.agent.schemas import (
    AgentModeConfig, AgentMode, AgentTrace, AgentPlan,
    TaskDefinition, TaskType, WorkerResult, DocumentReference,
    DataSource, EvaluationDecision
)
from backend.agent.orchestrator import Orchestrator
from backend.agent.worker_pool import WorkerPool
from backend.agent.federated_search import FederatedSearch, get_federated_search
from backend.core.config import settings
from backend.routers.prompts import get_agent_prompt_sync

logger = logging.getLogger(__name__)


class FederatedAgent:
    """Main agent coordinator for orchestrator-worker architecture."""
    
    def __init__(
        self,
        config: Optional[AgentModeConfig] = None,
        federated_search: Optional[FederatedSearch] = None
    ):
        """Initialize the federated agent.
        
        Args:
            config: Agent configuration (mode, models, etc.)
            federated_search: FederatedSearch instance
        """
        self.config = config or AgentModeConfig()
        self.federated_search = federated_search or get_federated_search()
        
        # Initialize components with provider configuration
        self.orchestrator = Orchestrator(
            model=self.config.orchestrator_model,
            provider=settings.orchestrator_provider
        )
        self.worker_pool = WorkerPool(
            model=self.config.worker_model,
            provider=settings.worker_provider,
            max_workers=self.config.parallel_workers,
            federated_search=self.federated_search
        )
        
        # Current trace
        self.trace: Optional[AgentTrace] = None
    
    def _create_trace(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> AgentTrace:
        """Create a new trace for this execution.
        
        Args:
            user_id: User ID
            session_id: Session ID
        
        Returns:
            New AgentTrace instance
        """
        return AgentTrace(
            orchestrator_model=self.config.orchestrator_model,
            worker_model=self.config.worker_model,
            mode=self.config.mode,
            user_id=user_id,
            session_id=session_id
        )
    
    async def process(
        self,
        user_message: str,
        user_id: str,
        user_email: str,
        session_id: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        active_profile_key: Optional[str] = None,
        active_profile_database: Optional[str] = None,
        accessible_profile_keys: Optional[List[str]] = None
    ) -> Tuple[str, AgentTrace]:
        """Process a user message through the orchestrator-worker pipeline.
        
        Args:
            user_message: The user's message
            user_id: User's ID
            user_email: User's email
            session_id: Chat session ID
            conversation_history: Previous messages
            active_profile_key: Currently active profile key
            active_profile_database: Database of the active profile
            accessible_profile_keys: List of profile keys user has access to
        
        Returns:
            Tuple of (response text, AgentTrace)
        """
        # Reset components
        self.orchestrator.reset()
        self.worker_pool.reset()
        
        # Create trace
        self.trace = self._create_trace(user_id, session_id)
        
        # Determine execution mode
        use_thinking = self.config.should_use_thinking(user_message)
        
        if use_thinking:
            response = await self._process_with_orchestrator(
                user_message=user_message,
                user_id=user_id,
                user_email=user_email,
                conversation_history=conversation_history or [],
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys
            )
        else:
            response = await self._process_fast(
                user_message=user_message,
                user_id=user_id,
                user_email=user_email,
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys
            )
        
        # Finalize trace - use add methods to properly accumulate timing and token stats
        for step in self.orchestrator.steps:
            self.trace.add_orchestrator_step(step)
        for step in self.worker_pool.steps:
            self.trace.add_worker_step(step)
        self.trace.finalize()
        
        return response, self.trace
    
    async def _process_with_orchestrator(
        self,
        user_message: str,
        user_id: str,
        user_email: str,
        conversation_history: List[Dict[str, Any]],
        active_profile_key: Optional[str],
        active_profile_database: Optional[str],
        accessible_profile_keys: Optional[List[str]]
    ) -> str:
        """Process with full orchestrator-worker flow.
        
        Args:
            user_message: The user's message
            user_id: User's ID
            user_email: User's email
            conversation_history: Previous messages
            active_profile_key: Active profile key
            active_profile_database: Active profile database
            accessible_profile_keys: Accessible profile keys
        
        Returns:
            Response text
        """
        logger.info(f"Processing with orchestrator: '{user_message[:50]}...'")
        
        # Phase 1: Analyze
        analysis = await self.orchestrator.analyze(user_message, conversation_history)
        logger.info(f"Analysis: {analysis.get('intent_summary', 'unknown')}")
        
        # Get available sources
        available_sources = self.federated_search.get_accessible_sources(
            user_id=user_id,
            user_email=user_email,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=accessible_profile_keys
        )
        
        # Phase 2: Plan
        plan = await self.orchestrator.plan(
            analysis=analysis,
            available_sources=[{
                "id": s.id,
                "type": s.type if isinstance(s.type, str) else s.type.value,
                "display_name": s.display_name,
                "database": s.database
            } for s in available_sources]
        )
        self.trace.initial_plan = plan
        logger.info(f"Plan: {len(plan.tasks)} tasks, strategy: {plan.strategy}")
        
        # Execution loop
        all_results: List[WorkerResult] = []
        iteration = 0
        empty_result_iterations = 0  # Track consecutive iterations with no results
        
        while iteration < plan.max_iterations:
            iteration += 1
            self.trace.iterations = iteration
            
            # Get tasks for this iteration
            if iteration == 1:
                tasks = plan.tasks
            else:
                tasks = evaluation.follow_up_tasks
            
            if not tasks:
                break
            
            # Execute tasks
            results = await self.worker_pool.execute_tasks(
                tasks=tasks,
                user_id=user_id,
                user_email=user_email,
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys
            )
            all_results.extend(results)
            
            # Check if this iteration found any results
            iteration_docs = sum(len(r.documents_found) for r in results)
            iteration_links = sum(len(r.web_links_found) for r in results)
            
            if iteration_docs == 0 and iteration_links == 0:
                empty_result_iterations += 1
                logger.warning(f"Iteration {iteration} found no results ({empty_result_iterations} consecutive empty iterations)")
                
                # Early exit if we've had 2 consecutive iterations with no results
                # No point in continuing to refine queries that aren't finding anything
                if empty_result_iterations >= 2:
                    logger.info("Exiting early: 2 consecutive iterations with no results")
                    break
            else:
                empty_result_iterations = 0  # Reset counter if we found something
            
            # Collect sources for trace
            for r in results:
                for doc in r.documents_found:
                    if doc.id not in [d.id for d in self.trace.all_documents]:
                        self.trace.all_documents.append(doc)
                for link in r.web_links_found:
                    if link.url not in [l.url for l in self.trace.all_web_links]:
                        self.trace.all_web_links.append(link)
            
            # Phase 3: Evaluate
            evaluation = await self.orchestrator.evaluate(plan, all_results, iteration)
            self.trace.evaluation_history.append(evaluation)
            
            logger.info(f"Iteration {iteration}: {evaluation.decision}, confidence: {evaluation.confidence}")
            
            if evaluation.decision in ["sufficient", "cannot_answer"]:
                break
            
            if not evaluation.follow_up_tasks:
                break
        
        # Phase 4: Synthesize
        response = await self.orchestrator.synthesize(user_message, all_results)
        
        return response
    
    async def _process_fast(
        self,
        user_message: str,
        user_id: str,
        user_email: str,
        active_profile_key: Optional[str],
        active_profile_database: Optional[str],
        accessible_profile_keys: Optional[List[str]]
    ) -> str:
        """Process with fast single-model approach.
        
        For simple queries, skip orchestration and do direct search + response.
        
        Args:
            user_message: The user's message
            user_id: User's ID
            user_email: User's email
            active_profile_key: Active profile key
            active_profile_database: Active profile database
            accessible_profile_keys: Accessible profile keys
        
        Returns:
            Response text
        """
        logger.info(f"Processing fast: '{user_message[:50]}...'")
        
        # Create simple search tasks
        tasks = [
            TaskDefinition(
                id="search_all",
                type=TaskType.SEARCH_ALL,
                query=user_message,
                max_results=10
            )
        ]
        
        # Add web search for questions that might need external info
        question_words = ["what is", "who is", "how to", "why", "when", "where"]
        if any(w in user_message.lower() for w in question_words):
            tasks.append(TaskDefinition(
                id="web_search",
                type=TaskType.WEB_SEARCH,
                query=user_message,
                max_results=5
            ))
        
        # Execute tasks
        results = await self.worker_pool.execute_tasks(
            tasks=tasks,
            user_id=user_id,
            user_email=user_email,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=accessible_profile_keys
        )
        
        # Collect sources for trace
        for r in results:
            for doc in r.documents_found:
                if doc.id not in [d.id for d in self.trace.all_documents]:
                    self.trace.all_documents.append(doc)
            for link in r.web_links_found:
                if link.url not in [l.url for l in self.trace.all_web_links]:
                    self.trace.all_web_links.append(link)
        
        self.trace.iterations = 1
        
        # Generate response using fast model
        response = await self._generate_fast_response(user_message, results)
        
        return response
    
    def _get_worker_model_string(self) -> str:
        """Get the worker model string in LiteLLM format."""
        provider = settings.worker_provider.lower()
        model = self.config.worker_model
        
        if provider == "openai":
            return model
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
    
    async def _generate_fast_response(
        self,
        user_message: str,
        results: List[WorkerResult]
    ) -> str:
        """Generate response using fast model.
        
        Args:
            user_message: User's message
            results: Search results
        
        Returns:
            Response text
        """
        from litellm import acompletion
        from backend.core.config import settings
        
        # Compile context from results
        context_parts = []
        for r in results:
            for doc in r.documents_found[:5]:
                context_parts.append(f"[Document: {doc.title}]\n{doc.full_content or doc.excerpt}")
            for link in r.web_links_found[:3]:
                context_parts.append(f"[Web: {link.title}]\n{link.excerpt}")
        
        context = "\n\n".join(context_parts) if context_parts else "No relevant information found."
        
        # Get prompt from database/defaults
        prompt_template = get_agent_prompt_sync("agent_fast_response")
        prompt = prompt_template.format(
            user_message=user_message,
            context=context
        )
        
        try:
            # Use the correct model string and API key based on provider
            model_string = self._get_worker_model_string()
            api_key = settings.get_worker_api_key()
            
            # Handle newer OpenAI models that require max_completion_tokens
            llm_params = {
                "model": model_string,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "api_key": api_key,
            }
            
            # Check if this is a newer OpenAI model
            if "gpt-5" in model_string.lower() or "gpt-4o" in model_string.lower():
                llm_params["max_completion_tokens"] = 1500
            else:
                llm_params["max_tokens"] = 1500
            
            response = await acompletion(**llm_params)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Fast response generation failed: {e}")
            return f"I encountered an error while generating a response: {str(e)}"
    
    async def cleanup(self):
        """Cleanup resources."""
        await self.worker_pool.cleanup()


# Factory function
def create_federated_agent(
    mode: AgentMode = AgentMode.AUTO,
    orchestrator_model: Optional[str] = None,
    worker_model: Optional[str] = None,
    max_iterations: int = 3
) -> FederatedAgent:
    """Create a FederatedAgent with the specified configuration.
    
    Args:
        mode: Agent mode (auto, thinking, fast)
        orchestrator_model: Model for orchestration
        worker_model: Model for worker tasks
        max_iterations: Maximum iterations
    
    Returns:
        Configured FederatedAgent
    """
    config = AgentModeConfig(
        mode=mode,
        orchestrator_model=orchestrator_model or settings.orchestrator_model,
        worker_model=worker_model or settings.worker_model,
        max_iterations=max_iterations
    )
    
    return FederatedAgent(config=config)
