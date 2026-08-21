export type NavKey = "project" | "story" | "assets" | "storyboard" | "review" | "delivery" | "operations";

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
export interface AuditLog { id: string; actor_id: string; action: string; target_type: string; target_id: string; created_at: string; detail: Record<string, unknown>; }
export interface OperationalMetrics {
  metric_source: string; shot_first_pass_rate: number | null; rework_rate: number;
  average_cost_per_completed_shot: number | null; completed_duration_sec: number;
  budget_used: number; budget_limit: number; unknown_task_count: number; stale_shot_count: number;
}
export interface Project {
  id: string; name: string; source_text: string; logline: string; style: string; aspect_ratio: string;
  target_duration_sec: number; workflow_status: string; workflow_step: number; episode_title: string;
  characters: Character[]; scenes: Scene[]; events: StoryEvent[]; shots: Shot[];
  generation_runs: GenerationRun[]; qa_reports: QAReport[]; cost_events: { amount: number; category: string }[];
  data_mode?: "demo" | "generated";
  agent_trace?: { node: string; status: string; output: string; model?: string }[];
  organization_id?: string; owner_id?: string; budget_limit?: number; version?: number;
  audit_logs?: AuditLog[];
}
