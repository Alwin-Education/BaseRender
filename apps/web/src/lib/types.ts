export type MediaReferenceStatus = "linked" | "empty" | "missing" | "unsupported";

export type MediaConfigResponse = {
  provider: string;
  allowed_prefix: string;
  default_media_prefix: string;
  default_output_prefix: string;
  default_output_path: string;
  default_container: string;
  default_width: number;
  default_height: number;
  default_fps: number;
  default_video_codec: string;
  default_video_bitrate: number;
  default_video_preset: string;
  default_video_faststart: boolean;
  default_audio_codec: string;
  default_audio_bitrate: number;
};

export type CloudMediaObjectPayload = {
  key: string;
  size: number;
  last_modified: string | null;
  etag: string | null;
};

export type MatchSuggestionPayload = {
  key: string;
  score: number;
};

export type MediaReferencePayload = {
  id: string | null;
  clip_name: string;
  track_path: string;
  reference_kind: string;
  target_url: string | null;
  normalized_url: string | null;
  status: MediaReferenceStatus;
  clip_count: number;
  suggestions: MatchSuggestionPayload[];
};

export type MediaObjectsResponse = {
  provider: string;
  prefix: string;
  objects: CloudMediaObjectPayload[];
  next_continuation_token: string | null;
  object_count: number;
  truncated: boolean;
};

export type MediaLinkingRequest = {
  prefix?: string;
  timeline_path?: string | null;
  otio_content_base64?: string | null;
  continuation_token?: string | null;
  max_keys?: number | null;
  suggestion_limit?: number;
  min_score?: number;
};

export type MediaLinkingResponse = MediaObjectsResponse & {
  references: MediaReferencePayload[];
};

export type RenderSettingsPayload = {
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  audio_sample_rate?: number;
  audio_channel_layout?: string;
  clip_luts?: Record<string, string>;
  video_codec?: string;
  video_bitrate?: number;
  video_encoder_preset?: string;
  video_faststart?: boolean;
  audio_codec?: string;
  audio_bitrate?: number;
  video_crf?: number | null;
};

export type RenderLutFile = {
  id: string;
  name: string;
  content_base64: string;
};

export type RenderJobCreate = {
  input_path?: string | null;
  output_path: string;
  settings?: RenderSettingsPayload;
  track_index?: number | null;
  dry_run?: boolean;
  overwrite?: boolean;
  fail_fast?: boolean;
  otio_content_base64?: string | null;
  media_references?: MediaReferencePayload[];
  media_assignments?: Record<string, string>;
  lut_files?: RenderLutFile[];
  lut_assignments?: Record<string, string>;
};

export type RenderOutput = {
  path: string;
  key?: string | null;
  size?: number | null;
};

export type RenderOutputUrlResponse = {
  url: string;
};

export type RenderJobError = {
  message: string;
  detail?: string | null;
};

export type RenderProgress = {
  percent: number;
  elapsed_seconds: number;
  eta_seconds?: number | null;
  out_time_seconds?: number | null;
  frame?: number | null;
  fps?: number | null;
  speed?: number | null;
  phase?: "encoding" | "uploading" | null;
};

export type RenderJobStatus = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  job: RenderJobCreate;
  worker_payload: Record<string, unknown>;
  report: Record<string, unknown> | null;
  output?: RenderOutput | null;
  error?: RenderJobError | null;
  progress?: RenderProgress | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type TranscodeJobCreate = {
  inputs: string[];
  settings?: RenderSettingsPayload;
  container?: string;
  prepend_folder?: string | null;
  append_folder?: string | null;
  dry_run?: boolean;
};

export type TranscodeResultItem = {
  source_key: string;
  output_key: string;
  mediaconvert_job_id: string | null;
};

export type TranscodeResponse = {
  results: TranscodeResultItem[];
};
