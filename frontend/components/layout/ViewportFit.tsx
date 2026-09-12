"use client";

import { useEffect } from "react";

/** 앱 셸을 시각 뷰포트(visualViewport)에 맞춘다 - 모바일 키보드의 근본 처방.
 *
 * iOS(Safari·Chrome 모두 WebKit)는 키보드가 올라와도 레이아웃 뷰포트(100dvh)를
 * 줄이지 않는다. 그래서 (1) 화면 바닥의 입력창이 키보드 뒤로 가려지고, (2) 포커스
 * 요소를 보이게 하려고 WebKit이 문서 자체를 위로 밀며, (3) 키보드가 내려가도 그
 * 오프셋이 남아 입력창이 화면 중간에 뜨고 탭이 어긋난다(2026-09-12 실사고 영상).
 *
 * 처방은 하나다: 셸의 높이를 키보드 위에 실제로 보이는 높이(--vvh)로, 위치를 그
 * 시작점(--vvt)으로 맞춘다. 그러면 입력창은 늘 키보드 바로 위에 있고, WebKit이 밀
 * 이유가 없어지며, 대화창은 셸 안에서만 스크롤한다. 데스크톱에선 두 값이 창 크기와
 * 같아 아무 일도 없다. 핀치 줌 중(scale>1)에는 손대지 않는다 - 줌은 줌대로 둔다. */
export default function ViewportFit() {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    root.classList.add("app-locked");
    if (!vv) return () => root.classList.remove("app-locked");
    const apply = () => {
      if (vv.scale > 1.01) {
        root.style.removeProperty("--vvh");
        root.style.removeProperty("--vvt");
        return;
      }
      root.style.setProperty("--vvh", `${Math.round(vv.height)}px`);
      root.style.setProperty("--vvt", `${Math.round(vv.offsetTop)}px`);
      // 그래도 WebKit이 문서를 밀었으면 되돌린다 - 셸은 --vvt로 이미 제자리다.
      if (window.scrollY !== 0) window.scrollTo(0, 0);
    };
    apply();
    vv.addEventListener("resize", apply);
    vv.addEventListener("scroll", apply);
    window.addEventListener("resize", apply);
    return () => {
      vv.removeEventListener("resize", apply);
      vv.removeEventListener("scroll", apply);
      window.removeEventListener("resize", apply);
      root.classList.remove("app-locked");
      root.style.removeProperty("--vvh");
      root.style.removeProperty("--vvt");
    };
  }, []);
  return null;
}
