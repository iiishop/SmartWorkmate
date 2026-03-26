from __future__ import annotations

import re

from .ast import Token


TOKEN_REGEX = re.compile(
    r"""
    (?P<WS>[ \t\r\n]+)
  | (?P<COMMENT>//[^\n]*)
  | (?P<OP><=|>=|==|!=|<|>|=)
  | (?P<SYMBOL>[{}()\[\],:;])
  | (?P<NUMBER>-?\d+(?:\.\d+)?)
  | (?P<STRING>"(?:[^"\\]|\\.)*")
  | (?P<BUILTIN>\$[A-Za-z_][A-Za-z0-9_\.]*)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_\.]*)
    """,
    re.VERBOSE,
)


KEYWORDS = {"using", "test", "as", "given", "expect", "and", "true", "false"}


class LexError(ValueError):
    pass


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    col = 1

    while index < len(source):
        match = TOKEN_REGEX.match(source, index)
        if match is None:
            raise LexError(f"Unexpected character at {line}:{col}")

        text = match.group(0)
        kind = match.lastgroup or ""

        if kind not in {"WS", "COMMENT"}:
            lowered = text.lower() if kind == "IDENT" else ""
            if kind == "IDENT" and lowered in KEYWORDS:
                token_kind = lowered.upper()
            else:
                token_kind = kind
            tokens.append(Token(token_kind, text, line, col))

        line, col = _advance_position(text, line, col)
        index = match.end()

    tokens.append(Token("EOF", "", line, col))
    return tokens


def _advance_position(text: str, line: int, col: int) -> tuple[int, int]:
    for char in text:
        if char == "\n":
            line += 1
            col = 1
        else:
            col += 1
    return line, col
