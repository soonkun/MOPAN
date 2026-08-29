import "./globals.css";

export const metadata = {
  title: "MOPAN",
  description: "MOPAN AI Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
