"""Validate LLM tool docstrings the same way AstrBot parses them."""

import ast
import re
import unittest

from plugin_loader import ROOT

try:
    import docstring_parser
except ModuleNotFoundError:  # AstrBot ships it; local runs may not have it.
    docstring_parser = None

SUPPORTED_TYPES = {"string", "number", "object", "array", "boolean"}
PY_TO_JSON_TYPE = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


def llm_tool_functions():
    tree = ast.parse((ROOT / "main.py").read_text())
    plugin = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "VikunjaPlugin"
    )
    for node in plugin.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and getattr(decorator.func, "attr", "") == "llm_tool"
            for decorator in node.decorator_list
        ):
            yield node


@unittest.skipIf(docstring_parser is None, "docstring_parser not installed")
class ToolSchemaTests(unittest.TestCase):
    def test_every_tool_declares_a_description_and_typed_arguments(self):
        tools = list(llm_tool_functions())
        self.assertGreaterEqual(len(tools), 12)
        for node in tools:
            with self.subTest(tool=node.name):
                parsed = docstring_parser.parse(ast.get_docstring(node) or "")
                self.assertTrue(
                    (parsed.description or "").strip(),
                    "AstrBot uses the summary as the tool description",
                )
                documented = {param.arg_name for param in parsed.params}
                declared = {
                    arg.arg
                    for arg in node.args.args
                    if arg.arg not in {"self", "event"}
                }
                self.assertEqual(documented, declared)
                for param in parsed.params:
                    type_name = param.type_name or ""
                    match = re.match(r"(\w+)\[(\w+)\]", type_name)
                    if match:
                        type_name = match.group(1)
                    type_name = PY_TO_JSON_TYPE.get(type_name, type_name)
                    self.assertIn(type_name, SUPPORTED_TYPES)


if __name__ == "__main__":
    unittest.main()
