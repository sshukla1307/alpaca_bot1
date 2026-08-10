"""
AI Portfolio Experiment V2 — API Adapters

Unified interface for interacting with different LLM providers.
Handles prompt construction, tool calling looping, and output parsing.
All models are forced to temperature=0 and parallel_tool_calls=False for fairness.
"""

import os
import json
import time
import random
import logging
from typing import Any, Optional, Dict, List, Callable

from .config import LLMProvider

logger = logging.getLogger(__name__)

def retry_with_backoff(func: Callable, max_retries: int = 5, base_delay: float = 2.0):
    """Generic 2026-style exponential backoff wrapper."""
    def wrapper(*args, **kwargs):
        retries = 0
        while retries < max_retries:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                err_msg = str(e).lower()
                # Categorize common 2026 rate/quota limits
                is_rate_limit = any(x in err_msg for x in ["429", "rate limit", "quota", "resource exhausted"])
                is_timeout = any(x in err_msg for x in ["timeout", "timed out", "deadline exceeded"])
                is_unavailable = any(x in err_msg for x in ["503", "unavailable", "service unavailable"])
                
                if is_rate_limit or is_timeout or is_unavailable:
                    retries += 1
                    if retries >= max_retries:
                        logger.error(f"Max retries reached. Final error: {e}")
                        raise e
                    
                    delay = base_delay * (2 ** (retries - 1)) + random.uniform(0, 1)
                    logger.warning(f"Retry {retries}/{max_retries} due to: {e}. Sleeping {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    # Non-retryable error
                    logger.error(f"Non-retryable error: {e}")
                    raise e
        return None
    return wrapper

class UnifiedLLMClient:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.api_key = os.getenv(provider.api_key_env)
        if not self.api_key:
            logger.warning(f"Key {provider.api_key_env} not found. Agent may fail.")
            
    def generate(self, system_prompt: str, user_prompt: str, tools: List[Dict], max_tool_calls: int = 8) -> str:
        """
        Run the LLM loop until it provides a final answer.
        Returns the final JSON string from propose_trades.
        """
        if self.provider.api_type == "openai":
            return self._run_openai_loop(system_prompt, user_prompt, tools, max_tool_calls)
        else:
            raise ValueError(f"Unsupported API type: {self.provider.api_type}")

    def _get_openai_client(self):
        import openai
        return openai.OpenAI(
            api_key=self.api_key,
            base_url=self.provider.base_url
        )

    def _convert_tools_to_openai(self, tools: List[Dict]) -> List[Dict]:
        return [{"type": "function", "function": t} for t in tools]

    def _run_openai_loop(self, system_prompt: str, user_prompt: str, tools: List[Dict], max_calls: int) -> str:
        client = self._get_openai_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        formatted_tools = self._convert_tools_to_openai(tools)
        
        from .mcp_servers import execute_tool
        
        if not formatted_tools or max_calls == 0:
            @retry_with_backoff
            def _call_oai():
                return client.chat.completions.create(
                    model=self.provider.model_id,
                    messages=messages,
                    temperature=self.provider.temperature,
                    max_tokens=self.provider.max_tokens,
                )
            response = _call_oai()
            return response.choices[0].message.content or ""
            
        call_count = 0
        while call_count < max_calls:
            @retry_with_backoff
            def _call_oai_tool():
                return client.chat.completions.create(
                    model=self.provider.model_id,
                    messages=messages,
                    tools=formatted_tools,
                    temperature=self.provider.temperature,
                    max_tokens=self.provider.max_tokens,
                    parallel_tool_calls=self.provider.parallel_tool_calls
                )
            response = _call_oai_tool()
            
            message = response.choices[0].message
            messages.append(message)
            
            if not message.tool_calls:
                # Agent didn't call a tool. It either failed to use propose_trades or it just answered.
                # If we are here, it means we don't have a strict forced tool, so we return the content.
                return message.content or ""
                
            tool_call = message.tool_calls[0] # parallel_tool_calls is False
            func_name = tool_call.function.name
            
            try:
                args = json.loads(tool_call.function.arguments)
            except Exception:
                args = {}
                
            if func_name == "propose_trades":
                # The agent has finalized its decision
                return tool_call.function.arguments
                
            # Execute standard tool
            call_count += 1
            logger.info(f"{self.provider.name} called {func_name}")
            result = execute_tool(func_name, args)
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": func_name,
                "content": json.dumps(result)
            })
            
        logger.warning(f"{self.provider.name} hit max tool calls ({max_calls})")
        return ""

