import type {
  CloudMediaObjectPayload,
  MediaReferencePayload,
} from "@/lib/types";

export type MediaAssignmentOption = {
  value: string;
  label: string;
  description?: string;
};

export function referenceId(
  reference: MediaReferencePayload,
  index: number,
): string {
  return reference.id ?? `${reference.clip_name}-${index}`;
}

export function otioMediaName(reference: MediaReferencePayload): string {
  const sourceUrl = otioMediaPath(reference);
  return sourceUrl ? fileNameFromKey(sourceUrl) : reference.clip_name;
}

export function otioMediaPath(reference: MediaReferencePayload): string | null {
  return reference.target_url ?? reference.normalized_url;
}

export function mediaOptions(
  reference: MediaReferencePayload,
  objects: CloudMediaObjectPayload[],
  assignedKey?: string,
): MediaAssignmentOption[] {
  const options = new Map<string, MediaAssignmentOption>();

  for (const suggestion of reference.suggestions) {
    options.set(suggestion.key, {
      value: suggestion.key,
      label: mediaLabelFromKey(suggestion.key),
      description: `${suggestion.key} · ${Math.round(suggestion.score)}% match`,
    });
  }

  for (const object of objects) {
    if (!options.has(object.key)) {
      options.set(object.key, {
        value: object.key,
        label: mediaLabelFromKey(object.key),
        description: object.key,
      });
    }
  }

  if (assignedKey && !options.has(assignedKey)) {
    options.set(assignedKey, {
      value: assignedKey,
      label: mediaLabelFromKey(assignedKey),
      description: assignedKey,
    });
  }

  return [...options.values()];
}

export function mergeMediaAssignments(
  references: MediaReferencePayload[],
  existingAssignments: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    references.map((reference, index) => {
      const id = referenceId(reference, index);
      return [
        id,
        hasAssignment(existingAssignments, id)
          ? existingAssignments[id]
          : reference.suggestions[0]?.key ?? "",
      ];
    }),
  );
}

export function mergeLutAssignments(
  references: MediaReferencePayload[],
  existingAssignments: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    references.map((reference, index) => {
      const id = referenceId(reference, index);
      return [
        id,
        hasAssignment(existingAssignments, id) ? existingAssignments[id] : "none",
      ];
    }),
  );
}

export function fileNameFromKey(key: string): string {
  return key.split("/").filter(Boolean).at(-1) ?? key;
}

function mediaLabelFromKey(key: string): string {
  const parts = key.split("/").filter(Boolean);
  return parts.length > 1 ? parts.join("/") : fileNameFromKey(key);
}

function hasAssignment(
  assignments: Record<string, string>,
  referenceId: string,
): boolean {
  return Object.prototype.hasOwnProperty.call(assignments, referenceId);
}

export function isMediaReferenceLinked(
  reference: MediaReferencePayload,
  index: number,
  mediaAssignments: Record<string, string>,
): boolean {
  const id = referenceId(reference, index);
  return Boolean(mediaAssignments[id]?.trim());
}

export function unlinkedMediaReferences(
  references: MediaReferencePayload[],
  mediaAssignments: Record<string, string>,
): MediaReferencePayload[] {
  return references.filter(
    (reference, index) =>
      !isMediaReferenceLinked(reference, index, mediaAssignments),
  );
}

export function unlinkedMediaWarningMessage(
  references: MediaReferencePayload[],
  mediaAssignments: Record<string, string>,
): string | null {
  const unlinked = unlinkedMediaReferences(references, mediaAssignments);
  if (unlinked.length === 0) {
    return null;
  }

  const names = unlinked.map((reference) => reference.clip_name);
  const preview = names.slice(0, 5).join(", ");
  const overflow =
    names.length > 5 ? ` and ${names.length - 5} more` : "";
  const countLabel =
    unlinked.length === 1 ? "1 media reference is" : `${unlinked.length} media references are`;

  return `${countLabel} not linked (${preview}${overflow}). Unlinked clips will be skipped and the output timeline will be shorter.`;
}
