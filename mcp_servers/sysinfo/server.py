import platform
import psutil
from mcp.server.fastmcp.server import FastMCP

mcp = FastMCP("sysinfo")


@mcp.tool()
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
def get_cpu() -> str:
    """Get CPU usage per core and frequency."""
    per_core = psutil.cpu_percent(interval=1, percpu=True)
    freq = psutil.cpu_freq()
    freq_str = f"{freq.current:.0f} MHz" if freq else "N/A"
    return f"Cores: {psutil.cpu_count()} | Per-core usage: {per_core} | Freq: {freq_str}"


@mcp.tool()
def get_memory() -> str:
    """Get RAM and swap memory usage."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return (
        f"RAM:  {mem.used // 1024**2} / {mem.total // 1024**2} MB ({mem.percent}%)\n"
        f"Swap: {swap.used // 1024**2} / {swap.total // 1024**2} MB ({swap.percent}%)"
    )


@mcp.tool()
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
def get_top_processes(limit: int = 10) -> str:
    """Get top processes sorted by CPU usage."""
    procs = sorted(
        psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
        key=lambda p: p.info["cpu_percent"] or 0,
        reverse=True,
    )[:limit]
    header = f"{'PID':<7}{'Name':<25}{'CPU%':<8}{'MEM%'}"
    rows = [
        f"{p.info['pid']:<7}{p.info['name']:<25}{p.info['cpu_percent'] or 0:<8.1f}{p.info['memory_percent'] or 0:.1f}"
        for p in procs
    ]
    return "\n".join([header] + rows)


if __name__ == "__main__":
    mcp.run()
