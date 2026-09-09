"use client";

import { useEffect } from "react";

/** 시트/드로어가 열린 동안 뒤 페이지가 스크롤되지 않게 잠근다.
 *  body에 overflow:hidden만 주면 iOS Safari는 터치 스크롤을 그대로 문서에 넘긴다 -
 *  시트 안 내용이 화면보다 짧을 때 시트를 끌면 뒤 화면이 움직였던 원인. position:fixed로
 *  문서 자체를 못 움직이게 하고, 그때 잃는 스크롤 위치는 top으로 붙잡았다가 풀 때 되돌린다. */
export function useBodyScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const { style } = document.body;
    const scrollY = window.scrollY;
    const previous = { position: style.position, top: style.top, width: style.width, overflow: style.overflow };
    style.position = "fixed";
    style.top = `-${scrollY}px`;
    style.width = "100%";
    style.overflow = "hidden";
    return () => {
      style.position = previous.position;
      style.top = previous.top;
      style.width = previous.width;
      style.overflow = previous.overflow;
      window.scrollTo(0, scrollY);
    };
  }, [active]);
}
