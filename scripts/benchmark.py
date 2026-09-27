"""
Benchmark the Planner Agent.
Measures planning time (LLM), execution time, and parallelism speedup.

Usage:
    python -m scripts.benchmark
"""
import asyncio
import json
import os
import time
from pathlib import Path

from orchestrator.planner import plan
from orchestrator.executor import execute
from orchestrator.registry import discover_all_tools, build_tools_description

SCENARIOS = [
    "get system memory usage and save it as a note titled Memory Report",
    "get CPU info and memory info simultaneously, then write data/sysreport.txt combining both",
    "get disk usage and write a DOCX report to data/disk_report.docx",
    "list files in the current directory and save the result to data/listing.txt",
    "get full system info (CPU, memory, disk) all at once, then write data/full_sysreport.txt",
]


async def run_scenario(task: str, tools_desc: str) -> dict:
    # ── Planning ──────────────────────────────────────────────
    t0 = time.perf_counter()
    task_plan = plan(task, tools_desc)
    t_plan = time.perf_counter() - t0

    # ── Execution ─────────────────────────────────────────────
    t1 = time.perf_counter()
    log = await execute(task_plan)
    t_exec = time.perf_counter() - t1

    # ── Speedup calculation ────────────────────────────────────
    waves: dict[int, list[int]] = {}
    for entry in log:
        g = entry.get("parallel_group", 0)
        waves.setdefault(g, []).append(entry.get("duration_ms", 0))

    t_sequential_ms = sum(e.get("duration_ms", 0) for e in log)
    t_parallel_ms   = sum(max(w) for w in waves.values()) if waves else 0
    speedup = round(t_sequential_ms / t_parallel_ms, 2) if t_parallel_ms > 0 else 1.0

    return {
        "task":             task,
        "steps":            len(log),
        "waves":            len(waves),
        "success":          all(e["status"] == "done" for e in log),
        "t_plan_s":         round(t_plan, 2),
        "t_exec_s":         round(t_exec, 2),
        "t_total_s":        round(t_plan + t_exec, 2),
        "t_sequential_ms":  t_sequential_ms,
        "t_parallel_ms":    t_parallel_ms,
        "speedup_x":        speedup,
    }


def print_table(results: list[dict]) -> None:
    W = 44
    header = f"{'Task':<{W}} {'Stps':>4} {'Plan':>6} {'Exec':>6} {'Total':>6} {'Speedup':>8}"
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        label = (r["task"][:W - 2] + "..") if len(r["task"]) > W else r["task"]
        ok    = "✓" if r["success"] else "✗"
        print(
            f"{label:<{W}} {r['steps']:>4}"
            f"  {r['t_plan_s']:>4.1f}s  {r['t_exec_s']:>4.1f}s"
            f"  {r['t_total_s']:>4.1f}s  {r['speedup_x']:>6.2f}x {ok}"
        )
    print("=" * len(header))

    avg_plan  = sum(r["t_plan_s"]   for r in results) / len(results)
    avg_exec  = sum(r["t_exec_s"]   for r in results) / len(results)
    avg_speed = sum(r["speedup_x"]  for r in results) / len(results)
    print(f"\nAverage — plan: {avg_plan:.1f}s  exec: {avg_exec:.1f}s  speedup: {avg_speed:.2f}x")


_INTER_SCENARIO_DELAY = float(os.getenv("BENCHMARK_DELAY_SEC", "5"))


async def main() -> None:
    print("Initialising tool registry…")
    tools      = await discover_all_tools()
    tools_desc = build_tools_description(tools)
    active     = [t["server"] for t in tools if "error" not in t]
    print(f"Active servers: {', '.join(sorted(set(active)))}\n")

    results = []
    for i, scenario in enumerate(SCENARIOS, 1):
        short = scenario[:60] + ("…" if len(scenario) > 60 else "")
        print(f"[{i}/{len(SCENARIOS)}] {short}")
        result = await run_scenario(scenario, tools_desc)
        results.append(result)
        if i < len(SCENARIOS) and _INTER_SCENARIO_DELAY > 0:
            await asyncio.sleep(_INTER_SCENARIO_DELAY)
        status = "✓" if result["success"] else "✗"
        print(
            f"         {status}  steps={result['steps']}  "
            f"plan={result['t_plan_s']}s  exec={result['t_exec_s']}s  "
            f"speedup={result['speedup_x']}x"
        )

    print_table(results)

    out = Path("data/benchmark_results.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results → {out}")


if __name__ == "__main__":
    asyncio.run(main())
