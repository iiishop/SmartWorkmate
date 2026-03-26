from __future__ import annotations


def sort_non_decreasing(values: list[int]) -> list[int]:
    return sorted(values)


def max_value(values: list[int]) -> int:
    if not values:
        raise ValueError("values must not be empty")
    return max(values)


class Algo:
    @staticmethod
    def sort_non_decreasing(values: list[int]) -> list[int]:
        return sort_non_decreasing(values)

    @staticmethod
    def max_value(values: list[int]) -> int:
        return max_value(values)
