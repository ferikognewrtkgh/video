export type NavKey = "project" | "story" | "assets" | "storyboard" | "review" | "delivery";

export interface ContinuityState {
  location: string; emotion: string; holding: string[]; time_of_day: string; appearance_version?: string;
}
export interface Shot {
  id: string; order: number; title: string; scene_id: string; duration_sec: number; shot_type: string;
  shot_size: string; camera_motion: string; characters: string[]; character_versions: string[];
  start_state: ContinuityState; end_state: ContinuityState; action: string; dialogue: string;
  status: string; route?: string; qa_score?: number; previous_shot_id?: string; allowed_changes: string[];
}
export interface Appearance { id: string; name: string; outfit: string; palette: string[]; locked: boolean; prompt_fragment: string; }
export interface Character { id: string; name: string; role: string; identity: string; voice: string; locked: boolean; accent_color: string; appearances: Appearance[]; }
export interface Scene { id: string; name: string; location: string; time_of_day: string; style: string; locked: boolean; }
export interface StoryEvent { id: string; title: string; summary: string; tension: number; character_ids: string[]; leads_to: string[]; }
export interface QAMetrics { identity: number; prompt_alignment: number; temporal_stability: number; motion: number; aesthetics: number; }
export interface QAReport { id: string; shot_id: string; hard_gate_passed: boolean; score: number; metrics: QAMetrics; failures: string[]; repair_strategy?: string; attempt: number; needs_human_review: boolean; }
export interface GenerationRun { id: string; shot_id: string; status: string; progress: number; cost: number; elapsed_sec: number; provider: string; }
export interface Project {
  id: string; name: string; source_text: string; logline: string; style: string; aspect_ratio: string;
  target_duration_sec: number; workflow_status: string; workflow_step: number; episode_title: string;
  characters: Character[]; scenes: Scene[]; events: StoryEvent[]; shots: Shot[];
  generation_runs: GenerationRun[]; qa_reports: QAReport[]; cost_events: { amount: number; category: string }[];
}

