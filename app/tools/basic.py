"""通用占位工具，用于验证 tool-calling 是否正常。"""

from datetime import datetime

from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def calculator(expression: str) -> str:
    """计算数学表达式，例如 '2 + 3 * 4' 或 '(10 - 2) / 4'。"""
    allowed = set("0123456789+-*/(). ")
    if not expression or not all(char in allowed for char in expression):
        return "表达式包含不允许的字符，仅支持数字和 + - * / ( ) ."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as exc:
        return f"计算失败: {exc}"
