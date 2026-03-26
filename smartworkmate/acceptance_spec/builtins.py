from __future__ import annotations

from dataclasses import dataclass


BuiltinCategory = str


@dataclass(frozen=True, slots=True)
class BuiltinFunctionSpec:
    name: str
    category: BuiltinCategory
    arg_counts: tuple[int, ...]
    description: str


class BuiltinRegistry:
    def __init__(self) -> None:
        self._items: dict[str, BuiltinFunctionSpec] = {}

    def register(self, spec: BuiltinFunctionSpec) -> None:
        self._items[spec.name] = spec

    def get(self, name: str) -> BuiltinFunctionSpec | None:
        return self._items.get(name)

    def is_builtin(self, name: str) -> bool:
        return name in self._items

    def is_category(self, name: str, category: BuiltinCategory) -> bool:
        spec = self.get(name)
        return spec is not None and spec.category == category

    def reserved_aliases(self) -> set[str]:
        reserved = set(self._items)
        for item in self._items:
            if item.startswith("$") and len(item) > 1:
                reserved.add(item[1:])
        return {value.lower() for value in reserved}


BUILTINS = BuiltinRegistry()

# Performance builtins
BUILTINS.register(
    BuiltinFunctionSpec(
        name="$p_ms",
        category="perf",
        arg_counts=(1, 2),
        description="Average latency in milliseconds over n runs (optional warmup)",
    )
)
BUILTINS.register(
    BuiltinFunctionSpec(
        name="$p95_ms",
        category="perf",
        arg_counts=(1, 2),
        description="P95 latency in milliseconds over n runs (optional warmup)",
    )
)

# Value builtins
BUILTINS.register(
    BuiltinFunctionSpec(
        name="$multiset",
        category="value",
        arg_counts=(1,),
        description="Converts an iterable into hashable multiset view",
    )
)
BUILTINS.register(
    BuiltinFunctionSpec(
        name="$approx_eq",
        category="value",
        arg_counts=(2, 3),
        description="Approximate equality for numeric values",
    )
)
BUILTINS.register(
    BuiltinFunctionSpec(
        name="$contains",
        category="value",
        arg_counts=(2,),
        description="Checks membership within an iterable",
    )
)
BUILTINS.register(
    BuiltinFunctionSpec(
        name="$len",
        category="value",
        arg_counts=(1,),
        description="Length helper mirroring builtin len()",
    )
)
