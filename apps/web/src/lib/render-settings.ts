export type ContainerFormat = "mp4" | "mov";
export type VideoCodec = "h264" | "hevc" | "prores";
export type AudioCodec = "aac" | "pcm";

export const VIDEO_PRESETS = [
  "ultrafast",
  "superfast",
  "veryfast",
  "faster",
  "fast",
  "medium",
  "slow",
  "slower",
  "veryslow",
] as const;

export function containerFromPath(
  path: string,
  fallback: ContainerFormat = "mp4",
): ContainerFormat {
  const match = path.trim().toLowerCase().match(/\.(mp4|mov)$/);
  if (match?.[1] === "mov") {
    return "mov";
  }
  if (match?.[1] === "mp4") {
    return "mp4";
  }
  return fallback;
}

export function replacePathExtension(
  path: string,
  container: ContainerFormat,
): string {
  const trimmed = path.trim().replace(/\\/g, "/");
  if (!trimmed) {
    return `output.${container}`;
  }

  const slash = trimmed.lastIndexOf("/");
  const dir = slash >= 0 ? trimmed.slice(0, slash + 1) : "";
  const filename = slash >= 0 ? trimmed.slice(slash + 1) : trimmed;
  const dot = filename.lastIndexOf(".");
  const basename = dot > 0 ? filename.slice(0, dot) : filename;
  return `${dir}${basename}.${container}`;
}

export function videoCodecLabel(codec: VideoCodec): string {
  switch (codec) {
    case "h264":
      return "H.264";
    case "hevc":
      return "HEVC";
    case "prores":
      return "ProRes";
  }
}

export function audioCodecLabel(codec: AudioCodec): string {
  return codec === "aac" ? "AAC" : "PCM";
}

export function parseOptionalInt(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return Math.trunc(parsed);
}

export function parseOptionalFloat(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parsePositiveInt(value: string, fallback: number): number {
  const parsed = parseOptionalInt(value);
  if (parsed == null || parsed <= 0) {
    return fallback;
  }
  return parsed;
}
