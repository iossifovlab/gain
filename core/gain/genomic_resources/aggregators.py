"""Score aggregator classes and factory utilities."""

from __future__ import annotations

import abc
import math
import re
from collections import Counter
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast


class Aggregator(abc.ABC):
    """Base class for score aggregators."""

    def __init__(self) -> None:
        self.total_count = 0
        self.used_count = 0

    parametrized: ClassVar[bool] = False
    default_parameter: ClassVar[str | None] = None

    def __call__(self) -> Any:
        return self.get_final()

    def add(self, value: Any, count: int = 1) -> None:
        """Add a single value to the aggregator."""
        self.total_count += count
        self._add_internal(value)

    def aggregate(self, values: list[Any] | None) -> Any:
        """Clear state, add all values, and return the final result."""
        self.clear()
        if values is None:
            return self.get_final()
        for value in values:
            self.add(value)
        return self.get_final()

    @abc.abstractmethod
    def _add_internal(self, value: Any) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        """Reset the aggregator to its initial state."""
        self.total_count = 0
        self.used_count = 0
        self._clear_internal()

    @abc.abstractmethod
    def _clear_internal(self) -> None:
        raise NotImplementedError

    def get_final(self) -> Any:
        """Return the aggregated result."""
        raise NotImplementedError

    def get_total_count(self) -> int:
        """Return the total number of values seen (including None)."""
        return self.total_count

    def get_used_count(self) -> int:
        """Return the number of non-None values that were added."""
        return self.used_count

    def __eq__(self, obj: object) -> bool:
        return cast(bool, self.get_final() == obj)

    @staticmethod
    def build(source: AggregatorSource) -> Aggregator:
        """Build an aggregator from a definition, string, or dict."""
        if isinstance(source, AggregatorDefinition):
            definition = source
        elif isinstance(source, str):
            definition = AggregatorDefinition.from_string(source)
        else:
            definition = AggregatorDefinition.from_dict(source)
        aggregator_class = get_aggregator_class(definition.aggregator_type)
        if definition.parameters:
            return aggregator_class(*definition.parameters)
        return aggregator_class()


class MaxAggregator(Aggregator):
    """Maximum value aggregator for genomic scores."""

    def __init__(self) -> None:
        super().__init__()
        self.current_max = None

    def _add_internal(self, value: Any) -> None:
        if value is None:
            return
        if self.current_max is not None:
            self.current_max = max(value, self.current_max)
        else:
            self.current_max = value

        self.used_count += 1

    def _clear_internal(self) -> None:
        self.current_max = None

    def get_final(self) -> Any:
        return self.current_max


class MinAggregator(Aggregator):
    """Minimum value aggregator for genomic scores."""

    def __init__(self) -> None:
        super().__init__()
        self.current_min = None

    def _add_internal(self, value: Any) -> None:
        if value is None:
            return
        if self.current_min is not None:
            self.current_min = min(self.current_min, value)
        else:
            self.current_min = value

        self.used_count += 1

    def _clear_internal(self) -> None:
        self.current_min = None

    def get_final(self) -> Any:
        return self.current_min


class MeanAggregator(Aggregator):
    """Aggregator for genomic scores that calculates mean value."""

    def __init__(self) -> None:
        super().__init__()
        self.sum = 0

    def _add_internal(self, value: Any) -> None:
        if value is None:
            return

        self.sum += value
        self.used_count += 1

    def _clear_internal(self) -> None:
        self.sum = 0

    def get_final(self) -> Any:
        if self.used_count > 0:
            return self.sum / self.used_count
        return None


class CountAggregator(Aggregator):
    """Aggregator that counts values."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def _add_internal(self, value: Any) -> None:
        if value is None:
            return

        self.count += 1

    def _clear_internal(self) -> None:
        self.count = 0

    def get_final(self) -> Any:
        if self.count > 0:
            return self.count
        return None


class ConcatAggregator(Aggregator):
    """Aggregator that concatenates all passed values."""

    def __init__(self) -> None:
        super().__init__()
        self.out = ""

    def _add_internal(self, value: Any) -> None:
        if value is not None:
            self.out += str(value)
            self.used_count += 1

    def _clear_internal(self) -> None:
        self.out = ""

    def get_final(self) -> Any:
        if self.out == "":
            return None

        return self.out


class MedianAggregator(Aggregator):
    """Aggregator for genomic scores that calculates median value."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[Any] = []

    def _add_internal(self, value: Any) -> None:
        if value is not None:
            self.values.append(value)
            self.used_count += 1

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> Any:
        if not self.values:
            return None
        self.values.sort()
        if len(self.values) % 2 == 1:
            return self.values[math.floor(len(self.values) / 2)]

        first = self.values[int(len(self.values) / 2) - 1]
        second = self.values[int(len(self.values) / 2)]
        if isinstance(first, str):
            assert isinstance(second, str)
            return first + second

        return (first + second) / 2


class ModeAggregator(Aggregator):
    """Aggregator for genomic scores that calculates mode value."""

    def __init__(self) -> None:
        super().__init__()
        self.value_counts: dict[Any, int] = {}

    def _add_internal(self, value: Any) -> None:
        if value is not None:
            if value not in self.value_counts:
                self.value_counts[value] = 0
            self.value_counts[value] += 1
            self.used_count += 1

    def _clear_internal(self) -> None:
        self.value_counts.clear()

    def get_final(self) -> Any:
        if not self.value_counts:
            return None
        count_values: dict[Any, Any] = {}
        current_max = None
        for value, count in self.value_counts.items():
            if count not in count_values:
                count_values[count] = []

            count_values[count].append(value)

            if current_max is None or current_max < count:
                current_max = count
        modes = count_values[current_max]
        modes.sort()
        return modes[0]


class JoinAggregator(Aggregator):
    """Aggregator that joins all passed values using a separator."""

    parametrized: ClassVar[bool] = True
    default_parameter: ClassVar[str | None] = ","

    def __init__(self, separator: str):
        super().__init__()
        self.values: list[Any] = []
        self.separator = separator

    def _add_internal(self, value: Any) -> None:
        if value is not None:
            self.values.append(str(value))
            self.used_count += 1

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> Any:
        if len(self.values) > 0:
            return self.separator.join(self.values)
        return None


class ListAggregator(Aggregator):
    """Aggregator that builds a list of all passed values."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[Any] = []

    def _flatten(self, items: Any) -> Generator[Any, None, None]:
        for item in items:
            if (
                isinstance(item, Iterable)
                and not isinstance(item, (str, bytes))
            ):
                yield from self._flatten(item)
            else:
                yield item

    def _add_internal(self, value: Any) -> None:
        if value is not None:
            self.values.append(value)
            self.used_count += 1

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> Any:
        return list(self._flatten(self.values))


class BoolAggregator(Aggregator):
    """Aggregator that returns True if any non-None value was added."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[Any] = []

    def _add_internal(self, value: Any) -> None:
        if value is not None:
            self.values.append(value)
            self.used_count += 1

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> bool:
        return bool(self.values)


class CounterAggregator(Aggregator):
    """Aggregator that counts values."""

    def __init__(self) -> None:
        super().__init__()
        self.counter: Counter = Counter()

    def _add_internal(self, value: Any) -> None:
        if value is None:
            return

        if not isinstance(value, list):
            self.counter.update([value])
        else:
            self.counter.update(value)

    def _clear_internal(self) -> None:
        self.counter.clear()

    def get_final(self) -> Any:
        return dict(self.counter)


AGGREGATOR_CLASS_DICT: dict[str, type[Aggregator]] = {
    "max": MaxAggregator,
    "min": MinAggregator,
    "mean": MeanAggregator,
    "count": CountAggregator,
    "concatenate": ConcatAggregator,
    "median": MedianAggregator,
    "mode": ModeAggregator,
    "join": JoinAggregator,
    "list": ListAggregator,
    "bool": BoolAggregator,
    "value_count": CounterAggregator,
}

AGGREGATOR_SCHEMA = {
    "type": "string",
    "oneof": [
        {"regex": "^min$"},
        {"regex": "^max$"},
        {"regex": "^mean$"},
        {"regex": "^concatenate$"},
        {"regex": "^median$"},
        {"regex": "^mode$"},
        {"regex": "^join\\(.+\\)$"},
        {"regex": "^list$"},
        {"regex": "^bool$"},
        {"regex": "^value_count$"},
    ],
}


def get_aggregator_class(aggregator: str) -> Callable[[], Aggregator]:
    """Return the aggregator class for the given aggregator name."""
    return AGGREGATOR_CLASS_DICT[aggregator]


@dataclass
class AggregatorDefinition:
    """Parsed representation of an aggregator type string."""
    aggregator_type: str
    parameters: list[Any] = field(default_factory=list)

    @classmethod
    def from_string(cls, raw: str) -> AggregatorDefinition:
        """Parse an aggregator definition from a string.

        Format: ``name`` or ``name(parameter)``.
        """
        match = re.match(r"^(\w+)(?:\(([^)]*)\))?$", raw)
        if match is None:
            raise ValueError(f"Invalid aggregator definition: {raw!r}")
        name, parameter = match.group(1), match.group(2)
        if parameter is None:
            return cls(aggregator_type=name)
        return cls(aggregator_type=name, parameters=[parameter])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AggregatorDefinition:
        """Construct an aggregator definition from a dictionary."""
        return cls(
            aggregator_type=data["aggregator_type"],
            parameters=list(data.get("parameters", [])),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        d: dict[str, Any] = {"aggregator_type": self.aggregator_type}
        if self.parameters:
            d["parameters"] = self.parameters
        return d

    def __str__(self) -> str:
        if self.parameters:
            return f"{self.aggregator_type}({self.parameters[0]})"
        return self.aggregator_type


AggregatorSource = AggregatorDefinition | str | dict[str, Any]


NUMERIC_ONLY_AGGREGATORS = {"max", "min", "mean", "median"}


def validate_aggregator(
    aggregator: AggregatorSource, value_type: str | None = None,
) -> None:
    """Raise ValueError for invalid aggregator or value type combinations."""
    try:
        Aggregator.build(aggregator)
    except Exception as ex:
        raise ValueError(
            f"Incorrect aggregator '{aggregator}'", ex) from ex
    if value_type is not None:
        if isinstance(aggregator, AggregatorDefinition):
            definition = aggregator
        elif isinstance(aggregator, str):
            definition = AggregatorDefinition.from_string(aggregator)
        else:
            definition = AggregatorDefinition.from_dict(aggregator)
        if definition.aggregator_type in NUMERIC_ONLY_AGGREGATORS \
                and value_type not in {"int", "float"}:
            raise ValueError(
                f"Aggregator '{aggregator}' requires a numeric value "
                f"type (int or float), but attribute has type '{value_type}'",
            )
