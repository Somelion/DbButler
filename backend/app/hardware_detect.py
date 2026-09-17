import psutil

# On Docker Desktop (Windows/Mac) this reads the Docker VM's allocated memory,
# not the real host machine's — the architecture doc's own flagged risk. There
# is no reliable way to tell that apart from a genuine bare-metal Linux host
# from inside the container, so the caveat is always shown rather than only
# when some fragile heuristic fires.
DOCKER_CAVEAT = (
    "Detected from inside the backend's own container. On Docker Desktop (Windows/Mac) this is "
    "the Docker VM's allocated memory, not necessarily your real machine's — check it matches "
    "before saving."
)


def detect_hardware() -> dict:
    ram_mb = int(psutil.virtual_memory().total / (1024 * 1024))
    cpu_cores = psutil.cpu_count(logical=True) or 1
    return {"detected_ram_mb": ram_mb, "detected_cpu_cores": cpu_cores, "caveat": DOCKER_CAVEAT}
