"""Base Agent Class"""
from abc import ABC, abstractmethod
from typing import Dict, Any
import structlog

logger = structlog.get_logger()

class BaseAgent(ABC):
    agent_type: str = "base"

    def __init__(self):
        self.logger = logger.bind(agent=self.agent_type)

    @abstractmethod
    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """Execute agent logic"""
        pass

    async def call_mcp_tool(self, server: str, tool: str, arguments: dict):
        """Call MCP server tool"""
        # Implement MCP tool calling
        self.logger.info(f"Calling MCP: {server}.{tool}", arguments=arguments)
        return {}
