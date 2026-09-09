import type { ReactNode } from "react";

/** 모든 화면의 머리 줄 - 햄버거 · 제목 · 오른쪽 버튼이 한 줄에 같은 세로 중심으로 선다.
 *
 * 모바일: 햄버거는 Sidebar가 화면에 고정해 띄운다(fixed left-4 top-6, 40px). PageShell의 py-6·px-4와
 * 같은 좌표라 이 줄의 첫 40px 높이와 겹치므로 왼콽 48px(pl-12)을 비워 두고, 제목은 남은 폭의 가운데,
 * 버튼은 오른쪽 끝. min-h-10으로 줄 높이를 햄버거와 같게 맞춰 세로 중심이 일치한다(실사고: 제목과
 * 버튼이 햄버거보다 14px 아래 떠 보였다). 데스크톱: 햄버거가 없으니 비우지 않고 왼쪽 정렬.
 * `leading`(뒤로 가기 등)이 있으면 제목은 그 옆에 왼쪽 정렬로 붙는다 - 가운데 정렬은 좌우가 비었을 때만
 * 균형이 맞는다. */
export default function PageHeader({
  title,
  actions,
  leading,
  subtitle,
}: {
  title: ReactNode;
  actions?: ReactNode;
  leading?: ReactNode;
  subtitle?: ReactNode;
}) {
  return (
    <div>
      <div className="flex min-h-10 items-center gap-2 pl-12 md:pl-0">
        {leading}
        <h1 className={`min-w-0 flex-1 truncate text-headline font-medium ${leading ? "text-left" : "text-center md:text-left"}`}>
          {title}
        </h1>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {subtitle && <p className="mt-1 pl-12 text-center text-caption text-on-surface-variant md:pl-0 md:text-left">{subtitle}</p>}
    </div>
  );
}
