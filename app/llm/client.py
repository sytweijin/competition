"""
A1: LLM 调用封装 + Structured Output
所有 Agent 通过此模块调用 LLM，不直接调用 OpenAI SDK。

v1.1 改进：
- 加 timeout 防止永久挂起
- 错误分类：auth_error / rate_limit / parse_error / timeout / unknown
- structured output fallback：parse 失败时回退到普通 create + validate
- chat_text 改用 .create() 而非 .parse()
"""

import json
import logging
import re
from typing import Optional, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.config import (
    APP_MODEL_MODE, ASCEND_OMNI_WS_URL, LLM_API_KEY, LLM_BASE_URL,
    LLM_DISABLE_THINKING, LLM_MAX_RETRIES, LLM_MAX_TOKENS, LLM_MODEL,
    LLM_PREFER_PLAIN, LLM_TIMEOUT, MAP_REALTIME_API_KEY,
)
from app.models.schemas import AgentError
from app.performance import (
    current_llm_observation, observe_logical_llm_call,
    physical_llm_call, record_logical_llm_call,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)



def _classify_error(e: Exception) -> str:
    """将异常分类为可操作的 error_type。

    优先用 OpenAI SDK 异常类型，避免关键字误判（兼容非英文报错）。
    """
    import openai as _oai
    def _t(name):
        cls = getattr(_oai, name, None)
        return (cls,) if cls is not None else ()
    if isinstance(e, (TimeoutError,) + _t("APITimeoutError")):
        return "timeout"
    if isinstance(e, _t("AuthenticationError")):
        return "auth_error"
    if isinstance(e, _t("RateLimitError")):
        return "rate_limit"
    if isinstance(e, _t("APIConnectionError")):
        return "connection_error"
    if isinstance(e, (ValidationError, ValueError)):
        return "parse_error"
    if isinstance(e, _t("BadRequestError")):
        return "parse_error"
    if isinstance(e, _t("APIStatusError")):
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code is not None and 500 <= code < 600:
            return "unknown"
        return "parse_error"
    return "unknown"


class LLMClient:
    """线程安全的 LLM 调用客户端（每个 Agent 可独立实例化）"""

    # 模块级单例：避免每次请求都新建 OpenAI 客户端导致首次请求冷启动超时。
    # OpenAI SDK 内部的 httpx 连接池在同一个 client 实例上复用，
    # 新建 client 会触发 TCP/TLS 握手，首次请求更容易超时。
    _singleton: "LLMClient | None" = None

    @classmethod
    def get_shared(cls) -> "LLMClient":
        """获取全局共享实例（复用连接池，消除首次请求冷启动）。"""
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton

    def __init__(self, model: Optional[str] = None):
        self.model = model or LLM_MODEL
        self._mode = APP_MODEL_MODE
        if self._mode == "minicpm":
            # 合规模式（创新应用赛道要求不得使用其他模型）：
            # 仅调用 MiniCPM-o 4.5（本地 A3 或云端 ModelBest Realtime），
            # 不创建任何外部模型客户端。
            self._enabled = bool(MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL)
            self._prefer_plain = True
            self._client = None
        else:
            self._enabled = bool(LLM_API_KEY)
            self._prefer_plain = LLM_PREFER_PLAIN
            # 禁用 SDK 隐式重试；否则应用层超时会被默认重试放大。
            self._client = (
                OpenAI(
                    api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
                    max_retries=0,
                )
                if self._enabled else None
            )

    def _realtime_text(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """合规模式：通过 MiniCPM-o Realtime 文本接口调用（本地 A3 / 云端）。"""
        import asyncio

        from app.services.omni_chat import (
            _NO_CANNED_NUDGE, _flatten_history,
            _looks_like_canned_reply, _looks_like_garbage)
        from app.services.realtime_client import RealtimeClient, RealtimeError

        history = list(messages or [])
        if len(history) > 1:
            # 本地 A3 忽略分条多轮消息：摊平历史，和抽屉对话同一套兼容写法。
            *prev, last = history
            ctx = _flatten_history(prev)
            content = last.get("content")
            if ctx and isinstance(content, str):
                history = [{"role": "user", "content": (
                    f"【对话上下文】\n{ctx}\n\n【当前问题】\n{content}")}]

        async def _call(attempt: int = 1) -> str:
            final_system = system_prompt
            if attempt > 1:
                final_system = (
                    (final_system or "") + "\n\n" + _NO_CANNED_NUDGE).strip()
            result = await RealtimeClient().chat(
                messages=history,
                system_prompt=final_system,
                max_new_tokens=max_tokens or LLM_MAX_TOKENS,
                omni_mode=False,
                tts_enabled=False,
                timeout=timeout or LLM_TIMEOUT,
            )
            text = (result.text or "").strip()
            if _looks_like_garbage(text) or _looks_like_canned_reply(text):
                # 云端 ModelBest 偶发开场白/客套/乱码：带防客套指令重试一次，
                # 提升建议抽题/答辩模拟等文字链路的成功率；
                # 本地 A3 推理慢（约 50 token/s）且已有确定性兜底，保持单次
                # 命中即抛，避免把演示等待时间再翻倍。
                if attempt == 1 and not ASCEND_OMNI_WS_URL:
                    logger.warning(
                        "MiniCPM-o 云端文本输出异常（乱码/客套），"
                        "带防客套指令重试：%r", text[:120])
                    return await _call(2)
                raise ValueError("MiniCPM-o 输出异常（乱码/客套回复），请重试")
            return text

        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # 无运行中事件循环：直接 run。
                return asyncio.run(_call(1))
            # 已在事件循环内（异步路由直接调用）：换线程跑，避免
            # "asyncio.run() cannot be called from a running event loop"。
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _call(1)).result()
        except RealtimeError as exc:
            raise ValueError(str(exc)) from exc

    def _vendor_options(self) -> dict:
        """仅为 DeepSeek V4 结构化业务调用补充厂商参数。"""
        if LLM_DISABLE_THINKING and self.model.lower().startswith("deepseek-v4"):
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}

    def chat_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.3,
        max_retries: int = LLM_MAX_RETRIES,
    ) -> T | AgentError:
        """记录一次完整逻辑调用，并执行分类重试策略。"""
        if not self._enabled:
            label = "MiniCPM-o" if self._mode == "minicpm" else "LLM_API_KEY"
            return AgentError(agent="LLMClient", error_type="auth_error",
                              message=f"{label} 未配置，跳过 LLM 调用",
                              recoverable=False)
        record_logical_llm_call()
        with observe_logical_llm_call() as observation:
            result = self._chat_structured_impl(
                system_prompt, user_prompt, response_model,
                temperature, max_retries)
            observation.success = not isinstance(result, AgentError)
            observation.first_attempt_success = (
                observation.success and observation.attempts == 1)
            return result

    def _chat_structured_impl(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float,
        max_retries: int,
    ) -> T | AgentError:
        """调用 LLM 并返回结构化输出。

        策略：先尝试 structured output（response_format），失败后回退到
        普通 create + 手动 JSON 提取 + model_validate_json。
        """
        observation = current_llm_observation()
        if self._mode == "minicpm":
            # 合规模式：MiniCPM-o 文本 → 手动 JSON 提取 + 验证，
            # 复用现有 JSON 修复逻辑，保证结构化输出可用。
            try:
                return self._try_plain_validate_minicpm(
                    system_prompt, user_prompt, response_model, temperature)
            except Exception as exc:
                return AgentError(
                    agent="LLMClient",
                    error_type=_classify_error(exc),
                    message=f"MiniCPM-o 调用失败：{exc}",
                    recoverable=True,
                )
        if self._prefer_plain:
            try:
                return self._try_plain_validate(
                    system_prompt, user_prompt, response_model, temperature)
            except Exception as exc:
                if observation is not None and _classify_error(exc) == "timeout":
                    observation.timeout_seen = True
                return AgentError(
                    agent="LLMClient", error_type=_classify_error(exc),
                    message=f"LLM JSON 调用失败：{exc}", recoverable=True)
        # 核心 Agent 最多一次网络型重试；格式错误不重复 structured，
        # 限流/鉴权也不立即重试，避免固定三次造成尾延迟与限流放大。
        retries = min(2, max(1, max_retries))
        last_error_type = "unknown"
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return self._try_structured(system_prompt, user_prompt,
                                            response_model, temperature)
            except Exception as e:
                err_type = _classify_error(e)
                last_error_type, last_error = err_type, e
                if observation is not None and err_type == "timeout":
                    observation.timeout_seen = True
                logger.warning("LLM structured attempt %d/%d (%s): %s",
                               attempt + 1, retries, err_type, e)
                if err_type == "auth_error":
                    return AgentError(agent="LLMClient", error_type=err_type,
                                     message=f"API 鉴权失败：{e}",
                                     recoverable=False)
                if err_type == "parse_error":
                    break  # 结构化重试无意义，直接回退 plain create
                if err_type in ("rate_limit", "connection_error"):
                    break
                if attempt + 1 < retries and observation is not None:
                    observation.retries += 1
        # 网络错误不切换 plain 再等待一个完整超时周期；plain 只解决格式兼容，
        # 无法解决超时/限流。直接交给 Agent 的确定性兜底。
        if last_error_type in ("timeout", "rate_limit", "connection_error", "unknown"):
            return AgentError(
                agent="LLMClient",
                error_type=last_error_type,
                message=f"LLM 调用失败（已尝试至多 {retries} 次）：{last_error}",
                recoverable=True,
            )
        try:
            logger.info("Falling back to plain create + validate")
            if observation is not None:
                observation.plain_fallback = True
            return self._try_plain_validate(
                system_prompt, user_prompt,
                response_model, temperature)
        except Exception as e2:
            return AgentError(
                agent="LLMClient",
                error_type=_classify_error(e2),
                message=(f"LLM 调用失败（已尝试 {retries} "
                         f"次结构化 + 1 次回退）：{e2}"),
                recoverable=True,
            )

    def _try_structured(self, system_prompt, user_prompt,
                        response_model, temperature) -> T:
        """尝试使用 beta structured output API。"""
        with physical_llm_call():
            resp = self._client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
                temperature=temperature,
                timeout=LLM_TIMEOUT,
                **self._vendor_options(),
            )
            msg = resp.choices[0].message
            parsed = getattr(msg, "parsed", None)
            if parsed is not None:
                if isinstance(parsed, response_model):
                    return parsed
                return response_model.model_validate(parsed)
            raw = getattr(msg, "content", None)
            if not raw:
                raise ValueError("Empty response from LLM")
            return response_model.model_validate_json(raw)

    def _try_plain_validate(self, system_prompt, user_prompt,
                            response_model, temperature) -> T:
        """普通 create + 手动提取 JSON + 验证。

        推理模型（如 deepseek-v4-flash）思考慢、首字延迟高，网络/模型偶发超时；
        这里对超时做有限重试（默认 2 次），以容忍慢响应。若响应被截断
        （finish_reason=="length"），再把预算翻倍重试一次（上限 32000）。
        """
        enhanced_system = system_prompt + "\n\n重要：你必须输出合法 JSON，不要包含 markdown 代码块标记。"
        messages = [
            {"role": "system", "content": enhanced_system},
            {"role": "user", "content": user_prompt},
        ]
        budget = LLM_MAX_TOKENS
        for _ in range(2):  # 首次 + 截断后重试一次
            resp = self._call_with_timeout_retry(
                messages, budget, temperature, max_retries=0)
            msg = resp.choices[0].message
            raw = msg.content or ""
            # 推理模型正文为空但思考含 JSON 时回退抽取（避免把思考当正文误用）。
            if not raw.strip():
                rc = getattr(msg, "reasoning_content", None) or ""
                if "{" in rc:
                    raw = rc
            finish = getattr(resp.choices[0], "finish_reason", None)
            logger.info("=== LLM Plain Response Start (budget=%d) ===", budget)
            logger.info("Raw response length: %d chars, finish_reason=%s",
                        len(raw), finish)
            # 不再把完整原文/JSON 倾倒到终端——这些内容含 presenter/qa_primary
            # 等内部字段名，以及模型可能回吐的「主讲/主答/辅答」等遗留术语，
            # 会给用户造成"系统还在围绕答辩责任"的误解。改为只记长度与状态。
            extracted = self._extract_json(raw)
            logger.info("Extracted JSON length: %d chars", len(extracted))
            try:
                result = response_model.model_validate_json(extracted)
                logger.info("✓ LLM plain validate SUCCESS")
                logger.info("=== LLM Plain Response End ===")
                return result
            except (ValidationError, ValueError) as e:
                logger.warning("✗ LLM plain validate FAILED: %s", str(e)[:500])
                logger.info("Attempting schema repair...")
                repaired = self._repair_response(extracted, response_model)
                if repaired is not None:
                    logger.info("✓ LLM response passed local schema repair")
                    logger.info("=== LLM Plain Response End ===")
                    return repaired
                logger.error("✗ LLM plain validate and repair both failed")
                # 校验彻底失败时才在 DEBUG 级别打印原文，便于排查但不污染正常运行输出
                logger.debug("Full extracted JSON for debugging:\n%s", extracted)
                # 若确属截断且还能加预算，重试一次；否则放弃。
                if finish == "length" and budget < 32000:
                    observation = current_llm_observation()
                    if observation is not None:
                        observation.retries += 1
                    budget = min(32000, budget * 2)
                    logger.warning("Response truncated (length); retrying with budget=%d", budget)
                    continue
                break
        # 两次都失败（含一次截断重试）；抛出以走上层兜底。
        raise ValueError("LLM 返回的 JSON 经校验与本地修复均不可用（可能仍未完整）")

    def _try_plain_validate_minicpm(self, system_prompt, user_prompt,
                                    response_model, temperature) -> T:
        """合规模式：MiniCPM-o 文本 → 手动 JSON 提取 + 验证 + 本地修复。"""
        enhanced_system = (
            system_prompt
            + "\n\n重要：你必须输出合法 JSON，不要包含 markdown 代码块标记。"
        )
        raw = self._realtime_text(
            enhanced_system,
            [{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=LLM_MAX_TOKENS,
        )
        extracted = self._extract_json(raw)
        try:
            return response_model.model_validate_json(extracted)
        except (ValidationError, ValueError):
            repaired = self._repair_response(extracted, response_model)
            if repaired is not None:
                return repaired
            raise ValueError("MiniCPM-o 返回的 JSON 经校验与本地修复仍不可用")

    def _call_with_timeout_retry(self, messages, budget, temperature,
                                 max_retries: int = 2):
        """带超时重试的 create 调用，容忍推理模型的慢响应。"""
        last_exc: Exception | None = None
        for i in range(max_retries + 1):
            try:
                with physical_llm_call():
                    return self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=budget,
                        timeout=LLM_TIMEOUT,
                        **self._vendor_options(),
                    )
            except Exception as e:  # noqa: BLE001
                if _classify_error(e) == "timeout" and i < max_retries:
                    logger.warning("LLM 请求超时，第 %d/%d 次重试", i + 1, max_retries)
                    last_exc = e
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("unreachable")

    @staticmethod
    def _repair_response(raw: str, response_model: type[T]) -> T | None:
        """本地修复 Planner 的轻微 JSON/字段问题，不再次请求模型。

        兼容两类异常：
        1) 字段缺失/类型偏差：走 _normalize_task_objs / _normalize_assignment_objs 规范化；
        2) JSON 被截断/不完整（推理模型 max_tokens 预算被思考吃掉）：
           走 _salvage_task_objs 抢救已完整落地的任务对象，而非整次失败。
        """
        if response_model.__name__ == "QAOutput":
            # Matcher 输出：模型可能回吐「主讲/主答/辅答」等旧术语或字符串
            # 形式的 qa_support / 百分制 score，统一归一化后仍可校验通过。
            return LLMClient._repair_qa_output(raw, response_model)
        if response_model.__name__ != "PlanOutput":
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if payload is None:
            # 截断/不完整：尝试从残缺 JSON 中抢救完整任务对象
            salvaged = LLMClient._salvage_task_objs(raw)
            if not salvaged:
                return None
            payload = {"tasks": salvaged,
                       "summary": "AI 返回被截断，系统已尽力保留已生成的任务"}
        if isinstance(payload, list):
            payload = {"tasks": payload}
        if not isinstance(payload, dict):
            return None
        for wrapper in ("plan", "draft", "result", "data"):
            if isinstance(payload.get(wrapper), dict):
                payload = payload[wrapper]
                break
        task_objs = payload.get("tasks", payload.get("subtasks", payload.get("task_list")))
        if not isinstance(task_objs, list):
            return None
        normalized = LLMClient._normalize_task_objs(task_objs)
        if not normalized:
            return None
        repaired_payload = {
            "tasks": normalized,
            "summary": str(payload.get("summary", "AI 任务草案（本地修复）")),
            "reasoning": str(payload.get("reasoning", "")),
        }
        try:
            return response_model.model_validate(repaired_payload)
        except (ValidationError, ValueError):
            return None

    @staticmethod
    def _repair_qa_output(raw: str, response_model: type[T]) -> T | None:
        """本地修复 Matcher（QAOutput）的轻微 JSON/字段问题，不再次请求模型。

        MiniCPM-o 等模型对 QAOutput 常见两类退化：
        1) 字段别名/类型偏差：回吐「主讲/主答/辅答」等旧字段名、字符串形式
           qa_support、0-100 百分制 score，统一归一化；
        2) JSON 被截断/不完整：抢救已完整落地的 assignment 对象。
        全部失败时返回 None，交由上层确定性 B3 兜底。
        """
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if payload is None:
            payload = {
                "assignments": LLMClient._salvage_assignment_objs(raw),
                "note": "AI 返回被截断，系统已尽力保留已生成的分工",
            }
        if isinstance(payload, list):
            payload = {"assignments": payload}
        if not isinstance(payload, dict):
            return None
        for wrapper in ("qa_matrix", "result", "data"):
            if isinstance(payload.get(wrapper), dict):
                payload = payload[wrapper]
                break
        assign_objs = payload.get(
            "assignments", payload.get("assignment_list"))
        if not isinstance(assign_objs, list):
            return None
        normalized = LLMClient._normalize_assignment_objs(assign_objs)
        if not normalized:
            return None
        repaired_payload = {
            "assignments": normalized,
            "note": str(payload.get("note", "本地修复的自动分工")),
            "workload": payload.get("workload") or {},
        }
        try:
            return response_model.model_validate(repaired_payload)
        except (ValidationError, ValueError):
            return None

    @staticmethod
    def _normalize_assignment_objs(assign_objs: list) -> list[dict] | None:
        """把原始分工对象列表规范化为 QAAssignment 兼容的 dict 列表。"""
        normalized: list[dict] = []
        for item in assign_objs[:80]:
            if not isinstance(item, dict):
                continue
            task_id_raw = item.get(
                "task_id",
                item.get("taskId", item.get("id", item.get("task", ""))))
            if isinstance(task_id_raw, dict):
                continue
            task_id = str(task_id_raw or "").strip()
            if not task_id:
                continue
            task_name = str(
                item.get(
                    "task_name",
                    item.get("taskName",
                             item.get("name", item.get("title", "")))),
            ).strip() or task_id
            presenter = str(
                item.get(
                    "presenter",
                    item.get("主讲",
                             item.get("owner",
                                      item.get("assignee",
                                               item.get("负责人", ""))))),
            ).strip()
            qa_primary = str(
                item.get(
                    "qa_primary",
                    item.get("主答",
                             item.get("primary",
                                      item.get("主要协助",
                                               item.get("协助", ""))))),
            ).strip()
            support_raw = item.get(
                "qa_support",
                item.get("辅答",
                         item.get("support",
                                  item.get("supporters",
                                           item.get("qa_support_list", [])))))
            if isinstance(support_raw, str):
                support = [
                    value.strip()
                    for value in re.split(r"[,，、/;；]", support_raw)
                    if value.strip()
                ]
            else:
                support = [
                    str(value).strip()
                    for value in (support_raw or [])
                    if str(value).strip()
                ]
            support = list(dict.fromkeys(
                name for name in support
                if name and name != presenter and name != qa_primary))
            normalized.append({
                "task_id": task_id,
                "task_name": task_name,
                "presenter": presenter,
                "qa_primary": qa_primary,
                "qa_support": support,
                "score": LLMClient._coerce_score(item.get(
                    "score",
                    item.get("match_score",
                             item.get("matching",
                                      item.get("匹配度", 0.0))))),
                "reasoning": str(
                    item.get("reasoning",
                             item.get("reason", item.get("why", "")))),
            })
        return normalized or None

    @staticmethod
    def _coerce_score(raw) -> float:
        """把模型输出的匹配分归一化到 0-1：百分制自动除以 100。"""
        if raw is None:
            return 0.0
        if isinstance(raw, (int, float)):
            value = float(raw)
        else:
            match = re.search(r"\d+(?:\.\d+)?", str(raw))
            value = float(match.group()) if match else 0.0
        if value > 1.0:
            value = value / 100.0
        return max(0.0, min(1.0, value))

    @staticmethod
    def _salvage_assignment_objs(raw: str) -> list[dict]:
        """从截断的 QA JSON 中抢救已完整闭合的 assignment 对象。"""
        if not raw:
            return []
        candidate = None
        for key in ("assignments", "assignment_list"):
            match = re.search(rf'"{key}"\s*:\s*\[', raw)
            if match:
                candidate = raw[match.end():]
                break
        keys = ("task_id", "task_name", "presenter", "name")
        if candidate is not None:
            return LLMClient._extract_balanced_objs(
                candidate, required_keys=keys)
        return [
            obj for obj in LLMClient._extract_balanced_objs(
                raw, required_keys=keys)
            if isinstance(obj, dict)
            and (obj.get("task_id") or obj.get("task_name")
                 or obj.get("presenter"))
        ]

    @staticmethod
    def _salvage_task_objs(raw: str) -> list[dict]:
        """AI 返回 JSON 被截断/不完整时，尽量抽取其中已完整闭合的任务对象。

        支持 tasks / subtasks / task_list 三种数组键名；若都找不到，则退化为
        整段扫描所有含 name 的顶层 {...} 对象（仅当对象带有任务特征字段，避免
        把推理模型的思考过程误当正文）。对字段类型不做要求，交给
        _normalize_task_objs 兜底。
        """
        if not raw:
            return []
        candidate = None
        for key in ("tasks", "subtasks", "task_list"):
            m = re.search(rf'"{key}"\s*:\s*\[', raw)
            if m:
                candidate = raw[m.end():]
                break
        if candidate is not None:
            return LLMClient._extract_balanced_objs(candidate)
        # 兜底：整段扫描，但只接受"看起来像任务"的对象，降低误抓率。
        return [
            o for o in LLMClient._extract_balanced_objs(raw)
            if isinstance(o, dict) and o.get("name")
            and any(k in o for k in (
                "estimated_hours", "dependencies", "required_skills",
                "description", "task_name", "hours", "duration"))
        ]

    @staticmethod
    def _extract_balanced_objs(
        body: str, required_keys: tuple = ("name",),
    ) -> list[dict]:
        """括号配平扫描，逐条取出深度归零的 {...} 对象。"""
        objs: list[dict] = []
        depth = 0
        buf: list[str] = []
        in_str = False
        esc = False
        for ch in body:
            if in_str:
                if depth >= 1:
                    buf.append(ch)
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                if depth >= 1:
                    buf.append(ch)
                continue
            if ch == "{":
                depth += 1
                if depth == 1:
                    buf = ["{"]          # 新对象开始：丢弃此前空白/逗号
                else:
                    buf.append(ch)
                continue
            if ch == "}":
                depth -= 1
                buf.append(ch)
                if depth == 0:
                    seg = "".join(buf)
                    try:
                        obj = json.loads(seg)
                        if isinstance(obj, dict) and any(
                                key in obj for key in required_keys):
                            objs.append(obj)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    buf = []
                continue
            if depth >= 1:
                buf.append(ch)
        return objs

    @staticmethod
    def _normalize_task_objs(task_objs: list) -> list[dict] | None:
        """把原始任务对象列表规范化为 SubTask 兼容的 dict 列表。"""
        from app.file_analysis import (
            _classify_requirement_unit, _looks_like_constraint,
            _strip_dangling_brackets,
        )

        normalized: list[dict] = []
        old_to_new: dict[str, str] = {}
        raw_dependencies: list[list[str]] = []
        for item in task_objs[:40]:
            if not isinstance(item, dict):
                continue
            original_name = str(
                item.get("name", item.get("task_name", item.get("title", "")))
            ).strip()
            if not original_name:
                continue
            task_name, name_constraints = _classify_requirement_unit(original_name)
            if not task_name:
                if name_constraints or _looks_like_constraint(original_name):
                    if normalized:
                        extra = "；".join(name_constraints) or _strip_dangling_brackets(
                            original_name)
                        normalized[-1]["description"] = (
                            normalized[-1]["description"]
                            + f"；附属限制：{extra}").strip("；")
                    continue
                task_name = _strip_dangling_brackets(original_name)

            # 任务名不保留任何括号说明；括号内容转移到 description。
            parenthetical = [
                _strip_dangling_brackets(value)
                for value in re.findall(r"[（(]([^（）()]*)[）)]", task_name)
                if _strip_dangling_brackets(value)
            ]
            task_name = re.sub(r"[（(][^（）()]*[）)]", "", task_name)
            task_name = _strip_dangling_brackets(task_name)[:32]
            if not task_name:
                continue

            description = str(
                item.get("description", item.get("details", ""))
            ).strip()
            notes = list(dict.fromkeys(name_constraints + parenthetical))
            if notes:
                description = (
                    description + "；附属限制：" + "；".join(notes)
                ).strip("；")

            hours_raw = item.get(
                "estimated_hours", item.get("hours", item.get("duration", 2)))
            match = re.search(r"\d+(?:\.\d+)?", str(hours_raw))
            hours = float(match.group()) if match else 2.0
            if hours <= 0:
                hours = 2.0

            skills_raw = item.get(
                "required_skills", item.get("skills", []))
            if isinstance(skills_raw, str):
                skills = [
                    value.strip() for value in re.split(r"[,，、/]", skills_raw)
                    if value.strip()]
            else:
                skills = [str(value) for value in (skills_raw or [])]

            deps_raw = item.get(
                "dependencies", item.get("depends_on", []))
            if isinstance(deps_raw, str):
                dependencies = [
                    value.strip() for value in re.split(r"[,，、/]", deps_raw)
                    if value.strip()]
            else:
                dependencies = [
                    str(value) for value in (deps_raw or [])]

            stage_raw = str(item.get(
                "execution_stage", item.get("stage", "实践中")))
            stage_mapping = {
                "前期": "准备", "准备": "准备", "实践前": "准备",
                "中期": "执行", "执行": "执行", "实践中": "执行",
                "后期": "收尾", "收尾": "收尾", "实践后": "收尾",
                "自定义": "自定义",
            }
            stage = stage_mapping.get(stage_raw, "执行")
            people_raw = item.get(
                "suggested_people", item.get("people", 1))
            people_match = re.search(r"\d+", str(people_raw))
            people = int(people_match.group()) if people_match else 1

            new_id = f"T{len(normalized) + 1}"
            old_id = str(item.get("id", new_id))
            old_to_new[old_id] = new_id
            normalized.append({
                "id": new_id,
                "name": task_name,
                "description": description,
                "category": str(item.get("category", "其他")),
                "estimated_hours": hours,
                "required_skills": skills,
                "dependencies": [],
                "execution_stage": stage,
                "custom_stage": str(item.get("custom_stage", "")) or None if stage == "自定义" else None,
                "start_date": None,
                "end_date": None,
                "suggested_people": max(1, min(10, people)),
                "order": len(normalized) + 1,
                "status": "pending",
            })
            raw_dependencies.append(dependencies)

        if not normalized:
            return None
        for task, dependencies in zip(normalized, raw_dependencies):
            task["dependencies"] = list(dict.fromkeys(
                old_to_new[value] for value in dependencies
                if value in old_to_new and old_to_new[value] != task["id"]
            ))
        return normalized

    @staticmethod
    def _extract_json(raw: str) -> str:
        """从可能包含 markdown 代码块的响应中提取 JSON。"""
        # 去掉 ```json ... ``` 包裹
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 尝试找到第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return raw[start:end + 1]
        return raw.strip()

    def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str | AgentError:
        """自由文本调用（用于 B1 答辩模拟等无需严格结构化的场景）"""
        if not self._enabled and self._client is None:
            return AgentError(agent="LLMClient", error_type="auth_error", message="LLM_API_KEY 未配置，跳过调用", recoverable=False)
        record_logical_llm_call()
        if self._mode == "minicpm":
            # 合规模式：MiniCPM-o Realtime（本地 A3 / 云端），
            # 不创建外部模型客户端，与 chat_messages 同一路由。
            try:
                return self._realtime_text(
                    system_prompt,
                    [{"role": "user", "content": user_prompt}],
                    temperature,
                    max_tokens or LLM_MAX_TOKENS,
                )
            except Exception as exc:
                err_type = _classify_error(exc)
                logger.error("MiniCPM-o text call failed (%s): %s",
                             err_type, exc)
                return AgentError(
                    agent="LLMClient",
                    error_type=err_type,
                    message=str(exc),
                    recoverable=(err_type != "auth_error"),
                )
        try:
            with physical_llm_call():
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    timeout=LLM_TIMEOUT,
                )
            return resp.choices[0].message.content or ""
        except Exception as e:
            err_type = _classify_error(e)
            logger.error("LLM text call failed (%s): %s", err_type, e)
            return AgentError(
                agent="LLMClient",
                error_type=err_type,
                message=str(e),
                recoverable=(err_type not in ("auth_error",)),
            )

    def chat_messages(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.7,
        timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> str | AgentError:
        """多轮对话调用（用于 AI 调整建议等需要记忆的场景）。

        messages 为 [{role:'user'|'assistant', content:'...'}] 列表，
        方法内部自动在头部插入 system prompt。
        """
        if not self._enabled:
            label = "MiniCPM-o" if self._mode == "minicpm" else "LLM_API_KEY"
            return AgentError(agent="LLMClient", error_type="auth_error",
                              message=f"{label} 未配置，跳过调用",
                              recoverable=False)
        record_logical_llm_call()
        if self._mode == "minicpm":
            try:
                return self._realtime_text(
                    system_prompt, messages, temperature,
                    max_tokens, timeout)
            except Exception as exc:
                err_type = _classify_error(exc)
                logger.error("MiniCPM-o messages call failed (%s): %s",
                             err_type, exc)
                return AgentError(
                    agent="LLMClient",
                    error_type=err_type,
                    message=str(exc),
                    recoverable=(err_type != "auth_error"),
                )
        try:
            full_messages = [{"role": "system", "content": system_prompt}] + messages
            kwargs = {
                "model": self.model,
                "messages": full_messages,
                "temperature": temperature,
                "timeout": timeout or LLM_TIMEOUT,
                **self._vendor_options(),
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            with physical_llm_call():
                resp = self._client.chat.completions.create(
                    **kwargs,
                )
            return resp.choices[0].message.content or ""
        except Exception as e:
            err_type = _classify_error(e)
            logger.error("LLM messages call failed (%s): %s", err_type, e)
            return AgentError(
                agent="LLMClient",
                error_type=err_type,
                message=str(e),
                recoverable=(err_type not in ("auth_error",)),
            )

    def stream_messages(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.7,
        timeout: float | None = None,
        max_tokens: int | None = None,
    ):
        """按 OpenAI 标准流式返回文本片段，降低首字等待。"""
        if not self._enabled:
            label = "MiniCPM-o" if self._mode == "minicpm" else "LLM_API_KEY"
            yield AgentError(
                agent="LLMClient",
                error_type="auth_error",
                message=f"{label} 未配置，跳过调用",
                recoverable=False,
            )
            return
        if self._mode == "minicpm":
            # 合规模式暂不支持流式：一次性返回 MiniCPM-o 文本。
            try:
                text = self._realtime_text(
                    system_prompt, messages, temperature,
                    max_tokens, timeout)
                yield text
            except Exception as exc:
                yield AgentError(
                    agent="LLMClient",
                    error_type=_classify_error(exc),
                    message=str(exc),
                    recoverable=True,
                )
            return
        try:
            kwargs = {
                "model": self.model,
                "messages": ([{"role": "system", "content": system_prompt}]
                             + messages),
                "temperature": temperature,
                "timeout": timeout or LLM_TIMEOUT,
                "stream": True,
                **self._vendor_options(),
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            response = self._client.chat.completions.create(**kwargs)
            for chunk in response:
                if not chunk.choices:
                    continue
                content = getattr(chunk.choices[0].delta, "content", None)
                if content:
                    yield content
        except Exception as exc:
            error_type = _classify_error(exc)
            logger.error("LLM stream call failed (%s): %s", error_type, exc)
            yield AgentError(
                agent="LLMClient",
                error_type=error_type,
                message=str(exc),
                recoverable=(error_type not in ("auth_error",)),
            )
