"use client";

import { useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageHeader from "@/components/layout/PageHeader";
import PageShell from "@/components/layout/PageShell";
import DataTable from "@/components/ui/DataTable";
import ErrorBanner from "@/components/ui/ErrorBanner";

interface WebSite {
  pattern: string;
  name: string;
  description: string;
}

/** 입력한 주소에서 도메인만. 서버의 정규화(soonkun/mcp_server/web normalize)와 같은 규칙의 화면용 축약 - 최종
 * 판정은 서버가 한다. */
function hostOf(address: string): string {
  return address.trim().replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[/?#]/)[0].toLowerCase().replace(/^www\./, "");
}

/** 주소에 경로가 붙어 있는가(https://site/sub/page). 있으면 "사이트 전체"와 "이 경로 아래만"은 다른 뜻이다. */
function hasPath(address: string): boolean {
  const rest = address.trim().replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[?#]/)[0];
  return rest.replace(/\/+$/, "").includes("/");
}

/** 인터넷 설정 - 인터넷 검색이 드나들 수 있는 사이트 목록. 목록은 검색 서버(soonkun/mcp_server/web)가 갖고
 * 경계도 그 서버가 지킨다 - 이 화면은 그 목록을 읽고 통째로 바꿔 쓴다. 비관리자와 검색 서버가 설정되지 않은
 * 배포는 서버의 한국어 거절(403·404)이 그대로 오류 줄에 나온다. */
export default function InternetPage() {
  const [sites, setSites] = useState<WebSite[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pattern, setPattern] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // 주소를 통째로 붙여 넣는 것이 보통이고(첫 화면 주소 …/main.do), 그 뜻은 대개 "이 사이트"다. 경로까지 그대로
  // 등록하면 그 한 페이지만 허용돼 검색이 아무것도 못 찾는다(2026-10-01 실사고) - 그래서 기본은 사이트 전체.
  const [pathOnly, setPathOnly] = useState(false);
  // 고치는 중인 행의 주소. null이면 새로 추가.
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiFetch<{ sites: WebSite[] }>("/api/mcp/web-sites")
      .then((res) => setSites(res.sites))
      .catch((err) => setError(errorMessage(err)));
  }, []);

  async function save(next: WebSite[]): Promise<boolean> {
    setSaving(true);
    setError(null);
    try {
      // 서버가 정규화한 목록(https://www. 를 떼고 중복을 합친 것)을 그대로 보인다.
      const res = await apiFetch<{ sites: WebSite[] }>("/api/mcp/web-sites", {
        method: "PUT",
        body: JSON.stringify({ sites: next }),
      });
      setSites(res.sites);
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  function resetForm() {
    setPattern("");
    setName("");
    setDescription("");
    setPathOnly(false);
    setEditing(null);
  }

  function startEdit(site: WebSite) {
    setPattern(site.pattern);
    setName(site.name);
    setDescription(site.description ?? "");
    setPathOnly(site.pattern.includes("/"));
    setEditing(site.pattern);
  }

  return (
    <PageShell>
      <PageHeader title="인터넷 설정" />
      <section className="space-y-3 rounded-md bg-surface-container-low p-6">
        <h2 className="text-title font-medium">검색 허용 사이트</h2>
        <div className="notice">
          <ul className="list-disc space-y-1 pl-5 text-on-surface-variant">
            <li>
              프롬프트 창에서 인터넷을 켠 질문은 <strong>여기 등록된 사이트 안에서만</strong> 검색하고
              읽습니다. 목록에 없는 주소는 사용자가 직접 적어 줘도 읽지 않습니다.
            </li>
            <li>
              도메인을 등록하면 하위 도메인까지 허용됩니다(rda.go.kr → www.rda.go.kr, ncpms.rda.go.kr).
              주소를 통째로 붙여 넣어도 기본은 사이트 전체이고, 특정 경로 아래만 허용하려면 추가할 때
              &quot;이 경로 아래만&quot;을 고릅니다(예: korea.kr/news).
            </li>
            <li>
              <strong>설명</strong>에는 그 사이트에 어떤 정보가 있는지 적습니다. 질문이 들어오면 이 설명을
              보고 찾아볼 사이트를 고르므로, 적어 둘수록 알맞은 곳을 찾습니다.
            </li>
          </ul>
        </div>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!sites) return;
            const entry = { pattern: pathOnly && hasPath(pattern) ? pattern : hostOf(pattern), name, description };
            const next = editing ? sites.map((s) => (s.pattern === editing ? entry : s)) : [...sites, entry];
            if (await save(next)) resetForm();
          }}
          className="grid gap-3 sm:grid-cols-[2fr_2fr_auto] sm:items-end"
        >
          <div>
            <label htmlFor="web-pattern" className="text-label font-medium text-on-surface-variant">
              사이트 주소
            </label>
            <input
              id="web-pattern"
              value={pattern}
              onChange={(e) => setPattern(e.target.value)}
              required
              maxLength={300}
              placeholder="예) rda.go.kr 또는 https://www.korea.kr/news"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label htmlFor="web-name" className="text-label font-medium text-on-surface-variant">
              이름 (선택)
            </label>
            <input
              id="web-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={100}
              placeholder="예) 농촌진흥청"
              className="field mt-1 w-full"
            />
          </div>
          <div className="flex gap-2">
            <button type="submit" disabled={saving || sites === null} className="btn-filled">
              {editing ? "저장" : "추가"}
            </button>
            {editing && (
              <button type="button" onClick={resetForm} className="btn-tonal">
                취소
              </button>
            )}
          </div>
          <div className="sm:col-span-3">
            <label htmlFor="web-description" className="text-label font-medium text-on-surface-variant">
              설명 (선택) - 이 사이트에 어떤 정보가 있는지
            </label>
            <input
              id="web-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={300}
              placeholder="예) 농업기술·재배법, 병해충 발생 정보, 연구성과 보도자료, 농업인 지원사업 공고"
              className="field mt-1 w-full"
            />
          </div>
          {hasPath(pattern) && (
            <fieldset className="space-y-1 text-body sm:col-span-3">
              <legend className="text-label font-medium text-on-surface-variant">허용 범위</legend>
              <label className="flex items-center gap-2">
                <input type="radio" name="web-scope" checked={!pathOnly} onChange={() => setPathOnly(false)} />
                <span>
                  사이트 전체 <span className="text-on-surface-variant">({hostOf(pattern)}와 그 하위 도메인)</span>
                </span>
              </label>
              <label className="flex items-center gap-2">
                <input type="radio" name="web-scope" checked={pathOnly} onChange={() => setPathOnly(true)} />
                <span>
                  이 경로 아래만 <span className="text-on-surface-variant">(다른 페이지는 읽지 않습니다)</span>
                </span>
              </label>
            </fieldset>
          )}
        </form>
        <ErrorBanner message={error} />
        {sites === null ? (
          !error && <p className="py-4 text-center text-body text-on-surface-variant">불러오는 중...</p>
        ) : sites.length === 0 ? (
          <p className="py-4 text-center text-body text-on-surface-variant">
            허용된 사이트가 없습니다. 사이트를 등록하기 전에는 인터넷 검색이 아무것도 찾지 않습니다.
          </p>
        ) : (
          <DataTable caption="인터넷 검색 허용 사이트 목록">
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">사이트</th>
                <th scope="col" className="px-3 py-3">주소</th>
                <th scope="col" className="px-3 py-3">허용 범위</th>
                <th scope="col" className="px-3 py-3">관리</th>
              </tr>
            </thead>
            <tbody>
              {sites.map((site) => (
                <tr key={site.pattern} className="border-b border-outline-variant align-top">
                  <td className="px-3 py-3">
                    <div className="font-medium">{site.name || "-"}</div>
                    <div className="text-caption text-on-surface-variant">
                      {site.description || "설명 없음"}
                    </div>
                  </td>
                  <td className="break-all px-3 py-3">{site.pattern}</td>
                  <td className="px-3 py-3">
                    {site.pattern.includes("/") ? "이 경로 아래만" : "사이트 전체"}
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={saving}
                        onClick={() => startEdit(site)}
                        className="btn-tonal btn-compact"
                      >
                        수정
                      </button>
                      {site.pattern.includes("/") && (
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() =>
                            void save(
                              sites.map((s) =>
                                s.pattern === site.pattern ? { ...s, pattern: hostOf(s.pattern) } : s,
                              ),
                            )
                          }
                          className="btn-tonal btn-compact"
                        >
                          사이트 전체로
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={saving}
                        onClick={() => void save(sites.filter((s) => s.pattern !== site.pattern))}
                        className="btn-danger btn-compact"
                      >
                        삭제
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </section>
    </PageShell>
  );
}
