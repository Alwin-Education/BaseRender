"use client";

import { ChevronsUpDownIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

export type ComboboxOption = {
  value: string;
  label: string;
  description?: string;
};

type MediaComboboxProps = {
  value: string;
  options: ComboboxOption[];
  placeholder: string;
  searchPlaceholder: string;
  emptyMessage: string;
  recommendedValue?: string;
  footerAction?: {
    label: string;
    description?: string;
    onSelect: () => void;
  };
  onValueChange: (value: string) => void;
};

export function MediaCombobox({
  value,
  options,
  placeholder,
  searchPlaceholder,
  emptyMessage,
  recommendedValue,
  footerAction,
  onValueChange,
}: MediaComboboxProps) {
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value);
  const orderedOptions = useMemo(() => {
    if (!recommendedValue) {
      return options;
    }

    const recommended = options.find((option) => option.value === recommendedValue);
    const rest = options.filter((option) => option.value !== recommendedValue);
    return recommended ? [recommended, ...rest] : options;
  }, [options, recommendedValue]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between"
        >
          <span className="truncate">{selected?.label ?? placeholder}</span>
          <ChevronsUpDownIcon data-icon="inline-end" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {orderedOptions.map((option) => {
                const isRecommended = option.value === recommendedValue;
                const isSelected = option.value === value;

                return (
                  <CommandItem
                    key={option.value}
                    value={option.value}
                    data-checked={isSelected}
                    onSelect={() => {
                      onValueChange(option.value);
                      setOpen(false);
                    }}
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate">{option.label}</span>
                        {isRecommended ? (
                          <Badge variant="secondary">Recommended</Badge>
                        ) : null}
                      </span>
                      {option.description ? (
                        <span className="truncate text-xs text-muted-foreground">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                  </CommandItem>
                );
              })}
            </CommandGroup>
            {footerAction ? (
              <>
                <CommandSeparator />
                <CommandGroup>
                  <CommandItem
                    value="__footer_action__"
                    keywords={["choose", "file", "upload", "lut"]}
                    onSelect={() => {
                      setOpen(false);
                      footerAction.onSelect();
                    }}
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate">{footerAction.label}</span>
                      {footerAction.description ? (
                        <span className="truncate text-xs text-muted-foreground">
                          {footerAction.description}
                        </span>
                      ) : null}
                    </span>
                  </CommandItem>
                </CommandGroup>
              </>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
