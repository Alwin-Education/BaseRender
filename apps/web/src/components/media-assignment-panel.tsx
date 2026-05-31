"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type VisibilityState,
} from "@tanstack/react-table";
import { ChevronDownIcon } from "lucide-react";
import { useMemo, useState } from "react";

import {
  LutAssignmentControl,
  type ClientLut,
} from "@/components/lut-assignment-control";
import { MediaCombobox } from "@/components/media-combobox";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  mediaOptions,
  otioMediaName,
  otioMediaPath,
  referenceId,
  type MediaAssignmentOption,
} from "@/lib/media-assignment";
import type {
  CloudMediaObjectPayload,
  MediaReferencePayload,
} from "@/lib/types";

type MediaAssignmentPanelProps = {
  references: MediaReferencePayload[];
  objects: CloudMediaObjectPayload[];
  mediaAssignments: Record<string, string>;
  lutAssignments: Record<string, string>;
  luts: ClientLut[];
  onMediaAssignmentChange: (referenceId: string, value: string) => void;
  onLutAssignmentChange: (referenceId: string, value: string) => void;
  onLutUpload: (referenceId: string, file: File) => void;
};

type MediaAssignmentRow = {
  reference: MediaReferencePayload;
  referenceId: string;
  recommendedKey: string | undefined;
  options: MediaAssignmentOption[];
  assignedMedia: string;
  lutAssignment: string;
  sourceUrl: string | null;
  clipCount: number;
};

const columnLabels: Record<string, string> = {
  name: "Name",
  sourceMedia: "Source media",
  lut: "LUT",
  path: "Path",
  clips: "Clips",
  track: "Track",
};

export function MediaAssignmentPanel({
  references,
  objects,
  mediaAssignments,
  lutAssignments,
  luts,
  onMediaAssignmentChange,
  onLutAssignmentChange,
  onLutUpload,
}: MediaAssignmentPanelProps) {
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    path: false,
    clips: false,
    track: false,
  });
  const data = useMemo(
    () =>
      references.map((reference, index) => {
        const id = referenceId(reference, index);
        const recommendedKey = reference.suggestions[0]?.key;

        return {
          reference,
          referenceId: id,
          recommendedKey,
          options: mediaOptions(reference, objects, mediaAssignments[id]),
          assignedMedia: mediaAssignments[id] ?? recommendedKey ?? "",
          lutAssignment: lutAssignments[id] ?? "none",
          sourceUrl: otioMediaPath(reference),
          clipCount: reference.clip_count ?? 1,
        };
      }),
    [references, objects, mediaAssignments, lutAssignments],
  );
  const columns = useMemo<ColumnDef<MediaAssignmentRow>[]>(
    () => [
      {
        id: "name",
        header: "Name",
        enableHiding: false,
        cell: ({ row }) => {
          const { reference, sourceUrl } = row.original;

          return (
            <div
              className="max-w-[280px] truncate font-medium"
              title={sourceUrl ?? reference.clip_name}
            >
              {otioMediaName(reference)}
            </div>
          );
        },
      },
      {
        id: "sourceMedia",
        header: "Source media",
        enableHiding: false,
        cell: ({ row }) => {
          const item = row.original;

          return (
            <div className="min-w-[260px]">
              <MediaCombobox
                value={item.assignedMedia}
                options={item.options}
                placeholder="-"
                searchPlaceholder="Search media objects..."
                emptyMessage="No matching media objects available."
                recommendedValue={item.recommendedKey}
                onValueChange={(value) =>
                  onMediaAssignmentChange(item.referenceId, value)
                }
              />
            </div>
          );
        },
      },
      {
        id: "lut",
        header: "LUT",
        enableHiding: false,
        cell: ({ row }) => {
          const item = row.original;

          return (
            <LutAssignmentControl
              value={item.lutAssignment}
              luts={luts}
              showLabel={false}
              onUpload={(file) => onLutUpload(item.referenceId, file)}
              onValueChange={(value) =>
                onLutAssignmentChange(item.referenceId, value)
              }
            />
          );
        },
      },
      {
        id: "path",
        header: "Path",
        cell: ({ row }) => (
          <div
            className="max-w-[360px] truncate text-muted-foreground"
            title={row.original.sourceUrl ?? undefined}
          >
            {row.original.sourceUrl ?? "No path"}
          </div>
        ),
      },
      {
        id: "clips",
        header: "Clips",
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {clipCountLabel(row.original.clipCount)}
          </span>
        ),
      },
      {
        id: "track",
        header: "Track",
        cell: ({ row }) => (
          <div
            className="max-w-[280px] truncate text-muted-foreground"
            title={row.original.reference.track_path || undefined}
          >
            {row.original.reference.track_path || "No track"}
          </div>
        ),
      },
    ],
    [luts, onLutAssignmentChange, onLutUpload, onMediaAssignmentChange],
  );
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    onColumnVisibilityChange: setColumnVisibility,
    state: {
      columnVisibility,
    },
  });

  return (
    <Card className="overflow-visible ring-0">
      <CardHeader>
        <CardTitle>Media assignments</CardTitle>
        <CardAction>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="outline" size="sm">
                Columns
                <ChevronDownIcon data-icon="inline-end" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuGroup>
                {table
                  .getAllLeafColumns()
                  .filter((column) => column.getCanHide())
                  .map((column) => (
                    <DropdownMenuCheckboxItem
                      key={column.id}
                      checked={column.getIsVisible()}
                      onCheckedChange={(value) =>
                        column.toggleVisibility(value === true)
                      }
                    >
                      {columnLabels[column.id] ?? column.id}
                    </DropdownMenuCheckboxItem>
                  ))}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </CardAction>
      </CardHeader>
      <CardContent>
        <Table containerClassName="overflow-x-visible" className="w-auto min-w-full">
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    className={columnClassName(header.column.id)}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.original.referenceId}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell
                    key={cell.id}
                    className={columnClassName(cell.column.id)}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function columnClassName(columnId: string): string {
  if (columnId === "sourceMedia" || columnId === "lut") {
    return "min-w-[280px]";
  }

  if (columnId === "path") {
    return "min-w-[320px]";
  }

  if (columnId === "clips") {
    return "w-[120px]";
  }

  if (columnId === "track") {
    return "min-w-[240px]";
  }

  return "min-w-[220px]";
}

function clipCountLabel(count: number): string {
  return count === 1 ? "1 clip" : `${count} clips`;
}
