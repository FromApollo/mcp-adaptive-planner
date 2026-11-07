from contextlib import nullcontext

import psutil
import pytest

from mcp_servers.sysinfo import server


class Process:
    def __init__(self, pid, cpu, state, *, fail_first=None, fail_second=None):
        self.pid = pid
        self.cpu = cpu
        self.state = state
        self.calls = 0
        self.fail_first = fail_first
        self.fail_second = fail_second

    def cpu_percent(self, interval=None):
        assert interval is None
        self.calls += 1
        if self.calls == 1:
            assert not self.state["sampled"]
            if self.fail_first:
                raise self.fail_first(self.pid)
            return 0.0
        assert self.state["sampled"]
        if self.fail_second:
            raise self.fail_second(self.pid)
        return self.cpu

    def oneshot(self):
        return nullcontext()

    def name(self):
        return f"process-{self.pid}"

    def memory_percent(self):
        return 2.5


@pytest.fixture
def sampling(monkeypatch):
    state = {"sampled": False, "waits": []}
    def sleep(seconds):
        state["waits"].append(seconds)
        state["sampled"] = True
    monkeypatch.setattr(server.time, "sleep", sleep)
    return state


def test_ranks_second_cpu_samples_after_one_shared_interval(monkeypatch, sampling):
    processes = [Process(1, 5.0, sampling), Process(2, 175.0, sampling), Process(3, 30.0, sampling)]
    monkeypatch.setattr(server.psutil, "process_iter", lambda: iter(processes))
    result = server.get_top_processes(limit=2).splitlines()
    assert [line.split()[0] for line in result[1:]] == ["2", "3"]
    assert "175.0" in result[1]  # Multi-core processes may legitimately exceed 100%.
    assert "30.0" in result[2]
    assert all(p.calls == 2 for p in processes)
    assert sampling["waits"] == [1]


@pytest.mark.parametrize("error", [psutil.NoSuchProcess, psutil.AccessDenied])
def test_process_exiting_or_denied_does_not_break_sample(monkeypatch, sampling, error):
    processes = [Process(1, 1, sampling, fail_first=error),
                 Process(2, 2, sampling, fail_second=error), Process(3, 40, sampling)]
    monkeypatch.setattr(server.psutil, "process_iter", lambda: iter(processes))
    result = server.get_top_processes()
    assert "process-1" not in result and "process-2" not in result
    assert "process-3" in result and "40.0" in result


def test_system_idle_process_is_not_cpu_load(monkeypatch, sampling):
    idle, busy = Process(0, 999, sampling), Process(1, 15, sampling)
    monkeypatch.setattr(server.psutil, "process_iter", lambda: iter([idle, busy]))
    result = server.get_top_processes()
    assert idle.calls == 0
    assert "process-0" not in result
    assert "process-1" in result
