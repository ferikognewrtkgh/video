from .domain import (
    Approval, Character, CharacterAppearanceVersion, ContinuityState, CostEvent,
    Event, GenerationRun, GenerationStatus, MetricScores, Project, QAReport, Scene,
    ShotSpec, WorkflowStatus,
)


def _state(location: str, emotion: str, holding=None, time="day", appearance="linxia_office_v1"):
    return ContinuityState(location=location, emotion=emotion, holding=holding or [], time_of_day=time, appearance_version=appearance)


def build_demo_project() -> Project:
    characters = [
        Character(
            id="char_linxia", name="林夏", role="调查记者", voice="冷静、清晰、略带疲惫",
            identity="26岁，乌黑及肩发，琥珀色眼睛，左眼下有一颗小痣", locked=True, accent_color="#ff7058",
            appearances=[
                CharacterAppearanceVersion(id="linxia_office_v1", character_id="char_linxia", name="办公室装", outfit="象牙白衬衫、墨黑长裙、银色腕表", palette=["#eee8dc", "#242226", "#b9bbc2"], locked=True, prompt_fragment="ivory shirt, long black skirt, silver watch"),
                CharacterAppearanceVersion(id="linxia_rain_v1", character_id="char_linxia", name="雨夜装", outfit="深色风衣、办公室内搭", palette=["#252b31", "#eee8dc"], locked=False, prompt_fragment="charcoal trench coat over ivory shirt"),
            ],
        ),
        Character(
            id="char_chenshu", name="陈述", role="失踪的证人", voice="低沉、克制、语速缓慢",
            identity="31岁，短黑发，轮廓锋利，右眉旧伤", locked=True, accent_color="#4cc9c0",
            appearances=[CharacterAppearanceVersion(id="chenshu_coat_v1", character_id="char_chenshu", name="夜行装", outfit="深灰高领、旧皮夹克", palette=["#34363a", "#161719"], locked=True, prompt_fragment="dark turtleneck, worn leather jacket")],
        ),
    ]
    scenes = [
        Scene(id="office_day", name="旧报社编辑部", location="office", time_of_day="day", style="百叶窗切光，暖灰色纸张与冷青阴影", locked=True),
        Scene(id="corridor_dusk", name="报社走廊", location="corridor", time_of_day="dusk", style="狭长透视，闪烁的旧日光灯", locked=True),
        Scene(id="alley_rain", name="后巷雨夜", location="alley", time_of_day="night", style="潮湿路面，霓虹倒影，青橙对比", locked=False),
    ]
    raw = [
        ("shot_01", "office_day", "未寄出的信", 6, "narration", "wide", "slow_pan", [], _state("office", "calm", ["document"]), "雨声之外，只有旧报社的时钟还在走。林夏拆开一封没有署名的信。", _state("office", "focused", ["document"]), "", None, 92),
        ("shot_02", "office_day", "照片里的证人", 6, "static_display", "insert", "slow_push_in", [], _state("office", "focused", ["document"]), "信封中滑出陈述三年前的照片，背面写着今晚九点。", _state("office", "uneasy", ["document", "photo"]), "", "shot_01", 88),
        ("shot_03", "office_day", "门口的人", 5, "dialogue_closeup", "medium_close_up", "rack_focus", ["char_linxia"], _state("office", "uneasy", ["document", "photo"]), "林夏抬头看向门口，文件从手中滑落。", _state("office", "shocked", ["photo"]), "你怎么会在这里？", "shot_02", 83),
        ("shot_04", "office_day", "消失的陈述", 7, "dialogue_closeup", "close_up", "locked", ["char_chenshu"], ContinuityState(location="office", emotion="guarded", holding=[], time_of_day="day", appearance_version="chenshu_coat_v1"), "陈述站在逆光里，只说出一句警告。", ContinuityState(location="office", emotion="urgent", holding=[], time_of_day="day", appearance_version="chenshu_coat_v1"), "别相信你收到的任何东西。", "shot_03", 79),
        ("shot_05", "corridor_dusk", "追入走廊", 6, "light_action", "tracking", "handheld_follow", ["char_linxia", "char_chenshu"], _state("corridor", "urgent", ["photo"], "dusk"), "陈述转身离开，林夏追入忽明忽暗的走廊。", _state("corridor", "tense", ["photo"], "dusk"), "等等！", "shot_04", 76),
        ("shot_06", "corridor_dusk", "断电", 5, "transition", "wide", "dolly_out", ["char_linxia"], _state("corridor", "tense", ["photo"], "dusk"), "灯光熄灭。脚步声在黑暗中骤然停止。", _state("corridor", "afraid", ["photo"], "dusk"), "", "shot_05", 72),
        ("shot_07", "alley_rain", "雨夜真相", 8, "premium_action", "medium", "crane_down", ["char_linxia", "char_chenshu"], _state("alley", "determined", ["photo"], "night", "linxia_rain_v1"), "后巷雨幕中，陈述将一枚存储卡按进林夏掌心，远处车灯逼近。", _state("alley", "alarmed", ["photo", "memory_card"], "night", "linxia_rain_v1"), "把它交给真正还记得真相的人。", "shot_06", 68),
        ("shot_08", "alley_rain", "追光", 7, "premium_action", "wide", "fast_pull_back", ["char_linxia"], _state("alley", "alarmed", ["photo", "memory_card"], "night", "linxia_rain_v1"), "车灯吞没画面，林夏转身冲进雨夜。", _state("alley", "determined", ["memory_card"], "night", "linxia_rain_v1"), "这一次，我会找到他们。", "shot_07", 86),
    ]
    shots = []
    for idx, row in enumerate(raw, start=1):
        sid, scene, title, duration, stype, size, camera, chars, start, action, end, dialogue, prev, score = row
        versions = [v for v in {start.appearance_version, end.appearance_version} if v]
        allowed_changes = ["location", "time_of_day", "appearance_version"] if sid in {"shot_05", "shot_07"} else []
        if sid == "shot_05":
            # Cut back from Chen Shu to Lin Xia; her photo remains in hand.
            allowed_changes.append("holding")
        if sid == "shot_04":
            # Reverse shot: the subject changes from Lin Xia to Chen Shu.
            allowed_changes = ["appearance_version", "holding"]
        shots.append(ShotSpec(id=sid, scene_id=scene, order=idx, title=title, duration_sec=duration, shot_type=stype, shot_size=size, camera_motion=camera, characters=chars, character_versions=versions, start_state=start, action=action, end_state=end, dialogue=dialogue, reference_asset_ids=[scene, *versions], previous_shot_id=prev, must_preserve=["identity", "outfit", "prop_state"], allowed_changes=allowed_changes, status="completed" if idx < 5 else "ready", route="2.5d_parallax" if stype in {"narration", "static_display"} else "portrait_drive" if stype == "dialogue_closeup" else "i2v", qa_score=score))
    events = [
        Event(id="evt_01", title="匿名来信", summary="林夏收到指向失踪证人的信件", tension=28, character_ids=["char_linxia"], leads_to=["evt_02"]),
        Event(id="evt_02", title="死者归来", summary="失踪三年的陈述突然出现并发出警告", tension=62, character_ids=["char_linxia", "char_chenshu"], leads_to=["evt_03"]),
        Event(id="evt_03", title="雨夜交付", summary="陈述交出存储卡，追踪者随即出现", tension=94, character_ids=["char_linxia", "char_chenshu"]),
    ]
    runs = [
        GenerationRun(id=f"run_0{i}", shot_id=f"shot_0{i}", provider="mock", provider_task_id=f"mock_demo_0{i}", idempotency_key=f"demo_key_0{i}", status=GenerationStatus.completed, progress=100, cost=cost, elapsed_sec=9+i, output_uri=f"mock://renders/shot_0{i}.mp4", recipe_id=f"recipe_shot_0{i}")
        for i, cost in enumerate([0.08, 0.08, 0.18, 0.18], start=1)
    ]
    qa = [
        QAReport(id="qa_03", shot_id="shot_03", hard_gate_passed=True, score=83, metrics=MetricScores(identity=.91, prompt_alignment=.86, temporal_stability=.80, motion=.75, aesthetics=.78)),
        QAReport(id="qa_06", shot_id="shot_06", hard_gate_passed=True, score=72, metrics=MetricScores(identity=.94, prompt_alignment=.78, temporal_stability=.52, motion=.61, aesthetics=.82), failures=["flicker"], repair_strategy="缩短镜头并降低动作幅度，必要时切换 2.5D 路线", attempt=1),
    ]
    return Project(
        id="project_afterimage", name="余像 / AFTERIMAGE", source_text="一名记者收到失踪证人的匿名来信，并在旧报社发现被掩埋三年的真相。", logline="失踪三年的证人突然归来，一名调查记者必须在雨夜追杀开始前交出真相。", style="电影感青年漫画 · 暖灰与冷青 · 雨夜霓虹", workflow_status=WorkflowStatus.running, workflow_step=7, episode_title="EP.01 雨停之前", characters=characters, scenes=scenes, events=events, shots=shots, generation_runs=runs, qa_reports=qa,
        cost_events=[CostEvent(id=f"cost_{i}", shot_id=run.shot_id, category="video", provider=run.provider, amount=run.cost) for i, run in enumerate(runs, 1)],
        approvals=[Approval(id="approval_assets", target_type="asset_bible", target_id="project_afterimage", status="approved")],
    )
