import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost";
  const protocol = requestHeaders.get("x-forwarded-proto") || (host.includes("localhost") ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);
  return {
    metadataBase,
    title: "Portfolio Laboratory",
    description: "A transparent portfolio risk and optimization dashboard.",
    icons: {
      icon: [{ url: "/seer-favicon.svg?v=faceted-seer-3", type: "image/svg+xml" }],
      shortcut: "/seer-favicon.svg?v=faceted-seer-3",
    },
    openGraph: {
      title: "Portfolio Laboratory",
      description: "Risk, correlation & allocation — made legible.",
      type: "website",
      images: [{ url: "/og-portfolio-lab.png", width: 1731, height: 909, alt: "The Seer's Portfolio Lab analytics preview" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Portfolio Laboratory",
      description: "Risk, correlation & allocation — made legible.",
      images: ["/og-portfolio-lab.png"],
    },
  };
}

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
