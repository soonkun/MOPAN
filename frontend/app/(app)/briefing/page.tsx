"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageHeader from "@/components/layout/PageHeader";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import { useHelp } from "@/components/ui/HelpToggle";

// 회차 하나 = 따로 가는 설정. "HH:MM" KST. start == end면 24시간, 자정을 넘는 범위도 됨. deliver가 비면 만들어지는 대로 보냄
type Window = { label: string; start: string; end: string; run: string; deliver: string };
type Entity = { name: string; group: string; note: string; role: string };
type Subscription = {
  id: string;
  name: string;
  style: "topic" | "tracker";
  queries: string[];
  entities?: Entity[];
  scope?: string;
  sites: string[];
  mail_to: string;
  formats: string[];
  windows: Window[];
  keep_days?: number;
  estimate_minutes: number;
};
type Report = {
  id: string;
  sub_id: string;
  name: string;
  style?: "topic" | "tracker";
  date: string;
  start?: string;
  end?: string;
  status: "queued" | "running" | "done" | "failed";
  label?: string;
  note?: string;
  articles?: number;
  issues?: number;
  headline?: string;
  mail?: string;
  error?: string;
  finished?: string;
  seconds?: number;
};
type Data = { subscriptions: Subscription[]; reports: Report[]; account_email: string; recent_seconds: number[] };

const FORMATS = [
  ["pdf", "PDF"],
  ["hwpx", "HWPX"],
  ["md", "MD"],
] as const;
const split = (text: string) => text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
const pad = (n: number) => String(n).padStart(2, "0");
const toMin = (hm: string) => Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5));
const toHM = (m: number) => `${pad(Math.floor((((m % 1440) + 1440) % 1440) / 60))}:${pad(((m % 1440) + 1440) % 60)}`;
const span = (w: Window) => ((toMin(w.end) - toMin(w.start) + 1440) % 1440) || 1440;
// 15분 단위 시각 목록. 시각 입력은 <select>로 - 아이폰의 <input type=time>은 너비·정렬을 제어할 수 없다(값이 칸 위에 붙음).
const QUARTERS = Array.from({ length: 96 }, (_, i) => toHM(i * 15));
const pretty = (hm: string) => {
  const h = Number(hm.slice(0, 2));
  return `${h < 12 ? "오전" : "오후"} ${h % 12 === 0 ? 12 : h % 12}:${hm.slice(3)}`;
};

/** 회차의 기본 꼴들. 회차마다 기사 시간대·조사 시작·보고 시각이 따로 간다(조간 브리핑과 석간 브리핑은 다른 설정이다).
 * Meltwater 같은 모니터링 서비스가 일간 다이제스트에 두 번째 발송 시각을 두는 것과 같은 쓰임. */
const PRESETS: { label: string; hint: string; windows: Window[] }[] = [
  { label: "전날 하루 → 아침 7시 보고", hint: "어제 00:00~24:00 기사 · 06:30 조사 · 07:00 보고", windows: [{ label: "일간", start: "00:00", end: "00:00", run: "06:30", deliver: "07:00" }] },
  { label: "조간 → 9시 보고", hint: "전날 16:00~당일 08:30 기사(조간 신문·밤사이) · 08:30 조사 · 09:00 보고", windows: [{ label: "조간", start: "16:00", end: "08:30", run: "08:30", deliver: "09:00" }] },
  {
    label: "조간·석간 두 번",
    hint: "조간: 전날 16:00~08:30 → 09:00 보고 / 석간: 08:30~16:30 → 17:00 보고",
    windows: [
      { label: "조간", start: "16:00", end: "08:30", run: "08:30", deliver: "09:00" },
      { label: "석간", start: "08:30", end: "16:30", run: "16:30", deliver: "17:00" },
    ],
  },
];

// 22대 국회 농림축산식품해양수산위원회(2026-10 기준, 소유자가 준 동향 자료의 명단). 바뀌면 화면에서 고친다.
const NONGHAESU = `서삼석 | 더불어민주당 | 전남 영암·무안·신안 | 위원장
윤준병 | 더불어민주당 | 전북 정읍·고창 | 간사
김성범 | 더불어민주당 | 제주 서귀포시
문금주 | 더불어민주당 | 전남 고흥·보성·장흥·강진
문대림 | 더불어민주당 | 제주 제주시 갑
송옥주 | 더불어민주당 | 경기 화성시갑
이개호 | 더불어민주당 | 전남 담양·함평·영광·장성
이재관 | 더불어민주당 | 충남 천안시 을
임미애 | 더불어민주당 | 비례대표
주철현 | 더불어민주당 | 전남 여수시갑
김선교 | 국민의힘 | 경기 여주·양평 | 간사
강승규 | 국민의힘 | 충남 홍성·예산
김용태 | 국민의힘 | 경기 포천·가평
이상휘 | 국민의힘 | 경북 포항·울릉
이성권 | 국민의힘 | 부산 사하구갑
이양수 | 국민의힘 | 강원 속초·인제·고성·양양
조승환 | 국민의힘 | 부산 중구영도구
신장식 | 조국혁신당 | 비례대표
강선우 | 무소속 | 서울 강서구 갑`;

function parseEntities(text: string): Entity[] {
  return text
    .split("\n")
    .map((line) => line.split("|").map((s) => s.trim()))
    .filter((parts) => parts[0])
    .map(([name, group = "", note = "", role = ""]) => ({ name, group, note, role }));
}

/** 24시간 시계판에 바늘 두 개(시작·끝)를 끌어 기사 구간을 고른다. iOS '수면 일정'의 두 손잡이 원형 선택기와 같은 방식 - 자정을 넘는 범위도
 * 한 번에 보인다. 15분 단위. 끝 바늘이 조사 시각의 기본값이다. */
function Dial({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  const svg = useRef<SVGSVGElement>(null);
  // ref, not state: 끌기는 매 pointermove마다 값을 바꾸니 끌기 여부까지 렌더에 태울 이유가 없고, 3초 폴링 재렌더와도 얽히지 않는다
  const drag = useRef<"start" | "end" | null>(null);
  const R = 84;
  const C = 110;
  const angle = (m: number) => ((m % 1440) / 1440) * 2 * Math.PI - Math.PI / 2;
  const point = (m: number, r = R) => [C + r * Math.cos(angle(m)), C + r * Math.sin(angle(m))];
  const s = toMin(value.start);
  const e = toMin(value.end);
  const length = span(value);
  const [sx, sy] = point(s);
  const [ex, ey] = point(e);
  const arc = length === 1440 ? `M ${C} ${C - R} A ${R} ${R} 0 1 1 ${C - 0.01} ${C - R}` : `M ${sx} ${sy} A ${R} ${R} 0 ${length > 720 ? 1 : 0} 1 ${ex} ${ey}`;

  function minutesAt(clientX: number, clientY: number) {
    const box = svg.current!.getBoundingClientRect();
    const x = ((clientX - box.left) / box.width) * 2 * C - C;
    const y = ((clientY - box.top) / box.height) * 2 * C - C;
    const deg = ((Math.atan2(y, x) * 180) / Math.PI + 90 + 360) % 360;
    return (Math.round((deg / 360) * 96) % 96) * 15; // 15분 단위
  }
  function move(ev: React.PointerEvent) {
    const kind = drag.current;
    if (!kind) return;
    const m = minutesAt(ev.clientX, ev.clientY);
    const next = { ...value, [kind]: toHM(m) };
    if (kind === "end" && toMin(value.run) === e) next.run = toHM(m); // 조사 시각이 끝 시각을 따라가던 중이면 같이 움직인다
    onChange(next);
  }
  // 캡처는 바늘(circle/text)이 아니라 svg에 건다. 바늘은 매 move마다 자리를 옮기는 자식이라 브라우저에 따라 캡처가 풀리거나
  // pointerleave가 끼어들어 끌기가 중간에 죽는다. svg가 잡고 있으면 손을 뗄 때까지 어디로 나가든 move가 svg로 온다.
  const grab = (kind: "start" | "end") => (ev: React.PointerEvent) => {
    ev.preventDefault();
    svg.current?.setPointerCapture(ev.pointerId);
    drag.current = kind;
  };
  const release = () => { drag.current = null; };
  const hand = (m: number, kind: "start" | "end") => {
    const [x, y] = point(m);
    return (
      <g key={kind} onPointerDown={grab(kind)} style={{ cursor: "grab" }}>
        <circle cx={x} cy={y} r={13} className={kind === "start" ? "fill-primary" : "fill-on-surface"} />
        <text x={x} y={y + 3.5} textAnchor="middle" className="fill-on-primary text-[9px] font-bold">{kind === "start" ? "시작" : "끝"}</text>
      </g>
    );
  };
  return (
    <svg
      ref={svg}
      viewBox={`0 0 ${2 * C} ${2 * C}`}
      className="h-56 w-56 touch-none select-none"
      role="group"
      aria-label={`기사 구간 ${value.start}부터 ${value.end}까지`}
      onPointerMove={move}
      onPointerUp={release}
      onPointerCancel={release}
    >
      <circle cx={C} cy={C} r={R} className="fill-none stroke-surface-container-highest" strokeWidth={14} />
      <path d={arc} className="fill-none stroke-primary" strokeWidth={14} strokeLinecap="round" opacity={0.85} />
      {Array.from({ length: 24 }, (_, h) => {
        const [x1, y1] = point(h * 60, R - 12);
        const [x2, y2] = point(h * 60, R - (h % 6 === 0 ? 20 : 16));
        const [tx, ty] = point(h * 60, R - 30);
        return (
          <g key={h}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} className="stroke-outline" strokeWidth={h % 6 === 0 ? 2 : 1} />
            {h % 3 === 0 && <text x={tx} y={ty + 3} textAnchor="middle" className="fill-on-surface-variant text-[9px]">{h}</text>}
          </g>
        );
      })}
      <text x={C} y={C - 6} textAnchor="middle" className="fill-on-surface text-[15px] font-semibold">{value.start} → {value.end}</text>
      <text x={C} y={C + 12} textAnchor="middle" className="fill-on-surface-variant text-[10px]">{length === 1440 ? "24시간" : `${Math.floor(length / 60)}시간${length % 60 ? ` ${length % 60}분` : ""}`}</text>
      {hand(s, "start")}
      {hand(e, "end")}
    </svg>
  );
}

function windowText(w: Window): string {
  const overnight = toMin(w.start) >= toMin(w.end);
  return span(w) === 1440 ? `전날 ${w.start}부터 24시간` : `${overnight ? "전날 " : ""}${w.start} ~ ${w.end}`;
}

/** 회차 한 줄 설명: "조간 · 전날 16:00 ~ 08:30 기사 → 08:30 조사 → 09:00 보고(약 3분 뒤 준비됨)" */
function scheduleText(w: Window, estimate: number): string {
  const ready = toHM(toMin(w.run) + estimate);
  const late = w.deliver && toMin(w.deliver) < toMin(w.run) + estimate;
  return `${w.label ? `${w.label} · ` : ""}${windowText(w)} 기사 → ${w.run} 조사 시작 → ${w.deliver ? `${w.deliver} 보고` : `${ready}쯤 도착`}${w.deliver ? ` (${ready}쯤 준비됨${late ? " - 보고 시각이 이보다 빠릅니다" : ""})` : ""}`;
}

function stateText(report: Report): string {
  switch (report.status) {
    case "queued":
      return "앞 작업이 끝나기를 기다리는 중";
    case "running":
      return report.note || "만드는 중";
    case "failed":
      return `실패: ${report.error ?? ""}`;
    default: {
      const took = report.seconds ? ` · ${Math.max(1, Math.round(report.seconds / 60))}분 걸림` : "";
      // eslint-disable-next-line no-fallthrough
      if (!report.articles) return `새 기사가 없었습니다.${took}`;
      return `기사 ${report.articles}건 · ${report.style === "tracker" ? `보도된 대상 ${report.issues}명` : `주요 이슈 ${report.issues}건`}${report.headline ? ` · ${report.headline}` : ""}${took}`;
    }
  }
}

/** 뉴스 브리핑 - 주제(검색어) 또는 인물·기관(동향표)을 구독하면 정한 조사 창에 나온 기사를 모아 브리핑을 만든다. 주제 브리핑은 낱말 구름·
 * 주요 이슈·전체 기사 표, 동향표는 대상별 ○ 기사·요점·보도매체 표(국회 상임위 의원 언론동향 자료의 꼴). 요약은 2차 사실 확인을 거친다.
 * 일은 별도 서비스(soonkun/news-briefing)가 하고 백엔드 app/briefing이 넘긴다. 진행은 짧은 GET을 되풀이해 읽는다. */
export default function BriefingPage() {
  const [data, setData] = useState<Data | null>(null);
  const [style, setStyle] = useState<"topic" | "tracker">("topic");
  const [name, setName] = useState("");
  const [queries, setQueries] = useState("");
  const [entities, setEntities] = useState("");
  const [scope, setScope] = useState("");
  const [sites, setSites] = useState("");
  const [mail, setMail] = useState(true); // 메일 발송 스위치. 끄면 화면에만 쌓인다
  const [mailTo, setMailTo] = useState(""); // 비우면 계정 메일
  const [formats, setFormats] = useState<string[]>(["pdf"]);
  const [keepDays, setKeepDays] = useState(10);
  const [windows, setWindows] = useState<Window[]>(PRESETS[0].windows);
  const [send, setSend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const help = useHelp("뉴스 브리핑");
  const windowHelp = useHelp("보고 회차");

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<Data>("/api/briefing/subscriptions"));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(task: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await task();
      await load();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  const entityList = parseEntities(entities);
  // 만드는 데 걸리는 시간 어림(서비스 estimate_minutes와 같은 식). 실측: 주제 140건 1~1.5분, 동향표 19명 약 3분.
  const estimate = style === "tracker" ? 1 + Math.round(0.15 * entityList.length + 0.5 * split(queries).length) : 2 + Math.max(1, split(queries || name).length);

  const subscribe = () =>
    act(() =>
      apiFetch("/api/briefing/subscriptions", {
        method: "POST",
        body: JSON.stringify({
          name,
          style,
          queries: style === "topic" ? split(queries || name) : split(queries),
          entities: style === "tracker" ? entityList : [],
          scope: style === "tracker" ? scope.trim() : "",
          sites: split(sites),
          mail,
          mail_to: mailTo.trim(),
          formats,
          windows,
          keep_days: keepDays,
        }),
      }),
    );
  const make = (id: string, body: Record<string, unknown>) =>
    act(() => apiFetch(`/api/briefing/subscriptions/${id}/run`, { method: "POST", body: JSON.stringify({ ...body, send }) }));
  const remove = (id: string) => act(() => apiFetch(`/api/briefing/subscriptions/${id}`, { method: "DELETE" }));
  const setWindow = (index: number, w: Window) => setWindows((prev) => prev.map((x, i) => (i === index ? w : x)));
  const recent = data?.recent_seconds ?? [];
  const recentText = recent.length ? `최근 ${recent.length}번은 ${Math.max(1, Math.round(Math.min(...recent) / 60))}~${Math.max(1, Math.round(Math.max(...recent) / 60))}분 걸렸습니다.` : "";

  return (
    <PageShell>
      <PageHeader title="뉴스 브리핑" actions={help.button} />
      {help.panel(
        <>
          <p className="break-keep">
            주제나 인물을 구독해 두면 정한 시간대에 나온 기사를 빠짐없이 모아 브리핑을 만들고, 보고 시각에 메일로 받습니다. 요약은 AI가 쓴 뒤 기사 원문과
            대조하는 2차 사실 확인(수치·발언 주체)을 거칩니다. 전에 실린 기사는 다시 싣지 않습니다.
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-caption text-on-surface-variant">
            <li>주제 브리핑: 검색어로 모은 기사를 낱말 구름 → 주요 이슈 → 그 밖의 소식 → 전체 기사 표로 정리합니다.</li>
            <li>인물·기관 동향표: 대상마다 ○ 기사 제목(날짜)·요점·보도매체를 표로 - 국회 상임위 의원 언론동향 자료의 꼴입니다.</li>
            <li>보고 회차: 기사 시간대(시계판) · 조사 시작 · 보고 시각을 회차마다 따로 둡니다. 조간·석간처럼 하루 두 번도 됩니다.</li>
            <li>만드는 시간: 주제 브리핑은 기사 100건에 1~2분, 동향표는 대상 1명당 약 10초. 보고 시각은 그만큼 뒤로 두세요.</li>
            <li>문서: PDF · 한글(hwpx) · Markdown. 메일에는 표지(낱말 구름)와 MOPAN으로 가는 단추만 들어갑니다.</li>
          </ul>
        </>,
      )}

      <form
        className="max-w-measure space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await subscribe()) {
            setName("");
            setQueries("");
            setEntities("");
            setSites("");
          }
        }}
      >
        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="브리핑 종류">
          {(
            [
              // 짧게, 끊기는 자리를 정해서(모바일 두 칸에서 읽히게). 긴 설명은 머리 줄의 ? 안에.
              ["topic", "주제 브리핑", "검색어로 모은 기사를 낱말 구름 · 주요 이슈 · 전체 기사 표로"],
              ["tracker", "인물·기관 동향표", "대상(의원·기관)마다 기사 제목 · 요점 · 보도매체를 표로"],
            ] as const
          ).map(([key, label, hint]) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={style === key}
              onClick={() => setStyle(key)}
              className={`flex h-full flex-col items-start gap-1 rounded-md border px-3 py-2.5 text-left ${style === key ? "border-primary bg-primary-container text-on-primary-container" : "border-outline-variant text-on-surface"}`}
            >
              <span className="block text-body font-medium">{label}</span>
              <span className="block break-keep text-caption leading-relaxed opacity-80">{hint}</span>
            </button>
          ))}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-label font-medium text-on-surface">
            {style === "topic" ? "주제 (브리핑 제목)" : "동향표 제목"}
            <input required maxLength={40} value={name} onChange={(e) => setName(e.target.value)} placeholder={style === "topic" ? "예) 농촌진흥청" : "예) 농해수위 의원 언론보도 동향"} className="field mt-1 w-full" />
          </label>
          <label className="block text-label font-medium text-on-surface">
            {style === "topic" ? "검색어 (쉼표로 나눠 10개까지, 비우면 주제로 검색)" : "기타 보도 키워드 (선택, 표 끝에 '기타 언론 주요 보도'로)"}
            <input value={queries} onChange={(e) => setQueries(e.target.value)} placeholder={style === "topic" ? "예) 농촌진흥청, 농진청" : "예) 농촌진흥청 국정감사, 농약 안전성, 쌀 작황"} className="field mt-1 w-full" />
          </label>
          {style === "tracker" && (
            <>
              <label className="block text-label font-medium text-on-surface sm:col-span-2">
                추적 대상 (한 줄에 한 명: 이름 | 소속 | 지역구·메모 | 직책) · {entityList.length}명
                <textarea rows={6} value={entities} onChange={(e) => setEntities(e.target.value)} placeholder={"예) 서삼석 | 더불어민주당 | 전남 영암·무안·신안 | 위원장"} className="field mt-1 h-auto w-full resize-y py-2 font-mono text-caption" />
                <button type="button" onClick={() => setEntities(NONGHAESU)} className="btn-text btn-compact mt-1">
                  농해수위 의원 명단(19명) 불러오기
                </button>
              </label>
              <label className="block text-label font-medium text-on-surface sm:col-span-2">
                관심 분야 (이 분야의 기사만 싣습니다. 비우면 전부)
                <input value={scope} maxLength={60} onChange={(e) => setScope(e.target.value)} placeholder="예) 농업·농촌·농식품·수산 분야(농촌진흥청 관련 포함)" className="field mt-1 w-full" />
              </label>
            </>
          )}
          <label className="block text-label font-medium text-on-surface">
            따로 볼 사이트 (선택)
            <input value={sites} onChange={(e) => setSites(e.target.value)} placeholder="예) rda.go.kr, korea.kr" className="field mt-1 w-full" />
          </label>
          <div className="block text-label font-medium text-on-surface">
            <span className="flex items-center justify-between gap-3">
              메일 발송
              <span className="flex items-center gap-2 text-caption font-normal text-on-surface-variant">
                {mail ? "켜짐" : "꺼짐"}
                {/* 트랙 색은 늘 같고 손잡이만 좌우로 움직인다(소유자 지시). 이 팔레트에는 white가 없어 손잡이는 primary로. */}
                <button
                  type="button"
                  role="switch"
                  aria-checked={mail}
                  aria-label="메일 발송"
                  onClick={() => setMail((v) => !v)}
                  className="relative h-6 w-11 shrink-0 rounded-full border border-outline-variant bg-surface-container-highest"
                >
                  <span className={`absolute top-0.5 h-[18px] w-[18px] rounded-full transition-[left] duration-200 ${mail ? "left-[22px] bg-primary" : "left-0.5 bg-on-surface-variant"}`} />
                </button>
              </span>
            </span>
            <input
              type="email"
              disabled={!mail}
              value={mailTo}
              onChange={(e) => setMailTo(e.target.value)}
              placeholder={mail ? `비우면 계정 메일 주소(${data?.account_email ?? "…"})로 발송됩니다` : "끄면 메일 없이 이 화면에만 쌓입니다"}
              className="field mt-1 w-full"
            />
          </div>
        </div>

        <div className="space-y-3 rounded-md bg-surface-container-low px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-label font-medium text-on-surface">
              보고 회차 <span className="font-normal text-on-surface-variant">(하루 {windows.length}회)</span>
            </p>
            {windowHelp.button}
          </div>
          {windowHelp.panel(
            <p className="break-keep text-caption">
              회차마다 기사 시간대·조사 시작·보고 시각이 따로 갑니다. 시작·끝 바늘을 끌어 기사가 나온 시간대를 정합니다(15분 단위, 자정을 넘겨도 됩니다). <b>끝</b>은 기사 모으기를 멈추는 시각이라{" "}
              <b>조사 시작</b>이 그 시각을 따라갑니다(직접 고르면 따로 갑니다). 조사 시작에 기사를 모아 브리핑을 만들고(이 구독은 약 {estimate}분 - 주제 브리핑은 기사 100건에 1~2분,
              동향표는 대상 1명당 약 10초, 사실 확인 포함. GPU가 바쁘면 2~3배), <b>보고 시각</b>에 메일을 보냅니다. 보고 시각은 조사 시작보다 만드는 시간만큼 뒤로
              두세요(예: 08:30 조사 → 09:00 보고). 비우면 만들어지는 대로 보냅니다. {recentText}
            </p>,
          )}
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button key={p.label} type="button" title={p.hint} onClick={() => setWindows(p.windows.map((w) => ({ ...w })))} className="btn-tonal btn-compact">
                {p.label}
              </button>
            ))}
            {windows.length < 3 && (
              <button
                type="button"
                onClick={() =>
                  setWindows((prev) => {
                    const last = prev[prev.length - 1];
                    const end = toHM(toMin(last.end) + 480);
                    return [...prev, { label: prev.length === 1 ? "석간" : "", start: last.end, end, run: end, deliver: toHM(toMin(end) + 30) }];
                  })
                }
                className="btn-text btn-compact"
              >
                회차 추가
              </button>
            )}
          </div>
          <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:gap-6">
            {windows.map((w, i) => (
              <div key={i} className="flex w-full flex-col items-center gap-2 rounded-md bg-surface px-3 py-3 sm:w-64">
                <div className="flex w-full items-center gap-2">
                  <input value={w.label} maxLength={20} onChange={(e) => setWindow(i, { ...w, label: e.target.value })} placeholder={`${i + 1}회차 이름 (예: 조간)`} aria-label="회차 이름" className="field w-full" />
                  {windows.length > 1 && (
                    <button type="button" onClick={() => setWindows((prev) => prev.filter((_, j) => j !== i))} className="btn-text btn-compact shrink-0">
                      빼기
                    </button>
                  )}
                </div>
                <Dial value={w} onChange={(next) => setWindow(i, next)} />
                <div className="grid w-full grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 text-caption text-on-surface">
                  <label className="flex min-w-0 flex-col justify-end">
                    조사 시작 <span className="text-on-surface-variant">(= 끝 시각)</span>
                    <select value={w.run} onChange={(e) => setWindow(i, { ...w, run: e.target.value })} className="field mt-1 w-full min-w-0">
                      {QUARTERS.map((q) => (
                        <option key={q} value={q}>
                          {pretty(q)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex min-w-0 flex-col justify-end">
                    보고 시각
                    <select value={w.deliver} onChange={(e) => setWindow(i, { ...w, deliver: e.target.value })} className="field mt-1 w-full min-w-0">
                      <option value="">만들어지는 대로</option>
                      {QUARTERS.map((q) => (
                        <option key={q} value={q}>
                          {pretty(q)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="w-full break-keep text-caption text-on-surface-variant">{scheduleText(w, estimate)}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
          <div className="flex items-center gap-x-3 text-body text-on-surface">
            <span>붙임 형식</span>
            {FORMATS.map(([key, label]) => (
              <label key={key} className="flex items-center gap-1.5">
                <input type="checkbox" checked={formats.includes(key)} onChange={(e) => setFormats((prev) => (e.target.checked ? [...prev, key] : prev.filter((f) => f !== key)))} />
                {label}
              </label>
            ))}
            <label className="flex items-center gap-1.5">
              보관
              <select value={keepDays} onChange={(e) => setKeepDays(Number(e.target.value))} className="field" aria-label="브리핑 보관 기간">
                {[10, 15, 20, 30].map((d) => (
                  <option key={d} value={d}>
                    {d}일
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button type="submit" disabled={busy || !name.trim() || formats.length === 0 || (style === "tracker" && entityList.length === 0)} className="btn-filled">
            구독 추가
          </button>
        </div>
      </form>
      <ErrorBanner message={error} />

      {data === null ? (
        !error && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : (
        <>
          <section className="space-y-3">
            <h2 className="text-title font-medium text-on-surface">내 구독</h2>
            {data.subscriptions.length === 0 ? (
              <p className="text-body text-on-surface-variant">아직 구독이 없습니다.</p>
            ) : (
              <ul className="space-y-3">
                {data.subscriptions.map((sub) => (
                  <li key={sub.id} className="rounded-md bg-surface-container-low px-4 py-3">
                    <p className="text-body font-medium text-on-surface">
                      {sub.name} <span className="font-normal text-on-surface-variant">· {sub.style === "tracker" ? `동향표 · 대상 ${sub.entities?.length ?? 0}명` : "주제 브리핑"}</span>
                    </p>
                    <p className="break-words text-caption text-on-surface-variant">
                      {sub.style === "tracker" ? (sub.scope ? `관점 ${sub.scope}` : "관점 없음") : `검색어 ${sub.queries.join(", ")}`}
                      {sub.style === "tracker" && sub.queries.length > 0 && ` · 기타 키워드 ${sub.queries.join(", ")}`}
                      {sub.sites.length > 0 && ` · 사이트 ${sub.sites.join(", ")}`} ·{" "}
                      {sub.mail_to ? `${sub.mail_to}로 발송(${sub.formats.join("·")})` : "메일 없음"} · {sub.keep_days ?? 10}일 보관
                    </p>
                    <ul className="text-caption text-on-surface-variant">
                      {sub.windows.map((w, i) => (
                        <li key={i}>{scheduleText(w, sub.estimate_minutes)}</li>
                      ))}
                    </ul>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {sub.windows.map((w, i) => (
                        <button key={i} type="button" disabled={busy} onClick={() => void make(sub.id, { window: i })} className="btn-tonal btn-compact">
                          {sub.windows.length > 1 ? `${w.label || `${i + 1}회차`} ` : ""}최근 구간으로 지금 만들기
                        </button>
                      ))}
                      <button type="button" disabled={busy} onClick={() => void make(sub.id, { hours: 24 })} className="btn-tonal btn-compact">
                        최근 24시간
                      </button>
                      {sub.mail_to && (
                        <label className="flex items-center gap-2 text-caption text-on-surface">
                          <input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} />
                          만들고 메일로도 보내기
                        </label>
                      )}
                      <button type="button" disabled={busy} onClick={() => void remove(sub.id)} className="btn-text btn-compact">
                        구독 삭제
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="space-y-3">
            <h2 className="text-title font-medium text-on-surface">브리핑</h2>
            {data.reports.length === 0 ? (
              <p className="text-body text-on-surface-variant">아직 만든 브리핑이 없습니다.</p>
            ) : (
              <ul className="space-y-3">
                {data.reports.map((report) => (
                  // id: 메일의 "MOPAN에서 보기"가 /briefing#<id>로 온다 - 그 브리핑으로 바로 내려가게.
                  <li key={report.id} id={report.id} className="scroll-mt-4 rounded-md bg-surface-container-low px-4 py-3 target:ring-2 target:ring-primary">
                    <p className="text-body font-medium text-on-surface">
                      {report.name}{" "}
                      <span className="font-normal text-on-surface-variant">
                        {report.label && ` · ${report.label}`} · {report.start && report.end ? `${report.start.slice(5)} ~ ${report.end.slice(5)}` : `${report.date} 소식`}
                      </span>
                    </p>
                    <p className={`mt-1 break-keep text-body ${report.status === "failed" ? "text-error" : "text-on-surface"}`}>{stateText(report)}</p>
                    {report.mail && <p className="text-caption text-on-surface-variant">{report.mail}</p>}
                    {report.status === "done" && !!report.articles && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        <a href={`/api/briefing/reports/${report.id}/report.html`} target="_blank" rel="noreferrer" className="btn-filled btn-compact">
                          화면으로 보기
                        </a>
                        <a href={`/api/briefing/reports/${report.id}/report.pdf`} className="btn-tonal btn-compact">
                          PDF 받기
                        </a>
                        <a href={`/api/briefing/reports/${report.id}/report.hwpx`} className="btn-tonal btn-compact">
                          한글(hwpx) 받기
                        </a>
                        <a href={`/api/briefing/reports/${report.id}/report.md`} className="btn-tonal btn-compact">
                          Markdown 받기
                        </a>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </PageShell>
  );
}
