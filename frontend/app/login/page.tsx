"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage, safeNextPath } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { User } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await apiFetch<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      // Read at submit time rather than via useSearchParams, which would force
      // a Suspense boundary on this page under Next 15's static rendering.
      const next = new URLSearchParams(window.location.search).get("next");
      router.push(safeNextPath(next));
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-6 rounded-lg bg-surface-container-low p-8"
      >
        <h1 className="text-headline font-medium">
          <span className="text-gradient-brand">MOPAN</span>
        </h1>
        <div>
          <label htmlFor="email" className="sr-only">
            이메일
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            placeholder="이메일"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="field w-full"
          />
        </div>
        <div>
          <label htmlFor="password" className="sr-only">
            비밀번호
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            placeholder="비밀번호"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="field w-full"
          />
        </div>
        <ErrorBanner message={error} />
        <button
          type="submit"
          disabled={loading}
          className="btn-filled w-full"
        >
          {loading ? "로그인 중..." : "로그인"}
        </button>
        <p className="text-center text-body text-on-surface-variant">
          계정이 없으신가요?{" "}
          <Link href="/register" className="text-primary underline">
            회원가입
          </Link>
        </p>
      </form>
    </div>
  );
}
