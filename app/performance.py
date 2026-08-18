"""请求级性能埋点，不记录 Prompt、密钥或用户原始内容。"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable

logger = logging.getLogger(__name__)

_trace_var: ContextVar["PerformanceTrace | None"] = ContextVar(
    "performance_trace", default=None)
_stage_var: ContextVar[str] = ContextVar("performance_stage", default="Other")
_llm_observation_var: ContextVar["LLMObservation | None"] = ContextVar(
    "llm_observation", default=None)
_llm_event_sink_var: ContextVar["Callable[[dict], None] | None"] = ContextVar(
    "llm_event_sink", default=None)
_request_id_var: ContextVar[str] = ContextVar("performance_request_id", default="")


@dataclass
class LLMObservation:
    stage: str
    started_at: float = field(default_factory=perf_counter)
    attempts: int = 0
    attempt_ms: list[float] = field(default_factory=list)
    retries: int = 0
    timeout_seen: bool = False
    plain_fallback: bool = False
    success: bool = False
    first_attempt_success: bool = False


@contextmanager
def llm_event_sink(request_id: str, sink: Callable[[dict], None]):
    """为 benchmark 临时接收逐次 LLM 事件；常规服务默认关闭。"""
    request_token = _request_id_var.set(request_id)
    sink_token = _llm_event_sink_var.set(sink)
    try:
        yield
    finally:
        _llm_event_sink_var.reset(sink_token)
        _request_id_var.reset(request_token)


class LLMMetricsRegistry:
    """进程内有界聚合，只保存数值指标，不保存 Prompt 或用户数据。"""

    def __init__(self, max_samples: int = 2000):
        self._samples = defaultdict(lambda: deque(maxlen=max_samples))
        self._lock = threading.Lock()

    def add(self, observation: LLMObservation) -> None:
        elapsed_ms = (perf_counter() - observation.started_at) * 1000
        retry_ms = sum(observation.attempt_ms[1:1 + observation.retries])
        sample = {
            "latency_ms": elapsed_ms,
            "success": observation.success,
            "first_attempt_success": observation.first_attempt_success,
            "timeout": observation.timeout_seen,
            "retries": observation.retries,
            "retry_extra_ms": retry_ms,
            "plain_fallback": observation.plain_fallback,
        }
        with self._lock:
            self._samples[observation.stage].append(sample)

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile)))
        return round(ordered[index], 3)

    def snapshot(self) -> dict:
        with self._lock:
            copied = {stage: list(samples) for stage, samples in self._samples.items()}
        result = {}
        for stage, samples in copied.items():
            count = len(samples)
            latencies = [item["latency_ms"] for item in samples]
            result[stage] = {
                "calls": count,
                "success_rate": round(sum(item["success"] for item in samples) / count, 4),
                "first_attempt_success_rate": round(
                    sum(item["first_attempt_success"] for item in samples) / count, 4),
                "timeout_rate": round(sum(item["timeout"] for item in samples) / count, 4),
                "average_retries": round(sum(item["retries"] for item in samples) / count, 3),
                "retry_extra_ms": round(sum(item["retry_extra_ms"] for item in samples), 3),
                "plain_fallback_rate": round(
                    sum(item["plain_fallback"] for item in samples) / count, 4),
                "p50_ms": self._percentile(latencies, 0.50),
                "p95_ms": self._percentile(latencies, 0.95),
                "p99_ms": self._percentile(latencies, 0.99),
            }
        return result


llm_metrics = LLMMetricsRegistry()


@dataclass
class PerformanceTrace:
    task_count: int
    member_count: int
    started_at: float = field(default_factory=perf_counter)
    stages_ms: dict[str, float] = field(default_factory=dict)
    llm_calls: dict[str, int] = field(default_factory=dict)
    llm_total_ms: dict[str, float] = field(default_factory=dict)
    logical_llm_calls: dict[str, int] = field(default_factory=dict)
    llm_retries: dict[str, int] = field(default_factory=dict)
    llm_retry_extra_ms: dict[str, float] = field(default_factory=dict)
    llm_timeouts: dict[str, int] = field(default_factory=dict)
    llm_plain_fallbacks: dict[str, int] = field(default_factory=dict)
    reflection_executed: bool = False
    reflection_reasons: list[str] = field(default_factory=list)
    reporter_blocks_response: bool = False
    first_useful_result_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_stage(self, name: str, elapsed_ms: float) -> None:
        with self._lock:
            self.stages_ms[name] = self.stages_ms.get(name, 0.0) + elapsed_ms

    def add_logical_llm_call(self, name: str) -> None:
        with self._lock:
            self.logical_llm_calls[name] = self.logical_llm_calls.get(name, 0) + 1

    def add_llm_call(self, name: str, elapsed_ms: float) -> None:
        with self._lock:
            self.llm_calls[name] = self.llm_calls.get(name, 0) + 1
            self.llm_total_ms[name] = self.llm_total_ms.get(name, 0.0) + elapsed_ms

    def add_llm_observation(self, observation: LLMObservation) -> None:
        with self._lock:
            name = observation.stage
            self.llm_retries[name] = self.llm_retries.get(name, 0) + observation.retries
            self.llm_retry_extra_ms[name] = (
                self.llm_retry_extra_ms.get(name, 0.0)
                + sum(observation.attempt_ms[1:1 + observation.retries]))
            self.llm_timeouts[name] = self.llm_timeouts.get(name, 0) + int(
                observation.timeout_seen)
            self.llm_plain_fallbacks[name] = self.llm_plain_fallbacks.get(name, 0) + int(
                observation.plain_fallback)

    def mark_first_useful_result(self) -> None:
        self.first_useful_result_ms = (perf_counter() - self.started_at) * 1000

    def finish(self) -> dict:
        total_ms = (perf_counter() - self.started_at) * 1000
        result = {
            "stages_ms": {key: round(value, 3) for key, value in self.stages_ms.items()},
            "llm_calls": dict(self.llm_calls),
            "logical_llm_calls": dict(self.logical_llm_calls),
            "llm_total_ms": {key: round(value, 3) for key, value in self.llm_total_ms.items()},
            "llm_retries": dict(self.llm_retries),
            "llm_retry_extra_ms": {
                key: round(value, 3) for key, value in self.llm_retry_extra_ms.items()},
            "llm_timeouts": dict(self.llm_timeouts),
            "llm_plain_fallbacks": dict(self.llm_plain_fallbacks),
            "total_llm_calls": sum(self.llm_calls.values()),
            "total_logical_llm_calls": sum(self.logical_llm_calls.values()),
            "task_count": self.task_count,
            "member_count": self.member_count,
            "reflection_executed": self.reflection_executed,
            "reflection_reasons": self.reflection_reasons,
            "reporter_blocks_response": self.reporter_blocks_response,
            "first_useful_result_ms": round(self.first_useful_result_ms, 3),
            "total_ms": round(total_ms, 3),
            "cpm_ms": round(self.stages_ms.get("Timeline", 0.0), 3),
        }
        logger.info("performance_metrics=%s", result)
        return result


@contextmanager
def request_trace(trace: PerformanceTrace):
    token = _trace_var.set(trace)
    try:
        yield trace
    finally:
        _trace_var.reset(token)


@contextmanager
def stage(name: str):
    trace = _trace_var.get()
    stage_token = _stage_var.set(name)
    started = perf_counter()
    try:
        yield
    finally:
        if trace is not None:
            trace.add_stage(name, (perf_counter() - started) * 1000)
        _stage_var.reset(stage_token)


def record_logical_llm_call() -> None:
    trace = _trace_var.get()
    if trace is not None:
        trace.add_logical_llm_call(_stage_var.get())


def current_llm_observation() -> LLMObservation | None:
    return _llm_observation_var.get()


@contextmanager
def observe_logical_llm_call():
    observation = LLMObservation(stage=_stage_var.get())
    token = _llm_observation_var.set(observation)
    try:
        yield observation
    finally:
        _llm_observation_var.reset(token)
        llm_metrics.add(observation)
        trace = _trace_var.get()
        if trace is not None:
            trace.add_llm_observation(observation)


@contextmanager
def physical_llm_call():
    trace = _trace_var.get()
    started = perf_counter()
    status = "success"
    try:
        yield
    except Exception as exc:
        # 延迟导入避免 performance 与 llm.client 形成循环依赖。
        from app.llm.client import _classify_error
        status = _classify_error(exc)
        raise
    finally:
        elapsed_ms = (perf_counter() - started) * 1000
        observation = _llm_observation_var.get()
        if observation is not None:
            observation.attempts += 1
            observation.attempt_ms.append(elapsed_ms)
            sink = _llm_event_sink_var.get()
            if sink is not None:
                sink({
                    "request_id": _request_id_var.get(),
                    "agent": observation.stage.lower(),
                    "attempt": observation.attempts,
                    "latency_ms": round(elapsed_ms, 3),
                    "status": status,
                    "fallback": observation.plain_fallback,
                })
        if trace is not None:
            trace.add_llm_call(_stage_var.get(), elapsed_ms)
