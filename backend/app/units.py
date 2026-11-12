"""Human-readable sizes and durations for text the backend writes itself
(the migration linter's impact sentences, the weekly report)."""


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown size"
    size = float(value)
    sign = "-" if size < 0 else ""
    size = abs(size)
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{sign}{size:.0f} {unit}" if unit == "bytes" else f"{sign}{size:.1f} {unit}"
        size /= 1024
    return f"{value} bytes"


def format_seconds(seconds: int | float | None) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
