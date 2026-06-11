"use client";

import { useEffect, useRef, useState } from "react";
import { XIcon } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AppNav } from "@/components/app-nav";
import { MediaAssignmentPanel } from "@/components/media-assignment-panel";
import { EncodingSettings } from "@/components/encoding-settings";
import { OtioFilePicker } from "@/components/otio-file-picker";
import type { ClientLut } from "@/components/lut-assignment-control";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  cancelCurrentRenderJob,
  createMediaLinking,
  createRenderJob,
  getCurrentRenderJob,
  getMediaConfig,
  getRenderOutputUrl,
} from "@/lib/api";
import {
  mergeLutAssignments,
  mergeMediaAssignments,
  referenceId,
  unlinkedMediaWarningMessage,
} from "@/lib/media-assignment";
import {
  activeJobConflictMessage,
  canOpenRenderOutput,
  isTerminalJobStatus,
  jobRemovedWhileActive,
  shouldApplyPollUpdate,
} from "@/lib/render-job-poll";
import {
  type AudioCodec,
  type ContainerFormat,
  type VideoCodec,
  containerFromPath,
  replacePathExtension,
} from "@/lib/render-settings";
import type {
  CloudMediaObjectPayload,
  MediaConfigResponse,
  MediaLinkingResponse,
  MediaObjectsResponse,
  MediaReferencePayload,
  RenderJobStatus,
} from "@/lib/types";

export function HomePage() {
  const [prefix, setPrefix] = useState("");
  const [objects, setObjects] = useState<CloudMediaObjectPayload[]>([]);
  const [mediaObjectCount, setMediaObjectCount] = useState(0);
  const [mediaListingPrefix, setMediaListingPrefix] = useState("");
  const [isMediaListingTruncated, setIsMediaListingTruncated] = useState(false);
  const [references, setReferences] = useState<MediaReferencePayload[]>([]);
  const [mediaAssignments, setMediaAssignments] = useState<Record<string, string>>({});
  const [lutAssignments, setLutAssignments] = useState<Record<string, string>>({});
  const [luts, setLuts] = useState<ClientLut[]>([]);
  const [otioFile, setOtioFile] = useState<File | null>(null);
  const [isLinking, setIsLinking] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [linkingError, setLinkingError] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [unlinkedDialogOpen, setUnlinkedDialogOpen] = useState(false);
  const [renderJob, setRenderJob] = useState<RenderJobStatus | null>(null);
  const [mediaConfig, setMediaConfig] = useState<MediaConfigResponse | null>(null);
  const [outputPath, setOutputPath] = useState("outputs/output.mp4");
  const [container, setContainer] = useState<ContainerFormat>("mp4");
  const [width, setWidth] = useState<number | null>(1920);
  const [height, setHeight] = useState<number | null>(1080);
  const [fps, setFps] = useState<number | null>(24);
  const [videoCodec, setVideoCodec] = useState<VideoCodec>("h264");
  const [videoBitrate, setVideoBitrate] = useState(8_000_000);
  const [videoPreset, setVideoPreset] = useState("faster");
  const [videoFaststart, setVideoFaststart] = useState(true);
  const [audioCodec, setAudioCodec] = useState<AudioCodec>("aac");
  const [audioBitrate, setAudioBitrate] = useState(192_000);
  const [dryRun] = useState(false);
  const lastRenderProgressRef = useRef<RenderJobStatus["progress"]>(null);

  useEffect(() => {
    if (renderJob?.progress) {
      lastRenderProgressRef.current = renderJob.progress;
    }
    if (!renderJob || !["queued", "running"].includes(renderJob.status)) {
      lastRenderProgressRef.current = null;
    }
  }, [renderJob?.id, renderJob?.status, renderJob?.progress]);

  useEffect(() => {
    getMediaConfig()
      .then((config) => {
        setMediaConfig(config);
        setPrefix(config.default_media_prefix);
        setOutputPath(config.default_output_path);
        setContainer(
          containerFromPath(
            config.default_output_path,
            config.default_container as ContainerFormat,
          ),
        );
        setWidth(config.default_width);
        setHeight(config.default_height);
        setFps(config.default_fps);
        setVideoCodec(config.default_video_codec as VideoCodec);
        setVideoBitrate(config.default_video_bitrate);
        setVideoPreset(config.default_video_preset);
        setVideoFaststart(config.default_video_faststart);
        setAudioCodec(config.default_audio_codec as AudioCodec);
        setAudioBitrate(config.default_audio_bitrate);
      })
      .catch((error) => setRenderError(errorMessage(error)));
  }, []);

  useEffect(() => {
    getCurrentRenderJob()
      .then((job) => {
        if (job) {
          setRenderJob(job);
        }
      })
      .catch((error) => setRenderError(errorMessage(error)));
  }, []);

  useEffect(() => {
    const jobId = renderJob?.id;
    const jobStatus = renderJob?.status;
    if (!jobId || !jobStatus) {
      return;
    }

    const shouldPollActive = ["queued", "running"].includes(jobStatus);
    const shouldPollTerminal =
      isTerminalJobStatus(jobStatus) &&
      renderJob.updated_at != null &&
      Date.now() - new Date(renderJob.updated_at).getTime() < 15_000;
    if (!shouldPollActive && !shouldPollTerminal) {
      return;
    }

    const interval = window.setInterval(async () => {
      try {
        const current = await getCurrentRenderJob();
        if (current) {
          setRenderJob((local) =>
            shouldApplyPollUpdate(local, current) ? current : local,
          );
          return;
        }
        setRenderJob((local) => jobRemovedWhileActive(local));
      } catch (error) {
        setRenderError(errorMessage(error));
      }
    }, 1000);

    return () => window.clearInterval(interval);
  }, [renderJob?.id, renderJob?.status, renderJob?.updated_at]);

  async function handleOtioFileChange(file: File | null) {
    setOtioFile(file);
    setReferences([]);
    setMediaAssignments({});
    setLutAssignments({});
    setObjects([]);
    setMediaObjectCount(0);
    setMediaListingPrefix("");
    setIsMediaListingTruncated(false);
    setLinkingError(null);
    setUnlinkedDialogOpen(false);

    if (!file) {
      return;
    }

    setIsLinking(true);

    try {
      const response = await linkMediaForFile(file, prefix);

      if (response.references.length === 0) {
        setReferences([]);
        setLinkingError("No media references were found in this OTIO file.");
        return;
      }

      setReferences(response.references);
      updateMediaListing(response);
      setMediaAssignments(defaultMediaAssignments(response.references));
      setLutAssignments(defaultLutAssignments(response.references));
    } catch (error) {
      setLinkingError(errorMessage(error));
    } finally {
      setIsLinking(false);
    }
  }

  async function linkMediaForFile(file: File, mediaPrefix: string): Promise<MediaLinkingResponse> {
    return createMediaLinking({
      otio_content_base64: await fileToBase64(file),
      prefix: mediaPrefix || undefined,
      max_keys: 1000,
      suggestion_limit: 5,
    });
  }

  function updateMediaListing(response: MediaObjectsResponse) {
    updateMediaListingState(
      response,
      setObjects,
      setMediaObjectCount,
      setMediaListingPrefix,
      setIsMediaListingTruncated,
    );
  }

  async function handleRefreshMediaObjects() {
    if (!otioFile) {
      return;
    }

    setIsLinking(true);
    setLinkingError(null);

    try {
      const response = await linkMediaForFile(otioFile, prefix);
      setReferences(response.references);
      updateMediaListing(response);
      setMediaAssignments((current) =>
        mergeMediaAssignments(response.references, current),
      );
      setLutAssignments((current) =>
        mergeLutAssignments(response.references, current),
      );
    } catch (error) {
      setLinkingError(errorMessage(error));
    } finally {
      setIsLinking(false);
    }
  }

  function handleSubmitRender() {
    if (!otioFile) {
      setRenderError("Choose an OTIO file before starting a render.");
      return;
    }

    if (unlinkedMediaWarningMessage(references, mediaAssignments)) {
      setUnlinkedDialogOpen(true);
      return;
    }

    void submitRenderJob();
  }

  async function submitRenderJob() {
    if (!otioFile) {
      return;
    }

    setUnlinkedDialogOpen(false);
    setIsSubmitting(true);
    setRenderError(null);

    try {
      const job = await createRenderJob({
        output_path: outputPath || "outputs/output.mp4",
        otio_content_base64: await fileToBase64(otioFile),
        media_references: references,
        media_assignments: mediaAssignments,
        lut_files: await Promise.all(
          luts.map(async (lut) => ({
            id: lut.id,
            name: lut.name,
            content_base64: await fileToBase64(lut.file),
          })),
        ),
        lut_assignments: lutAssignments,
        settings: {
          width,
          height,
          fps,
          video_codec: videoCodec,
          video_bitrate: videoBitrate,
          video_encoder_preset: videoPreset,
          video_faststart: videoFaststart,
          audio_codec: audioCodec,
          audio_bitrate: audioBitrate,
        },
        dry_run: dryRun,
      });
      setRenderJob(job);
    } catch (error) {
      const message = errorMessage(error);
      if (message.includes("already active")) {
        try {
          const current = await getCurrentRenderJob();
          if (current) {
            setRenderJob(current);
          }
          setRenderError(activeJobConflictMessage(current));
          return;
        } catch {
          // Fall back to the original API error message.
        }
      }
      setRenderError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCancelRender() {
    setIsCancelling(true);
    setRenderError(null);

    try {
      setRenderJob(await cancelCurrentRenderJob());
    } catch (error) {
      setRenderError(errorMessage(error));
    } finally {
      setIsCancelling(false);
    }
  }

  async function handleOpenOutput() {
    setRenderError(null);

    try {
      const current = await getCurrentRenderJob();
      if (!current || !canOpenRenderOutput(current)) {
        setRenderError("Render output is not ready yet.");
        return;
      }
      setRenderJob(current);
      const response = await getRenderOutputUrl(current.id);
      window.open(response.url, "_blank", "noopener,noreferrer");
    } catch (error) {
      setRenderError(errorMessage(error));
    }
  }

  const hasActiveJob = renderJob
    ? ["queued", "running"].includes(renderJob.status)
    : false;
  const settingsDisabled = hasActiveJob || isSubmitting;
  const unlinkedWarning = unlinkedMediaWarningMessage(references, mediaAssignments);

  function handleContainerChange(nextContainer: ContainerFormat) {
    setContainer(nextContainer);
    setOutputPath((current) => replacePathExtension(current, nextContainer));
    if (nextContainer === "mov") {
      setVideoFaststart(false);
    }
  }

  function handleVideoCodecChange(nextCodec: VideoCodec) {
    setVideoCodec(nextCodec);
    if (nextCodec === "prores") {
      setContainer("mov");
      setOutputPath((current) => replacePathExtension(current, "mov"));
      setVideoFaststart(false);
    }
  }

  function handleOutputPathChange(nextPath: string) {
    setOutputPath(nextPath);
    setContainer((current) => containerFromPath(nextPath, current));
  }

  return (
    <main className="min-h-screen bg-background px-4 py-8 text-foreground md:px-8">
      <section className="mx-auto flex w-fit min-w-full max-w-full flex-col gap-8 md:min-w-4xl">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <h1 className="font-sansation text-4xl font-bold tracking-tight">
            BaseRender
          </h1>
          <AppNav />
        </div>

        <div className="flex max-w-md flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="s3-prefix">Media prefix</Label>
            <div className="flex gap-2">
              <Input
                id="s3-prefix"
                value={prefix}
                onChange={(event) => setPrefix(event.target.value)}
                placeholder="projects/demo/day1/"
                disabled={isLinking}
              />
              {otioFile ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={isLinking}
                  onClick={handleRefreshMediaObjects}
                >
                  Refresh media
                </Button>
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground">
              {mediaObjectCount > 0 || mediaListingPrefix ? (
                <>
                  Showing {mediaObjectCount} media files from {mediaConfig?.provider ?? "media storage"}
                  {mediaListingPrefix ? ` under ${mediaListingPrefix}` : " at bucket root"}
                  {isMediaListingTruncated ? " (truncated at 10,000 objects)" : ""}
                </>
              ) : mediaConfig?.allowed_prefix ? (
                <>Media browsing is scoped to {mediaConfig.allowed_prefix}</>
              ) : (
                <>Media browsing starts at the bucket root.</>
              )}
            </p>
          </div>
          <OtioFilePicker
            file={otioFile}
            isLoading={isLinking}
            onFileChange={handleOtioFileChange}
          />
        </div>

        {linkingError ? (
          <Alert variant="destructive">
            <AlertDescription>{linkingError}</AlertDescription>
          </Alert>
        ) : null}

        {isLinking ? (
          <MediaAssignmentTableSkeleton />
        ) : null}

        {references.length > 0 ? (
          <>
            <MediaAssignmentPanel
              references={references}
              objects={objects}
              mediaAssignments={mediaAssignments}
              lutAssignments={lutAssignments}
              luts={luts}
              onMediaAssignmentChange={(id, value) =>
                setMediaAssignments((current) => ({ ...current, [id]: value }))
              }
              onLutAssignmentChange={(id, value) =>
                setLutAssignments((current) => ({ ...current, [id]: value }))
              }
              onLutUpload={(id, file) => {
                const lut = clientLut(file);
                setLuts((current) => [...current, lut]);
                setLutAssignments((current) => ({ ...current, [id]: lut.id }));
              }}
            />

            <section className="flex flex-col gap-4 rounded-lg p-4">
              <h2 className="text-xl font-medium text-foreground/90">Render</h2>
              <Accordion
                type="single"
                collapsible
                className="w-full max-w-3xl rounded-lg border bg-muted/30 px-4"
              >
                <AccordionItem value="settings" className="border-none">
                  <AccordionTrigger>Settings</AccordionTrigger>
                  <AccordionContent>
                    <EncodingSettings
                      disabled={settingsDisabled}
                      outputPath={outputPath}
                      onOutputPathChange={handleOutputPathChange}
                      container={container}
                      onContainerChange={handleContainerChange}
                      width={width}
                      onWidthChange={setWidth}
                      height={height}
                      onHeightChange={setHeight}
                      fps={fps}
                      onFpsChange={setFps}
                      videoCodec={videoCodec}
                      onVideoCodecChange={handleVideoCodecChange}
                      videoBitrate={videoBitrate}
                      onVideoBitrateChange={setVideoBitrate}
                      videoPreset={videoPreset}
                      onVideoPresetChange={setVideoPreset}
                      videoFaststart={videoFaststart}
                      onVideoFaststartChange={setVideoFaststart}
                      audioCodec={audioCodec}
                      onAudioCodecChange={setAudioCodec}
                      audioBitrate={audioBitrate}
                      onAudioBitrateChange={setAudioBitrate}
                    />
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
              <Button
                type="button"
                className="w-fit"
                disabled={settingsDisabled}
                onClick={handleSubmitRender}
              >
                {isSubmitting ? "Starting render..." : "Start render"}
              </Button>
            </section>
          </>
        ) : null}

        <Dialog open={unlinkedDialogOpen} onOpenChange={setUnlinkedDialogOpen}>
          <DialogContent showCloseButton={false}>
            <DialogHeader>
              <DialogTitle>Unlinked media</DialogTitle>
              <DialogDescription>{unlinkedWarning}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setUnlinkedDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                disabled={isSubmitting}
                onClick={() => void submitRenderJob()}
              >
                {isSubmitting ? "Starting render..." : "Render anyway"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {renderError ? (
          <Alert variant="destructive">
            <AlertDescription>{renderError}</AlertDescription>
          </Alert>
        ) : null}

        {renderJob ? (
          <section className="flex max-w-md flex-col gap-2 rounded-lg p-4">
            {renderJob.status === "queued" || renderJob.status === "running" ? (
              <div className="flex items-start gap-2">
                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  {renderJob.status === "running" ? (
                    (() => {
                      const progress =
                        renderJob.progress ?? lastRenderProgressRef.current;
                      return progress != null ? (
                      <>
                        <Progress
                          value={
                            isUploadingProgress(progress)
                              ? 100
                              : progress.percent
                          }
                        />
                        {isUploadingProgress(progress) ? (
                          <p className="text-sm text-muted-foreground">Uploading output…</p>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <Progress value={0} className="opacity-60" />
                        <p className="text-sm text-muted-foreground">Starting render…</p>
                      </>
                    );
                    })()
                  ) : (
                    <>
                      <Progress value={0} className="opacity-60" />
                      <p className="text-sm text-muted-foreground">Waiting for worker…</p>
                    </>
                  )}
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                  disabled={isCancelling}
                  aria-label="Cancel render"
                  onClick={() => void handleCancelRender()}
                >
                  <XIcon className="size-4" />
                </Button>
              </div>
            ) : null}
            {canOpenRenderOutput(renderJob) ? (
              <Button
                type="button"
                className="w-fit"
                variant="outline"
                onClick={handleOpenOutput}
              >
                Open latest render
              </Button>
            ) : null}
            {renderJob.error ? (
              <p className="text-sm text-destructive">{renderJob.error.message}</p>
            ) : null}
          </section>
        ) : null}
      </section>
    </main>
  );
}

function defaultMediaAssignments(
  references: MediaReferencePayload[],
): Record<string, string> {
  return Object.fromEntries(
    references.map((reference, index) => [
      referenceId(reference, index),
      reference.suggestions[0]?.key ?? "",
    ]),
  );
}

function updateMediaListingState(
  response: MediaObjectsResponse,
  setObjects: (objects: CloudMediaObjectPayload[]) => void,
  setMediaObjectCount: (count: number) => void,
  setMediaListingPrefix: (prefix: string) => void,
  setIsMediaListingTruncated: (truncated: boolean) => void,
) {
  setObjects(response.objects);
  setMediaObjectCount(response.object_count);
  setMediaListingPrefix(response.prefix);
  setIsMediaListingTruncated(response.truncated);
}

function defaultLutAssignments(
  references: MediaReferencePayload[],
): Record<string, string> {
  return Object.fromEntries(
    references.map((reference, index) => [referenceId(reference, index), "none"]),
  );
}

function clientLut(file: File): ClientLut {
  return {
    id: `${Date.now()}-${file.name}`,
    name: file.name,
    size: file.size,
    file,
  };
}

function MediaAssignmentTableSkeleton() {
  return (
    <Card className="overflow-visible ring-0">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-72" />
          </div>
          <Skeleton className="h-9 w-24" />
        </div>
      </CardHeader>
      <CardContent>
        <Table containerClassName="overflow-x-visible" className="w-auto min-w-full">
          <TableHeader>
            <TableRow>
              <TableHead>
                <Skeleton className="h-4 w-16" />
              </TableHead>
              <TableHead>
                <Skeleton className="h-4 w-24" />
              </TableHead>
              <TableHead>
                <Skeleton className="h-4 w-12" />
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Array.from({ length: 3 }, (_, index) => (
              <TableRow key={index}>
                <TableCell>
                  <Skeleton className="h-5 w-40" />
                </TableCell>
                <TableCell>
                  <Skeleton className="h-9 w-full min-w-[260px]" />
                </TableCell>
                <TableCell>
                  <Skeleton className="h-9 w-full min-w-[260px]" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return window.btoa(binary);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unknown error occurred.";
}

function isUploadingProgress(progress: {
  phase?: "encoding" | "uploading" | null;
}): boolean {
  return progress.phase === "uploading";
}
