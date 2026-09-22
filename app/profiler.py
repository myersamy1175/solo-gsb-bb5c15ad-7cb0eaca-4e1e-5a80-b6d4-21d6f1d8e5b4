"""Batch message statistics ("profile") over accepted parse results.

The API layer parses and validates each sample and feeds the accepted
results here. Statistics are accumulated **per message version** so that
fields sharing a name across versions never mix: each version group owns
independent accumulators for every field of its definition.

For every version group, in field-definition order:

* ``present_count`` -- how many accepted samples actually contained the
  field. A field skipped because its condition did not match is absent
  from the parse result and therefore never enters any denominator;
* for numeric fields (``uint``/``int``) additionally ``value_count``,
  ``min``, ``max`` and ``mean`` over the parsed ``value`` (i.e. after
  ``scale``/``offset``). Repeated numeric fields contribute **every array
  element** individually, so their ``value_count`` may exceed
  ``present_count``; ``bool``/``hex``/``ascii`` fields only report
  ``present_count``.
"""

from typing import Any, Dict, List

_NUMERIC_TYPES = ("uint", "int")


def _mean(total: Any, count: int) -> Any:
    """Mean rounded like the parser's scaled values (6 decimals).

    Arbitrarily wide fields (bit_length up to 4096) can overflow float
    division; fall back to the integer mean in that pathological case.
    """
    try:
        return round(total / count, 6)
    except OverflowError:
        return total // count


class _FieldStats:
    """Running statistics for one field of one version."""

    __slots__ = ("name", "numeric", "present", "value_count", "total", "min", "max")

    def __init__(self, name: str, numeric: bool):
        self.name = name
        self.numeric = numeric
        self.present = 0
        self.value_count = 0
        self.total: Any = 0  # stays an exact int until a scaled float shows up
        self.min: Any = None
        self.max: Any = None

    def add(self, value: Any) -> None:
        self.present += 1
        if not self.numeric:
            return
        # repeated fields arrive as lists: every element enters the stats
        values = value if isinstance(value, list) else [value]
        for v in values:
            self.value_count += 1
            self.total += v
            self.min = v if self.min is None else min(self.min, v)
            self.max = v if self.max is None else max(self.max, v)

    def to_dict(self) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"name": self.name, "present_count": self.present}
        if self.numeric:
            entry["value_count"] = self.value_count
            if self.value_count:
                entry["min"] = self.min
                entry["max"] = self.max
                entry["mean"] = _mean(self.total, self.value_count)
            else:
                # stable shape: numeric fields always carry the three keys
                entry["min"] = None
                entry["max"] = None
                entry["mean"] = None
        return entry


class VersionProfile:
    """Accumulates the statistics of one message version."""

    def __init__(self, record: Dict[str, Any]):
        self.version: str = record["version"]
        self.active: bool = record["active"]
        self.accepted = 0
        # Definition order is the output order; every defined field gets an
        # accumulator even when no sample ever presents it (counts stay 0).
        self._fields: List[_FieldStats] = [
            _FieldStats(f["name"], f["type"] in _NUMERIC_TYPES)
            for f in record["fields"]
        ]
        self._by_name = {f.name: f for f in self._fields}

    def add(self, result: Dict[str, Any]) -> None:
        """Fold one accepted /parse-style result into the statistics."""
        self.accepted += 1
        for parsed in result["fields"]:
            stats = self._by_name.get(parsed["name"])
            if stats is not None:
                stats.add(parsed["value"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "definition_active": self.active,
            "accepted_count": self.accepted,
            "fields": [f.to_dict() for f in self._fields],
        }
