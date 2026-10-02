"use client";

import { useEffect, useRef, useState } from "react";

/** 마이크 받아적기 - 누르면 듣고, 다시 누르면 들은 문장을 `onText`로 준다. Web Speech API가 없는 브라우저는
 * `supported`가 false라 버튼을 그리지 않는다. components/chat/Composer.tsx의 것과 같은 동작(ko-KR, 최종 결과만).
 * ponytail: 채팅 입력창은 아직 자기 복사본을 쓴다 - 다음에 그쪽을 손댈 때 이 훅으로 합친다. */
export function useSpeechInput(onText: (heard: string) => void) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef<{ stop: () => void } | null>(null);
  const heardRef = useRef("");
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  useEffect(() => {
    const w = window as unknown as Record<string, unknown>;
    setSupported(Boolean(w.SpeechRecognition || w.webkitSpeechRecognition));
    return () => recognitionRef.current?.stop();
  }, []);

  function toggle() {
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const w = window as unknown as Record<string, unknown>;
    const Recognition = (w.SpeechRecognition || w.webkitSpeechRecognition) as
      | (new () => {
          lang: string;
          continuous: boolean;
          interimResults: boolean;
          onresult: ((event: { resultIndex: number; results: { length: number; [i: number]: { isFinal: boolean; 0: { transcript: string } } } }) => void) | null;
          onend: (() => void) | null;
          onerror: (() => void) | null;
          start: () => void;
          stop: () => void;
        })
      | undefined;
    if (!Recognition) return;
    const recognition = new Recognition();
    recognition.lang = "ko-KR";
    recognition.continuous = true;
    recognition.interimResults = false;
    heardRef.current = "";
    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) heardRef.current += event.results[i][0].transcript;
      }
    };
    recognition.onerror = () => {
      /* onend가 뒤따라 정리한다 */
    };
    recognition.onend = () => {
      setListening(false);
      recognitionRef.current = null;
      const heard = heardRef.current.trim();
      heardRef.current = "";
      if (heard) onTextRef.current(heard);
    };
    recognitionRef.current = recognition;
    setListening(true);
    recognition.start();
  }

  return { supported, listening, toggle };
}
