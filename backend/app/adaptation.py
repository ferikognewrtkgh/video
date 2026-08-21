from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from .domain import (
    AdaptationResult,
    Character,
    CharacterAppearanceVersion,
    ContinuityState,
    CreateProjectRequest,
    Event,
    Scene,
    ShotSpec,
)


class StructuredAdaptationModel(Protocol):
    def generate(self, request: CreateProjectRequest) -> AdaptationResult: ...


@dataclass(frozen=True)
class StoryProfile:
    key: str
    title: str
    protagonist: str
    protagonist_role: str
    partner: str
    partner_role: str
    locations: tuple[tuple[str, str, str, str], ...]
    event_titles: tuple[str, str, str]
    prop: str


PROFILES = (
    (
        ("火星", "机器人", "基地", "mars", "robot"),
        StoryProfile(
            "mars", "最后一段信号", "艾岚", "火星维修工程师", "R-17", "失联勘探机器人",
            (
                ("mars_habitat", "火星栖息舱", "habitat", "冷白舱灯、红色尘暴压迫舷窗"),
                ("mars_tunnel", "废弃维护隧道", "tunnel", "应急红灯、狭长金属透视"),
                ("mars_surface", "火星风暴地表", "surface", "赤红沙幕、远处基地信标"),
            ),
            ("异常信号", "隧道唤醒", "风暴救援"), "signal_core",
        ),
    ),
    (
        ("海底", "潜水", "深海", "ocean", "submarine"),
        StoryProfile(
            "ocean", "深蓝回声", "周澜", "深海声呐员", "顾潜", "失踪潜航员",
            (
                ("sub_control", "潜艇控制舱", "control_room", "幽蓝仪表光、狭窄舱室"),
                ("wreck", "沉船内部", "wreck", "漂浮颗粒、手电光束切开黑暗"),
                ("trench", "海沟边缘", "trench", "深蓝水压、巨型阴影掠过"),
            ),
            ("深海回声", "沉船重逢", "海沟逃生"), "sonar_recording",
        ),
    ),
    (
        ("森林", "古树", "猎人", "forest"),
        StoryProfile(
            "forest", "雾林之心", "叶青", "森林巡护员", "阿洛", "迷路的少年",
            (
                ("watchtower", "林间瞭望塔", "watchtower", "晨雾与斑驳木纹"),
                ("ancient_forest", "古树迷宫", "forest", "湿润苔藓、绿色逆光"),
                ("cliff", "雾崖祭坛", "cliff", "翻涌云海、金色裂光"),
            ),
            ("失踪呼救", "古树引路", "雾崖抉择"), "wooden_charm",
        ),
    ),
)

DEFAULT_PROFILE = StoryProfile(
    "urban", "倒计时之后", "许言", "独立调查员", "陆衡", "关键证人",
    (
        ("studio", "旧城区工作室", "studio", "暖灰窗光、堆叠卷宗"),
        ("station", "废弃地铁站", "station", "冷青灯带、纵深站台"),
        ("rooftop", "城市天台", "rooftop", "夜色霓虹、强风云层"),
    ),
    ("意外线索", "证人现身", "限时交付"), "evidence_drive",
)


class DeterministicAdaptationModel:
    """Offline structured model used when no LLM is configured.

    It is deliberately input-driven and deterministic, making it suitable for tests and
    local demos. A cloud structured-output model can implement the same protocol.
    """

    def generate(self, request: CreateProjectRequest) -> AdaptationResult:
        profile = self._select_profile(request.source_text)
        digest = hashlib.sha256(request.source_text.encode("utf-8")).hexdigest()
        main_id, partner_id = f"char_{profile.key}_main", f"char_{profile.key}_partner"
        main_appearance = f"{profile.key}_main_v1"
        partner_appearance = f"{profile.key}_partner_v1"
        characters = [
            Character(
                id=main_id, name=profile.protagonist, role=profile.protagonist_role,
                identity=f"{profile.protagonist}，轮廓清晰，具有可追踪的固定面部特征 #{digest[:4]}",
                voice="清晰、克制，情绪随危机逐渐增强", accent_color="#ff7058",
                appearances=[CharacterAppearanceVersion(
                    id=main_appearance, character_id=main_id, name="主造型",
                    outfit=f"符合{profile.protagonist_role}身份的深浅对比服装",
                    palette=["#ede7db", "#25282c", "#ff7058"],
                    prompt_fragment=f"consistent {profile.key} protagonist outfit, identity token {digest[:6]}",
                )],
            ),
            Character(
                id=partner_id, name=profile.partner, role=profile.partner_role,
                identity=f"{profile.partner}，与主角有明显区分的轮廓与配色 #{digest[4:8]}",
                voice="低沉、短句、带有环境噪声", accent_color="#4cc9c0",
                appearances=[CharacterAppearanceVersion(
                    id=partner_appearance, character_id=partner_id, name="主造型",
                    outfit=f"符合{profile.partner_role}身份的功能性服装",
                    palette=["#26343a", "#111416", "#4cc9c0"],
                    prompt_fragment=f"consistent {profile.key} partner outfit, identity token {digest[6:12]}",
                )],
            ),
        ]
        scenes = [Scene(id=sid, name=name, location=location, time_of_day="night" if idx == 2 else "day", style=style) for idx, (sid, name, location, style) in enumerate(profile.locations)]
        events = [
            Event(id=f"evt_{idx+1:02}", title=title, summary=self._event_summary(request.source_text, title, idx), tension=(32, 64, 93)[idx], character_ids=[main_id] if idx == 0 else [main_id, partner_id], leads_to=[f"evt_{idx+2:02}"] if idx < 2 else [])
            for idx, title in enumerate(profile.event_titles)
        ]
        shots = self._shots(request, profile, main_id, partner_id, main_appearance)
        return AdaptationResult(
            logline=self._logline(request.source_text, profile), episode_title=f"EP.01 {profile.title}",
            characters=characters, scenes=scenes, events=events, shots=shots,
            trace=[
                {"node": "adaptation", "status": "completed", "output": "3 events", "model": "deterministic-structured-v1"},
                {"node": "asset_extraction", "status": "completed", "output": "2 characters / 3 scenes"},
                {"node": "director", "status": "completed", "output": "8 ShotSpec objects"},
                {"node": "continuity", "status": "completed", "output": "state transitions annotated"},
            ],
        )

    @staticmethod
    def _select_profile(text: str) -> StoryProfile:
        lowered = text.lower()
        for keywords, profile in PROFILES:
            if any(keyword in lowered for keyword in keywords):
                return profile
        return DEFAULT_PROFILE

    @staticmethod
    def _logline(text: str, profile: StoryProfile) -> str:
        compact = re.sub(r"\s+", "", text)
        premise = compact[:52] + ("…" if len(compact) > 52 else "")
        return f"{profile.protagonist}与{profile.partner}被卷入一场限时危机：{premise}"

    @staticmethod
    def _event_summary(text: str, title: str, index: int) -> str:
        compact = re.sub(r"\s+", "", text)
        width = max(12, len(compact) // 3)
        excerpt = compact[index * width:(index + 1) * width] or compact[:width]
        return f"{title}：{excerpt[:38]}"

    @staticmethod
    def _shots(request: CreateProjectRequest, profile: StoryProfile, main_id: str, partner_id: str, appearance: str):
        durations = [6, 6, 5, 7, 6, 5, 8, 7]
        delta = request.target_duration_sec - sum(durations)
        durations[-1] += delta
        titles = (
            f"{profile.event_titles[0]}之前", profile.event_titles[0], "第一次回应",
            profile.event_titles[1], "越过边界", "短暂失控", profile.event_titles[2], "留下钩子",
        )
        types = ("narration", "static_display", "dialogue_closeup", "dialogue_closeup", "light_action", "transition", "premium_action", "premium_action")
        motions = ("slow_pan", "slow_push_in", "rack_focus", "locked", "handheld_follow", "dolly_out", "crane_down", "fast_pull_back")
        scene_indices = (0, 0, 0, 1, 1, 1, 2, 2)
        source = re.sub(r"\s+", "", request.source_text)
        shots = []
        previous_end = None
        for idx in range(8):
            scene = profile.locations[scene_indices[idx]]
            holding = [] if idx == 0 else [profile.prop]
            start = ContinuityState(location=scene[2], emotion=("calm", "focused", "uneasy", "guarded", "urgent", "afraid", "determined", "alarmed")[idx], holding=holding, time_of_day="night" if scene_indices[idx] == 2 else "day", appearance_version=appearance)
            if previous_end and scene_indices[idx] == scene_indices[idx-1]:
                start = previous_end.model_copy(deep=True)
            end_holding = [profile.prop] if idx < 7 else []
            end = start.model_copy(update={"emotion": ("focused", "uneasy", "shocked", "urgent", "tense", "determined", "alarmed", "resolved")[idx], "holding": end_holding})
            allowed = ["location", "time_of_day"] if idx in {3, 6} else []
            action_excerpt = source[(idx * 17) % max(1, len(source)):][:28]
            shots.append(ShotSpec(
                id=f"shot_{idx+1:02}", scene_id=scene[0], order=idx+1, title=titles[idx],
                duration_sec=durations[idx], shot_type=types[idx], shot_size="wide" if idx in {0, 6, 7} else "medium_close_up",
                camera_motion=motions[idx], characters=[main_id] if idx != 3 else [main_id, partner_id],
                character_versions=[appearance], start_state=start,
                action=f"{titles[idx]}。{action_excerpt}", end_state=end,
                dialogue="我们没有多少时间了。" if idx in {2, 3, 6} else "",
                reference_asset_ids=[scene[0], appearance], previous_shot_id=f"shot_{idx:02}" if idx else None,
                must_preserve=["identity", "outfit", "prop_state"], allowed_changes=allowed,
            ))
            previous_end = end
        return shots


class AdaptationAgent:
    def __init__(self, model: StructuredAdaptationModel | None = None) -> None:
        self.model = model or DeterministicAdaptationModel()

    def run(self, request: CreateProjectRequest) -> AdaptationResult:
        result = self.model.generate(request)
        if not (6 <= len(result.shots) <= 10):
            raise ValueError("Adaptation must produce 6-10 shots")
        if not (45 <= sum(shot.duration_sec for shot in result.shots) <= 60):
            raise ValueError("Adaptation duration must be 45-60 seconds")
        return result
