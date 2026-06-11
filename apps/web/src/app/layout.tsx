import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "BaseRender",
  description: "OTIO cloud render pipeline",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
