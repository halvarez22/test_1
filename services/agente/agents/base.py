import json
import logging
from typing import Any, Callable

logger = logging.getLogger("licitai-agents")

class BaseAgent:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    async def emit_progress(self, progress_callback: Callable, val: int, msg: str):
        """Standard way for any agent to talk to the UI"""
        if progress_callback:
            await progress_callback(json.dumps({
                "status": "progress", 
                "agent": self.name,
                "val": val, 
                "msg": f"[{self.name}] {msg}"
            }) + "\n")

    async def execute(self, *args, **kwargs) -> Any:
        raise NotImplementedError("Each agent must implement its own execute method")
