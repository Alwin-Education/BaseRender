"use client";

import { useRef } from "react";

import { Field, FieldLabel } from "@/components/ui/field";
import { type ComboboxOption, MediaCombobox } from "@/components/media-combobox";

export type ClientLut = {
  id: string;
  name: string;
  size: number;
  file: File;
};

type LutAssignmentControlProps = {
  value: string;
  luts: ClientLut[];
  showLabel?: boolean;
  onUpload: (file: File) => void;
  onValueChange: (value: string) => void;
};

const noLutOption: ComboboxOption = {
  value: "none",
  label: "-",
  description: "None",
};

export function LutAssignmentControl({
  value,
  luts,
  showLabel = true,
  onUpload,
  onValueChange,
}: LutAssignmentControlProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const options = [
    noLutOption,
    ...luts.map((lut) => ({
      value: lut.id,
      label: lut.name,
      description: formatBytes(lut.size),
    })),
  ];
  const combobox = (
    <MediaCombobox
      value={value || "none"}
      options={options}
      placeholder="Choose LUT"
      searchPlaceholder="Search LUTs..."
      emptyMessage="No LUTs uploaded in this session."
      footerAction={{
        label: "Choose a file",
        onSelect: () => fileInputRef.current?.click(),
      }}
      onValueChange={onValueChange}
    />
  );

  return (
    <div className="grid gap-3">
      {showLabel ? (
        <Field>
          <FieldLabel>Associate LUT</FieldLabel>
          {combobox}
        </Field>
      ) : (
        combobox
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept=".cube,.3dl,.lut"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];

          if (file) {
            onUpload(file);
            event.target.value = "";
          }
        }}
      />
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toFixed(1)} ${units[unitIndex]}`;
}
