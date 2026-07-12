import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Repo Assistant",
  description: "Ask questions about a GitHub repository, grounded in cited code.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
