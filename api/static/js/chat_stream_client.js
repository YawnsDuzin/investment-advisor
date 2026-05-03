// api/static/js/chat_stream_client.js
// SSE 기반 채팅 스트림 클라이언트 컨트롤러
//
// 사용:
//   const stream = attachChatStream("general", sessionId, {
//     onReplay: (text, startedAt) => {...},   // 기존 진행 중 응답 재연결 시
//     onToken: (text) => {...},               // 토큰 단위 스트리밍
//     onDone: ({message_id, final_text}) => {...},  // 응답 완료
//     onError: ({message, code}) => {...},    // 서버 명시 에러
//     onIdle: () => {...},                    // 대기 상태 (진행 중 응답 없음)
//   });
//   stream.detach();   // 페이지 떠날 때 SSE 연결 해제
//
// 자동 재연결: onerror 시 지수 백오프 (1s → 2s → 4s → ... → max 30s)
// kind: 'general' | 'theme' | 'education'

(function (global) {
  /**
   * SSE 채팅 스트림 연결
   * @param {string} kind - 채팅 종류 ('general' | 'theme' | 'education')
   * @param {number|string} sessionId - 세션 ID
   * @param {Object} callbacks - 이벤트 콜백
   * @returns {{ detach: Function }} - 연결 해제 핸들러
   */
  function attachChatStream(kind, sessionId, callbacks) {
    let es = null;        // EventSource 인스턴스
    let retry = 0;        // 재연결 시도 횟수
    let detached = false; // detach() 호출 여부

    function connect() {
      if (detached) return;

      const url = `/api/chat-stream/${kind}/${sessionId}`;
      es = new EventSource(url, { withCredentials: true });

      // 기존 진행 중 응답 재연결 — 현재까지 누적 텍스트 전달
      es.addEventListener('replay', function(e) {
        try {
          const d = JSON.parse(e.data);
          callbacks.onReplay && callbacks.onReplay(d.text, d.started_at);
        } catch (_) { /* 파싱 실패 무시 */ }
      });

      // 토큰 단위 스트리밍 — 누적이 아닌 증분 텍스트
      es.addEventListener('token', function(e) {
        try {
          const d = JSON.parse(e.data);
          callbacks.onToken && callbacks.onToken(d.text);
        } catch (_) { /* 파싱 실패 무시 */ }
      });

      // 응답 완료 — message_id + 최종 전체 텍스트
      // ★ 종결 이벤트이므로 EventSource 를 닫고 자동 재연결 차단.
      //   broker 가 completed 채널을 600s TTL 로 보관 → 재연결 시 같은 done 이 또 도착하여
      //   클라이언트에 중복 bubble 이 누적되는 버그 방지.
      es.addEventListener('done', function(e) {
        try {
          const d = JSON.parse(e.data);
          callbacks.onDone && callbacks.onDone(d);
        } catch (_) { /* 파싱 실패 무시 */ }
        detached = true;
        if (es) { es.close(); es = null; }
      });

      // 에러 이벤트 — 서버 명시 에러 또는 네트워크 오류
      es.addEventListener('error', function(e) {
        if (e && e.data) {
          // 서버가 명시적으로 에러 데이터를 전송한 경우 — 종결 이벤트로 취급
          try {
            const d = JSON.parse(e.data);
            callbacks.onError && callbacks.onError(d);
            detached = true;
            if (es) { es.close(); es = null; }
            return;
          } catch (_) { /* fallthrough — 네트워크 에러로 처리 */ }
        }

        // 네트워크 단절 → 지수 백오프로 자동 재연결
        if (es) {
          es.close();
          es = null;
        }
        if (detached) return;

        retry += 1;
        const delay = Math.min(30000, 1000 * Math.pow(2, retry - 1));
        setTimeout(connect, delay);
      });

      // 유휴 상태 — 현재 진행 중인 응답 없음 (placeholder 유지)
      es.addEventListener('idle', function() {
        callbacks.onIdle && callbacks.onIdle();
      });
    }

    connect();

    return {
      /** SSE 연결 해제 (페이지 떠날 때 호출) */
      detach: function() {
        detached = true;
        if (es) {
          es.close();
          es = null;
        }
      },
    };
  }

  // 전역 노출
  global.attachChatStream = attachChatStream;
})(window);
