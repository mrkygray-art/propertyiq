import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PropertyIQ",
  description: "Private local AI analytics on a synthetic real estate portfolio",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="banner" role="note">
          <span>Synthetic data</span>
          <span className="sep">·</span>
          <span>Private local model</span>
          <span className="sep">·</span>
          <span>Not investment advice</span>
        </div>
        {children}
      </body>
    </html>
  );
}
