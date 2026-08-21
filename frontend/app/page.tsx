"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity, Archive, ArrowLeft, ArrowRight, AudioLines, BookOpen, Box, Check,
  ChevronDown, ChevronRight, CircleDollarSign, Clapperboard, Clock3, Cloud,
  Download, Film, Gauge, GitBranch, Grid2X2, Image as ImageIcon, Layers3, LayoutDashboard,
  Lock, Menu, MessageSquareText, MoreHorizontal, Pause, Play, Plus, RefreshCw,
  Search, Settings2, ShieldCheck, Sparkles, TimerReset, Upload, UserRound, WandSparkles,
  X, Zap,
} from "lucide-react";
import { demoProject } from "@/lib/demo-data";
import { NavKey, Project, Shot } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nav: { key: NavKey; label: string; icon: typeof Film }[] = [
  { key: "project", label: "项目中心", icon: LayoutDashboard },
  { key: "story", label: "剧情改编", icon: GitBranch },
  { key: "assets", label: "资产圣经", icon: Box },
  { key: "storyboard", label: "分镜工作台", icon: Clapperboard },
  { key: "review", label: "生成与审核", icon: WandSparkles },
  { key: "delivery", label: "质量与交付", icon: Gauge },
];

function cn(...values: (string | false | undefined)[]) { return values.filter(Boolean).join(" "); }
function money(n: number) { return `¥${n.toFixed(2)}`; }
function statusText(status: string) { return ({ completed: "已完成", generating: "生成中", repairing: "修复中", ready: "待生成", running: "生产中", paused: "已暂停" }[status] || status); }

export default function Studio() {
  const [page, setPage] = useState<NavKey>("storyboard");
  const [project, setProject] = useState<Project>(demoProject);
  const [selected, setSelected] = useState("shot_03");
  const [apiOnline, setApiOnline] = useState(false);
  const [notice, setNotice] = useState("");
  const [sidebar, setSidebar] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/projects/project_afterimage`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => { setProject(data); setApiOnline(true); })
      .catch(() => setApiOnline(false));
  }, []);

  const shot = project.shots.find(s => s.id === selected) || project.shots[0];
  const done = project.shots.filter(s => s.status === "completed").length;
  const totalCost = project.cost_events.reduce((sum, c) => sum + c.amount, 0);
  const flash = (message: string) => { setNotice(message); setTimeout(() => setNotice(""), 2600); };

  async function workflow(command: string) {
    setBusy(true);
    try {
      if (apiOnline) {
        const r = await fetch(`${API}/api/projects/${project.id}/workflow`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command }) });
        if (!r.ok) throw new Error(await r.text());
        setProject(await r.json());
      } else {
        setProject(p => ({ ...p, workflow_status: command === "pause" ? "paused" : command === "cancel" ? "cancelled" : "running" }));
      }
      flash(command === "pause" ? "生产工作流已安全暂停，检查点已保存" : "工作流已从最近检查点恢复");
    } catch { flash("当前状态无法执行此操作"); }
    setBusy(false);
  }

  async function regenerate() {
    setBusy(true);
    if (apiOnline) {
      try {
        const r = await fetch(`${API}/api/projects/${project.id}/shots/${shot.id}/generate`, { method: "POST" });
        if (!r.ok) throw new Error();
        setProject(p => ({ ...p, shots: p.shots.map(s => s.id === shot.id ? { ...s, status: "generating" } : s) }));
        flash(`${shot.id.toUpperCase()} 已进入队列，仅重跑当前镜头`);
      } catch { flash("连续性门禁未通过，请先解决冲突"); }
    } else {
      setProject(p => ({ ...p, shots: p.shots.map(s => s.id === shot.id ? { ...s, status: "generating" } : s) }));
      flash("演示模式：镜头已进入 Mock Provider 队列");
    }
    setBusy(false);
  }

  return (
    <main className="app-shell">
      {notice && <div className="toast"><Check size={16}/>{notice}</div>}
      <aside className={cn("side-nav", !sidebar && "side-nav--closed")}>
        <div className="brand"><div className="brand-mark"><span>M</span></div><div><b>MangaFlow</b><small>STUDIO</small></div></div>
        <button className="new-project"><Plus size={16}/> 新建作品</button>
        <div className="nav-label">创作空间</div>
        <nav>{nav.map(item => <button key={item.key} onClick={() => setPage(item.key)} className={cn("nav-item", page === item.key && "active")}><item.icon size={18}/><span>{item.label}</span>{item.key === "review" && <i>3</i>}</button>)}</nav>
        <div className="side-project">
          <div className="project-cover mini-frame frame-office"><span>AFTER<br/>IMAGE</span></div>
          <div><b>余像</b><small>{done}/{project.shots.length} 镜头 · {money(totalCost)}</small></div>
          <MoreHorizontal size={16}/>
        </div>
        <div className="side-bottom"><button><Archive size={17}/><span>素材仓库</span></button><button><Settings2 size={17}/><span>系统设置</span></button></div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <div className="topbar-left"><button className="icon-btn menu-btn" onClick={() => setSidebar(!sidebar)}><Menu size={19}/></button><span className="crumb">作品</span><ChevronRight size={14}/><b>{project.name}</b><span className="episode-chip">{project.episode_title}</span></div>
          <div className="topbar-actions">
            <div className={cn("connection", apiOnline && "online")}><i/>{apiOnline ? "API 已连接" : "演示模式"}</div>
            <button className="icon-btn"><Search size={18}/></button>
            <button className="outline-btn"><Cloud size={16}/> 保存于 2 分钟前</button>
            <button disabled={busy} className="primary-btn" onClick={() => workflow(project.workflow_status === "paused" ? "resume" : "pause")}>{project.workflow_status === "paused" ? <Play size={15}/> : <Pause size={15}/>} {project.workflow_status === "paused" ? "继续生产" : "暂停生产"}</button>
            <div className="avatar">VX</div>
          </div>
        </header>

        {page === "project" && <ProjectCenter project={project} setPage={setPage} />}
        {page === "story" && <StoryDesk project={project} />}
        {page === "assets" && <AssetBible project={project} flash={flash} />}
        {page === "storyboard" && <Storyboard project={project} shot={shot} setSelected={setSelected} regenerate={regenerate} busy={busy} />}
        {page === "review" && <ReviewDesk project={project} shot={shot} setSelected={setSelected} regenerate={regenerate} />}
        {page === "delivery" && <Delivery project={project} flash={flash} />}
      </section>
    </main>
  );
}

function SectionHead({ eyebrow, title, copy, children }: { eyebrow: string; title: string; copy: string; children?: React.ReactNode }) {
  return <div className="section-head"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div><div className="section-actions">{children}</div></div>;
}

function ProjectCenter({ project, setPage }: { project: Project; setPage: (p: NavKey) => void }) {
  const total = project.cost_events.reduce((a,c)=>a+c.amount,0);
  const done = project.shots.filter(s=>s.status==="completed").length;
  const avg = Math.round(project.shots.reduce((a,s)=>a+(s.qa_score||0),0)/project.shots.length);
  return <div className="page scroll-page">
    <SectionHead eyebrow="PROJECT OVERVIEW" title="一切从故事开始" copy="生产进度、质量与预算，在同一个视图里保持清晰。"><button className="outline-btn"><Upload size={16}/> 导入小说</button><button className="primary-btn" onClick={()=>setPage("story")}><Sparkles size={16}/> 新建改编</button></SectionHead>
    <div className="hero-project">
      <div className="hero-poster frame-rain"><div className="rain-lines"/><span className="poster-kicker">A MANGAFLOW ORIGINAL</span><strong>余<br/>像</strong><small>AFTERIMAGE</small></div>
      <div className="hero-info"><div className="hero-meta"><span>正在制作</span><span>9:16 竖屏</span><span>青年悬疑</span></div><h2>{project.episode_title}</h2><p>{project.logline}</p><div className="progress-wrap"><div><span>总进度</span><b>{Math.round(done/project.shots.length*100)}%</b></div><div className="progress"><i style={{width:`${done/project.shots.length*100}%`}}/></div><small>{done} 个镜头完成 · 1 个正在修复 · 预计还需 18 分钟</small></div><div className="hero-buttons"><button className="primary-btn" onClick={()=>setPage("storyboard")}><Clapperboard size={16}/> 继续制作</button><button className="outline-btn"><MoreHorizontal size={17}/></button></div></div>
    </div>
    <div className="metrics-row"><Metric icon={Film} label="镜头进度" value={`${done} / ${project.shots.length}`} sub="4 个已通过质检" color="coral"/><Metric icon={Gauge} label="平均质量分" value={`${avg}`} sub="目标 ≥ 75" color="teal"/><Metric icon={CircleDollarSign} label="累计成本" value={money(total)} sub="预算 ¥8.00" color="yellow"/><Metric icon={Clock3} label="成片时长" value={`${project.shots.reduce((a,s)=>a+s.duration_sec,0)}s`} sub="目标 45–60 秒" color="blue"/></div>
    <div className="two-column"><div className="panel"><PanelTitle title="生产流水线" extra="查看全部"/><div className="pipeline">{["叙事规划","资产锁定","关键帧","镜头生成","质量门禁","成片交付"].map((s,i)=><div className={cn("pipe-step",i<3&&"done",i===3&&"current")} key={s}><i>{i<3?<Check size={14}/>:i+1}</i><span>{s}<small>{i<3?"已完成":i===3?"4 / 8 处理中":"等待上游"}</small></span>{i<5&&<ChevronRight size={15}/>}</div>)}</div></div><div className="panel"><PanelTitle title="需要你的关注" extra="3 项"/><div className="attention"><Attention color="coral" title="SHOT 06 · 时序闪烁" copy="自动修复已完成，等待重新审核"/><Attention color="yellow" title="雨夜服装尚未锁定" copy="影响 SHOT 07–08 的身份一致性"/><Attention color="teal" title="关键帧候选已就绪" copy="SHOT 07 有 3 个候选等待选择"/></div></div></div>
  </div>;
}

function Metric({icon:Icon,label,value,sub,color}:{icon:typeof Film;label:string;value:string;sub:string;color:string}) { return <div className="metric"><div className={`metric-icon ${color}`}><Icon size={19}/></div><div><small>{label}</small><strong>{value}</strong><span>{sub}</span></div></div>; }
function PanelTitle({title,extra}:{title:string;extra:string}) { return <div className="panel-title"><h3>{title}</h3><button>{extra}<ChevronRight size={14}/></button></div>; }
function Attention({color,title,copy}:{color:string;title:string;copy:string}) { return <div className="attention-item"><i className={color}/><div><b>{title}</b><span>{copy}</span></div><ChevronRight size={16}/></div>; }

function StoryDesk({ project }: { project: Project }) {
  return <div className="page scroll-page"><SectionHead eyebrow="ADAPTATION AGENT" title="剧情改编台" copy="将长文本压缩为可验证的事件图谱，而不是一串失去因果的摘要。"><button className="outline-btn"><BookOpen size={16}/> 查看原文</button><button className="primary-btn"><Sparkles size={16}/> 重新分析</button></SectionHead>
    <div className="story-grid"><div className="panel source-panel"><div className="source-head"><span>输入文本</span><small>1,286 字</small></div><h3>序章：雨停之前</h3><p>雨是从傍晚开始的。林夏独自留在旧报社整理三年前的卷宗时，一封没有邮戳的信从门缝下滑了进来。</p><p>信里只有一张照片。照片上的男人叫陈述——三年前那场大火后，所有人都说他已经死了。</p><blockquote>“今晚九点，后巷。带上你仍然相信真相的那部分。”</blockquote><div className="agent-note"><Sparkles size={15}/><span>Adaptation Agent 识别出 3 个关键事件、2 位主要角色和 1 个悬念钩子。</span></div></div>
    <div className="event-canvas"><div className="canvas-toolbar"><b>事件图谱</b><div><button><Grid2X2 size={15}/></button><button><Plus size={15}/></button><button>100%</button></div></div><div className="event-flow">{project.events.map((event,i)=><div key={event.id} className="event-row"><div className="event-node"><div className="event-top"><span>EVENT 0{i+1}</span><i style={{background:`hsl(${160-event.tension},70%,60%)`}}>张力 {event.tension}</i></div><h3>{event.title}</h3><p>{event.summary}</p><div className="event-people">{event.character_ids.map(id=><span key={id}>{id.includes("linxia")?"林":"陈"}</span>)}</div></div>{i<project.events.length-1&&<div className="event-link"><ArrowRight size={19}/><small>{i===0?"照片触发":"警告升级"}</small></div>}</div>)}</div></div></div>
    <div className="panel episode-plan"><PanelTitle title="分集节奏" extra="已自动保存"/><div className="beat-line">{[{n:"01",t:"冷开场",d:"0–8s",c:32},{n:"02",t:"证人归来",d:"8–24s",c:58},{n:"03",t:"走廊追逐",d:"24–36s",c:76},{n:"04",t:"雨夜交付",d:"36–50s",c:94}].map(x=><div className="beat" key={x.n}><i style={{height:`${x.c}%`}}/><span>{x.n}</span><b>{x.t}</b><small>{x.d}</small></div>)}</div></div>
  </div>;
}

function AssetBible({ project, flash }: { project: Project; flash:(s:string)=>void }) {
  const [tab,setTab]=useState("角色");
  return <div className="page scroll-page"><SectionHead eyebrow="CONTINUITY SOURCE OF TRUTH" title="资产圣经" copy="锁定身份、服装、场景和声音，让每个镜头共享同一份事实。"><button className="outline-btn"><Plus size={16}/> 添加资产</button><button className="primary-btn" onClick={()=>flash("已锁定 6 项资产，后续生成将引用固定版本")}><Lock size={15}/> 锁定当前版本</button></SectionHead>
    <div className="asset-tabs">{["角色","场景","服装","道具","声音"].map(x=><button onClick={()=>setTab(x)} className={tab===x?"active":""} key={x}>{x}<span>{x==="角色"?2:x==="场景"?3:x==="服装"?3:1}</span></button>)}</div>
    {tab==="角色"?<div className="asset-grid">{project.characters.map((c,i)=><div className="asset-card" key={c.id}><div className={cn("character-art",i?"char-man":"char-woman")}><div className="art-grid"/><div className="silhouette"><i/><b/></div><span>{c.id.toUpperCase()}</span><button><MoreHorizontal size={17}/></button></div><div className="asset-body"><div className="asset-title"><div><h3>{c.name}</h3><p>{c.role}</p></div><span className={c.locked?"locked":"draft"}>{c.locked?<Lock size={11}/>:null}{c.locked?"已锁定":"草稿"}</span></div><p className="identity">{c.identity}</p><div className="appearance-list">{c.appearances.map(a=><div key={a.id}><div className="palette">{a.palette.map(p=><i key={p} style={{background:p}}/>)}</div><span>{a.name}</span><small>{a.outfit}</small></div>)}</div><div className="voice"><AudioLines size={15}/><span>{c.voice}</span><button><Play size={12}/></button></div></div></div>)}</div>:<div className="scene-grid">{project.scenes.map((s,i)=><div className="scene-card" key={s.id}><div className={cn("scene-art",i===0?"frame-office":i===1?"frame-corridor":"frame-rain")}><span>SCENE 0{i+1}</span></div><div><span className={s.locked?"locked":"draft"}>{s.locked?<Lock size={11}/>:null}{s.locked?"已锁定":"待确认"}</span><h3>{s.name}</h3><p>{s.style}</p><small>{s.time_of_day.toUpperCase()} · {s.location}</small></div></div>)}</div>}
  </div>;
}

function Storyboard({ project, shot, setSelected, regenerate, busy }: { project: Project; shot: Shot; setSelected:(id:string)=>void; regenerate:()=>void; busy:boolean }) {
  const [panel,setPanel]=useState("镜头参数");
  return <div className="workspace-page">
    <div className="workspace-title"><div><span className="eyebrow">DIRECTOR WORKSPACE</span><h1>分镜工作台 <small>{project.episode_title}</small></h1></div><div><button className="outline-btn"><Activity size={16}/> 连续性检查</button><button className="primary-btn" onClick={regenerate} disabled={busy}><Zap size={15}/> 生成当前镜头</button></div></div>
    <div className="studio-grid">
      <div className="shot-rail"><div className="rail-head"><b>镜头</b><span>{project.shots.length}</span><button><Plus size={15}/></button></div><div className="shot-list">{project.shots.map(s=><button key={s.id} onClick={()=>setSelected(s.id)} className={cn("shot-row",s.id===shot.id&&"active")}><div className={cn("shot-thumb", s.scene_id.includes("alley")?"frame-rain":s.scene_id.includes("corridor")?"frame-corridor":"frame-office")}><span>{String(s.order).padStart(2,"0")}</span><i className={s.status}/></div><div><b>{s.title}</b><span>{s.shot_size.replaceAll("_"," ")}</span><small>{s.duration_sec}s · {statusText(s.status)}</small></div><MoreHorizontal size={15}/></button>)}</div><button className="add-shot"><Plus size={15}/> 添加镜头</button></div>
      <div className="preview-stage"><div className="preview-top"><div><span>{shot.id.replace("shot_","SHOT ")}</span><b>{shot.title}</b></div><div><button><Grid2X2 size={15}/></button><button><MoreHorizontal size={16}/></button></div></div><div className={cn("phone-preview",shot.scene_id.includes("alley")?"frame-rain":shot.scene_id.includes("corridor")?"frame-corridor":"frame-office")}><div className="cinema-bars"/><div className="subject"><div className="subject-head"/><div className="subject-body"/></div><div className="manga-speedlines"/><div className="dialogue-caption">{shot.dialogue||shot.action}</div><button className="play-big"><Play fill="currentColor" size={21}/></button><div className="preview-badge">KEYFRAME · CANDIDATE A</div></div><div className="transport"><button><ArrowLeft size={18}/></button><button className="transport-play"><Play fill="currentColor" size={17}/></button><button><ArrowRight size={18}/></button><div className="timecode">00:00:00 / 00:00:{String(shot.duration_sec).padStart(2,"0")}</div><div className="transport-line"><i style={{width:"38%"}}/><b style={{left:"38%"}}/></div></div><div className="continuity-strip"><div className="strip-head"><span><GitBranch size={15}/> 连续性状态</span><b><ShieldCheck size={14}/> 无阻断冲突</b></div><div className="state-flow"><State label="开始状态" state={shot.start_state}/><ArrowRight size={17}/><div className="action-state"><span>ACTION</span><p>{shot.action}</p></div><ArrowRight size={17}/><State label="结束状态" state={shot.end_state}/></div></div></div>
      <div className="inspector"><div className="inspector-tabs">{["镜头参数","连续性"].map(t=><button className={panel===t?"active":""} onClick={()=>setPanel(t)} key={t}>{t}</button>)}</div>{panel==="镜头参数"?<div className="inspector-body"><InspectorSection title="画面设计"><Field label="景别" value={shot.shot_size.replaceAll("_"," ")}/><Field label="运镜" value={shot.camera_motion.replaceAll("_"," ")}/><Field label="时长" value={`${shot.duration_sec}.0 秒`}/></InspectorSection><InspectorSection title="生成路线"><div className="route-card"><div className="route-icon"><Layers3 size={18}/></div><div><b>{routeLabel(shot.route)}</b><span>{routeCopy(shot.route)}</span></div><ChevronDown size={15}/></div><div className="route-facts"><span>身份优先<b>0.95</b></span><span>质量优先<b>0.80</b></span><span>预计成本<b>{shot.route?.includes("premium")?"¥2.70":"¥0.18"}</b></span></div></InspectorSection><InspectorSection title="角色与版本">{shot.character_versions.map(v=><div className="ref-item" key={v}><div className="ref-avatar">林</div><div><b>{v.includes("rain")?"林夏 · 雨夜装":"林夏 · 办公室装"}</b><span>{v}</span></div><Lock size={13}/></div>)}</InspectorSection><InspectorSection title="对白"><textarea defaultValue={shot.dialogue||"无对白 · 使用旁白轨道"}/><div className="voice-row"><AudioLines size={15}/><span>林夏 / 冷静叙事</span><button><Play size={12}/></button></div></InspectorSection></div>:<ContinuityPanel shot={shot}/>}<div className="inspector-footer"><button className="outline-btn" onClick={regenerate}><RefreshCw size={15}/> 单镜重试</button><button className="primary-btn"><Check size={15}/> 确认关键帧</button></div></div>
    </div>
  </div>;
}

function State({label,state}:{label:string;state:Shot["start_state"]}) { return <div className="state-card"><span>{label}</span><div><i/><b>{state.location}</b></div><div><i/><b>{state.emotion}</b></div><div><i/><b>{state.holding.join("、")||"无道具"}</b></div></div>; }
function Field({label,value}:{label:string;value:string}) { return <label className="field"><span>{label}</span><button>{value}<ChevronDown size={14}/></button></label>; }
function InspectorSection({title,children}:{title:string;children:React.ReactNode}) { return <div className="inspector-section"><div className="inspector-section-title">{title}<ChevronDown size={14}/></div>{children}</div>; }
function routeLabel(r?:string) { return r==="portrait_drive"?"肖像 / 口型驱动":r==="2.5d_parallax"?"2.5D 景深运镜":r==="premium_i2v"?"高质量 I2V · 3 候选":r==="t2v"?"文本生成视频":"标准 I2V"; }
function routeCopy(r?:string) { return r==="portrait_drive"?"身份稳定 · 低成本":r==="2.5d_parallax"?"关键帧稳定 · 最低成本":r==="premium_i2v"?"复杂动作 · 人工选择":"轻动作 · 锁定身份"; }
function ContinuityPanel({shot}:{shot:Shot}) { return <div className="inspector-body continuity-panel"><div className="gate-ok"><ShieldCheck size={22}/><div><b>连续性门禁通过</b><span>4 项硬约束已验证</span></div></div>{[["场景",shot.start_state.location],["时间",shot.start_state.time_of_day],["服装版本",shot.start_state.appearance_version||"—"],["手持道具",shot.start_state.holding.join("、")||"无"]].map(x=><div className="constraint" key={x[0]}><span>{x[0]}</span><b>{x[1]}</b><Check size={14}/></div>)}<div className="preserve"><span>MUST PRESERVE</span><p>identity · outfit · prop_state</p></div></div>; }

function ReviewDesk({project,shot,setSelected,regenerate}:{project:Project;shot:Shot;setSelected:(id:string)=>void;regenerate:()=>void}) {
  const [candidate,setCandidate]=useState(0);
  return <div className="page scroll-page"><SectionHead eyebrow="GENERATION & HUMAN REVIEW" title="生成与审核台" copy="先选对关键帧，再为昂贵的视频生成买单。"><button className="outline-btn"><TimerReset size={16}/> 任务中心</button><button className="primary-btn" onClick={regenerate}><RefreshCw size={15}/> 重试当前镜头</button></SectionHead><div className="review-layout"><div className="review-shots panel"><div className="panel-title"><h3>待审核镜头</h3><span>3</span></div>{project.shots.filter(s=>["repairing","ready","generating"].includes(s.status)).map(s=><button className={s.id===shot.id?"active":""} onClick={()=>setSelected(s.id)} key={s.id}><div className={cn("review-thumb",s.scene_id.includes("alley")?"frame-rain":"frame-corridor")}/><div><b>{s.id.toUpperCase()}</b><span>{s.title}</span><small>{statusText(s.status)}</small></div><ChevronRight size={15}/></button>)}</div><div className="candidate-area panel"><div className="candidate-head"><div><span>{shot.id.toUpperCase()}</span><h2>{shot.title}</h2></div><div className="candidate-switch"><button>A / B 对比</button><button><Grid2X2 size={15}/></button></div></div><div className="candidates">{[0,1,2].map(i=><button onClick={()=>setCandidate(i)} className={cn("candidate",candidate===i&&"selected",i===2&&"frame-rain",i!==2&&"frame-office")} key={i}><span>CANDIDATE {String.fromCharCode(65+i)}</span>{candidate===i&&<i><Check size={15}/></i>}<div className="candidate-person"/><small>{i===0?"身份 94 · 构图 88":i===1?"身份 89 · 构图 92":"身份 91 · 构图 84"}</small></button>)}</div><div className="review-actions"><button className="danger-text"><X size={15}/> 全部拒绝</button><div><button className="outline-btn"><RefreshCw size={15}/> 再生成一组</button><button className="primary-btn"><Check size={15}/> 选择候选 {String.fromCharCode(65+candidate)}</button></div></div></div></div><div className="generation-queue panel"><PanelTitle title="异步生成队列" extra="Mock Provider"/><div className="queue-row"><div className="queue-icon"><Film size={17}/></div><div><b>SHOT 05 · 标准 I2V</b><span>生成视频帧 68 / 120</span><div className="progress"><i style={{width:"57%"}}/></div></div><strong>57%</strong><button><Pause size={14}/></button></div><div className="queue-row"><div className="queue-icon teal"><ImageIcon size={17}/></div><div><b>SHOT 07 · 关键帧候选</b><span>队列中 · 幂等键已保存</span><div className="progress"><i style={{width:"8%"}}/></div></div><strong>排队</strong><button><X size={14}/></button></div></div></div>;
}

function Delivery({project,flash}:{project:Project;flash:(s:string)=>void}) {
  const qa=project.qa_reports[project.qa_reports.length-1];
  const metrics=qa?.metrics||{identity:.91,prompt_alignment:.84,temporal_stability:.78,motion:.76,aesthetics:.86};
  return <div className="page scroll-page"><SectionHead eyebrow="QUALITY GATE & DELIVERY" title="质量与交付台" copy="硬门禁、软评分和每一次修复都有据可查。"><button className="outline-btn"><Activity size={16}/> 运行全片质检</button><button className="primary-btn" onClick={()=>flash("导出任务已创建：1080 × 1920 / H.264 / AAC")}><Download size={16}/> 导出成片</button></SectionHead><div className="quality-hero"><div className="score-ring" style={{"--score":`${qa?.score||82}` } as React.CSSProperties}><div><strong>{qa?.score||82}</strong><span>综合质量分</span></div></div><div className="quality-summary"><span className="passed"><ShieldCheck size={15}/> 硬门禁通过</span><h2>整体质量良好，1 个镜头建议修复</h2><p>角色身份与构图保持稳定；SHOT 06 在黑场转场中检测到时序闪烁，已生成定向修复方案。</p><div className="quality-bars">{Object.entries(metrics).map(([k,v])=><div key={k}><span>{({identity:"身份一致",prompt_alignment:"动作对齐",temporal_stability:"时序稳定",motion:"运动合理",aesthetics:"构图美学"} as Record<string,string>)[k]}<b>{Math.round(v*100)}</b></span><div><i style={{width:`${v*100}%`}}/></div></div>)}</div></div><div className="delivery-card"><span>DELIVERY PRESET</span><h3>竖屏漫剧 · 高清</h3><div><span>画面<b>1080 × 1920</b></span><span>帧率<b>24 FPS</b></span><span>时长<b>50 秒</b></span><span>编码<b>H.264 / AAC</b></span></div><button className="primary-btn" onClick={()=>flash("正在准备 MP4、SRT 和生产追溯报告")}><Download size={16}/> 导出 MP4 + SRT</button></div></div><div className="two-column quality-columns"><div className="panel"><PanelTitle title="失败与修复记录" extra="2 条"/><div className="repair-log"><div><i className="warning"><Zap size={15}/></i><div><span>SHOT 06 · FLICKER</span><b>时序闪烁严重</b><p>策略：缩短镜头、降低动作幅度，并切换为 2.5D 路线。</p><small>自动修复 1/2 · 等待复检</small></div><button>查看对比</button></div><div><i className="success"><Check size={15}/></i><div><span>SHOT 03 · IDENTITY DRIFT</span><b>角色身份漂移 · 已解决</b><p>提高主参考图权重，重新生成关键帧后通过。</p><small>修复前 68 → 修复后 83</small></div><button>查看记录</button></div></div></div><div className="panel"><PanelTitle title="成本追溯" extra="预算 ¥8.00"/><div className="cost-total"><div><small>本集累计</small><strong>{money(project.cost_events.reduce((a,c)=>a+c.amount,0))}</strong></div><span>预算使用 15%</span></div><div className="cost-bars">{[["关键帧",.16,"#ff7058"],["肖像驱动",.36,"#4cc9c0"],["视频生成",.72,"#f1bc5b"],["语音 / 合成",.08,"#8194ff"]].map(x=><div key={x[0] as string}><span>{x[0]}<b>{money(x[1] as number)}</b></span><div><i style={{width:`${(x[1] as number)/.72*100}%`,background:x[2] as string}}/></div></div>)}</div></div></div></div>;
}

