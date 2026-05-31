import type { ContainerFormat } from "@/lib/render-settings";

function normalizeFolder(folder: string | null | undefined): string | null {
  if (folder == null) {
    return null;
  }
  const normalized = folder.trim().replace(/^\/+|\/+$/g, "");
  return normalized || null;
}

export function buildTranscodeOutputKey(
  sourceKey: string,
  {
    container,
    prependFolder,
    appendFolder,
  }: {
    container: ContainerFormat | string;
    prependFolder?: string | null;
    appendFolder?: string | null;
  },
): string {
  const normalizedKey = sourceKey.trim().replace(/^\/+|\/+$/g, "");
  if (!normalizedKey) {
    throw new Error("source_key must not be empty.");
  }

  const lastSlash = normalizedKey.lastIndexOf("/");
  const directory = lastSlash >= 0 ? normalizedKey.slice(0, lastSlash) : "";
  const filename = lastSlash >= 0 ? normalizedKey.slice(lastSlash + 1) : normalizedKey;
  const dot = filename.lastIndexOf(".");
  const stem = dot > 0 ? filename.slice(0, dot) : filename;
  const suffix = container.toString().trim().toLowerCase().replace(/^\./, "");
  if (!suffix) {
    throw new Error("container must not be empty.");
  }

  const parts: string[] = [];
  const prepend = normalizeFolder(prependFolder);
  const append = normalizeFolder(appendFolder);
  if (prepend) {
    parts.push(prepend);
  }
  if (directory) {
    parts.push(directory);
  }
  if (append) {
    parts.push(append);
  }
  parts.push(stem);
  return `${parts.join("/")}.${suffix}`;
}

export function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  if (size < 1024 * 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function formatModified(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}
