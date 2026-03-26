from __future__ import annotations

from .ast import (
    ArrayLiteral,
    BoolLiteral,
    CallExpr,
    Comparison,
    ExpectStatement,
    GivenVar,
    Identifier,
    NumberLiteral,
    SpecDocument,
    StringLiteral,
    TestBinding,
    Token,
)
from .lexer import lex


class ParseError(ValueError):
    pass


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def parse(self) -> SpecDocument:
        self._expect("USING")
        language = self._expect("IDENT").value
        self._expect_value(";")

        tests: list[TestBinding] = []
        while self._peek().kind == "TEST":
            tests.append(self._parse_test_binding())

        given = self._parse_given_block()
        expect = self._parse_expect_block()
        self._expect("EOF")
        return SpecDocument(language=language, tests=tests, given=given, expect=expect)

    def _parse_test_binding(self) -> TestBinding:
        self._expect("TEST")
        target = self._expect("IDENT").value
        self._expect("AS")
        alias = self._expect("IDENT").value
        self._expect_value(";")
        return TestBinding(target_path=target, alias=alias)

    def _parse_given_block(self) -> list[GivenVar]:
        self._expect("GIVEN")
        self._expect_value("{")
        values: list[GivenVar] = []
        while self._peek().value != "}":
            name = self._expect("IDENT").value
            self._expect_value(":")
            type_name = self._parse_type_name()
            self._expect_value("=")
            expr = self._parse_expression()
            self._expect_value(";")
            values.append(GivenVar(name=name, type_name=type_name, value=expr))
        self._expect_value("}")
        return values

    def _parse_type_name(self) -> str:
        token = self._expect("IDENT")
        name = token.value
        if self._peek().value == "[":
            self._expect_value("[")
            self._expect_value("]")
            name += "[]"
        return name

    def _parse_expect_block(self) -> list[ExpectStatement]:
        self._expect("EXPECT")
        self._expect_value("{")
        statements: list[ExpectStatement] = []
        while self._peek().value != "}":
            predicates = [self._parse_comparison()]
            while self._peek().kind == "AND":
                self._advance()
                predicates.append(self._parse_comparison())
            self._expect_value(";")
            statements.append(ExpectStatement(predicates=predicates))
        self._expect_value("}")
        return statements

    def _parse_comparison(self) -> Comparison:
        left = self._parse_expression()
        op = self._expect("OP").value
        right = self._parse_expression()
        return Comparison(left=left, op=op, right=right)

    def _parse_expression(self):
        token = self._peek()
        if token.kind == "NUMBER":
            self._advance()
            if "." in token.value:
                return NumberLiteral(float(token.value))
            return NumberLiteral(int(token.value))
        if token.kind == "STRING":
            self._advance()
            return StringLiteral(_unquote(token.value))
        if token.kind in {"TRUE", "FALSE"}:
            self._advance()
            return BoolLiteral(token.kind == "TRUE")
        if token.value == "[":
            return self._parse_array()
        if token.kind in {"IDENT", "BUILTIN"}:
            return self._parse_identifier_or_call()
        raise ParseError(
            f"Unexpected token {token.value!r} at {token.line}:{token.column}"
        )

    def _parse_array(self) -> ArrayLiteral:
        self._expect_value("[")
        items = []
        if self._peek().value != "]":
            items.append(self._parse_expression())
            while self._peek().value == ",":
                self._advance()
                items.append(self._parse_expression())
        self._expect_value("]")
        return ArrayLiteral(items=items)

    def _parse_identifier_or_call(self):
        token = self._peek()
        if token.kind not in {"IDENT", "BUILTIN"}:
            raise ParseError(
                f"Expected identifier or builtin, got {token.kind} at {token.line}:{token.column}"
            )
        name = self._advance().value
        if self._peek().value != "(":
            if token.kind == "BUILTIN":
                raise ParseError(
                    f"Builtin {name!r} must be called with parentheses at {token.line}:{token.column}"
                )
            return Identifier(name=name)
        self._expect_value("(")
        args = []
        if self._peek().value != ")":
            args.append(self._parse_expression())
            while self._peek().value == ",":
                self._advance()
                args.append(self._parse_expression())
        self._expect_value(")")
        return CallExpr(name=name, args=args)

    def _peek(self) -> Token:
        return self.tokens[self.index]

    def _advance(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _expect(self, kind: str) -> Token:
        token = self._peek()
        if token.kind != kind:
            raise ParseError(
                f"Expected {kind}, got {token.kind} ({token.value!r}) at {token.line}:{token.column}"
            )
        return self._advance()

    def _expect_value(self, value: str) -> Token:
        token = self._peek()
        if token.value != value:
            raise ParseError(
                f"Expected {value!r}, got {token.value!r} at {token.line}:{token.column}"
            )
        return self._advance()


def parse_spec(source: str) -> SpecDocument:
    return Parser(lex(source)).parse()


def _unquote(value: str) -> str:
    return bytes(value[1:-1], "utf-8").decode("unicode_escape")
