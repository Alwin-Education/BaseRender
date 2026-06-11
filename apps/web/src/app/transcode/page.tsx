"use client";

import { Suspense } from "react";

import { TranscodePage } from "@/views/TranscodePage";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <TranscodePage />
    </Suspense>
  );
}
