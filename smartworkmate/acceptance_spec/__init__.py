"""Acceptance specification pipeline package."""

from .builtins import BUILTINS, BuiltinFunctionSpec
from .ir import compile_to_ir
from .parser import parse_spec
from .pytest_codegen import generate_pytest_module
from .reporting import build_verdict_from_pytest, render_lvf, write_verdict_files
from .semantic import validate_semantics

__all__ = [
    "parse_spec",
    "validate_semantics",
    "compile_to_ir",
    "generate_pytest_module",
    "BUILTINS",
    "BuiltinFunctionSpec",
    "build_verdict_from_pytest",
    "render_lvf",
    "write_verdict_files",
]
