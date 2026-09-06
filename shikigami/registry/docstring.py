"""Docstring 解析（零第三方依赖，兼容 Sphinx 与 Google/NumPy 风格）"""
import inspect
import re


def parse_docstring(func) -> tuple[str | None, dict[str, str]]:
    """
    轻量级 Docstring 解析器（零第三方依赖）
    提取：
    1. 函数的整体功能描述 (Summary)
    2. 各个参数的描述字典 {param_name: description}
    """
    doc = inspect.getdoc(func)
    if not doc:
        return None, {}

    lines = doc.strip().splitlines()
    func_summary = lines[0].strip() if lines else None
    param_docs = {}

    # 1. 解析 Sphinx 风格: :param keyword: 搜索关键字
    sphinx_matches = re.findall(r":param\s+(\w+):\s*(.+)", doc)
    for p_name, p_desc in sphinx_matches:
        param_docs[p_name] = p_desc.strip()

    # 2. 解析 Google / NumPy 风格:
    # Args:
    #     keyword: 搜索关键字
    #     page: 页码
    in_args_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(("args:", "parameters:", "params:")):
            in_args_section = True
            continue
        if in_args_section:
            if not line.startswith((" ", "\t")) and stripped:
                in_args_section = False
                continue
            match = re.match(r"\s*(\w+)\s*:\s*(.+)", line)
            if match:
                p_name, p_desc = match.groups()
                if p_name not in param_docs:
                    param_docs[p_name] = p_desc.strip()

    return func_summary, param_docs
