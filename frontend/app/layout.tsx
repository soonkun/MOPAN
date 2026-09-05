import { Inter, Noto_Sans_KR } from "next/font/google";
import "./globals.css";

// §5. next/font/google downloads and self-hosts these at BUILD time, so there
// is no runtime request to fonts.googleapis.com and no layout shift: Next
// emits the @font-face with size-adjust metrics and preloads the woff2.
//
// Two families, not one: Inter has no Hangul and Noto Sans KR's Latin is
// noticeably wider and looser. The fallback stack in tailwind.config.ts picks
// up whatever neither covers.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  display: "swap",
  variable: "--font-noto-sans-kr",
});

export const metadata = {
  title: "MOPAN",
  description: "MOPAN AI Platform",
};

// maximumScale 1: iOS 사파리가 입력칸 포커스마다 화면 전체를 자동 줌해서,
// 계정 창만 열어도 줌인이 되던 실사고(아이폰 실측). iOS 10부터 사용자의 핀치
// 줌은 이 값과 무관하게 항상 동작하므로 접근성 손실은 없고, 잃는 것은 그
// 원치 않는 자동 줌뿐이다. globals.css의 모바일 16px 규칙과 한 쌍.
export const viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning is required, not cosmetic: public/theme.js sets
    // data-theme on this element before React exists, so the client DOM
    // legitimately differs from the server HTML by exactly that attribute and
    // React 19 otherwise logs a hydration mismatch on every dark-mode load. It
    // suppresses the warning for THIS element's attributes only - children are
    // still diffed normally.
    <html
      lang="ko"
      className={`${inter.variable} ${notoSansKr.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* Parser-blocking on purpose - it sets data-theme from localStorage
            before the first paint, so a dark-theme user never sees a white
            flash on reload. See public/theme.js. */}
        <script src="/theme.js" />
      </head>
      <body>{children}</body>
    </html>
  );
}
