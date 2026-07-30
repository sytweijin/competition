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
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_RETRIES, LLM_TIMEOUT,
    LLM_PREFER_PLAIN, LLM_MAX_TOKENS,
)
from app.models.schemas import AgentError

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
        return "timeout"
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
        self._enabled = bool(LLM_API_KEY)
        self._prefer_plain = LLM_PREFER_PLAIN
        # 禁用 SDK 隐式重试；否则应用层 12s 超时会被默认 2 次重试放大到 30s+。
        self._client = OpenAI(
            api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
            max_retries=0,
        )

    def chat_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.3,
        max_retries: int = LLM_MAX_RETRIES,
    ) -> T | AgentError:
        """调用 LLM 并返回结构化输出。

        策略：先尝试 structured output（response_format），失败后回退到
        普通 create + 手动 JSON 提取 + model_validate_json。
        """
        if not self._enabled:
            return AgentError(agent="LLMClient", error_type="auth_error",
                              message="LLM_API_KEY 未配置，跳过 LLM 调用",
                              recoverable=False)
        if self._prefer_plain:
            try:
                return self._try_plain_validate(
                    system_prompt, user_prompt, response_model, temperature)
            except Exception as exc:
                return AgentError(
                    agent="LLMClient", error_type=_classify_error(exc),
                    message=f"LLM JSON 调用失败：{exc}", recoverable=True)
        retries = max(1, max_retries)
        last_error_type = "unknown"
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return self._try_structured(system_prompt, user_prompt,
                                            response_model, temperature)
            except Exception as e:
                err_type = _classify_error(e)
                last_error_type, last_error = err_type, e
                logger.warning("LLM structured attempt %d/%d (%s): %s",
                               attempt + 1, retries, err_type, e)
                if err_type == "auth_error":
                    return AgentError(agent="LLMClient", error_type=err_type,
                                     message=f"API 鉴权失败：{e}",
                                     recoverable=False)
                if err_type == "parse_error":
                    break  # 结构化重试无意义，直接回退 plain create
                # rate_limit / timeout / unknown：可重试，最后一次落到 fallback
        # timeout/rate_limit 时也尝试一次 plain 回退（救回偶发冷启动/网络抖动）。
        # 原逻辑直接返回错误走兜底，但首次请求常因连接建立慢而超时，
        # 此时连接可能已建立，plain 回退成功率较高，值得多等一个超时周期。
        if last_error_type in ("timeout", "rate_limit", "unknown"):
            try:
                logger.info("LLM %s, trying plain fallback before giving up",
                            last_error_type)
                return self._try_plain_validate(
                    system_prompt, user_prompt,
                    response_model, temperature)
            except Exception as e2:
                return AgentError(
                    agent="LLMClient",
                    error_type=last_error_type,
                    message=(f"LLM 调用失败（已尝试 {retries} "
                             f"次结构化 + 1 次 plain 回退）：{e2}"),
                    recoverable=True,
                )
        try:
            logger.info("Falling back to plain create + validate")
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
        resp = self._client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_model,
            temperature=temperature,
            timeout=LLM_TIMEOUT,
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
            resp = self._call_with_timeout_retry(messages, budget, temperature)
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
                    budget = min(32000, budget * 2)
                    logger.warning("Response truncated (length); retrying with budget=%d", budget)
                    continue
        # 两次都失败（含一次截断重试）；抛出以走上层兜底。
        raise ValueError("LLM 返回的 JSON 经校验与本地修复均不可用（可能仍未完整）")

    def _call_with_timeout_retry(self, messages, budget, temperature,
                                 max_retries: int = 2):
        """带超时重试的 create 调用，容忍推理模型的慢响应。"""
        last_exc: Exception | None = None
        for i in range(max_retries + 1):
            try:
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=budget,
                    timeout=LLM_TIMEOUT,
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
        1) 字段缺失/类型偏差：走 _normalize_task_objs 规范化；
        2) JSON 被截断/不完整（推理模型 max_tokens 预算被思考吃掉）：
           走 _salvage_task_objs 抢救已完整落地的任务对象，而非整次失败。
        """
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
    def _extract_balanced_objs(body: str) -> list[dict]:
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
                        if isinstance(obj, dict) and obj.get("name"):
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
    ) -> str | AgentError:
        """自由文本调用（用于 B1 答辩模拟等无需严格结构化的场景）"""
        if not self._enabled:
            return AgentError(agent="LLMClient", error_type="auth_error", message="LLM_API_KEY 未配置，跳过调用", recoverable=False)
        try:
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
