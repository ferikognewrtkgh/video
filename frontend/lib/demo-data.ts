import { Project } from "./types";

const state = (location: string, emotion: string, holding: string[] = [], time_of_day = "day", appearance_version = "linxia_office_v1") => ({ location, emotion, holding, time_of_day, appearance_version });

export const demoProject: Project = {
  id: "project_afterimage", data_mode: "demo", name: "余像 / AFTERIMAGE", source_text: "一名记者收到失踪证人的匿名来信，并在旧报社发现被掩埋三年的真相。", logline: "失踪三年的证人突然归来，一名调查记者必须在雨夜追杀开始前交出真相。", style: "电影感青年漫画 · 暖灰与冷青 · 雨夜霓虹", aspect_ratio: "9:16", target_duration_sec: 50, workflow_status: "running", workflow_step: 7, episode_title: "EP.01 雨停之前",
  characters: [
    { id: "char_linxia", name: "林夏", role: "调查记者", identity: "26岁，乌黑及肩发，琥珀色眼睛，左眼下有一颗小痣", voice: "冷静、清晰、略带疲惫", locked: true, accent_color: "#ff7058", appearances: [
      { id: "linxia_office_v1", name: "办公室装", outfit: "象牙白衬衫、墨黑长裙、银色腕表", palette: ["#eee8dc", "#242226", "#b9bbc2"], locked: true, prompt_fragment: "ivory shirt, long black skirt, silver watch" },
      { id: "linxia_rain_v1", name: "雨夜装", outfit: "深色风衣、办公室内搭", palette: ["#252b31", "#eee8dc"], locked: false, prompt_fragment: "charcoal trench coat over ivory shirt" }
    ]},
    { id: "char_chenshu", name: "陈述", role: "失踪的证人", identity: "31岁，短黑发，轮廓锋利，右眉旧伤", voice: "低沉、克制、语速缓慢", locked: true, accent_color: "#4cc9c0", appearances: [{ id: "chenshu_coat_v1", name: "夜行装", outfit: "深灰高领、旧皮夹克", palette: ["#34363a", "#161719"], locked: true, prompt_fragment: "dark turtleneck, worn leather jacket" }] }
  ],
  scenes: [
    { id: "office_day", name: "旧报社编辑部", location: "office", time_of_day: "day", style: "百叶窗切光，暖灰色纸张与冷青阴影", locked: true },
    { id: "corridor_dusk", name: "报社走廊", location: "corridor", time_of_day: "dusk", style: "狭长透视，闪烁的旧日光灯", locked: true },
    { id: "alley_rain", name: "后巷雨夜", location: "alley", time_of_day: "night", style: "潮湿路面，霓虹倒影，青橙对比", locked: false }
  ],
  events: [
    { id: "evt_01", title: "匿名来信", summary: "林夏收到指向失踪证人的信件", tension: 28, character_ids: ["char_linxia"], leads_to: ["evt_02"] },
    { id: "evt_02", title: "死者归来", summary: "失踪三年的陈述突然出现并发出警告", tension: 62, character_ids: ["char_linxia", "char_chenshu"], leads_to: ["evt_03"] },
    { id: "evt_03", title: "雨夜交付", summary: "陈述交出存储卡，追踪者随即出现", tension: 94, character_ids: ["char_linxia", "char_chenshu"], leads_to: [] }
  ],
  shots: [
    ["未寄出的信",6,"narration","slow_pan","雨声之外，只有旧报社的时钟还在走。林夏拆开一封没有署名的信。","","office_day",92,"completed","2.5d_parallax"],
    ["照片里的证人",6,"static_display","slow_push_in","信封中滑出陈述三年前的照片，背面写着今晚九点。","","office_day",88,"completed","2.5d_parallax"],
    ["门口的人",5,"dialogue_closeup","rack_focus","林夏抬头看向门口，文件从手中滑落。","你怎么会在这里？","office_day",83,"completed","portrait_drive"],
    ["消失的陈述",7,"dialogue_closeup","locked","陈述站在逆光里，只说出一句警告。","别相信你收到的任何东西。","office_day",79,"completed","portrait_drive"],
    ["追入走廊",6,"light_action","handheld_follow","陈述转身离开，林夏追入忽明忽暗的走廊。","等等！","corridor_dusk",76,"generating","i2v"],
    ["断电",5,"transition","dolly_out","灯光熄灭。脚步声在黑暗中骤然停止。","","corridor_dusk",72,"repairing","t2v"],
    ["雨夜真相",8,"premium_action","crane_down","后巷雨幕中，陈述将一枚存储卡按进林夏掌心，远处车灯逼近。","把它交给真正还记得真相的人。","alley_rain",68,"ready","premium_i2v"],
    ["追光",7,"premium_action","fast_pull_back","车灯吞没画面，林夏转身冲进雨夜。","这一次，我会找到他们。","alley_rain",86,"ready","premium_i2v"]
  ].map((s, i) => ({ id: `shot_0${i+1}`, order: i+1, title: s[0] as string, duration_sec: s[1] as number, shot_type: s[2] as string, shot_size: i % 3 === 0 ? "wide" : "medium_close_up", camera_motion: s[3] as string, action: s[4] as string, dialogue: s[5] as string, scene_id: s[6] as string, qa_score: s[7] as number, status: s[8] as string, route: s[9] as string, characters: i < 2 ? [] : i === 3 ? ["char_chenshu"] : ["char_linxia"], character_versions: [i > 5 ? "linxia_rain_v1" : "linxia_office_v1"], start_state: state(i > 5 ? "alley" : i > 3 ? "corridor" : "office", i < 2 ? "focused" : "tense", i > 5 ? ["photo"] : ["document"], i > 5 ? "night" : i > 3 ? "dusk" : "day", i > 5 ? "linxia_rain_v1" : "linxia_office_v1"), end_state: state(i > 5 ? "alley" : i > 3 ? "corridor" : "office", i > 4 ? "alarmed" : "shocked", i > 5 ? ["memory_card"] : ["photo"], i > 5 ? "night" : i > 3 ? "dusk" : "day", i > 5 ? "linxia_rain_v1" : "linxia_office_v1"), previous_shot_id: i ? `shot_0${i}` : undefined, allowed_changes: i === 4 || i === 6 ? ["location", "time_of_day", "appearance_version"] : [] })),
  generation_runs: [1,2,3,4].map(i => ({ id: `run_0${i}`, shot_id: `shot_0${i}`, status: "completed", progress: 100, cost: i < 3 ? .08 : .18, elapsed_sec: 10 + i, provider: "mock" })),
  qa_reports: [{ id: "qa_06", shot_id: "shot_06", hard_gate_passed: true, score: 72, metrics: { identity: .94, prompt_alignment: .78, temporal_stability: .52, motion: .61, aesthetics: .82 }, failures: ["flicker"], repair_strategy: "缩短镜头并降低动作幅度，切换 2.5D 路线", attempt: 1, needs_human_review: false }],
  cost_events: [{ amount: .08, category: "keyframe" }, { amount: .08, category: "keyframe" }, { amount: .18, category: "portrait" }, { amount: .18, category: "portrait" }, { amount: .72, category: "video" }]
};
