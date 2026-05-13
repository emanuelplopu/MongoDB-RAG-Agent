"""Worker Pool - Fast parallel task execution.

The Worker Pool executes search tasks in parallel using a fast model
(like Gemini Flash). It handles:
- Parallel execution of independent tasks
- Sequential execution of dependent tasks
- Result summarization and quality assessment
- Web search and browsing
"""

import asyncio
import logging
import time
import httpx
from typing import List, Dict, Any, Optional, Callable, Awaitable, Tuple

from backend.agent.schemas import (
    TaskDefinition, TaskType, WorkerResult, WorkerStep,
    DocumentReference, WebReference, ResultQuality
)
from backend.agent.federated_search import FederatedSearch, get_federated_search
from backend.agent.tool_gate import ToolGate
from backend.core.config import settings
from backend.services.activity_logger import NullActivityLogger
try:
    from backend.routers.prompts import get_agent_prompt_sync
except ImportError:
    def get_agent_prompt_sync(prompt_key: str) -> str:
        """Fallback used in tests when prompt router dependencies are unavailable."""
        return ""

logger = logging.getLogger(__name__)

# Callback type for task completion: (task_id, result, step) -> None
TaskCompleteCallback = Callable[[str, 'WorkerResult', 'WorkerStep'], Awaitable[None]]


class WorkerPool:
    """Pool of fast workers for parallel task execution."""
    
    def __init__(
        self,
        model: str = None,
        provider: str = None,
        max_workers: int = 4,
        federated_search: FederatedSearch = None,
        activity_logger=None,
        req_id: str = "no-req"
    ):
        """Initialize worker pool.
        
        Args:
            model: LLM model for worker tasks (summarization, etc.)
            provider: LLM provider (openai, google, anthropic)
            max_workers: Maximum concurrent workers
            federated_search: FederatedSearch instance for database searches
            activity_logger: ActivityLogger for observability (defaults to NullActivityLogger)
            req_id: Request correlation ID for structured logging
        """
        self.model = model or settings.worker_model
        self.provider = provider or settings.worker_provider
        self.max_workers = max_workers
        self.federated_search = federated_search or get_federated_search()
        self.steps: List[WorkerStep] = []
        self._http_client: Optional[httpx.AsyncClient] = None
        self.activity_logger = activity_logger or NullActivityLogger()
        self.req_id = req_id
    
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
    
    def _get_api_key(self) -> str:
        """Get the API key for the worker model based on the resolved provider."""
        # Use instance provider (which may have been resolved from model prefix)
        return settings.get_api_key_for_provider(self.provider)
    
    def reset(self):
        """Reset steps for a new session."""
        self.steps = []
    
    @property
    def http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def execute_tasks(
        self,
        tasks: List[TaskDefinition],
        user_id: str,
        user_email: str,
        active_profile_key: Optional[str] = None,
        active_profile_database: Optional[str] = None,
        accessible_profile_keys: Optional[List[str]] = None,
        on_task_complete: Optional[TaskCompleteCallback] = None
    ) -> List[WorkerResult]:
        """Execute tasks respecting dependencies.
        
        Args:
            tasks: List of tasks to execute
            user_id: Current user's ID
            user_email: Current user's email
            active_profile_key: Currently active profile key
            active_profile_database: Database of the active profile
            accessible_profile_keys: List of profile keys user has access to
            on_task_complete: Optional callback called when each task completes
        
        Returns:
            List of WorkerResult for each task
        """
        completed: Dict[str, WorkerResult] = {}
        pending = list(tasks)
        
        while pending:
            # Find tasks with satisfied dependencies
            ready = [t for t in pending if all(d in completed for d in t.depends_on)]
            
            if not ready:
                if pending:
                    logger.error(f"[req={self.req_id}] Circular dependency detected in tasks: {[t.id for t in pending]}")
                    # Break circular dependency by taking first pending task
                    ready = [pending[0]]
                else:
                    break
            
            # Execute ready tasks in parallel (up to max_workers)
            batch = ready[:self.max_workers]
            
            results = await asyncio.gather(*[
                self._execute_task(
                    task=task,
                    user_id=user_id,
                    user_email=user_email,
                    active_profile_key=active_profile_key,
                    active_profile_database=active_profile_database,
                    accessible_profile_keys=accessible_profile_keys,
                    prior_results=completed
                )
                for task in batch
            ], return_exceptions=True)
            
            # Record results and call callbacks
            for task, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.error(f"[req={self.req_id}] Task {task.id} failed with exception: {result}")
                    result = WorkerResult(
                        task_id=task.id,
                        task_type=task.type,
                        query=task.query,
                        success=False,
                        error=str(result),
                        result_quality=ResultQuality.EMPTY,
                        sources_searched=[]  # No sources searched on failure
                    )
                completed[task.id] = result
                pending.remove(task)
                
                # Call the completion callback for real-time streaming
                if on_task_complete:
                    try:
                        # Find the step for this task
                        step = next((s for s in self.steps if s.task_id == task.id), None)
                        await on_task_complete(task.id, result, step)
                    except Exception as e:
                        logger.error(f"[req={self.req_id}] Error calling on_task_complete for task {task.id}: {e}")
        
        return list(completed.values())
    
    async def _execute_task(
        self,
        task: TaskDefinition,
        user_id: str,
        user_email: str,
        active_profile_key: Optional[str],
        active_profile_database: Optional[str],
        accessible_profile_keys: Optional[List[str]],
        prior_results: Dict[str, WorkerResult]
    ) -> WorkerResult:
        """Execute a single task.
        
        Args:
            task: Task to execute
            user_id: User ID
            user_email: User email
            active_profile_key: Active profile key
            active_profile_database: Active profile database
            accessible_profile_keys: Accessible profile keys
            prior_results: Results from previously completed tasks
        
        Returns:
            WorkerResult
        """
        # Last line of defense: reject disabled task types
        task_type_val = task.type.value if hasattr(task.type, 'value') else str(task.type)
        if not ToolGate.is_enabled(task_type_val):
            logger.warning(f"[req={self.req_id}] ToolGate: Blocked disabled task {task.id} (type={task_type_val})")
            return WorkerResult(
                task_id=task.id,
                task_type=task.type,
                query=task.query,
                success=False,
                error=f"Tool '{task_type_val}' is disabled for this tenant",
                result_quality=ResultQuality.EMPTY,
                duration_ms=0.0,
            )
        
        self.activity_logger.log_phase(f"worker_task_{task.id}", "started", details={"type": task_type_val, "query": task.query[:200]})
        start_time = time.time()
        documents = []
        web_links = []
        sources_searched = []
        error = None
        success = True
        tokens_used = 0  # Track LLM token usage for tasks that call models
        
        try:
            if task.type in [TaskType.SEARCH_PROFILE, TaskType.SEARCH_CLOUD, 
                            TaskType.SEARCH_PERSONAL, TaskType.SEARCH_ALL]:
                # Database search (no LLM call — tokens remain 0)
                documents, sources_searched = await self._execute_search(
                    task=task,
                    user_id=user_id,
                    user_email=user_email,
                    active_profile_key=active_profile_key,
                    active_profile_database=active_profile_database,
                    accessible_profile_keys=accessible_profile_keys
                )
                
            elif task.type == TaskType.WEB_SEARCH:
                # Web search using Brave API (no LLM call — tokens remain 0)
                web_links = await self._execute_web_search(task.query)
                sources_searched = ["web"]
                
            elif task.type == TaskType.BROWSE_WEB:
                # Browse a specific URL (no LLM call — tokens remain 0)
                web_link = await self._execute_browse(task.query)
                if web_link:
                    web_links = [web_link]
                sources_searched = ["web"]
                    
            elif task.type == TaskType.SUMMARIZE:
                # Summarize prior results (uses LLM - tokens captured in _execute_summarize)
                summary, tokens_used = await self._execute_summarize(task, prior_results)
                # Store summary in metadata
                
            elif task.type == TaskType.REFINE_QUERY:
                # Refine a query based on prior results (uses LLM - tokens captured in _execute_refine_query)
                refined, tokens_used = await self._execute_refine_query(task, prior_results)
                # Could trigger follow-up search
                
        except Exception as e:
            logger.error(f"[req={self.req_id}] Task {task.id} execution failed: {e}")
            self.activity_logger.log_error(str(e), context={"task_id": task.id, "task_type": task_type_val})
            error = str(e)
            success = False
        
        duration_ms = (time.time() - start_time) * 1000
        
        # Log search results for search tasks
        if task.type in [TaskType.SEARCH_PROFILE, TaskType.SEARCH_CLOUD,
                         TaskType.SEARCH_PERSONAL, TaskType.SEARCH_ALL]:
            scores = [d.similarity_score for d in documents] if documents else None
            self.activity_logger.log_search(
                query=task.query[:200], source=task_type_val,
                results_count=len(documents), documents=None,
                scores=scores, duration_ms=duration_ms
            )
        
        self.activity_logger.log_phase(
            f"worker_task_{task.id}", "completed",
            duration_ms=duration_ms,
            details={"docs": len(documents), "links": len(web_links), "success": success}
        )
        
        # Assess quality
        quality = self._assess_quality(documents, web_links)
        
        # Generate summary
        summary = await self._generate_summary(task, documents, web_links)
        
        # Suggest refinements if results are poor
        refinements = self._suggest_refinements(task, documents, web_links, quality)
        
        result = WorkerResult(
            task_id=task.id,
            task_type=task.type,
            query=task.query,
            success=success,
            error=error,
            documents_found=documents,
            web_links_found=web_links,
            summary=summary,
            result_quality=quality,
            suggested_refinements=refinements,
            duration_ms=duration_ms,
            tokens_used=tokens_used,
            sources_searched=sources_searched
        )
        
        # Record step
        step = WorkerStep(
            task_id=task.id,
            task_type=task.type,
            model=self.model,
            tool_name=self._get_tool_name(task.type),
            tool_input={"query": task.query, "sources": task.sources},
            tool_output_summary=f"{len(documents)} docs, {len(web_links)} links",
            documents=documents,
            web_links=web_links,
            duration_ms=duration_ms,
            tokens_used=tokens_used,
            success=success,
            error=error
        )
        self.steps.append(step)
        
        return result
    
    async def _execute_search(
        self,
        task: TaskDefinition,
        user_id: str,
        user_email: str,
        active_profile_key: Optional[str],
        active_profile_database: Optional[str],
        accessible_profile_keys: Optional[List[str]]
    ) -> Tuple[List[DocumentReference], List[str]]:
        """Execute a database search task.
        
        Args:
            task: The search task
            user_id: User ID
            user_email: User email
            active_profile_key: Active profile key
            active_profile_database: Active profile database
            accessible_profile_keys: Accessible profile keys
        
        Returns:
            Tuple of (List of DocumentReference, List of source IDs searched)
        """
        # Determine sources based on task type
        sources = task.sources if task.sources else None
        
        if task.type == TaskType.SEARCH_PROFILE:
            sources = ["profile"]
        elif task.type == TaskType.SEARCH_CLOUD:
            sources = ["cloud_shared", "cloud_private"]
        elif task.type == TaskType.SEARCH_PERSONAL:
            sources = ["personal"]
        # SEARCH_ALL uses all sources (None)
        
        documents, metadata = await self.federated_search.search(
            query=task.query,
            user_id=user_id,
            user_email=user_email,
            sources=sources,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=accessible_profile_keys,
            match_count=task.max_results,
            search_type="hybrid"
        )
        
        # Extract source IDs from metadata
        sources_searched = [s.get("id", s.get("type", "unknown")) for s in metadata.get("sources", [])]
        
        logger.info(
            f"[req={self.req_id}] Search task {task.id}: '{task.query[:50]}' returned {len(documents)} results "
            f"from {metadata.get('sources_with_results', 0)} sources"
        )
        
        return documents, sources_searched
    
    async def _execute_web_search(self, query: str) -> List[WebReference]:
        """Execute a web search using Brave API.
        
        Args:
            query: Search query
        
        Returns:
            List of WebReference
        """
        if not settings.brave_api_key:
            logger.warning(f"[req={self.req_id}] Brave API key not configured, skipping web search")
            return []
        
        try:
            response = await self.http_client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "X-Subscription-Token": settings.brave_api_key,
                    "Accept": "application/json"
                },
                params={
                    "q": query,
                    "count": 10
                }
            )
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get("web", {}).get("results", [])[:10]:
                results.append(WebReference(
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    excerpt=item.get("description", ""),
                    search_query=query
                ))
            
            logger.info(f"[req={self.req_id}] Web search for '{query[:50]}' returned {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"[req={self.req_id}] Web search failed: {e}")
            return []
    
    async def _execute_browse(self, url: str) -> Optional[WebReference]:
        """Browse a URL and extract content.
        
        Args:
            url: URL to browse
        
        Returns:
            WebReference with fetched content, or None on failure
        """
        try:
            response = await self.http_client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; RAGBot/1.0)"
                },
                follow_redirects=True
            )
            response.raise_for_status()
            
            # Extract text content (basic HTML stripping)
            content = response.text
            
            # Try to extract title
            title = ""
            if "<title>" in content.lower():
                start = content.lower().find("<title>") + 7
                end = content.lower().find("</title>")
                if end > start:
                    title = content[start:end].strip()
            
            # Strip HTML tags (basic)
            import re
            text_content = re.sub(r'<[^>]+>', ' ', content)
            text_content = re.sub(r'\s+', ' ', text_content).strip()
            
            # Limit content size
            if len(text_content) > 10000:
                text_content = text_content[:10000] + "..."
            
            return WebReference(
                url=url,
                title=title,
                excerpt=text_content[:500],
                fetched_content=text_content,
                search_query=url
            )
            
        except Exception as e:
            logger.error(f"[req={self.req_id}] Failed to browse {url}: {e}")
            return None
    
    async def _execute_summarize(
        self,
        task: TaskDefinition,
        prior_results: Dict[str, WorkerResult]
    ) -> tuple:
        """Summarize prior results.
        
        Args:
            task: The summarize task
            prior_results: Prior task results
        
        Returns:
            Tuple of (summary string, tokens_used int)
        """
        # Collect all content from prior results
        all_content = []
        for result in prior_results.values():
            for doc in result.documents_found:
                all_content.append(f"Document: {doc.title}\n{doc.excerpt}")
            for link in result.web_links_found:
                all_content.append(f"Web: {link.title}\n{link.excerpt}")
        
        if not all_content:
            return "No content to summarize.", 0
        
        # Use LLM to summarize
        from litellm import acompletion
        
        # Get prompt from database/defaults
        prompt_template = get_agent_prompt_sync("worker_summarize")
        prompt = prompt_template.format(
            context_hint=task.context_hint or task.query,
            content=chr(10).join(all_content[:10])
        )
        
        try:
            # Handle newer OpenAI models that require max_completion_tokens
            model_string = self._get_model_string()
            llm_params = {
                "model": model_string,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "api_key": self._get_api_key(),
            }
            
            # Set api_base for Ollama models
            if self.provider.lower() == "ollama" or "ollama/" in model_string:
                llm_params["api_base"] = settings.ollama_base_url
                llm_params["timeout"] = settings.agent_llm_request_timeout
            
            # Check if this is a newer OpenAI model
            if "gpt-5" in model_string.lower() or "gpt-4o" in model_string.lower():
                llm_params["max_completion_tokens"] = 500
            else:
                llm_params["max_tokens"] = 500
            
            logger.info(f"[req={self.req_id}] Worker LLM call (summarize): model={model_string}, provider={self.provider}, api_base={llm_params.get('api_base', 'default')}")
            
            self.activity_logger.log_llm_request(
                model=model_string, provider=self.provider,
                messages=llm_params["messages"],
                temperature=llm_params.get("temperature", 0),
                max_tokens=llm_params.get("max_tokens", llm_params.get("max_completion_tokens", 0)),
                phase="summarize"
            )
            
            summ_start = time.time()
            response = await acompletion(**llm_params)
            summ_duration_ms = (time.time() - summ_start) * 1000
            
            content = response.choices[0].message.content
            tokens_used = response.usage.total_tokens if response.usage else 0
            self.activity_logger.log_llm_response(
                model=model_string, content=content, tokens_used=tokens_used,
                duration_ms=summ_duration_ms,
                finish_reason=response.choices[0].finish_reason or "",
                phase="summarize"
            )
            
            return content, tokens_used
        except Exception as e:
            logger.error(f"[req={self.req_id}] Summarization failed: {e}")
            self.activity_logger.log_error(str(e), context={"phase": "summarize"})
            return "Summarization failed.", 0
    
    async def _execute_refine_query(
        self,
        task: TaskDefinition,
        prior_results: Dict[str, WorkerResult]
    ) -> tuple:
        """Refine a query based on prior results.
        
        Args:
            task: The refine task
            prior_results: Prior task results
        
        Returns:
            Tuple of (refined query string, tokens_used int)
        """
        # Analyze prior results to refine query
        from litellm import acompletion
        
        results_summary = []
        for result in prior_results.values():
            results_summary.append({
                "query": result.query,
                "found": result.total_results,
                "quality": result.result_quality
            })
        
        # Get prompt from database/defaults
        prompt_template = get_agent_prompt_sync("worker_refine_query")
        prompt = prompt_template.format(
            query=task.query,
            results_summary=results_summary
        )
        
        try:
            # Handle newer OpenAI models that require max_completion_tokens
            model_string = self._get_model_string()
            llm_params = {
                "model": model_string,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "api_key": self._get_api_key(),
            }
            
            # Set api_base for Ollama models
            if self.provider.lower() == "ollama" or "ollama/" in model_string:
                llm_params["api_base"] = settings.ollama_base_url
                llm_params["timeout"] = settings.agent_llm_request_timeout
            
            # Check if this is a newer OpenAI model
            if "gpt-5" in model_string.lower() or "gpt-4o" in model_string.lower():
                llm_params["max_completion_tokens"] = 100
            else:
                llm_params["max_tokens"] = 100
            
            logger.info(f"[req={self.req_id}] Worker LLM call (refine): model={model_string}, provider={self.provider}, api_base={llm_params.get('api_base', 'default')}")
            
            self.activity_logger.log_llm_request(
                model=model_string, provider=self.provider,
                messages=llm_params["messages"],
                temperature=llm_params.get("temperature", 0),
                max_tokens=llm_params.get("max_tokens", llm_params.get("max_completion_tokens", 0)),
                phase="refine_query"
            )
            
            refine_start = time.time()
            response = await acompletion(**llm_params)
            refine_duration_ms = (time.time() - refine_start) * 1000
            
            content = response.choices[0].message.content.strip()
            tokens_used = response.usage.total_tokens if response.usage else 0
            self.activity_logger.log_llm_response(
                model=model_string, content=content, tokens_used=tokens_used,
                duration_ms=refine_duration_ms,
                finish_reason=response.choices[0].finish_reason or "",
                phase="refine_query"
            )
            
            return content, tokens_used
        except Exception as e:
            logger.error(f"[req={self.req_id}] Query refinement failed: {e}")
            self.activity_logger.log_error(str(e), context={"phase": "refine_query"})
            return task.query, 0
    
    async def _generate_summary(
        self,
        task: TaskDefinition,
        documents: List[DocumentReference],
        web_links: List[WebReference]
    ) -> str:
        """Generate a brief summary of task results.
        
        Args:
            task: The executed task
            documents: Found documents
            web_links: Found web links
        
        Returns:
            Brief summary string
        """
        if not documents and not web_links:
            return "No results found."
        
        parts = []
        if documents:
            doc_titles = [d.title for d in documents[:3]]
            parts.append(f"Found {len(documents)} documents including: {', '.join(doc_titles)}")
        if web_links:
            link_titles = [l.title for l in web_links[:3] if l.title]
            parts.append(f"Found {len(web_links)} web results including: {', '.join(link_titles)}")
        
        return " ".join(parts)
    
    def _assess_quality(
        self,
        documents: List[DocumentReference],
        web_links: List[WebReference]
    ) -> ResultQuality:
        """Assess result quality.
        
        Args:
            documents: Found documents
            web_links: Found web links
        
        Returns:
            ResultQuality enum
        """
        total = len(documents) + len(web_links)
        
        if total == 0:
            return ResultQuality.EMPTY
        
        if documents:
            avg_score = sum(d.similarity_score for d in documents) / len(documents)
            if total >= 5 and avg_score > 0.8:
                return ResultQuality.EXCELLENT
            elif total >= 3 and avg_score > 0.5:
                return ResultQuality.GOOD
        
        if total >= 1:
            return ResultQuality.PARTIAL
        
        return ResultQuality.EMPTY
    
    def _suggest_refinements(
        self,
        task: TaskDefinition,
        documents: List[DocumentReference],
        web_links: List[WebReference],
        quality: ResultQuality
    ) -> List[str]:
        """Suggest query refinements if results are poor.
        
        Args:
            task: The executed task
            documents: Found documents
            web_links: Found web links
            quality: Assessed quality
        
        Returns:
            List of suggested refined queries
        """
        if quality in [ResultQuality.EXCELLENT, ResultQuality.GOOD]:
            return []
        
        suggestions = []
        query = task.query.lower()
        
        # Suggest synonyms/variations
        if "company" in query:
            suggestions.append(query.replace("company", "organization"))
        if "email" in query:
            suggestions.append(query.replace("email", "message"))
        if "document" in query:
            suggestions.append(query.replace("document", "file"))
        
        # Suggest broader search
        if task.type != TaskType.SEARCH_ALL:
            suggestions.append(f"Try searching all sources for: {task.query}")
        
        # Suggest web search if no local results
        if not documents and task.type != TaskType.WEB_SEARCH:
            suggestions.append(f"Try web search for: {task.query}")
        
        return suggestions[:3]
    
    def _get_tool_name(self, task_type: TaskType) -> str:
        """Get tool name for a task type.
        
        Args:
            task_type: The task type
        
        Returns:
            Tool name string
        """
        tool_names = {
            TaskType.SEARCH_PROFILE: "search_profile_documents",
            TaskType.SEARCH_CLOUD: "search_cloud_storage",
            TaskType.SEARCH_PERSONAL: "search_personal_data",
            TaskType.SEARCH_ALL: "search_all_sources",
            TaskType.WEB_SEARCH: "web_search",
            TaskType.BROWSE_WEB: "browse_web",
            TaskType.SUMMARIZE: "summarize_results",
            TaskType.REFINE_QUERY: "refine_query"
        }
        return tool_names.get(task_type, "unknown")
    
    async def cleanup(self):
        """Cleanup resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
