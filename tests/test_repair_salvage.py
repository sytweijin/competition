# -*- coding: utf-8 -*-
"""验证 LLMClient 的本地修复/截断抢救逻辑（不依赖网络）。"""
import json

from app.llm.client import LLMClient
from app.models.schemas import PlanOutput


def test_repair_full_plan():
    payload = {
        "tasks": [{"name": "X", "estimated_hours": 2}],
        "summary": "s", "reasoning": "r",
    }
    r = LLMClient._repair_response(json.dumps(payload), PlanOutput)
    assert r is not None
    assert len(r.tasks) == 1
    assert r.tasks[0].name == "X"


def test_salvage_truncated_plan_recovers_complete_tasks():
    # 第三个任务对象被截断（skills 数组未闭合），不应被计入
    raw = '''{
      "tasks": [
        {"name": "任务A", "estimated_hours": 3, "required_skills": ["设计"]},
        {"name": "任务B", "estimated_hours": 5, "dependencies": ["T1"]},
        {"name": "任务C", "estimated_hours": 4, "required_skills": ["调研"
    '''
    r = LLMClient._repair_response(raw, PlanOutput)
    assert r is not None, "截断 JSON 应当抢救出已完整任务而非整次失败"
    assert len(r.tasks) == 2
    assert {t.name for t in r.tasks} == {"任务A", "任务B"}
    # 依赖应被重映射为内部 id
    assert r.tasks[1].dependencies == ["T1"]


def test_salvage_empty_returns_none():
    assert LLMClient._salvage_task_objs("") == []
    assert LLMClient._salvage_task_objs('{"foo": "bar"}') == []


def test_repair_non_plan_model_returns_none():
    # _repair_response 仅支持 PlanOutput；其余模型直接放弃（交给上层兜底）
    from app.models.schemas import QAOutput
    assert LLMClient._repair_response('{"x": 1}', QAOutput) is None


def test_salvage_alternative_array_keys():
    # subtasks / task_list 等非标准数组键也应被识别
    raw_sub = ('{"summary":"x","subtasks":['
               '{"id":"T1","name":"需求调研","estimated_hours":2,"required_skills":["调研"]},'
               '{"id":"T2","name":"开发","esti')  # 第二个被截断
    assert len(LLMClient._salvage_task_objs(raw_sub)) == 1
    raw_list = '{"task_list":[{"name":"A","hours":3},{"name":"B","hours":4}]}'
    assert len(LLMClient._salvage_task_objs(raw_list)) == 2


def test_salvage_whole_raw_fallback_rejects_reasoning_noise():
    # 整段兜底只接受带任务特征字段的对象，避免把思考过程误当正文
    raw_real = ('说明文字 {"name":"调研","estimated_hours":2,"description":"d"} '
                '还有 {"name":"开发","required_skills":["dev"]}')
    assert len(LLMClient._salvage_task_objs(raw_real)) == 2
    raw_noise = '思考：{"name":"假任务"} 结束'
    assert len(LLMClient._salvage_task_objs(raw_noise)) == 0
