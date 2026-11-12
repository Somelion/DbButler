"""Prometheus text exposition (format 0.0.4) for what DbButler already
collects — routers/prometheus.py reads the store, this renders. Hand-written
rather than prometheus_client: a handful of gauges rebuilt from the store on
every scrape, with no process-level registry to keep in sync."""

import math
from dataclasses import dataclass, field

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@dataclass
class Family:
    name: str
    help: str
    type: str = "gauge"
    samples: list[tuple[dict[str, str], float]] = field(default_factory=list)

    def add(self, labels: dict[str, str], value: float | int | bool | None) -> None:
        if value is not None:
            self.samples.append((labels, float(value)))


def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _format_value(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return str(int(value)) if value.is_integer() and abs(value) < 2**53 else repr(value)


def render(families: list[Family]) -> str:
    """Every family gets its HELP and TYPE lines, even with no samples, so
    the metric names stay discoverable before the first collection."""
    lines = []
    for family in families:
        lines.append(f"# HELP {family.name} {_escape_help(family.help)}")
        lines.append(f"# TYPE {family.name} {family.type}")
        for labels, value in family.samples:
            label_text = ",".join(f'{key}="{_escape_label(val)}"' for key, val in labels.items())
            lines.append(f"{family.name}{{{label_text}}} {_format_value(value)}" if label_text else f"{family.name} {_format_value(value)}")
    return "\n".join(lines) + "\n"
