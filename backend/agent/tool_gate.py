"""Tool Gate - Per-tenant tool availability control.

Determines which agent tools are available for the current tenant/profile.
Tools can be disabled via the TENANT_DISABLED_TOOLS environment variable
(comma-separated TaskType values, e.g. 'web_search,browse_web').

This is the central enforcement point for tool gating. All tool systems
(federated agent, chat router, orchestrator) should consult ToolGate
before planning or executing tools.
"""

import logging
from typing import List, Set, Dict, Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Mapping from chat router tool names to TaskType values
_CHAT_TOOL_TO_TASK_TYPE: Dict[str, str] = {
    "search_knowledge_base": "",  # Always enabled, no TaskType equivalent
    "browse_web": "browse_web",
    "web_search": "web_search",
}


class ToolGate:
    """Determines which tools are available for the current tenant/profile.
    
    All methods are static and read from the global settings singleton,
    so they reflect the tenant configuration of the running backend instance.
    """
    
    # All TaskType values from backend.agent.schemas.TaskType
    ALL_TOOL_TYPES: Set[str] = {
        "search_profile", "search_cloud", "search_personal", "search_all",
        "web_search", "browse_web", "summarize", "refine_query",
    }
    
    @staticmethod
    def get_disabled_tools() -> Set[str]:
        """Get the set of tool type strings disabled for the current tenant.
        
        Returns:
            Set of disabled TaskType value strings (e.g. {'web_search', 'browse_web'}).
            Empty set when nothing is disabled.
        """
        return settings.get_disabled_tools()
    
    @staticmethod
    def is_enabled(tool: str) -> bool:
        """Check if a specific tool/TaskType is enabled for the current tenant.
        
        Args:
            tool: TaskType value string (e.g. 'web_search', 'browse_web')
        
        Returns:
            True if the tool is enabled, False if disabled.
        """
        return tool.lower() not in settings.get_disabled_tools()
    
    @staticmethod
    def filter_tasks(tasks: list) -> list:
        """Filter a list of TaskDefinition objects, removing those with disabled types.
        
        Args:
            tasks: List of TaskDefinition objects (from backend.agent.schemas)
        
        Returns:
            Filtered list with disabled task types removed.
        """
        disabled = settings.get_disabled_tools()
        if not disabled:
            return tasks
        
        filtered = []
        for task in tasks:
            task_type_val = task.type.value if hasattr(task.type, 'value') else str(task.type)
            if task_type_val.lower() in disabled:
                logger.info(f"ToolGate: Filtered out disabled task {task.id} (type={task_type_val})")
            else:
                filtered.append(task)
        return filtered
    
    @staticmethod
    def get_enabled_task_types() -> List[str]:
        """Get the list of enabled TaskType value strings.
        
        Returns:
            List of enabled TaskType values for prompt injection.
        """
        disabled = settings.get_disabled_tools()
        return [t for t in ToolGate.ALL_TOOL_TYPES if t not in disabled]
    
    @staticmethod
    def get_enabled_tools_schema(full_schema: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter the chat router TOOLS_SCHEMA, removing disabled tools.
        
        Maps chat router function names to TaskType values and removes
        entries whose corresponding TaskType is disabled.
        
        Args:
            full_schema: The complete TOOLS_SCHEMA list from chat router.
        
        Returns:
            Filtered schema list with disabled tools removed.
        """
        disabled = settings.get_disabled_tools()
        if not disabled:
            return full_schema
        
        filtered = []
        for tool_def in full_schema:
            func_name = tool_def.get("function", {}).get("name", "")
            mapped_type = _CHAT_TOOL_TO_TASK_TYPE.get(func_name, "")
            
            if mapped_type and mapped_type.lower() in disabled:
                logger.info(f"ToolGate: Removed chat tool '{func_name}' (maps to disabled '{mapped_type}')")
            else:
                filtered.append(tool_def)
        
        return filtered
