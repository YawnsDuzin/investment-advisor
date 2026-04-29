/* 공용 SSE 로그 뷰어 — 분석/번역/systemd 카드에서 공유 사용
 *
 * window.attachSseLog(panelId, url, opts?)
 *   panelId : 로그를 append 할 <pre> 또는 컨테이너 element id
 *   url     : EventSource URL (예: /admin/systemd/units/analyzer/logs/stream)
 *   opts.maxLines : 최대 라인 수 (기본 1000)
 *   opts.reverse  : true 면 최신 로그가 최상단에 표시 (기본 false — 시간순 append)
 *
 * window.detachSseLog(panelId) — EventSource 닫기
 */
(function () {
  const _conns = new Map();

  function attachSseLog(panelId, url, opts) {
    opts = opts || {};
    const panel = document.getElementById(panelId);
    if (!panel) return;
    detachSseLog(panelId);
    const maxLines = opts.maxLines || 1000;
    const reverse = !!opts.reverse;
    const es = new EventSource(url);
    es.onmessage = function (e) {
      const line = document.createElement('div');
      line.textContent = e.data;
      if (reverse) {
        // 최신 라인을 최상단에 — 사용자가 스크롤 안 해도 최신 자동 표시
        panel.insertBefore(line, panel.firstChild);
        while (panel.childNodes.length > maxLines) {
          panel.removeChild(panel.lastChild);
        }
        panel.scrollTop = 0;
      } else {
        panel.appendChild(line);
        while (panel.childNodes.length > maxLines) {
          panel.removeChild(panel.firstChild);
        }
        panel.scrollTop = panel.scrollHeight;
      }
    };
    es.addEventListener('done', function () {
      detachSseLog(panelId);
      if (opts && opts.onDone) opts.onDone();
    });
    es.onerror = function () {
      /* EventSource 자동 재연결 */
      if (opts && opts.onError) opts.onError();
    };
    _conns.set(panelId, es);
  }

  function detachSseLog(panelId) {
    const es = _conns.get(panelId);
    if (es) {
      es.close();
      _conns.delete(panelId);
    }
  }

  window.attachSseLog = attachSseLog;
  window.detachSseLog = detachSseLog;
})();
