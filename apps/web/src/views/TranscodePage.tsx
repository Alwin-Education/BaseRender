"use client";

import { useEffect, useMemo, useState } from "react";

import { AppNav } from "@/components/app-nav";
import { EncodingSettings } from "@/components/encoding-settings";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { createTranscodeJob, getMediaConfig, listMediaObjects } from "@/lib/api";
import {
  type AudioCodec,
  type ContainerFormat,
} from "@/lib/render-settings";
import {
  buildTranscodeOutputKey,
  formatBytes,
  formatModified,
} from "@/lib/transcode";
import type {
  CloudMediaObjectPayload,
  MediaConfigResponse,
  TranscodeResultItem,
} from "@/lib/types";

export function TranscodePage() {
  const [prefix, setPrefix] = useState("");
  const [objects, setObjects] = useState<CloudMediaObjectPayload[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [mediaObjectCount, setMediaObjectCount] = useState(0);
  const [mediaListingPrefix, setMediaListingPrefix] = useState("");
  const [isMediaListingTruncated, setIsMediaListingTruncated] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [results, setResults] = useState<TranscodeResultItem[] | null>(null);
  const [mediaConfig, setMediaConfig] = useState<MediaConfigResponse | null>(null);
  const [prependFolder, setPrependFolder] = useState("");
  const [appendFolder, setAppendFolder] = useState("");
  const [container, setContainer] = useState<ContainerFormat>("mp4");
  const [width, setWidth] = useState<number | null>(1920);
  const [height, setHeight] = useState<number | null>(1080);
  const [fps, setFps] = useState<number | null>(24);
  const [videoCodec, setVideoCodec] = useState<"h264" | "hevc" | "prores">("h264");
  const [videoBitrate, setVideoBitrate] = useState(8_000_000);
  const [videoPreset, setVideoPreset] = useState("faster");
  const [videoFaststart, setVideoFaststart] = useState(true);
  const [audioCodec, setAudioCodec] = useState<AudioCodec>("aac");
  const [audioBitrate, setAudioBitrate] = useState(192_000);

  useEffect(() => {
    getMediaConfig()
      .then((config) => {
        setMediaConfig(config);
        setPrefix(config.default_media_prefix);
        setContainer(config.default_container as ContainerFormat);
        setWidth(config.default_width);
        setHeight(config.default_height);
        setFps(config.default_fps);
        setVideoCodec(config.default_video_codec as "h264" | "hevc" | "prores");
        setVideoBitrate(config.default_video_bitrate);
        setVideoPreset(config.default_video_preset);
        setVideoFaststart(config.default_video_faststart);
        setAudioCodec(config.default_audio_codec as AudioCodec);
        setAudioBitrate(config.default_audio_bitrate);
      })
      .catch((error) => setLoadError(errorMessage(error)));
  }, []);

  const previewSourceKey = useMemo(() => {
    const selected = objects.filter((object) => selectedKeys.has(object.key));
    if (selected.length > 0) {
      return selected[0].key;
    }
    return objects[0]?.key ?? null;
  }, [objects, selectedKeys]);

  const previewOutputKey = useMemo(() => {
    if (!previewSourceKey) {
      return null;
    }
    try {
      return buildTranscodeOutputKey(previewSourceKey, {
        container,
        prependFolder: prependFolder || null,
        appendFolder: appendFolder || null,
      });
    } catch {
      return null;
    }
  }, [appendFolder, container, prependFolder, previewSourceKey]);

  const allSelected = objects.length > 0 && selectedKeys.size === objects.length;

  async function handleLoadFolder() {
    setIsLoading(true);
    setLoadError(null);
    setResults(null);

    try {
      const response = await listMediaObjects({ prefix: prefix || undefined });
      setObjects(response.objects);
      setMediaObjectCount(response.object_count);
      setMediaListingPrefix(response.prefix);
      setIsMediaListingTruncated(response.truncated);
      setSelectedKeys(new Set(response.objects.map((object) => object.key)));
    } catch (error) {
      setLoadError(errorMessage(error));
      setObjects([]);
      setSelectedKeys(new Set());
      setMediaObjectCount(0);
      setMediaListingPrefix("");
      setIsMediaListingTruncated(false);
    } finally {
      setIsLoading(false);
    }
  }

  function toggleSelectAll(checked: boolean) {
    if (checked) {
      setSelectedKeys(new Set(objects.map((object) => object.key)));
      return;
    }
    setSelectedKeys(new Set());
  }

  function toggleSelection(key: string, checked: boolean) {
    setSelectedKeys((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  }

  function handleContainerChange(nextContainer: ContainerFormat) {
    setContainer(nextContainer);
    if (nextContainer === "mov") {
      setVideoFaststart(false);
    }
  }

  function handleVideoCodecChange(nextCodec: "h264" | "hevc" | "prores") {
    setVideoCodec(nextCodec);
    if (nextCodec === "prores") {
      setContainer("mov");
      setVideoFaststart(false);
    }
  }

  async function handleStartTranscode() {
    const inputs = [...selectedKeys];
    if (inputs.length === 0) {
      setSubmitError("Select at least one file to transcode.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    setResults(null);

    try {
      const response = await createTranscodeJob({
        inputs,
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
        container,
        prepend_folder: prependFolder.trim() || null,
        append_folder: appendFolder.trim() || null,
      });
      setResults(response.results);
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-background px-4 py-8 text-foreground md:px-8">
      <section className="mx-auto flex w-fit min-w-full max-w-full flex-col gap-8 md:min-w-4xl">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <h1 className="font-sansation text-4xl font-bold tracking-tight">Transcode</h1>
          <AppNav />
        </div>

        <div className="flex max-w-2xl flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="transcode-prefix">Media prefix</Label>
            <div className="flex gap-2">
              <Input
                id="transcode-prefix"
                value={prefix}
                onChange={(event) => setPrefix(event.target.value)}
                placeholder="projects/demo/day1/"
                disabled={isLoading || isSubmitting}
              />
              <Button
                type="button"
                variant="outline"
                disabled={isLoading || isSubmitting}
                onClick={() => void handleLoadFolder()}
              >
                {isLoading ? "Loading..." : "Load folder"}
              </Button>
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
        </div>

        {loadError ? (
          <Alert variant="destructive">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        ) : null}

        {isLoading ? <MediaTableSkeleton /> : null}

        {objects.length > 0 && !isLoading ? (
          <section className="flex flex-col gap-4">
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-xl font-medium text-foreground/90">Media files</h2>
              <p className="text-sm text-muted-foreground">
                {selectedKeys.size} of {objects.length} selected
              </p>
            </div>
            <Table containerClassName="overflow-x-auto rounded-lg border">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={allSelected}
                      onCheckedChange={(checked) => toggleSelectAll(checked === true)}
                      aria-label="Select all files"
                    />
                  </TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Size</TableHead>
                  <TableHead>Modified</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {objects.map((object) => (
                  <TableRow key={object.key}>
                    <TableCell>
                      <Checkbox
                        checked={selectedKeys.has(object.key)}
                        onCheckedChange={(checked) =>
                          toggleSelection(object.key, checked === true)
                        }
                        aria-label={`Select ${object.key}`}
                      />
                    </TableCell>
                    <TableCell className="font-mono text-xs">{object.key}</TableCell>
                    <TableCell>{formatBytes(object.size)}</TableCell>
                    <TableCell>{formatModified(object.last_modified)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        ) : null}

        {objects.length > 0 ? (
          <section className="flex max-w-3xl flex-col gap-4 rounded-lg p-4">
            <h2 className="text-xl font-medium text-foreground/90">Transcode settings</h2>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="prepend-folder">Prepend folder</Label>
                <Input
                  id="prepend-folder"
                  value={prependFolder}
                  placeholder="proxies"
                  disabled={isSubmitting}
                  onChange={(event) => setPrependFolder(event.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Inserted before the source path (e.g. proxies/projects/demo/clipA.mp4).
                </p>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="append-folder">Append folder</Label>
                <Input
                  id="append-folder"
                  value={appendFolder}
                  placeholder="proxies"
                  disabled={isSubmitting}
                  onChange={(event) => setAppendFolder(event.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Inserted before the filename (e.g. projects/demo/proxies/clipA.mp4).
                </p>
              </div>
            </div>

            {previewOutputKey ? (
              <p className="text-sm text-muted-foreground">
                Output preview
                {previewSourceKey ? ` for ${previewSourceKey}` : ""}:{" "}
                <span className="font-mono text-foreground">{previewOutputKey}</span>
              </p>
            ) : null}

            <Accordion
              type="single"
              collapsible
              className="w-full rounded-lg border bg-muted/30 px-4"
            >
              <AccordionItem value="settings" className="border-none">
                <AccordionTrigger>Encoding settings</AccordionTrigger>
                <AccordionContent>
                  <EncodingSettings
                    idPrefix="transcode-"
                    disabled={isSubmitting}
                    showOutputPath={false}
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
              disabled={isSubmitting || selectedKeys.size === 0}
              onClick={() => void handleStartTranscode()}
            >
              {isSubmitting ? "Submitting..." : "Start transcode"}
            </Button>
          </section>
        ) : null}

        {submitError ? (
          <Alert variant="destructive">
            <AlertDescription>{submitError}</AlertDescription>
          </Alert>
        ) : null}

        {results ? (
          <section className="flex max-w-4xl flex-col gap-4 rounded-lg border p-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-xl font-medium text-foreground/90">Submitted jobs</h2>
              <p className="text-sm text-muted-foreground">
                {results.length} MediaConvert job{results.length === 1 ? "" : "s"} submitted in
                parallel. Outputs will appear in S3 when processing completes.
              </p>
            </div>
            <Table containerClassName="overflow-x-auto">
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Output</TableHead>
                  <TableHead>MediaConvert job</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.map((result) => (
                  <TableRow key={result.source_key}>
                    <TableCell className="font-mono text-xs">{result.source_key}</TableCell>
                    <TableCell className="font-mono text-xs">{result.output_key}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {result.mediaconvert_job_id ?? "dry run"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        ) : null}
      </section>
    </main>
  );
}

function MediaTableSkeleton() {
  return (
    <div className="overflow-hidden rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">
              <Skeleton className="h-4 w-4" />
            </TableHead>
            <TableHead>
              <Skeleton className="h-4 w-16" />
            </TableHead>
            <TableHead>
              <Skeleton className="h-4 w-12" />
            </TableHead>
            <TableHead>
              <Skeleton className="h-4 w-20" />
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: 4 }, (_, index) => (
            <TableRow key={index}>
              <TableCell>
                <Skeleton className="h-4 w-4" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-64" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-16" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-28" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unknown error occurred.";
}
