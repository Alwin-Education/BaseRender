import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  type AudioCodec,
  type ContainerFormat,
  type VideoCodec,
  VIDEO_PRESETS,
  audioCodecLabel,
  parseOptionalFloat,
  parseOptionalInt,
  parsePositiveInt,
  videoCodecLabel,
} from "@/lib/render-settings";

export type EncodingSettingsProps = {
  disabled?: boolean;
  idPrefix?: string;
  showOutputPath?: boolean;
  outputPath?: string;
  onOutputPathChange?: (value: string) => void;
  container: ContainerFormat;
  onContainerChange: (value: ContainerFormat) => void;
  width: number | null;
  onWidthChange: (value: number | null) => void;
  height: number | null;
  onHeightChange: (value: number | null) => void;
  fps: number | null;
  onFpsChange: (value: number | null) => void;
  videoCodec: VideoCodec;
  onVideoCodecChange: (value: VideoCodec) => void;
  videoBitrate: number;
  onVideoBitrateChange: (value: number) => void;
  videoPreset: string;
  onVideoPresetChange: (value: string) => void;
  videoFaststart: boolean;
  onVideoFaststartChange: (value: boolean) => void;
  audioCodec: AudioCodec;
  onAudioCodecChange: (value: AudioCodec) => void;
  audioBitrate: number;
  onAudioBitrateChange: (value: number) => void;
};

export function EncodingSettings({
  disabled = false,
  idPrefix = "",
  showOutputPath = true,
  outputPath = "",
  onOutputPathChange,
  container,
  onContainerChange,
  width,
  onWidthChange,
  height,
  onHeightChange,
  fps,
  onFpsChange,
  videoCodec,
  onVideoCodecChange,
  videoBitrate,
  onVideoBitrateChange,
  videoPreset,
  onVideoPresetChange,
  videoFaststart,
  onVideoFaststartChange,
  audioCodec,
  onAudioCodecChange,
  audioBitrate,
  onAudioBitrateChange,
}: EncodingSettingsProps) {
  const isProRes = videoCodec === "prores";
  const isMovContainer = container === "mov";

  return (
    <div className="flex flex-col gap-6 pb-2">
      {showOutputPath ? (
        <>
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium text-foreground/80">Output</p>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="flex flex-col gap-2 md:col-span-2">
                <Label htmlFor={`${idPrefix}output-path`}>Output path</Label>
                <Input
                  id={`${idPrefix}output-path`}
                  value={outputPath}
                  placeholder="outputs/episode-1/final.mp4"
                  disabled={disabled}
                  onChange={(event) => onOutputPathChange?.(event.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`${idPrefix}container`}>Container</Label>
                <Select
                  value={container}
                  disabled={disabled}
                  onValueChange={(value) => onContainerChange(value as ContainerFormat)}
                >
                  <SelectTrigger id={`${idPrefix}container`} className="w-full">
                    <SelectValue placeholder="Select container" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="mp4">MP4</SelectItem>
                    <SelectItem value="mov">MOV</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <Separator />
        </>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium text-foreground/80">Output</p>
          <div className="flex flex-col gap-2 max-w-xs">
            <Label htmlFor={`${idPrefix}container`}>Container</Label>
            <Select
              value={container}
              disabled={disabled}
              onValueChange={(value) => onContainerChange(value as ContainerFormat)}
            >
              <SelectTrigger id={`${idPrefix}container`} className="w-full">
                <SelectValue placeholder="Select container" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="mp4">MP4</SelectItem>
                <SelectItem value="mov">MOV</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Separator />
        </div>
      )}

      <div className="flex flex-col gap-3">
        <p className="text-sm font-medium text-foreground/80">Canvas</p>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}width`}>Width</Label>
            <Input
              id={`${idPrefix}width`}
              type="number"
              min={1}
              step={1}
              value={width ?? ""}
              disabled={disabled}
              onChange={(event) => onWidthChange(parseOptionalInt(event.target.value))}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}height`}>Height</Label>
            <Input
              id={`${idPrefix}height`}
              type="number"
              min={1}
              step={1}
              value={height ?? ""}
              disabled={disabled}
              onChange={(event) => onHeightChange(parseOptionalInt(event.target.value))}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}fps`}>FPS</Label>
            <Input
              id={`${idPrefix}fps`}
              type="number"
              min={0}
              step="any"
              value={fps ?? ""}
              disabled={disabled}
              onChange={(event) => onFpsChange(parseOptionalFloat(event.target.value))}
            />
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <p className="text-sm font-medium text-foreground/80">Video</p>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}video-codec`}>Codec</Label>
            <Select
              value={videoCodec}
              disabled={disabled}
              onValueChange={(value) => onVideoCodecChange(value as VideoCodec)}
            >
              <SelectTrigger id={`${idPrefix}video-codec`} className="w-full">
                <SelectValue placeholder="Select codec" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="h264">{videoCodecLabel("h264")}</SelectItem>
                <SelectItem value="hevc">{videoCodecLabel("hevc")}</SelectItem>
                <SelectItem value="prores">{videoCodecLabel("prores")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}video-bitrate`}>Bitrate (bits/s)</Label>
            <Input
              id={`${idPrefix}video-bitrate`}
              type="number"
              min={1}
              step={1}
              value={videoBitrate}
              placeholder="8000000"
              disabled={disabled || isProRes}
              onChange={(event) =>
                onVideoBitrateChange(parsePositiveInt(event.target.value, videoBitrate))
              }
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}video-preset`}>Preset</Label>
            <Select
              value={videoPreset}
              disabled={disabled || isProRes}
              onValueChange={onVideoPresetChange}
            >
              <SelectTrigger id={`${idPrefix}video-preset`} className="w-full">
                <SelectValue placeholder="Select preset" />
              </SelectTrigger>
              <SelectContent>
                {VIDEO_PRESETS.map((preset) => (
                  <SelectItem key={preset} value={preset}>
                    {preset}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2 md:col-span-2">
            <Checkbox
              id={`${idPrefix}video-faststart`}
              checked={videoFaststart}
              disabled={disabled || isMovContainer}
              onCheckedChange={(checked) => onVideoFaststartChange(checked === true)}
            />
            <Label htmlFor={`${idPrefix}video-faststart`}>MP4 fast start</Label>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <p className="text-sm font-medium text-foreground/80">Audio</p>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}audio-codec`}>Codec</Label>
            <Select
              value={audioCodec}
              disabled={disabled}
              onValueChange={(value) => onAudioCodecChange(value as AudioCodec)}
            >
              <SelectTrigger id={`${idPrefix}audio-codec`} className="w-full">
                <SelectValue placeholder="Select codec" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="aac">{audioCodecLabel("aac")}</SelectItem>
                <SelectItem value="pcm">{audioCodecLabel("pcm")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${idPrefix}audio-bitrate`}>Bitrate (bits/s)</Label>
            <Input
              id={`${idPrefix}audio-bitrate`}
              type="number"
              min={1}
              step={1}
              value={audioBitrate}
              placeholder="192000"
              disabled={disabled || audioCodec === "pcm"}
              onChange={(event) =>
                onAudioBitrateChange(parsePositiveInt(event.target.value, audioBitrate))
              }
            />
          </div>
        </div>
      </div>
    </div>
  );
}
