"""
B3：完整角色匹配 —— 基于技能标签的确定性评分引擎。

不依赖 LLM。用「技能覆盖度 + 负载均衡」为每个 (任务, 成员) 打分，
为 Matcher 的 LLM 输出补充可解释的 score 与 workload 负载摘要。
也能在 LLM 不可用时独立生成一份匹配。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.models.schemas import (
    PlanOutput, TeamMember, QAOutput, QAAssignment,
)


def _normalize_tag(tag: str) -> str:
    """标签归一化：去空格、转小写，便于精确匹配。"""
    return tag.strip().lower().replace(" ", "")

# 负向偏好的前缀标记：命中即认为该成员「回避」其后跟随的技能。
# 用元组而非单字符串，避免把「想做」误判为负向（正向的「想做PPT」不含这些标记）。
_NEGATIVE_MARKERS = ("不想", "不太想", "不擅长", "不喜欢", "避免", "拒绝", "别让", "排斥", "怕做")

# 正向描述性前缀：在匹配前剥离，让「擅长PPT」→「ppt」
_POSITIVE_PREFIXES = ("擅长", "想做", "会做", "能做", "做过", "精通", "熟练", "熟悉")

# 描述性后缀：在匹配前剥离，让「文学素养不错」→「文学素养」
_DESCRIPTIVE_SUFFIXES = ("不错", "很好", "较好", "还可以", "挺强", "很强", "还行")

# 技能同义词映射表：将各种表达归一化为标准技能词，解决纯字符相似度
# 对中文描述性标签和跨语言近义词失效的问题（P0-3）。
_SKILL_SYNONYMS = {
    # 写作 / 文案类
    "写作": "文案撰写", "撰写": "文案撰写", "文笔": "文案撰写",
    "文学素养": "文案撰写", "文学": "文案撰写", "文字": "文案撰写",
    "总结": "文案撰写", "成文": "文案撰写", "报告撰写": "文案撰写",
    "文档撰写": "文案撰写", "编撰": "文案撰写", "编辑": "文案撰写",
    "内容撰写": "文案撰写", "文稿": "文案撰写",
    "写报告": "文案撰写", "撰写报告": "文案撰写", "报告": "文案撰写",
    "总结报告": "文案撰写", "调研报告": "文案撰写", "复盘报告": "文案撰写",
    "汇报": "文案撰写", "文档": "文案撰写", "论文": "文案撰写",
    "创意文案": "文案撰写", "方案撰写": "文案撰写", "推文": "文案撰写",
    "公众号": "文案撰写", "文案编辑": "文案撰写", "文字功底": "文案撰写",
    "文字表达": "文案撰写", "书面表达": "文案撰写", "润色": "文案撰写",
    # 前端类
    "前端": "前端开发", "frontend": "前端开发", "web前端": "前端开发",
    "html": "前端开发", "css": "前端开发", "javascript": "前端开发",
    "js": "前端开发", "vue": "前端开发", "react": "前端开发",
    "页面": "前端开发", "网页": "前端开发",
    # 后端类
    "后端": "后端开发", "backend": "后端开发", "服务端": "后端开发",
    "服务器": "后端开发", "api": "后端开发",
    # PPT / 演示类
    "ppt": "PPT制作", "幻灯片": "PPT制作", "幻灯片制作": "PPT制作",
    "演示文稿": "PPT制作",
    "powerpoint": "PPT制作", "演示": "PPT制作", "汇报展示": "PPT制作",
    # 排版类
    "排版": "排版设计", "秀米": "排版设计", "图文排版": "排版设计",
    # 数据分析类
    "数据分析": "数据分析", "dataanalysis": "数据分析",
    "数据处理": "数据分析", "统计": "数据分析", "数据统计": "数据分析",
    "数据挖掘": "数据分析", "数据": "数据分析", "图表": "数据分析",
    "excel": "数据分析", "spss": "数据分析", "数据可视化": "数据分析",
    # Python 类
    "python": "Python编程", "py": "Python编程",
    # 设计类
    "设计": "平面设计", "平面设计": "平面设计", "ui": "平面设计",
    "美工": "平面设计", "视觉设计": "平面设计", "海报设计": "平面设计",
    "海报": "平面设计", "插画": "平面设计", "画图": "平面设计",
    # 视频 / 剪辑类
    "视频": "视频剪辑", "剪辑": "视频剪辑", "video": "视频剪辑",
    "vlog": "视频剪辑", "拍摄": "视频剪辑", "摄像": "视频剪辑",
    "摄影": "视频剪辑", "拍照": "视频剪辑", "录像": "视频剪辑",
    # 策划类
    "策划": "内容策划", "内容策划": "内容策划", "活动策划": "内容策划",
    "创意": "内容策划", "方案策划": "内容策划", "策划案": "内容策划",
    # 调研类
    "调研": "调研分析", "调查": "调研分析", "访谈": "调研分析",
    "问卷": "调研分析", "采访": "调研分析", "实地调研": "调研分析",
    "资料收集": "调研分析", "资料整理": "调研分析", "文献检索": "调研分析",
    "查资料": "调研分析", "搜集资料": "调研分析", "信息检索": "调研分析",
    # 沟通 / 协调类
    "沟通": "沟通协调", "协调": "沟通协调", "组织": "沟通协调",
    "团队协作": "沟通协调", "领导力": "沟通协调",
    "演讲": "沟通协调", "口头表达": "沟通协调", "表达": "沟通协调",
    "答辩": "沟通协调", "展示": "沟通协调", "演讲表达": "沟通协调",
    "英语": "沟通协调", "翻译": "沟通协调",
    # 项目管理类
    "项目管理": "项目管理", "projectmanagement": "项目管理",
    "进度管理": "项目管理",
}


def _canonicalize(tag: str) -> str:
    """将技能标签归一化为标准形式。

    依次：去空格转小写 → 剥离描述性前缀/后缀 → 查同义词表。
    例：「擅长PPT」→「ppt」→「PPT制作」
         「文学素养不错」→「文学素养」→「文案撰写」
    """
    norm = _normalize_tag(tag)
    # 剥离正向描述性前缀
    for prefix in _POSITIVE_PREFIXES:
        if norm.startswith(prefix) and len(norm) > len(prefix):
            norm = norm[len(prefix):]
            break
    # 剥离描述性后缀
    for suffix in _DESCRIPTIVE_SUFFIXES:
        if norm.endswith(suffix) and len(norm) > len(suffix):
            norm = norm[:-len(suffix)]
            break
    return _SKILL_SYNONYMS.get(norm, norm)


def _split_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """把技能标签拆成 (正向技能, 负向回避技能)。

    - '不太想做PPT' -> 负向 'PPT'
    - '不想做前端'   -> 负向 '前端'
    - 'PPT' / '想做PPT' -> 正向 'PPT'
    负向标签里的技能词必须被剥离出来单独标记，否则 _similar 的子串包含
    会把「不太想做PPT」当成「擅长PPT」打高分（0.85）。
    """
    pos: list[str] = []
    neg: list[str] = []
    for tag in tags or []:
        norm = tag.strip()
        hit = None
        for marker in _NEGATIVE_MARKERS:
            if norm.find(marker) != -1:
                hit = marker
                break
        if hit is None:
            pos.append(norm)
            continue
        # 取负向标记之后的文本，剥掉常见连接词，剩下的即被回避的技能
        rest = norm[norm.find(hit) + len(hit):].strip("做要的会了、，。 ")
        if rest:
            neg.append(rest)
    return pos, neg


def format_skills_for_prompt(tags: list[str]) -> str:
    """把技能标签格式化为「擅长: X; 回避: Y」便于 LLM 区分正负向偏好。"""
    pos, neg = _split_tags(tags)
    parts = []
    if pos:
        parts.append("擅长: " + ", ".join(pos))
    if neg:
        parts.append("回避: " + ", ".join(neg))
    return "; ".join(parts) if parts else "未标注"


def _similar(a: str, b: str) -> float:
    """两个技能标签的相似度（大小写/空白不敏感，支持包含关系和同义词）。"""
    na, nb = _normalize_tag(a), _normalize_tag(b)
    if not na or not nb:
        return 0.0
    # 同义词归一化：让「文学素养不错」和「撰写报告」能匹配
    ca, cb = _canonicalize(a), _canonicalize(b)
    if ca == cb:
        return 1.0
    # 包含关系（如「前端」vs「前端开发」）给高分
    if ca in cb or cb in ca:
        return 0.85
    # 退化为字符相似度
    return SequenceMatcher(None, ca, cb).ratio()


# 角色投入系数：负责人承担任务全部工时，主要协助参与折算 15%，辅助协助各折算 5%。
# 降低协作者折算以减少表面负载放大（P0-4：原 0.3+0.15×2 导致 8h 任务算成 12.8h）。
PRESENTER_RATIO = 1.0
QA_PRIMARY_RATIO = 0.3
QA_SUPPORT_RATIO = 0.15
DEFAULT_BALANCE_THRESHOLD_HOURS = 1.0

# 多因子初始打分权重：技能匹配 + 总负载 + 阶段负载 + 剩余产能
ASSIGNMENT_WEIGHTS = {
    "skill": 0.55,
    "total_load": 0.20,
    "stage_load": 0.15,
    "capacity": 0.10,
}


def skill_score(member: TeamMember, required_skills: list[str]) -> float:
    """成员对所需技能的匹配分（0-1）。

    取每个所需技能的最佳匹配相似度后求均值；无所需技能则返回中性 0.5。
    负向标签（如「不太想做PPT」「避免前端」）命中的技能记 0 分——
    明确回避的技能不参与正向匹配，防止「不想做」被当成「擅长」。

    双输入模式：tags 模式用字符相似度 + 同义词表；
    bio 模式返回中性 0.5（实际匹配由 LLM 在 Planner 阶段完成，
    这里只做负载均衡时的兜底打分，避免阻塞确定性算法）。
    """
    if member.profile_mode == "bio" and member.bio:
        # bio 模式：Planner 已基于简介做了语义匹配并给出 assignee_id，
        # 确定性均衡阶段无法做 LLM 语义判断，返回中性分让均衡算法不误伤。
        return 0.5
    if not required_skills:
        return 0.5
    pos_tags, neg_tags = _split_tags(member.skill_tags)
    if not pos_tags and not neg_tags:
        return 0.0
    total = 0.0
    for req in required_skills:
        # 负向命中：该技能被成员明确回避，直接记 0
        if any(_similar(req, n) >= 0.6 for n in neg_tags):
            continue
        best = max((_similar(req, tag) for tag in pos_tags),
                   default=0.0)
        total += best
    return round(total / len(required_skills), 3)


def _avoids_required(member: TeamMember | None,
                     required_skills: list[str]) -> bool:
    if member is None or not required_skills:
        return False
    _, avoided = _split_tags(member.skill_tags)
    return any(_similar(req, neg) >= 0.6
               for req in required_skills for neg in avoided)



def _is_avoiding(member: TeamMember, required_skills: list[str]) -> bool:
    """成员是否对所需技能中的某项明确回避（负向标签命中）。

    只判断'明确不想做'这类负向偏好，不判断'单纯不擅长'。
    用于负载均衡搬运时排除明确回避者，同时允许不擅长但未回避者承接任务以维持均衡。
    """
    if not required_skills:
        return False
    _, neg_tags = _split_tags(member.skill_tags)
    if not neg_tags:
        return False
    return any(any(_similar(req, n) >= 0.6 for n in neg_tags) for req in required_skills)


def _work_from(assignments, task_hours, members, completed_ids=None):
    """按最终分配精确计算每人负载。

    completed_ids 里的任务直接跳过（不计入负载）——用于已完成任务
    保留原分工但不占产能的场景。
    """
    completed_ids = completed_ids or set()
    w = {m.name: 0.0 for m in members}
    for a in assignments:
        if a.task_id in completed_ids:
            continue
        h = task_hours.get(a.task_id, 0.0)
        if h <= 0 or a.presenter in ("", "(已完成)"):
            continue
        w[a.presenter] = w.get(a.presenter, 0.0) + h
        if a.qa_primary and a.qa_primary != a.presenter:
            w[a.qa_primary] = w.get(a.qa_primary, 0.0) + h * QA_PRIMARY_RATIO
        for mem in (a.qa_support or []):
            if mem not in (a.presenter, a.qa_primary):
                w[mem] = w.get(mem, 0.0) + h * QA_SUPPORT_RATIO
    return w


def _balance_workload(assignments, task_hours, members,
                      threshold=DEFAULT_BALANCE_THRESHOLD_HOURS,
                      max_passes=500, task_skills=None, member_map=None):
    """统一负载均衡：负责人/主要协助/辅助协助均可搬运，目标 max-min<=threshold。

    每步枚举所有可行搬运，用「真实重算负载」评估搬运后的全局 gap，选最小者执行；
    gap 不再下降即停。每次搬运前快照、评估后还原，杜绝近似误差。
    """
    names = [m.name for m in members]
    if member_map is None:
        member_map = {m.name: m for m in members}
    task_skills = task_skills or {}

    # 负责人回避门禁：对每个任务，预先算出非回避的合格候选人
    qualified = {}
    if task_skills and member_map:
        for tid, skills in task_skills.items():
            ok = [m.name for m in members if not _is_avoiding(m, skills)]
            qualified[tid] = set(ok) if ok else None
    else:
        qualified = {}

    def gap_of(w):
        vals = list(w.values())
        return (max(vals) - min(vals)) if vals else 0.0

    def snapshot(a):
        return (a.presenter, a.qa_primary, list(a.qa_support or []))

    def restore(a, snap):
        a.presenter, a.qa_primary, sup = snap[0], snap[1], list(snap[2])
        a.qa_support = sup

    def avoids(member_name, task_id):
        member = member_map.get(member_name)
        required = task_skills.get(task_id, [])
        if member is None or not required:
            return False
        return _avoids_required(member, required)

    rebalance_guard = 4  # 全局重排最多触发这么多次（贪心+重排交替迭代），防止死循环
    for _ in range(max_passes):
        gap = gap_of(_work_from(assignments, task_hours, members))
        if gap <= threshold + 1e-9:
            break
        best_gap = gap
        best = None  # (new_gap, assignment, kind, target)
        for a in assignments:
            if a.presenter in ("", "(已完成)"):
                continue
            snap = snapshot(a)
            cur_p, cur_q, cur_s = snap
            # 负责人换人
            for t in names:
                if t == cur_p:
                    continue
                required = task_skills.get(a.task_id, [])
                target_member = member_map.get(t)
                current_member = member_map.get(cur_p)
                # 负责人回避门禁：候选人对该任务明确回避（负向标签命中）时跳过
                qset = qualified.get(a.task_id)
                if qset is not None and t not in qset:
                    continue
                a.presenter = t
                ng = gap_of(_work_from(assignments, task_hours, members))
                if ng < best_gap - 1e-12:
                    best_gap, best = ng, (a, "presenter", t)
                restore(a, snap)
            # 主要协助换人
            if cur_q:
                for t in names:
                    if t in (cur_p, cur_q):
                        continue
                    if avoids(t, a.task_id):
                        continue
                    a.qa_primary = t
                    if t in (a.qa_support or []):
                        a.qa_support = [x for x in a.qa_support if x != t]
                    ng = gap_of(_work_from(assignments, task_hours, members))
                    if ng < best_gap - 1e-12:
                        best_gap, best = ng, (a, "primary", t)
                    restore(a, snap)
            # 辅助协助换人
            for owner in cur_s:
                for t in names:
                    if t in (cur_p, cur_q) or t in cur_s:
                        continue
                    if avoids(t, a.task_id):
                        continue
                    a.qa_support = [x for x in cur_s if x != owner] + [t]
                    ng = gap_of(_work_from(assignments, task_hours, members))
                    if ng < best_gap - 1e-12:
                        best_gap, best = ng, (a, "support", (owner, t))
                    restore(a, snap)
        if best is None:
            # 贪心卡在局部最优（单角色搬运粒度 > 需要的转移量，无法精细转移负载）。
            # 做一次全局重排（联合枚举负责人+主要协助）跳出局部最优。重排后新的
            # 协助位结构可能解锁更优解，所以改善就回到循环顶部让贪心+重排再迭代
            # 一轮；连续两次重排都不再改善才确认收敛。rebalance_guard 防止死循环。
            cur = _work_from(assignments, task_hours, members)
            cur_gap = (max(cur.values()) - min(cur.values())) if cur else 0.0
            if cur_gap <= threshold + 1e-9:
                break
            if rebalance_guard <= 0:
                break
            rebalance_guard -= 1
            new_gap = _rebalance_presenters(assignments, task_hours, members,
                                            task_skills, member_map, cur_gap)
            if new_gap < cur_gap - 1e-9:
                continue  # 重排改善了，新状态下贪心可能继续降 gap
            break
        a, kind, payload = best
        if kind == "presenter":
            # 搬运负责人后清理角色自指：新负责人若仍留在 qa_primary/qa_support，
            # 会出现「小红负责、小红协助」——workload 计算会漏算或重复，
            # 前端责任矩阵也显示异常。把旧负责人补到 qa_primary，保持三人不丢。
            old_p = a.presenter
            a.presenter = payload
            if a.qa_primary == payload:
                a.qa_primary = old_p if old_p and old_p != payload else ""
            if payload in (a.qa_support or []):
                a.qa_support = [x for x in a.qa_support if x != payload]
        elif kind == "primary":
            if payload in (a.qa_support or []):
                a.qa_support = [x for x in a.qa_support if x != payload]
            a.qa_primary = payload
        else:
            owner, t = payload
            a.qa_support = [x for x in (a.qa_support or []) if x != owner] + [t]
    return _work_from(assignments, task_hours, members)


def _rebalance_presenters(assignments, task_hours, members, task_skills, member_map, cur_gap):
    """全局重分配：联合枚举负责人 + 主要协助，找让总负载 gap 最小的组合。

    _balance_workload 的单角色贪心搬运在「最小搬运粒度 > 需要的转移量」时会卡在
    局部最优（如负责人整数工时任务下，单次搬运转移量过大，无法精细调节）。
    把负责人(权重1.0)和主要协助(权重0.3)都当成可重排变量做联合枚举，能跳出
    单步贪心够不着的解（实测把卡死的 1.4h gap 降到 0.85h 以内）。

    固定辅助协助位(qa_support 不变)，枚举每个任务的负责人与主要协助组合
    （尊重回避门槛），选总负载 gap 最小且严格优于 cur_gap 的应用；无改善则
    保持原状。组合数过大(>1e6)时退回只枚举负责人，仍过大则贪心近似。
    """
    import itertools
    names = [m.name for m in members]
    active = [a for a in assignments if a.presenter not in ("", "(已完成)")]
    if not active:
        return cur_gap
    cand_p = {}
    for a in active:
        skills = (task_skills or {}).get(a.task_id, [])
        ok = [n for n in names if member_map.get(n) and not _is_avoiding(member_map[n], skills)]
        cand_p[a.task_id] = ok if ok else list(names)
    p_lists = [cand_p[a.task_id] for a in active]
    q_lists = [cand_p[a.task_id] for a in active]

    size = 1
    for c in p_lists:
        size *= len(c)
    joint = size
    for c in q_lists:
        joint *= len(c)
        if joint > 1_000_000:
            break

    snaps = [(a.presenter, a.qa_primary) for a in active]
    best_choice = None
    best_gap = cur_gap
    if joint <= 1_000_000:
        # 联合枚举 presenter + qa_primary
        for p_choice in itertools.product(*p_lists):
            for q_choice in itertools.product(*q_lists):
                # 跳过退化组合（某任务负责人==主要协助）：qa_primary 语义上是协助者，
                # 不应等于负责人；且 p==q 时 _apply_role_remap 会把 qa_primary 回填成
                # 旧负责人，导致枚举预算负载与真实应用后不一致。
                if any(pp == qq for pp, qq in zip(p_choice, q_choice)):
                    continue
                load = {n: 0.0 for n in names}
                for a, p, q in zip(active, p_choice, q_choice):
                    h = task_hours.get(a.task_id, 0.0)
                    load[p] += h
                    if q and q != p:
                        load[q] += h * QA_PRIMARY_RATIO
                    for sx in (a.qa_support or []):
                        if sx != p and sx != q:
                            load[sx] += h * QA_SUPPORT_RATIO
                gap = max(load.values()) - min(load.values())
                if gap < best_gap - 1e-12:
                    best_gap = gap
                    best_choice = (p_choice, q_choice)
    elif size <= 1_000_000:
        # 只枚举负责人，协助位保持
        for p_choice in itertools.product(*p_lists):
            load = {n: 0.0 for n in names}
            for a, p in zip(active, p_choice):
                h = task_hours.get(a.task_id, 0.0)
                load[p] += h
                if a.qa_primary and a.qa_primary != p:
                    load[a.qa_primary] += h * QA_PRIMARY_RATIO
                for s in (a.qa_support or []):
                    if s != p and s != a.qa_primary:
                        load[s] += h * QA_SUPPORT_RATIO
            gap = max(load.values()) - min(load.values())
            if gap < best_gap - 1e-12:
                best_gap = gap
                best_choice = (p_choice, None)

    if best_choice is not None:
        p_choice, q_choice = best_choice
        _apply_role_remap(active, p_choice, q_choice, members)
    new_work = _work_from(assignments, task_hours, members)
    return (max(new_work.values()) - min(new_work.values())) if new_work else cur_gap


def _apply_role_remap(active, p_choice, q_choice, members):
    """应用一组负责人/主要协助选择，并清理角色自指与重复占位。"""
    names = {m.name for m in members}
    for a, new_p, new_q in zip(active, p_choice, (q_choice or [None] * len(active))):
        old_p = a.presenter
        a.presenter = new_p
        if new_p in (a.qa_support or []):
            a.qa_support = [x for x in a.qa_support if x != new_p]
        if new_q is not None:
            a.qa_primary = new_q
            if new_q in (a.qa_support or []):
                a.qa_support = [x for x in a.qa_support if x != new_q]
            if new_q == new_p:
                a.qa_primary = old_p if (old_p and old_p != new_p and old_p in names) else ""



def _split_suggestion(work, assignments, task_hours, members, threshold=DEFAULT_BALANCE_THRESHOLD_HOURS):
    """均衡后 gap 仍超阈值时，给"建议拆分超载成员最大任务"的提示。

    当任务结构本身无法在成员间均摊（如 5 个 5h 任务给 3 人，必有人扛 2 个），
    自动拆分会改动用户计划，故不改数据，只在 note 里给出拆分建议。
    """
    if not work:
        return ""
    gap = max(work.values()) - min(work.values())
    if gap <= threshold + 1e-9:
        return ""
    over_name = max(work, key=lambda n: work[n])
    cands = []
    for a in assignments:
        if a.presenter == over_name:
            h = task_hours.get(a.task_id, 0.0)
            cands.append((h, a))
    if not cands:
        return ""
    cands.sort(key=lambda x: x[0], reverse=True)
    h, a = cands[0]
    if h <= 0:
        return ""
    return (f" 建议拆分 {over_name} 的 {a.task_name}（{h:.1f}h），"
            f"当前成员最大工时差 {gap:.1f}h 超过 {threshold:g}h，"
            f"任务结构无法在 {len(members)} 人间均摊")


def _resync_scores(assignments, plan, members):
    """均衡搬运后重算 score/reasoning，使其与最终 presenter 一致。

    _balance_workload 会就地修改 presenter/qa_primary/qa_support，
    但不会更新 score 和 reasoning，导致前端和导出文档展示错误的匹配度。
    """
    member_map = {m.name: m for m in members}
    task_map = {t.id: t for t in plan.tasks}
    for a in assignments:
        if a.presenter in ("", "(已完成)"):
            continue
        t = task_map.get(a.task_id)
        m = member_map.get(a.presenter)
        if t is None or m is None:
            continue
        sc = skill_score(m, t.required_skills)
        a.score = round(sc, 3)
        a.reasoning = (
            f"{a.presenter}：{_fmt(t.required_skills)} 技能"
            f"匹配度 {sc:.2f}，均衡后综合最优"
        )


def assign_with_balance(plan: PlanOutput,
                        members: list[TeamMember]) -> QAOutput:
    """确定性任务分配 + 负载均衡 v2.3

    顺序 B：Planner 已在拆任务时给出 assignee_id（基于成员能力）。
    本函数尊重 Planner 的初始分配，只在负载严重不均时搬运负责人。
    若任务无 assignee_id（Planner 未给或能力缺口），走原有匹配逻辑。
    """
    if not members or not plan.tasks:
        return QAOutput(assignments=[], note="B3确定性兜底+超载校正")

    active_tasks = [t for t in plan.tasks if t.status != "completed"]
    member_map = {m.name: m for m in members}
    member_names = {m.name for m in members}
    task_hours = {t.id: t.estimated_hours for t in active_tasks}

    work = {m.name: 0.0 for m in members}
    stage_work = {m.name: {} for m in members}
    assignments = []

    for t in plan.tasks:
        if t.status == "completed":
            assignments.append(QAAssignment(
                task_id=t.id, task_name=t.name, chapter="",
                presenter="(已完成)", qa_primary="", qa_support=[],
                score=0.0, reasoning="任务已完成",
            ))
            continue
        # 顺序 B：优先用 Planner 给的 assignee_id
        planner_assignee = t.assignee_id if t.assignee_id in member_names else None
        if planner_assignee and not _is_avoiding(member_map[planner_assignee], t.required_skills):
            # Planner 已分配且非回避，尊重该分配
            presenter = planner_assignee
            skill = skill_score(member_map[presenter], t.required_skills)
        else:
            # Planner 未分配或该人回避，走多因子匹配
            scored = []
            for m in members:
                skill = skill_score(m, t.required_skills)
                avoiding = _is_avoiding(m, t.required_skills)
                total_ratio = work[m.name] / max(m.available_hours, 0.5)
                stage_ratio = stage_work[m.name].get(t.execution_stage, 0.0) / max(m.available_hours, 0.5)
                capacity = max(0.0, 1.0 - (work[m.name] + t.estimated_hours) / max(m.available_hours, 0.5))
                score = (ASSIGNMENT_WEIGHTS["skill"] * skill
                         - ASSIGNMENT_WEIGHTS["total_load"] * total_ratio
                         - ASSIGNMENT_WEIGHTS["stage_load"] * stage_ratio
                         + ASSIGNMENT_WEIGHTS["capacity"] * capacity)
                if m.available_stages and t.execution_stage not in m.available_stages:
                    score -= 0.35
                scored.append((m.name, skill, avoiding, score))
            scored.sort(key=lambda x: (x[2], -x[3], work[x[0]]))
            presenter = scored[0][0]
            skill = scored[0][1]
        work[presenter] += t.estimated_hours
        stage_work[presenter][t.execution_stage] = (
            stage_work[presenter].get(t.execution_stage, 0.0) + t.estimated_hours)

        # 根据建议参与人数决定协作者数量
        max_collaborators = max(0, t.suggested_people - 1)
        primary = ''
        support = []
        if max_collaborators > 0:
            # 主要协助：剩余成员中负载最轻者为先（匹配度作同负载时的次席）
            rest = [(m.name, skill_score(m, t.required_skills)) for m in members
                    if m.name != presenter and not _is_avoiding(m, t.required_skills)]
            rest.sort(key=lambda x: (work[x[0]], -x[1]))
            primary = rest[0][0] if rest else ''
            if primary and primary != presenter:
                work[primary] += t.estimated_hours * QA_PRIMARY_RATIO

            if max_collaborators >= 2:
                # 辅助协助：再从剩余中取负载最轻的 2 人
                rest2 = [n for n, _ in rest if n != primary]
                rest2.sort(key=lambda n: work[n])
                support = rest2[:min(2, max_collaborators - 1)]
                for s in support:
                    work[s] += t.estimated_hours * QA_SUPPORT_RATIO

        reasoning = (
            f"{presenter}：{_fmt(t.required_skills)} 技能"
            f"匹配度 {skill:.2f}，"
            f"当前负载 {work[presenter]-t.estimated_hours:.1f}h，"
            f"执行阶段 {t.execution_stage}"
            + ("（Planner 初始分配）" if planner_assignee == presenter else "")
        )
        assignments.append(QAAssignment(
            task_id=t.id, task_name=t.name, chapter="",
            presenter=presenter, qa_primary=primary, qa_support=support,
            score=round(skill, 3), reasoning=reasoning,
        ))

    # 零负载兜底——0 工时成员先从负载最重的任务拿一个辅助协助角色
    zero_load = [n for n, h in work.items() if h <= 0]
    for n in zero_load:
        active = [a for a in assignments if a.presenter != "(已完成)" and n not in (a.qa_support or [])]
        if not active:
            continue
        target = max(active, key=lambda a: task_hours.get(a.task_id, 0.0))
        if target.qa_support is None:
            target.qa_support = []
        if n not in target.qa_support:
            target.qa_support.append(n)
            work[n] += task_hours.get(target.task_id, 0.0) * QA_SUPPORT_RATIO

    # 在不明显破坏技能匹配、不违反负向偏好的前提下，将默认负载差

    # 尽量控制在 1h 内。
    original_presenters = {a.task_id: a.presenter for a in assignments}
    task_skills = {t.id: t.required_skills for t in active_tasks}
    work = _balance_workload(
        assignments, task_hours, members,
        threshold=DEFAULT_BALANCE_THRESHOLD_HOURS,
        task_skills=task_skills,
        member_map=member_map,
    )

    # B1: 均衡后重算 score/reasoning，使其与最终 presenter 一致
    _resync_scores(assignments, plan, members)

    # overload detection
    overload_warnings = []
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            overload_warnings.append(
                f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h"
            )
    note = "B3确定性兜底 + 1h负载均衡 v2.3"
    if overload_warnings:
        note += " 超载警告: " + "; ".join(overload_warnings)
    # 均衡后仍失衡（任务结构限制）：给出拆分建议而非自动改动计划
    note += _split_suggestion(
        work, assignments, task_hours, members,
        threshold=DEFAULT_BALANCE_THRESHOLD_HOURS)

    return QAOutput(assignments=assignments, workload=work, note=note)
def _fmt(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "未标注"


def recompute_preserve(plan: PlanOutput, old_qa: QAOutput | None,
                       members: list[TeamMember]) -> QAOutput:
    """状态切换后重算：保留原有分工，只把已完成任务标记为占位，并重算负载/告警。

    不从零重排——重排会把刚完成自己任务的人当成「闲人」塞到别人后续任务上，
    与现实不符（现实中完成自己部分并不等于要再去帮别人扛后续任务）。
    只有原矩阵里缺失的任务、或负责人已离开成员名单时，才用确定性逻辑补一个。
    """
    if not members or not plan.tasks:
        return QAOutput(assignments=[], note="无任务或无成员")
    member_map = {m.name: m for m in members}
    task_hours = {t.id: t.estimated_hours for t in plan.tasks}
    old_by_task = {a.task_id: a for a in (old_qa.assignments if old_qa else [])}

    completed_ids = {t.id for t in plan.tasks if t.status == "completed"}
    assignments = []
    for t in plan.tasks:
        old = old_by_task.get(t.id)
        if t.status == "completed":
            # 完成状态由 task.status 表达；分工/score/reasoning 完全保留原值，
            # 这样切回 pending 时是无损还原——上版误把 score 清零、reasoning 覆盖，
            # 导致来回切换后匹配度列从 80% 变 0%
            if old is not None and old.presenter in member_map:
                qa_p = old.qa_primary if old.qa_primary in member_map else ""
                qa_s = [s for s in (old.qa_support or []) if s in member_map]
                assignments.append(old.model_copy(update={
                    "task_name": t.name,
                    "qa_primary": qa_p,
                    "qa_support": qa_s,
                }))
            else:
                assignments.append(QAAssignment(
                    task_id=t.id, task_name=t.name, chapter="",
                    presenter="(已完成)", qa_primary="", qa_support=[],
                    score=0.0, reasoning="任务已完成",
                ))
            continue
        # 保留原有分工（负责人仍在职）；否则走兜底
        if old is not None and old.presenter in member_map:
            # 清理已移除成员的协助角色
            qa_p = old.qa_primary if old.qa_primary in member_map else ""
            qa_s = [s for s in (old.qa_support or []) if s in member_map]
            assignments.append(old.model_copy(update={
                "task_name": t.name,
                "qa_primary": qa_p,
                "qa_support": qa_s,
            }))
            continue
        # 兜底：原矩阵缺失或负责人已离开成员名单——按确定性逻辑补一个
        scored = [(m.name, skill_score(m, t.required_skills)) for m in members]
        scored.sort(key=lambda x: -x[1])
        presenter = scored[0][0] if scored else ""
        rest_names = [n for n, _ in scored if n != presenter]
        primary = rest_names[0] if rest_names else ""
        support = rest_names[1:3]
        score = scored[0][1] if scored else 0.0
        assignments.append(QAAssignment(
            task_id=t.id, task_name=t.name, chapter="",
            presenter=presenter, qa_primary=primary, qa_support=support,
            score=score, reasoning="状态切换后按匹配度补充分配",
        ))

    work = _work_from(assignments, task_hours, members, completed_ids=completed_ids)
    overload = []
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            overload.append(f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h")
    note = "状态切换重算（保留原分工）"
    if overload:
        note += "；超载警告: " + "; ".join(overload)
    return QAOutput(assignments=assignments, workload=work, note=note)


def enhance(qa: QAOutput, plan: PlanOutput,
            members: list[TeamMember],
            threshold: float = 1.0) -> QAOutput:
    """对 Matcher(LLM) 的输出做后处理：补 score、补 workload、跳过已完成任务。

    保留 LLM 的分配人选，但当成员间负载差超过 threshold 时（LLM 常无视
    提示词的均衡要求），用确定性负载均衡做兜底——这才是「LLM 负责、
    确定性算法保底」的异构设计。任务结构本身无法均摊时给出拆分建议。
    """
    active_tasks = [t for t in plan.tasks if t.status != "completed"]
    task_hours = {t.id: t.estimated_hours for t in active_tasks}
    member_map = {m.name: m for m in members}
    task_map = {t.id: t for t in plan.tasks}
    work: dict[str, float] = {m.name: 0.0 for m in members}

    enhanced: list[QAAssignment] = []
    for a in qa.assignments:
        t = task_map.get(a.task_id)
        # 已完成任务保持占位，不计入负载
        if t and t.status == "completed":
            enhanced.append(QAAssignment(
                task_id=a.task_id, task_name=a.task_name, chapter=a.chapter,
                presenter="(已完成)", qa_primary="", qa_support=[],
                score=0.0, reasoning="任务已完成",
            ))
            continue
        h = task_hours.get(a.task_id, 0.0)
        # 折算工时到 workload（仅活任务）
        if a.presenter and a.presenter not in ("", "(已完成)"):
            work[a.presenter] = work.get(a.presenter, 0.0) + h
        if a.qa_primary and a.qa_primary != a.presenter and a.qa_primary in member_map:
            work[a.qa_primary] = work.get(a.qa_primary, 0.0) + h * QA_PRIMARY_RATIO
        for s in (a.qa_support or []):
            if s not in (a.presenter, a.qa_primary) and s in member_map:
                work[s] = work.get(s, 0.0) + h * QA_SUPPORT_RATIO
        # 补 score
        score = a.score
        if score == 0.0 and t is not None and a.presenter in member_map:
            score = skill_score(member_map[a.presenter], t.required_skills)
        enhanced.append(a.model_copy(update={"score": round(score, 3)}))

    # 负向纠偏：若 LLM 把某任务的负责人交给明确回避者（如'不想做PPT'），
    # 先换成非回避、负载最轻的成员，再进入均衡。这纠正 LLM 的初始误判，
    # 避免后续均衡因初始就错而无法纠正（均衡只做局部搬运，不会主动搬走高负载者的任务）。
    avoid_fixed = False
    for a in enhanced:
        if a.presenter in ("", "(已完成)"):
            continue
        t = task_map.get(a.task_id)
        if t is None:
            continue
        cur_member = member_map.get(a.presenter)
        if cur_member is None or not _is_avoiding(cur_member, t.required_skills):
            continue
        # 在非回避者中选 skill_score 最高、当前负载最轻的
        cands = [m for m in members if m.name != a.presenter and not _is_avoiding(m, t.required_skills)]
        if not cands:
            continue  # 全员回避，交给均衡兜底
        cands.sort(key=lambda m: (-skill_score(m, t.required_skills), work[m.name]))
        new_p = cands[0].name
        old_p = a.presenter
        # 同步更新 work：旧负责人减去该任务工时，新负责人加上，
        # 保证后续多个回避任务纠偏时排序用的是最新负载而非初始累加值。
        h = task_hours.get(a.task_id, 0.0)
        work[old_p] = work.get(old_p, 0.0) - h
        work[new_p] = work.get(new_p, 0.0) + h
        a.presenter = new_p
        # 去重：新负责人若已在协助位则移出；主要协助若等于新负责人则换成旧负责人
        if new_p in (a.qa_support or []):
            a.qa_support = [x for x in a.qa_support if x != new_p]
        if a.qa_primary == new_p:
            a.qa_primary = old_p
        avoid_fixed = True

    # 先按 LLM 人选算负载；若成员间差距过大（LLM 常无视提示词的均衡要求），
    # 再用确定性负载均衡做兜底——这才是「LLM 负责、确定性算法保底」的异构设计。
    work = _work_from(enhanced, task_hours, members)
    gap = (max(work.values()) - min(work.values())) if work else 0.0
    note = qa.note or ""
    if work and gap > threshold:
        # 确定性均衡搬运负责人/主要协助/辅助协助，目标 max-min<=threshold
        task_skills = {t.id: t.required_skills for t in plan.tasks}
        work = _balance_workload(enhanced, task_hours, members, threshold=threshold,
                                 task_skills=task_skills, member_map=member_map)
        # 均衡后重算 score/reasoning，使其与最终负责人自洽
        _resync_scores(enhanced, plan, members)
        note = note + "（LLM 分配负载失衡，已用确定性均衡修正）"
    if avoid_fixed:
        note = (note or "") + "；已将明确回避某任务的负责人调整为更合适人选"
    imbalance = []
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            imbalance.append(
                f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h")
    if imbalance:
        note += "；负载警告：" + "；".join(imbalance)
    # 均衡后仍失衡（任务结构限制）：给出拆分建议而非自动改动计划
    note += _split_suggestion(
        work, enhanced, task_hours, members,
        threshold=DEFAULT_BALANCE_THRESHOLD_HOURS)
    return qa.model_copy(update={
        "assignments": enhanced, "workload": work, "note": note})
