import type { Metadata, Viewport } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "EchoVoiice Assistant",
  icons: { icon: "data:," },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0f5c4d",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ee">
      <body>
        <Nav />
        {children}
      </body>
    </html>
  );
}
