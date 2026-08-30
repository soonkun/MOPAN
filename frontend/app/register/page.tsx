"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { User } from "@/lib/types";

export default function RegisterPage() {
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
      await apiFetch<User>("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      await apiFetch<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      router.push("/chat");
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-shell flex items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-6 rounded-lg bg-surface-container-low p-8"
      >
        <h1 className="text-headline font-medium">회원가입</h1>
        <p className="text-body text-on-surface-variant">첫 번째 계정은 관리자 권한을 갖습니다.</p>
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
            minLength={8}
            // bcrypt caps at 72 BYTES; maxLength counts characters, so this only
            // closes the ASCII path. Korean is 3 bytes/char - the backend guard
            // in schemas/auth.py is still the authority.
            maxLength={72}
            autoComplete="new-password"
            placeholder="비밀번호 (8자 이상)"
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
          {loading ? "가입 중..." : "가입하기"}
        </button>
        <p className="text-center text-body text-on-surface-variant">
          <Link href="/login" className="text-primary underline">
            로그인으로 돌아가기
          </Link>
        </p>
      </form>
    </div>
  );
}
