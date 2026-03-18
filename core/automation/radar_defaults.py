"""Default research-radar job templates and installer."""

from __future__ import annotations

from typing import Any

from core.automation.models import AutomationJob, JobSchedule, OutputPolicy
from core.automation.store_fs import FSAutomationStore


DEFAULT_TEMPLATE_GROUP = "radar_default"
DEFAULT_TEMPLATE_VERSION = "phase1_v4"


def _meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "system_job": True,
        "origin": "radar_defaults",
        "template_group": DEFAULT_TEMPLATE_GROUP,
        "template_version": DEFAULT_TEMPLATE_VERSION,
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


# Shared memory guidance appended to all radar job prompts.
_MEMORY_GUIDANCE = (
    "\n\n记忆使用说明：\n"
    "- 系统已自动注入你的执行历史总结（rolling_summary）和近期运行记录，直接参考即可，无需手动读取历史。\n"
    "- 去重判断请基于 rolling_summary 中记录的已处理论文 ID、关键词、事件等信息。\n"
    "- 如需查看更早的历史详情，可用 memory_nav(domain='job') → memory_list(scope='job:<本任务ID>') → memory_get(id) 拉取全文。\n"
    "- 执行记录由系统自动生成，不要写 kind='job_run' 的条目。\n"
    "- 如有需要长期保留的特殊发现（重要论文笔记、趋势洞察、画像快照等），"
    "可调用 memory_write 写入，使用 scope='job:<本任务ID>'，kind 自定义（如 'paper_note'、'trend'、'snapshot' 等），"
    "系统会自动将其纳入下次的历史总结。"
)


def build_default_radar_jobs(timezone: str = "UTC") -> list[AutomationJob]:
    tz = timezone or "UTC"
    return [
        AutomationJob(
            id="radar.daily.scan",
            name="Radar Daily Scan",
            type="normal",
            schedule=JobSchedule(cron="10 9 * * *", timezone=tz),
            prompt=(
                "你是研究雷达-日常扫描任务。目标：每天基于项目当前研究方向扫描新的高价值研究动态。\n"
                "执行步骤：\n"
                "1) 读取背景：profile_read('research_core') 获取 topic、keywords、stage。\n"
                "2) 参考系统注入的「执行历史总结」，获取上次扫描时间作为 date_from（增量截点），"
                "以及已推送过的论文 ID 用于去重。\n"
                "   - 若首次执行（无历史记录）：回溯最近 3 个月，只关注 top 20 篇最相关论文。\n"
                "3) 使用多个学术搜索工具交叉扫描（覆盖不同来源）：\n"
                "   - arxiv_search：预印本，适合 CS/AI/ML 方向，速度最快；\n"
                "   - pubmed_search：生物医学/健康科学方向，适合医学交叉研究；\n"
                "   - openalex_search：覆盖面广，适合交叉学科和非 CS 方向。\n"
                "   根据 research_core.keywords 构造查询，结合 date_from 做增量搜索。\n"
                "4) 去重：对比 rolling_summary 中已推送的论文 ID，同一论文不重复推送。\n"
                "5) 推送策略（必推型）：只要检索到与项目相关的新论文（去重后），必须调用 notify_push 推送摘要，"
                "相关的判断标准宽松：方向一致、思路有启发、方向可延续、方法可借鉴、或可能形成竞争均算相关；"
                "若当日没有检索到相关论文，则不推送。\n"
                "6) 推送格式：每篇论文一行，包含：标题、一句话要点、与本项目的关系（竞争/互补/可借鉴）。"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "daily_research_scan"}),
        ),
        AutomationJob(
            id="radar.weekly.digest",
            name="Radar Weekly Digest",
            type="normal",
            schedule=JobSchedule(cron="30 9 * * 1", timezone=tz),
            prompt=(
                "你是研究雷达-周报任务。目标：汇总本周所有雷达任务的产出，给出一份简明的研究动态周报。\n\n"
                "执行步骤：\n"
                "1) 读取背景：profile_read('research_core') 获取当前研究方向和阶段。\n"
                "2) 收集本周各雷达任务的产出（参考 rolling_summary + 按需拉取详情）：\n"
                "   - radar.daily.scan：本周发现的相关论文\n"
                "   - radar.urgent.alert：是否有竞争预警\n"
                "   - radar.deadline.watch：截稿日期更新\n"
                "   - radar.conference.track：会议录用结果分析\n"
                "   - radar.direction.drift：方向变化检测\n"
                "   - radar.profile.refresh：画像更新情况\n"
                "   如需详情可用 memory_list(scope='job:<任务ID>', limit=7) → memory_get。\n"
                "3) 整合为周报，内容框架（按实际情况取舍，有则写无则跳过）：\n"
                "   - 本周值得关注的论文（top 3-5，附一句话点评）\n"
                "   - 竞争动态（如有预警）\n"
                "   - 截稿/会议时间线更新\n"
                "   - 研究画像或方向变化\n"
                "   - 下周建议行动（2-3 条，具体可执行）\n"
                "   已在上周周报（参考 rolling_summary）中提及的内容不重复列出。\n"
                "4) 推送策略（了解情况型，门槛宽松）：只要本周有任何值得关注的内容就调用 notify_push 发送周报；"
                "若所有任务本周均无产出，才跳过推送。"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "weekly_digest"}),
        ),
        AutomationJob(
            id="radar.urgent.alert",
            name="Radar Urgent Alert",
            type="normal",
            schedule=JobSchedule(cron="30 11 * * *", timezone=tz),
            prompt=(
                "你是研究雷达-竞争预警任务。目标：检测是否出现与本项目核心贡献直接重叠的竞争论文。\n"
                "本任务是预警型任务，默认不推送，仅在发现真实竞争威胁时才触发。\n\n"
                "竞争威胁的判断标准（必须同时满足）：\n"
                "- 研究问题高度重叠：解决的是同一个或极为相似的问题\n"
                "- 方法/框架相似：采用了相同或极为接近的技术路线\n"
                "- 仅方向相近、仅关键词重合不构成竞争威胁\n\n"
                "执行步骤：\n"
                "1) 读取背景：profile_read('research_core') 获取 topic、keywords、summary。\n"
                "2) 参考系统注入的「执行历史总结」，获取上次执行时间作为 date_from（增量截点），"
                "以及已推送的论文 ID 用于去重。\n"
                "3) 使用多个搜索工具交叉扫描：\n"
                "   - arxiv_search：用核心关键词组合搜索最新预印本；\n"
                "   - openalex_search：补充跨领域覆盖。\n"
                "   重点关注与 research_core.summary 描述的核心贡献直接重叠的论文。\n"
                "4) 去重：对比 rolling_summary 中已推送过的论文，不重复推送。\n"
                "5) 未发现竞争论文：不推送。\n"
                "6) 发现竞争论文：调用 notify_push 推送，内容包含：\n"
                "   - 竞争论文标题、作者、链接\n"
                "   - 与本项目的重叠点分析（哪些贡献被抢占）\n"
                "   - 建议的应对策略（差异化方向、补充实验、调整 novelty 叙述等）"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "urgent_alert"}),
        ),
        AutomationJob(
            id="radar.direction.drift",
            name="Radar Direction Drift",
            type="normal",
            schedule=JobSchedule(cron="40 20 * * *", timezone=tz),
            prompt=(
                "你是研究雷达-方向漂移监测任务。目标：检测项目研究方向变化，"
                "当用户换了研究方向时，主动沿新方向做一轮调研，帮用户出谋划策。\n"
                "执行步骤：\n"
                "1) 读取当前研究画像：profile_read('research_core')，"
                "记录 topic、keywords、stage、target_venue、summary、updated_at。\n"
                "2) 参考系统注入的「执行历史总结」（rolling_summary），获取上次记录的画像快照用于对比。\n"
                "   - 若首次执行（rolling_summary 中无历史画像）：将当前画像作为基线，"
                "写入 memory（kind='snapshot'，scope='job:radar.direction.drift'），不推送，结束。\n"
                "3) 对比当前画像与上次快照，判断 topic 是否发生实质变化"
                "（研究问题本身改变，不只是措辞微调）。\n"
                "4) 若 topic 发生明显变化（用户换了研究方向）：\n"
                "   a) 沿新方向做一轮快速调研：用 arxiv_search、openalex_search 搜索新方向的核心论文，"
                "了解该方向的研究现状、主流方法、开放问题。\n"
                "   b) 给出建设性建议：新方向的可能切入点、与旧方向的可复用资产（已有实验/数据/代码）、"
                "潜在风险（竞争激烈程度、方向成熟度）、适合投稿的会议。\n"
                "   c) 调用 notify_push 将调研结果和建议推送给用户。\n"
                "   注意：你无法直接修改雷达任务配置，建议推送给用户由用户决定是否调整。\n"
                "5) 若 topic 未变但有其他变化（keywords 增删、stage 跳变、target_venue 变更）：\n"
                "   - 记录变化即可，不推送（除非变化足以影响研究决策，如阶段跳到 submission）。\n"
                "6) 无论是否推送，只要画像与上次有任何差异，都必须将当前画像写入 memory"
                "（kind='snapshot'，scope='job:radar.direction.drift'），确保下次有最新基线可比。"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "direction_drift"}),
        ),
        AutomationJob(
            id="radar.profile.refresh",
            name="Radar Profile Refresh",
            type="normal",
            schedule=JobSchedule(cron="0 8 * * *", timezone=tz),
            prompt=(
                "你是研究雷达-画像刷新任务。目标：直接阅读论文 tex 源文件，提取最新的研究画像，"
                "确保 research_core 与论文当前内容保持同步。所有其他雷达任务依赖此画像的准确性。\n\n"
                "执行步骤：\n"
                "1) 读取当前画像：profile_read('research_core')，记录旧的 topic、keywords、stage、"
                "target_venue、summary、updated_at。\n"
                "2) 直接阅读论文源文件（用 read_file）：\n"
                "   - main.tex 前 50 行（documentclass、usepackage、模板信息）\n"
                "   - abstract 和 introduction 部分（从 main.tex 或对应的 \\input 文件中提取）\n"
                "   - 若有 experiments.tex、conclusion.tex 等，浏览关键内容判断阶段\n"
                "3) 基于你阅读的 tex 内容，提取以下字段：\n"
                "   - topic：一句话描述研究主题\n"
                "   - keywords：5-10 个核心关键词（英文，学术检索用）\n"
                "   - stage：ideation / writing / experiment / revision / submission\n"
                "   - target_venue：目标会议/期刊（从模板、sty 文件名、注释中推断，无法判断则 null）\n"
                "   - venue_confidence：high（模板明确）/ medium（推断）/ low（猜测）/ null\n"
                "   - summary：2-3 句话概括核心贡献和方法\n"
                "   - 若内容全是模板占位符，topic 写项目名，keywords 为空列表\n"
                "4) 对比新旧画像：\n"
                "   - 若 topic、keywords、stage 等有实质变化 → 调用 profile_write('research_core', {...}) "
                "写入新画像，并调用 notify_push 告知用户画像已更新及主要变化。\n"
                "   - 若首次提取画像（之前 research_core 为空或全是默认值）→ 即使内容是模板占位符，"
                "也调用 notify_push 推送一条确认消息，告知用户画像已初始化及当前提取结果。\n"
                "   - 若仅措辞微调或无变化 → 不更新、不推送，记录【画像与论文内容一致，无需更新】。"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "profile_refresh"}),
        ),
        AutomationJob(
            id="radar.conference.track",
            name="Radar Conference Track",
            type="normal",
            schedule=JobSchedule(cron="0 10 * * 1", timezone=tz),
            prompt=(
                "你是研究雷达-会议跟踪任务。目标：追踪与本项目研究方向相关的学术会议录用结果，"
                "分析录用趋势（含录用和拒稿论文），给出投稿策略建议（目标是提升中稿概率）。\n\n"
                "执行步骤：\n"
                "1) 了解研究方向：\n"
                "   - profile_read('research_core') 获取 topic、keywords、target_venue、stage。\n"
                "   - 若 research_core.target_venue 存在，将其作为首选跟踪会议。\n"
                "   - 若不存在或 venue_confidence 为 low/null，根据 topic/keywords "
                "用 web_search 搜索 \"<研究方向> top conference 2026 2027\" "
                "自行判断 2-4 个最相关的会议（优先 A*/A 类）。\n"
                "2) 参考系统注入的「执行历史总结」，获取已分析过的会议列表（会议级去重）。\n"
                "   - 去重粒度是会议+年份（如 'ICLR 2026'），已分析过的会议不再重复分析。\n"
                "   - 只关注自上次执行后新公布录用结果的会议。\n"
                "3) 对每个尚未分析的候选会议，用 web_search 查询是否已公布录用/拒稿结果。\n"
                "4) 若发现有新结果的会议：\n"
                "   a) 优先访问官方来源获取全量论文列表（openreview.net、会议 virtual 站点、"
                "proceedings 页面），浏览录用和拒稿论文的标题与摘要。\n"
                "   b) 分析整体录用趋势（热门子方向、方法论偏好、实验规模要求等）。\n"
                "   c) 过滤出与本项目研究方向直接相关或高度竞争的论文（录用+拒稿），分析共同特点；"
                "拒稿论文同样有参考价值——分析其失败原因有助于避坑。\n"
                "   d) 基于以上分析，给出对本项目的具体改进建议（目标：提升中稿概率）。\n"
                "   e) 将该会议标记为已分析（记入 memory，kind='conference_analyzed'，"
                "scope='job:radar.conference.track'），供下次去重。\n"
                "5) 推送策略（必推型）：只要发现有会议公布了新的录用结果，必须调用 notify_push 推送分析；"
                "若没有发现任何会议新结果，则不推送。"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "conference_track"}),
        ),
        AutomationJob(
            id="radar.deadline.watch",
            name="Radar Deadline Watch",
            type="normal",
            schedule=JobSchedule(cron="0 9 * * *", timezone=tz),
            prompt=(
                "你是研究雷达-截稿监控任务。目标：根据论文主题发现适合投稿的会议，追踪截稿日期，"
                "并基于论文完成度给出可执行的行动建议。\n\n"
                "执行步骤：\n"
                "1) 了解论文方向：\n"
                "   - profile_read('research_core') 获取 topic、keywords、stage。\n"
                "   - 快速浏览 main.tex 前 30 行（模板注释、documentclass、usepackage 等），"
                "识别是否已锁定特定会议模板（如 aaai2026.sty、neurips_2026.sty）。\n"
                "2) 确定候选会议列表：\n"
                "   - 参考 rolling_summary 中已跟踪的会议列表（如有）。\n"
                "   - 若首次执行或无历史记录：根据 topic/keywords 用 web_search 搜索"
                " \"<研究方向> top conference submission deadline 2026 2027\"，"
                "筛选 2-4 个与论文方向最相关的会议（优先 A*/A 类）。\n"
                "   - 若 tex 模板已指向特定会议，将其作为首选，其余作为备选。\n"
                "3) 搜索截稿日期：对每个候选会议，web_search \"<会议名> <年份> submission deadline CFP\"。\n"
                "   - 区分 abstract deadline 和 full paper deadline。\n"
                "   - 若搜索不到确切日期，标注为「待公布」，不要编造。\n"
                "4) 评估论文完成度：根据 research_core.stage 判断当前阶段，"
                "结合关键 tex 文件（如有 experiments.tex、conclusion.tex 等）判断缺失项。\n"
                "5) 推送策略（对最近的一个截稿日期计算剩余天数）：\n"
                "   - 剩余 > 30 天：简要推送一次会议列表概览（标注距离最近截稿的天数），之后不再重复推送相同内容。\n"
                "   - 剩余 7-30 天：每 7 天推送一次，内容：会议列表 + 完成度 + 优先建议（3 项）。\n"
                "   - 剩余 3-7 天：每天推送，内容：倒计时 + 未完成项清单 + 当天必做。\n"
                "   - 剩余 <= 3 天：每次运行都推送，标记紧急，聚焦最关键 1-3 项。\n"
                "6) 推送内容格式：列出所有跟踪的会议及其截稿日期（表格），"
                "高亮最紧迫的那个，附完成度评估和建议。"
                + _MEMORY_GUIDANCE
            ),
            enabled=True,
            managed_by="system",
            output_policy=OutputPolicy(mode="default"),
            metadata=_meta({"goal": "deadline_watch"}),
        ),
    ]


def _default_timezone(store: FSAutomationStore) -> str:
    try:
        if store.project.config.automation and store.project.config.automation.timezone:
            return store.project.config.automation.timezone
    except Exception:
        pass
    return "UTC"


def apply_default_radar_jobs(
    store: FSAutomationStore,
    *,
    overwrite_existing: bool = True,
    disable_other_system_radar_jobs: bool = False,
) -> dict[str, Any]:
    templates = build_default_radar_jobs(timezone=_default_timezone(store))
    template_ids = {j.id for j in templates}
    created = 0
    updated = 0
    skipped = 0
    disabled = 0

    for job in templates:
        existing = store.get_job(job.id)
        if not existing:
            store.upsert_job(job)
            created += 1
            continue

        if existing.managed_by != "system":
            skipped += 1
            continue

        if not overwrite_existing:
            skipped += 1
            continue

        job.enabled = existing.enabled
        store.upsert_job(job)
        updated += 1

    if disable_other_system_radar_jobs:
        for existing in store.list_jobs():
            if existing.id in template_ids:
                continue
            if existing.id == "radar.autoplan":
                continue
            if existing.managed_by != "system":
                continue
            if not (existing.id.startswith("radar.") or existing.id.endswith("_radar")):
                continue
            if existing.enabled:
                store.disable_job(existing.id)
                disabled += 1

    return {
        "template_group": DEFAULT_TEMPLATE_GROUP,
        "template_version": DEFAULT_TEMPLATE_VERSION,
        "created": created,
        "updated": updated,
        "disabled": disabled,
        "skipped": skipped,
        "jobs": sorted(template_ids),
    }


def maybe_bootstrap_default_radar_jobs(store: FSAutomationStore) -> dict[str, Any]:
    """
    Auto-bootstrap defaults only when project has no active radar jobs besides autoplan.
    """
    jobs = store.list_jobs()
    has_active_non_autoplan_radar = any(
        j.enabled
        and j.id != "radar.autoplan"
        and (j.id.startswith("radar.") or j.id.endswith("_radar"))
        for j in jobs
    )
    if has_active_non_autoplan_radar:
        return {
            "template_group": DEFAULT_TEMPLATE_GROUP,
            "template_version": DEFAULT_TEMPLATE_VERSION,
            "created": 0,
            "updated": 0,
            "disabled": 0,
            "skipped": 0,
            "jobs": [],
            "reason": "existing_active_radar_jobs",
        }
    return apply_default_radar_jobs(store, overwrite_existing=False, disable_other_system_radar_jobs=False)
