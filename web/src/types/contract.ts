// AUTO-GENERATED from contracts/openapi.yaml — do not edit.
// Regenerate: make contract-ts

export type Confidence = "low" | "medium" | "high";
export type ErrorCode = "insufficient_data" | "invalid_request" | "source_not_allowed" | "not_found" | "upstream_unavailable";
export type Factor = "patch_strength" | "counter_matchup" | "player_comfort" | "first_pick";
export type NoteKind = "ward" | "timing" | "lane" | "smoke" | "combat" | "resource" | "communication";
export type Recommendation = "pick" | "ban" | "leave_and_counter" | "insufficient_data";
/** 仅 Playbook 语义字段 */
export type Side = "us" | "them";
/** 0=Radiant, 1=Dire；仅数值字段 */
export type Team = 0 | 1;
export type TheirOpening = "teamfight" | "push" | "pickoff" | "splitpush" | "protect" | "initiate" | "unknown";
export type UnavailableReason = "needs_replay" | "insufficient_samples" | "source_not_allowed" | "stat_unavailable";

/** 资源名 → 契约组件名 */
export type Resource = 'Value' | 'Policy' | 'Playbook' | 'Profile' | 'Advise';
