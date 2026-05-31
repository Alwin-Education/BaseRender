"use client";

import { useRef } from "react";

import { Button } from "@/components/ui/button";

type OtioFilePickerProps = {
  file: File | null;
  isLoading: boolean;
  onFileChange: (file: File | null) => void;
};

export function OtioFilePicker({
  file,
  isLoading,
  onFileChange,
}: OtioFilePickerProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="flex flex-col gap-3">
      <input
        ref={inputRef}
        type="file"
        accept=".otio,application/json"
        disabled={isLoading}
        className="sr-only"
        onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
      />
      <Button
        type="button"
        variant="outline"
        disabled={isLoading}
        onClick={() => inputRef.current?.click()}
      >
        Choose the OTIO
      </Button>
      {file ? (
        <p className="truncate text-sm text-muted-foreground">
          {file.name}
        </p>
      ) : null}
    </div>
  );
}
