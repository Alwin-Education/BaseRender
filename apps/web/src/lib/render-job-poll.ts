import type { RenderJobStatus } from "@/lib/types";

export function isTerminalJobStatus(status: RenderJobStatus["status"]): boolean {
  return status === "failed" || status === "succeeded";
}

export function shouldApplyPollUpdate(
  local: RenderJobStatus | null,
  remote: RenderJobStatus,
): boolean {
  if (!local || local.id !== remote.id) {
    return true;
  }
  if (!isTerminalJobStatus(local.status) && isTerminalJobStatus(remote.status)) {
    return true;
  }
  if (isTerminalJobStatus(local.status) && !isTerminalJobStatus(remote.status)) {
    return true;
  }
  if (isTerminalJobStatus(remote.status) && isTerminalJobStatus(local.status)) {
    if (local.updated_at && remote.updated_at) {
      return new Date(remote.updated_at) >= new Date(local.updated_at);
    }
    return true;
  }
  if (local.updated_at && remote.updated_at) {
    return new Date(remote.updated_at) >= new Date(local.updated_at);
  }
  return true;
}

export function jobRemovedWhileActive(
  local: RenderJobStatus | null,
): RenderJobStatus | null {
  if (!local || !["queued", "running"].includes(local.status)) {
    return local;
  }
  return {
    ...local,
    status: "failed",
    error: { message: "Render cancelled." },
    progress: null,
  };
}

export function canOpenRenderOutput(job: RenderJobStatus | null): boolean {
  return (
    job?.status === "succeeded" &&
    job.output != null &&
    Boolean(job.output.path?.trim())
  );
}

export function activeJobConflictMessage(job: RenderJobStatus | null): string {
  if (!job) {
    return "Another render job is already active.";
  }
  return `A render job is still active (status: ${job.status}). Cancel it in the job panel or wait for it to finish.`;
}
