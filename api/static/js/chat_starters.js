/**
 * Starter question 카드 — 빈 채팅방 진입 시 동적 질문 예시 3개 노출.
 *
 * 사용법 (room.html welcome 영역에서):
 *   <div class="chat-starters" id="chatStarters" data-scope="theme" data-theme-id="42"></div>
 *   <script>attachChatStarters({ scope: 'theme', themeId: 42, onPick: function(text) { chatInput.value = text; sendMessage(); } });</script>
 *
 * scope:
 *   - "general"   — id 불필요 (user_id 기반 캐시)
 *   - "theme"     — themeId 필수
 *   - "education" — topicId 필수
 */
(function (global) {
  'use strict';

  function buildUrl(opts) {
    var params = new URLSearchParams();
    params.set('scope', opts.scope);
    if (opts.scope === 'theme' && opts.themeId != null) {
      params.set('theme_id', String(opts.themeId));
    }
    if (opts.scope === 'education' && opts.topicId != null) {
      params.set('topic_id', String(opts.topicId));
    }
    return '/api/chat-starters?' + params.toString();
  }

  function renderSkeleton(container) {
    container.innerHTML = '';
    var label = document.createElement('div');
    label.className = 'chat-starters-label';
    label.textContent = '추천 질문 불러오는 중...';
    container.appendChild(label);
    for (var i = 0; i < 3; i++) {
      var sk = document.createElement('div');
      sk.className = 'starter-skeleton';
      container.appendChild(sk);
    }
  }

  function renderQuestions(container, questions, onPick) {
    container.innerHTML = '';
    if (!questions || !questions.length) {
      container.style.display = 'none';
      return;
    }
    var label = document.createElement('div');
    label.className = 'chat-starters-label';
    label.textContent = '이런 질문은 어때요?';
    container.appendChild(label);

    questions.slice(0, 3).forEach(function (q) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'starter-card';
      btn.textContent = q;
      btn.addEventListener('click', function () {
        // 카드 그룹 즉시 사라짐 — 재클릭 방지
        try {
          container.parentNode && container.parentNode.removeChild(container);
        } catch (e) {
          container.style.display = 'none';
        }
        if (typeof onPick === 'function') {
          onPick(q);
        }
      });
      container.appendChild(btn);
    });
  }

  /**
   * @param {Object} opts
   * @param {string} opts.scope
   * @param {number} [opts.themeId]
   * @param {number} [opts.topicId]
   * @param {string} [opts.containerId='chatStarters']
   * @param {Function} opts.onPick - (questionText) => void
   */
  function attachChatStarters(opts) {
    var containerId = opts.containerId || 'chatStarters';
    var container = document.getElementById(containerId);
    if (!container) return;

    renderSkeleton(container);

    fetch(buildUrl(opts), { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('starter fetch ' + res.status);
        return res.json();
      })
      .then(function (data) {
        var questions = (data && data.questions) || [];
        renderQuestions(container, questions, opts.onPick);
      })
      .catch(function (err) {
        // 실패 시 카드 영역 그냥 숨김 — 채팅 자체는 멀쩡
        console.warn('[chat_starters] fetch 실패:', err);
        container.style.display = 'none';
      });
  }

  global.attachChatStarters = attachChatStarters;
})(window);
