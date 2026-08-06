# -*- coding: utf-8 -*-
"""
安全表达式求值器(AST 白名单,禁裸 eval/exec)。

用于资讯工具(计算器)等对用户输入求值的场景。只放行白名单节点,
禁一切函数调用 / 属性访问 / 下标 / 导入 / 赋值 / 循环 / lambda / 推导式 / f-string,
从语法层面杜绝注入(如 __import__('os')、().__class__ 之类都会被拒)。
受限 AST 无循环无递归,天然有界,无需超时。

用法:safe_eval("1 + 2 * 3") -> 7;非法/异常一律返回 None。
"""
import ast
from datetime import datetime

# AST 节点白名单:只允许这些节点参与求值
_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.BoolOp,
    ast.IfExp,
    ast.Name,
)

# 允许的运算符(二元 + 一元)
_ALLOWED_OPERATORS = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.USub, ast.UAdd, ast.Not,
)

# 允许的比较符
_ALLOWED_COMPARATORS = (
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

# 允许出现在表达式里的名字(其余名字一律当作未定义 -> 求值异常 -> 返回 None)
# 注意:Call 被禁,now 只能是"值"(每次求值时取当前时间字符串),不能是函数
SAFE_NAMES = {
    "now": "2026-08-06 00:00",  # 占位,实际由 safe_eval 每次求值时刷新
    "pi": 3.14159,
}

# 结果过大视为异常,返回 None(落到普通回复)
_MAX_RESULT = 10 ** 15


def _check(tree):
    """深度遍历 AST,任一节点不在白名单即返回 False。

    注意 ast.walk 会连同运算符 / 比较符 / 求值上下文(ctx=Load)节点一起遍历,
    这些"修饰型"节点也要显式放行,否则合法表达式(如 1+2)会被误拒。
    """
    for node in ast.walk(tree):
        # 修饰型节点:运算符 / 比较符 / 布尔操作符(and/or) / 求值上下文(Load)
        if isinstance(node, _ALLOWED_OPERATORS):
            continue
        if isinstance(node, _ALLOWED_COMPARATORS):
            continue
        if isinstance(node, (ast.And, ast.Or, ast.Load)):
            continue
        # 结构白名单节点(Expression/Constant/BinOp/UnaryOp/Compare/BoolOp/IfExp/Name)
        if isinstance(node, _ALLOWED_NODES):
            if isinstance(node, ast.Constant):
                # 只允许数值 / 字符串 / 布尔 / None(拒绝 bytes 等其他字面量)
                if isinstance(node.value, (int, float, str, bool)) or node.value is None:
                    continue
                return False
            continue  # 名字/运算符节点放行,求值命名空间只含 SAFE_NAMES + 空 builtins
        # 其余一切节点(Call/Attribute/Subscript/Import/Assign/循环/lambda/推导式/f-string 等)一律拒绝
        return False
    return True


def safe_eval(expr):
    """白名单求值:返回 str/int/float/bool;非法表达式或异常一律返回 None。"""
    if not isinstance(expr, str) or not expr.strip():
        return None
    try:
        tree = ast.parse(expr, mode="eval")
        if not _check(tree):
            return None
        # 命名空间清空 builtins,只放白名单名字,杜绝 __import__ / 属性链 / 下标
        names = dict(SAFE_NAMES)
        names["now"] = datetime.now().strftime("%Y-%m-%d %H:%M")  # now 解析为当前时间字符串
        result = eval(compile(tree, "<safe>", "eval"), {"__builtins__": {}}, names)
        if result is None or isinstance(result, (str, int, float, bool)):
            if isinstance(result, (int, float)):
                try:
                    if abs(result) > _MAX_RESULT:
                        return None  # 超大数视为异常
                except OverflowError:
                    return None
            return result
        return None
    except Exception:
        return None
