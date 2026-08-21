import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MangaFlow Studio — 连续性驱动漫剧生产",
  description: "Continuity-first agentic manga production workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

