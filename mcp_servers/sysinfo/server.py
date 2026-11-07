import platform
import time
import psutil
from mcp.server.fastmcp.server import FastMCP
from mcp_servers.common import tool_errors

mcp = FastMCP("sysinfo")


@mcp.tool()
@tool_errors
def get_system_info() -> str:
    """Get a full system overview: OS, CPU, memory, and disk usage."""
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return (
        f"OS: {platform.system()} {platform.release()} ({platform.architecture()[0]})\n"
        f"CPU: {psutil.cpu_count()} cores, {cpu_pct}% used\n"
        f"Memory: {mem.used // 1024**2} MB / {mem.total // 1024**2} MB ({mem.percent}%)\n"
        f"Disk: {disk.used // 1024**3} GB / {disk.total // 1024**3} GB ({disk.percent}%)"
    )


@mcp.tool()
@tool_errors
def get_cpu() -> str:
    """Get CPU usage per core and frequency."""
    per_core = psutil.cpu_percent(interval=1, percpu=True)
    freq = psutil.cpu_freq()
    freq_str = f"{freq.current:.0f} MHz" if freq else "N/A"
    return f"Cores: {psutil.cpu_count()} | Per-core usage: {per_core} | Freq: {freq_str}"


@mcp.tool()
@tool_errors
def get_memory() -> str:
    """Get RAM and swap memory usage."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return (
        f"RAM:  {mem.used // 1024**2} / {mem.total // 1024**2} MB ({mem.percent}%)\n"
        f"Swap: {swap.used // 1024**2} / {swap.total // 1024**2} MB ({swap.percent}%)"
    )


@mcp.tool()
@tool_errors
def get_disk() -> str:
    """Get disk usage for all mounted partitions."""
    lines = []
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            lines.append(
                f"{part.mountpoint}: {u.used // 1024**3} / {u.total // 1024**3} GB ({u.percent}%)"
            )
        except PermissionError:
            pass
    return "\n".join(lines) or "No disk info available."


@mcp.tool()
@tool_errors
def get_top_processes(limit: int = 10) -> str:
    """Get top processes by CPU usage measured over one shared one-second interval."""
    processes = []
    for process in psutil.process_iter():
        if process.pid == 0:  # Windows' System Idle Process measures idle time, not load.
            continue
        try:
            process.cpu_percent(interval=None)  # Prime the per-process counter.
            processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(1)
    samples = []
    for process in processes:
        try:
            with process.oneshot():
                cpu = process.cpu_percent(interval=None)
                samples.append((process.pid, process.name(), cpu, process.memory_percent()))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue  # A process can exit or become inaccessible during the sample.
    samples.sort(key=lambda sample: (-sample[2], sample[0]))
    header = f"{'PID':<7}{'Name':<25}{'CPU%':<8}{'MEM%'}"
    rows = [
        f"{pid:<7}{name:<25}{cpu:<8.1f}{memory:.1f}"
        for pid, name, cpu, memory in samples[:limit]
    ]
    return "\n".join([header] + rows)


if __name__ == "__main__":
    mcp.run()
