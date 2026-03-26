from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Token:
    kind: str
    value: str
    line: int
    column: int


@dataclass(slots=True)
class TestBinding:
    target_path: str
    alias: str


@dataclass(slots=True)
class GivenVar:
    name: str
    type_name: str
    value: "Expression"


@dataclass(slots=True)
class SpecDocument:
    language: str
    tests: list[TestBinding]
    given: list[GivenVar]
    expect: list["ExpectStatement"]


class Expression:
    pass


@dataclass(slots=True)
class Identifier(Expression):
    name: str


@dataclass(slots=True)
class NumberLiteral(Expression):
    value: int | float


@dataclass(slots=True)
class StringLiteral(Expression):
    value: str


@dataclass(slots=True)
class BoolLiteral(Expression):
    value: bool


@dataclass(slots=True)
class ArrayLiteral(Expression):
    items: list[Expression]


@dataclass(slots=True)
class CallExpr(Expression):
    name: str
    args: list[Expression]


@dataclass(slots=True)
class Comparison:
    left: Expression
    op: str
    right: Expression


@dataclass(slots=True)
class ExpectStatement:
    predicates: list[Comparison]
