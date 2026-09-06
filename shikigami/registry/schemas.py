"""固定元工具（list_categories / list_tools / get_tool_info / execute_tool）的 Pydantic Schema"""
from typing import Any, Dict

from pydantic import BaseModel, Field


class ToolInfoSchema(BaseModel):
    name: str = Field(..., description="工具名称")


class ToolsSchema(BaseModel):
    category: str = Field(..., description="工具类别")


class EmptySchema(BaseModel):
    pass


class ToolExecuteSchema(BaseModel):
    tool_name: str = Field(..., description="工具名称")
    tool_args: Dict[str, Any] = Field(default={}, description="工具参数")
