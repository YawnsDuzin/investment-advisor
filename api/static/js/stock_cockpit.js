/* Stock Cockpit — Phase 1 (Hero/§1/§2-A/§6) + Phase 2 (§2-B/§3 sector/§5)
 * Phase 1 인라인에서 분리됨 (Phase 2 Task 1).
 * 의존: lightweight-charts (CDN, 페이지 인라인), Chart.js (Phase 2 Task 6 부터).
 */
(function() {
  var ticker = document.getElementById('stock-cockpit').dataset.ticker;
  var market = document.getElementById('stock-cockpit').dataset.market;

  var CURRENCY_SYMBOLS = {KRW:'₩', USD:'$', EUR:'€', JPY:'¥', GBP:'£', CNY:'¥', HKD:'HK$', TWD:'NT$'};
  var INT_CURRENCIES = ['KRW', 'JPY'];

  function fmtPrice(v, cur) {
    if (v == null) return '-';
    var sym = CURRENCY_SYMBOLS[cur] || '';
    if (INT_CURRENCIES.indexOf(cur) >= 0) return sym + v.toLocaleString('ko-KR', {maximumFractionDigits:0});
    return sym + v.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function fmtBigNum(v, cur) {
    if (v == null) return '-';
    var sym = CURRENCY_SYMBOLS[cur] || '';
    if (Math.abs(v) >= 1e12) return sym + (v/1e12).toFixed(1) + '조';
    if (Math.abs(v) >= 1e8) return sym + Math.round(v/1e8) + '억';
    if (Math.abs(v) >= 1e6) return sym + (v/1e6).toFixed(1) + 'M';
    return sym + v.toLocaleString();
  }
  function fmtNum(v) {
    if (v == null) return '-';
    return v.toLocaleString('en-US', {maximumFractionDigits:2});
  }
  function escHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtPct(v, withSign) {
    if (v == null) return '-';
    if (withSign === undefined) withSign = true;
    var s = (withSign && v > 0 ? '+' : '') + v.toFixed(2) + '%';
    return s;
  }

  // ── Hero (overview) — getOverview() 공유 promise 사용 (§ 2-B / § 5 도 같은 캐시) ──
  var qs = market ? ('?market=' + encodeURIComponent(market)) : '';

  // _overviewPromise / getOverview 는 IIFE-0 본문 끝에서 정의 (window.__cockpit export 위해).
  // 단, var 선언은 hoist 되어도 할당은 hoist 안 되므로 여기서 호출 시점에 _overviewPromise 가
  // undefined → `=== null` 비교 false → return undefined → .then() TypeError 발생 가능.
  // 방어: getOverview 안의 비교를 truthy 체크로 (아래 정의 부분에서 처리).
  getOverview()
    .then(function(d) {
      document.getElementById('hero-loading').style.display = 'none';
      document.getElementById('hero-body').style.display = 'block';
      document.getElementById('stock-name').textContent = (d.name || ticker) + ' (' + ticker + ')';

      var cur = d.currency || '';
      if (d.latest) {
        document.getElementById('h-price').textContent = fmtPrice(d.latest.close, cur);
        var ch = document.getElementById('h-change');
        if (d.latest.change_pct != null) {
          ch.textContent = fmtPct(d.latest.change_pct, true);
          ch.style.color = d.latest.change_pct >= 0 ? 'var(--green)' : 'var(--red)';
        }
        document.getElementById('h-volume').textContent =
          d.latest.volume ? d.latest.volume.toLocaleString() + '주' : '-';
      }
      if (d.sector || d.industry) {
        document.getElementById('h-sector').textContent =
          (d.sector || '') + (d.industry ? ' / ' + d.industry : '');
      } else {
        document.getElementById('h-sector-wrap').style.display = 'none';
      }

      var s = d.stats || {};
      document.getElementById('h-ai-score').textContent =
        (s.ai_score != null ? s.ai_score : '-') + (s.ai_score != null ? '/100' : '');
      document.getElementById('h-prop-count').textContent =
        (s.proposal_count != null ? s.proposal_count + '회' : '-');
      document.getElementById('h-avg-3m').textContent = fmtPct(s.avg_post_return_3m_pct, true);
      document.getElementById('h-alpha').textContent = fmtPct(s.alpha_vs_benchmark_pct, true);
      document.getElementById('h-factor').textContent =
        s.factor_pctile_avg != null ? Math.round(s.factor_pctile_avg * 100) + '%ile' : '-';

      // AI 종합 점수 tooltip 산식
      var sb = d.score_breakdown || {};
      if (sb.weights) {
        document.getElementById('chip-ai').title =
          'factor ' + sb.factor_score + ' × ' + sb.weights.factor +
          ' + hist ' + sb.hist_score + ' × ' + sb.weights.hist +
          ' + consensus ' + sb.consensus_score + ' × ' + sb.weights.consensus;
      }
    })
    .catch(function() {
      document.getElementById('hero-loading').textContent = 'Hero 데이터 조회 실패';
    });

  // ── 펀더멘털 8카드 (기존 fundamentals API 그대로 사용) ──
  fetch('/api/stocks/' + encodeURIComponent(ticker) + '/fundamentals' + qs)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(d) {
      renderFundamentals(d);
    })
    .catch(function() {
      document.getElementById('fundamentals-error').style.display = 'block';
      document.getElementById('fundamentals-grid').style.display = 'none';
    });

  function pctClass(v) {
    if (v == null) return '';
    return v > 0 ? 'positive' : v < 0 ? 'negative' : '';
  }
  function addMetric(id, label, value, cls) {
    if (value == null) value = '-';
    var c = document.getElementById(id);
    if (!c) return;
    var row = document.createElement('div');
    row.className = 'fund-metric-row';
    row.innerHTML = '<span class="fund-metric-label">' + label + '</span>' +
                    '<span class="fund-metric-value' + (cls ? ' ' + cls : '') + '">' + value + '</span>';
    c.appendChild(row);
  }

  function renderFundamentals(d) {
    var cur = d.currency || '';
    var v = d.valuation || {};
    addMetric('valuation-metrics', 'PER (Trailing)', fmtNum(v.trailing_pe));
    addMetric('valuation-metrics', 'PER (Forward)', fmtNum(v.forward_pe));
    addMetric('valuation-metrics', 'PBR', fmtNum(v.pb_ratio));
    addMetric('valuation-metrics', 'PSR', fmtNum(v.ps_ratio));
    addMetric('valuation-metrics', 'PEG', fmtNum(v.peg_ratio));
    addMetric('valuation-metrics', 'EV/EBITDA', fmtNum(v.ev_ebitda));
    addMetric('valuation-metrics', 'EPS (Trailing)', fmtPrice(v.eps_trailing, cur));
    addMetric('valuation-metrics', 'EPS (Forward)', fmtPrice(v.eps_forward, cur));

    var p = d.profitability || {};
    addMetric('profitability-metrics', 'ROE', fmtPct(p.roe, true), pctClass(p.roe));
    addMetric('profitability-metrics', 'ROA', fmtPct(p.roa, true), pctClass(p.roa));
    addMetric('profitability-metrics', '매출총이익률', fmtPct(p.gross_margin, true), pctClass(p.gross_margin));
    addMetric('profitability-metrics', '영업이익률', fmtPct(p.operating_margin, true), pctClass(p.operating_margin));
    addMetric('profitability-metrics', '순이익률', fmtPct(p.net_margin, true), pctClass(p.net_margin));
    addMetric('profitability-metrics', 'EBITDA', fmtBigNum(p.ebitda, cur));

    var h = d.health || {};
    addMetric('health-metrics', '부채비율 (D/E)', fmtNum(h.debt_to_equity));
    addMetric('health-metrics', '유동비율', fmtNum(h.current_ratio));
    addMetric('health-metrics', '당좌비율', fmtNum(h.quick_ratio));
    addMetric('health-metrics', '총 부채', fmtBigNum(h.total_debt, cur));
    addMetric('health-metrics', '보유 현금', fmtBigNum(h.total_cash, cur));

    var g = d.growth || {};
    addMetric('growth-metrics', '매출 성장률 (YoY)', fmtPct(g.revenue_growth, true), pctClass(g.revenue_growth));
    addMetric('growth-metrics', '이익 성장률 (YoY)', fmtPct(g.earnings_growth, true), pctClass(g.earnings_growth));
    addMetric('growth-metrics', '분기 이익 성장률', fmtPct(g.earnings_quarterly_growth, true), pctClass(g.earnings_quarterly_growth));

    var dv = d.dividend || {};
    addMetric('dividend-metrics', '배당수익률', fmtPct(dv.dividend_yield, true));
    addMetric('dividend-metrics', '배당금', dv.dividend_rate != null ? fmtPrice(dv.dividend_rate, cur) : '-');
    addMetric('dividend-metrics', '배당성향', fmtPct(dv.payout_ratio, true));

    var cf = d.cashflow || {};
    addMetric('cashflow-metrics', '영업현금흐름', fmtBigNum(cf.operating_cashflow, cur), pctClass(cf.operating_cashflow));
    addMetric('cashflow-metrics', '잉여현금흐름 (FCF)', fmtBigNum(cf.free_cashflow, cur), pctClass(cf.free_cashflow));

    var t = d.technical || {};
    addMetric('technical-metrics', 'Beta', fmtNum(t.beta));
    addMetric('technical-metrics', '50일 이동평균', fmtPrice(t.fifty_day_avg, cur));
    addMetric('technical-metrics', '200일 이동평균', fmtPrice(t.two_hundred_day_avg, cur));
    if (t.fifty_day_avg && d.price) {
      var vs50 = ((d.price - t.fifty_day_avg) / t.fifty_day_avg * 100);
      addMetric('technical-metrics', '50일선 대비', fmtPct(vs50, true), vs50 >= 0 ? 'positive' : 'negative');
    }
    if (t.two_hundred_day_avg && d.price) {
      var vs200 = ((d.price - t.two_hundred_day_avg) / t.two_hundred_day_avg * 100);
      addMetric('technical-metrics', '200일선 대비', fmtPct(vs200, true), vs200 >= 0 ? 'positive' : 'negative');
    }

    var a = d.analyst || {};
    addMetric('analyst-metrics', '추천', a.recommendation ? escHtml(a.recommendation.toUpperCase()) : '-');
    addMetric('analyst-metrics', '목표가 (평균)', fmtPrice(a.target_mean, cur));
    addMetric('analyst-metrics', '목표가 (저)', fmtPrice(a.target_low, cur));
    addMetric('analyst-metrics', '목표가 (고)', fmtPrice(a.target_high, cur));
    if (a.target_mean && d.price) {
      var upside = ((a.target_mean - d.price) / d.price * 100);
      addMetric('analyst-metrics', '상승여력', fmtPct(upside, true), upside >= 0 ? 'positive' : 'negative');
    }
    addMetric('analyst-metrics', '분석 기관 수', a.num_analysts != null ? a.num_analysts + '개' : '-');

    // Hero 시총이 비어있으면 펀더멘털 시총으로 폴백
    var mcapEl = document.getElementById('h-mcap');
    if (mcapEl.textContent === '-' && d.market_cap) {
      mcapEl.textContent = fmtBigNum(d.market_cap, cur);
    }
  }

  // 차트/타임라인 모듈은 다음 task 들에서 추가됨
  // (window.__cockpit = {ticker, market, qs, fmtPrice, fmtPct, fmtBigNum, fmtNum, escHtml, getProposals, getOverview} 로 공유)
  // 변수 선언은 hoist 되지만 할당은 hoist 안 됨 — IIFE-0 위에서 getOverview() 호출 가능.
  // 따라서 truthy 체크 (!promise) 로 undefined·null 모두 안전 처리.
  var _proposalsPromise = null;
  function getProposals() {
    if (!_proposalsPromise) {
      _proposalsPromise = fetch('/api/stocks/' + encodeURIComponent(ticker) + '/proposals')
        .then(function(r) { return r.ok ? r.json() : Promise.reject(); });
    }
    return _proposalsPromise;
  }
  var _overviewPromise = null;
  function getOverview() {
    if (!_overviewPromise) {
      _overviewPromise = fetch('/api/stocks/' + encodeURIComponent(ticker) + '/overview' + qs)
        .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); });
    }
    return _overviewPromise;
  }
  window.__cockpit = {
    ticker: ticker, market: market, qs: qs,
    fmtPrice: fmtPrice, fmtPct: fmtPct,
    fmtBigNum: fmtBigNum, fmtNum: fmtNum,
    escHtml: escHtml,
    getProposals: getProposals,
    getOverview: getOverview,
  };
})();

// ── § 1 가격 차트 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof LightweightCharts === 'undefined') return;

  var container = document.getElementById('price-chart');
  container.innerHTML = '';
  var chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,  // 초기 width 명시 — autoSize 가 후속 resize 추적
    height: 380,
    autoSize: true,                 // 컨테이너 width 변경 자동 추적 (ResizeObserver)
    layout: { background: { color: 'transparent' }, textColor: '#a0a0a0' },
    grid: { vertLines: { color: '#2a2a2a' }, horzLines: { color: '#2a2a2a' } },
    rightPriceScale: { borderColor: '#3a3a3a' },
    timeScale: { borderColor: '#3a3a3a', timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  // overlay 셋업 — 에러 시 차트 인스턴스 보존하며 메시지만 덮어씌움
  container.style.position = 'relative';
  var overlay = document.createElement('div');
  overlay.className = 'chart-overlay';
  overlay.style.cssText =
    'position:absolute;top:0;left:0;width:100%;height:100%;display:none;' +
    'align-items:center;justify-content:center;text-align:center;color:var(--text-muted);' +
    'background:var(--bg-card);border-radius:10px;z-index:10;';
  container.appendChild(overlay);

  function showOverlay(msg) {
    overlay.textContent = msg;
    overlay.style.display = 'flex';
  }
  function hideOverlay() {
    overlay.style.display = 'none';
  }

  var lineSeries = chart.addLineSeries({ color: '#4ea3ff', lineWidth: 2 });
  var ma50Series = chart.addLineSeries({
    color: '#f5a623', lineWidth: 1, title: 'MA50', priceLineVisible: false, lastValueVisible: false,
  });
  var ma200Series = chart.addLineSeries({
    color: '#9b59b6', lineWidth: 1, title: 'MA200', priceLineVisible: false, lastValueVisible: false,
  });
  var volSeries = chart.addHistogramSeries({
    color: '#3a3a3a', priceFormat: { type: 'volume' },
    priceScaleId: '', scaleMargins: { top: 0.85, bottom: 0 },
  });

  var currentRange = 360;

  function movingAvg(series, n) {
    var out = []; var sum = 0; var q = [];
    for (var i = 0; i < series.length; i++) {
      var c2 = series[i].close;
      q.push(c2); sum += c2;
      if (q.length > n) sum -= q.shift();
      if (q.length === n) out.push({ time: series[i].date, value: +(sum / n).toFixed(4) });
    }
    return out;
  }

  function loadOhlcv(days) {
    var url = '/api/stocks/' + encodeURIComponent(c.ticker) + '/ohlcv?days=' + days +
              (c.market ? '&market=' + encodeURIComponent(c.market) : '');
    return fetch(url).then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); });
  }

  var proposalsCache = null;  // 추천 마커 원본 (한 번만 fetch)

  function buildMarkers(items, firstDate, lastDate) {
    var markers = items
      .filter(function(it) {
        var dt = (it.created_at || it.analysis_date || '').slice(0, 10);
        return dt >= firstDate && dt <= lastDate;
      })
      .map(function(it) {
        var positive = (it.post_return_3m_pct == null) || it.post_return_3m_pct >= 0;
        return {
          time: (it.created_at || it.analysis_date).slice(0, 10),
          position: 'belowBar',
          color: positive ? '#27ae60' : '#c0392b',
          shape: 'arrowUp',
          text: '추천' + (it.entry_price ? ' @' + c.fmtPrice(it.entry_price, '') : ''),
        };
      });
    markers.sort(function(a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    return markers;
  }

  function refreshMarkers(seriesData) {
    if (!proposalsCache || !proposalsCache.length || !seriesData.length) return;
    var firstDate = seriesData[0].date;
    var lastDate = seriesData[seriesData.length - 1].date;
    var markers = buildMarkers(proposalsCache, firstDate, lastDate);
    lineSeries.setMarkers(markers);  // 빈 배열 전달이 마커 클리어 역할
  }

  function applyData(d) {
    if (!d.series || !d.series.length) {
      showOverlay('OHLCV 데이터 수집 대기 중');
      return;
    }
    hideOverlay();
    var prices = d.series.map(function(p) { return { time: p.date, value: p.close }; });
    var vols = d.series.map(function(p) {
      return { time: p.date, value: p.volume || 0,
               color: (p.change_pct != null && p.change_pct < 0) ? '#c0392b66' : '#27ae6066' };
    });
    lineSeries.setData(prices);
    ma50Series.setData(movingAvg(d.series, 50));
    ma200Series.setData(movingAvg(d.series, 200));
    volSeries.setData(vols);

    if (proposalsCache === null) {
      c.getProposals()
        .then(function(p) {
          proposalsCache = (p && p.items) ? p.items : [];
          refreshMarkers(d.series);
        })
        .catch(function() { proposalsCache = []; });
    } else {
      refreshMarkers(d.series);
    }

    chart.timeScale().fitContent();
  }

  loadOhlcv(currentRange).then(applyData)
    .catch(function() {
      showOverlay('차트 데이터 조회 실패');
    });

  // 기간 토글
  document.querySelectorAll('#range-toggle button').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('#range-toggle button').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentRange = parseInt(btn.dataset.range, 10);
      loadOhlcv(currentRange).then(applyData)
        .catch(function() { console.warn('차트 재로드 실패'); });
    });
  });
})();

// ── § 2-A 벤치마크 상대성과 — 종목 + 4개 시장 인덱스 동시 비교 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof LightweightCharts === 'undefined') return;

  // 4개 벤치마크 인덱스 + 색상 (어두운 배경에서 잘 보이는 톤)
  var BENCH_INDICES = [
    { code: 'KOSPI',  color: '#f1c40f' }, // 노랑
    { code: 'KOSDAQ', color: '#e67e22' }, // 주황
    { code: 'SP500',  color: '#9b59b6' }, // 보라
    { code: 'NDX100', color: '#16a085' }, // 청록
  ];

  // 토글 영역은 안내 문구로 대체 (단일 선택 → 전체 동시 비교로 정책 변경)
  var toggleEl = document.getElementById('benchmark-toggle');
  if (toggleEl) {
    toggleEl.innerHTML = '<span style="font-size:11px;color:var(--text-muted);">전체 시장 동시 비교</span>';
  }

  var container = document.getElementById('benchmark-chart');
  container.innerHTML = '';
  var chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 260,
    autoSize: true,
    layout: { background: { color: 'transparent' }, textColor: '#a0a0a0' },
    grid: { vertLines: { color: '#2a2a2a' }, horzLines: { color: '#2a2a2a' } },
    rightPriceScale: { borderColor: '#3a3a3a' },
    timeScale: { borderColor: '#3a3a3a' },
  });
  // overlay 셋업
  container.style.position = 'relative';
  var overlay = document.createElement('div');
  overlay.className = 'chart-overlay';
  overlay.style.cssText =
    'position:absolute;top:0;left:0;width:100%;height:100%;display:none;' +
    'align-items:center;justify-content:center;text-align:center;color:var(--text-muted);' +
    'background:var(--bg-card);border-radius:10px;z-index:10;';
  container.appendChild(overlay);

  function showOverlay(msg) { overlay.textContent = msg; overlay.style.display = 'flex'; }
  function hideOverlay() { overlay.style.display = 'none'; }

  // 종목 라인 (파랑) + 4개 벤치마크 라인
  var stockLine = chart.addLineSeries({ color: '#4ea3ff', lineWidth: 2, title: c.ticker });
  var benchSeries = BENCH_INDICES.map(function(b) {
    return {
      code: b.code,
      line: chart.addLineSeries({ color: b.color, lineWidth: 1.5, title: b.code }),
    };
  });

  function normalize(series) {
    if (!series.length) return [];
    var base = series[0].close;
    if (!base || base === 0) return [];
    return series.map(function(p) {
      return { time: p.date, value: +(p.close / base * 100).toFixed(2) };
    });
  }

  function fetchOhlcv(url) {
    return fetch(url).then(function(r) { return r.ok ? r.json() : null; })
      .catch(function() { return null; });
  }

  // 종목 + 4 인덱스 병렬 fetch
  var stockUrl = '/api/stocks/' + encodeURIComponent(c.ticker) + '/ohlcv?days=360' +
                 (c.market ? '&market=' + encodeURIComponent(c.market) : '');
  var benchUrls = BENCH_INDICES.map(function(b) {
    return '/api/indices/' + b.code + '/ohlcv?days=360';
  });

  Promise.all([fetchOhlcv(stockUrl)].concat(benchUrls.map(fetchOhlcv)))
    .then(function(results) {
      var stockResp = results[0];
      var stockData = (stockResp && stockResp.series) || [];
      if (!stockData.length) {
        showOverlay('종목 OHLCV 데이터 없음');
        return;
      }
      hideOverlay();

      // 종목 = 자기 첫 거래일 기준 100
      stockLine.setData(normalize(stockData));

      // 각 벤치마크도 자기 첫 거래일 기준 100 (트렌드 비교 우선, 통화·거래일 차이 무시)
      var anyBenchOk = false;
      benchSeries.forEach(function(bs, i) {
        var resp = results[i + 1];
        var data = (resp && resp.series) || [];
        if (data.length) {
          bs.line.setData(normalize(data));
          anyBenchOk = true;
        } else {
          bs.line.setData([]);
        }
      });

      if (!anyBenchOk) {
        showOverlay('벤치마크 인덱스 데이터 없음 — universe-sync --mode indices 필요');
        return;
      }
      chart.timeScale().fitContent();
    });
})();

// ── § 6 추천 이력 타임라인 ──
(function() {
  var c = window.__cockpit;
  if (!c) return;

  function esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function pctSpan(v) {
    if (v == null) return '<strong>-</strong>';
    var cls = v > 0 ? 'tl-pos' : v < 0 ? 'tl-neg' : '';
    return '<strong class="' + cls + '">' + (v > 0 ? '+' : '') + v.toFixed(2) + '%</strong>';
  }

  function renderTimeline(items) {
    var listEl = document.getElementById('timeline-list');
    var emptyEl = document.getElementById('timeline-empty');
    if (!items || !items.length) {
      emptyEl.style.display = 'block';
      return;
    }
    items.forEach(function(it) {
      var card = document.createElement('div');
      card.className = 'timeline-card';
      var dt = (it.created_at || it.analysis_date || '').slice(0, 10);
      var entry = it.entry_price != null ? ' · 진입 ' + c.fmtPrice(it.entry_price, '') : '';
      var validation = '';
      if (it.validation_mismatches && it.validation_mismatches.length) {
        validation = ' <span class="tl-warn" title="AI 제시값과 실측 mismatch">⚠ ' +
                     it.validation_mismatches.length + '</span>';
      }
      var rationale = (it.rationale || '');
      var rationaleHtml = esc(rationale.slice(0, 240)) + (rationale.length > 240 ? '...' : '');
      card.innerHTML =
        '<div class="timeline-card-head">' +
          '<span class="timeline-date">' + dt + '</span>' +
          '<a class="timeline-theme" href="/pages/themes#theme-' + it.theme_id + '">' +
            esc(it.theme_name || '-') + '</a>' +
        '</div>' +
        '<div class="timeline-rationale">' + rationaleHtml + '</div>' +
        '<div class="timeline-metrics">' +
          '<span>' + esc((it.action || '-').toUpperCase()) + ' · ' + esc(it.conviction || '-') + entry + validation + '</span>' +
          '<span>1m ' + pctSpan(it.post_return_1m_pct) + '</span>' +
          '<span>3m ' + pctSpan(it.post_return_3m_pct) + '</span>' +
          '<span>6m ' + pctSpan(it.post_return_6m_pct) + '</span>' +
          '<span>1y ' + pctSpan(it.post_return_1y_pct) + '</span>' +
          '<span>MDD ' + pctSpan(it.max_drawdown_pct) + '</span>' +
          '<span>α ' + pctSpan(it.alpha_vs_benchmark_pct) + '</span>' +
        '</div>';
      listEl.appendChild(card);
    });
  }

  c.getProposals()
    .then(function(d) { renderTimeline(d.items || []); })
    .catch(function() {
      var emptyEl = document.getElementById('timeline-empty');
      emptyEl.textContent = '추천 이력 조회 실패';
      emptyEl.style.display = 'block';
    });
})();

// ── § 3 섹터 팩터 분위 ──
(function() {
  var c = window.__cockpit;
  if (!c) return;

  var FACTORS = [
    { key: "r1m", label: "1개월 모멘텀", unit: "%" },
    { key: "r3m", label: "3개월 모멘텀", unit: "%" },
    { key: "r6m", label: "6개월 모멘텀", unit: "%" },
    { key: "r12m", label: "12개월 모멘텀", unit: "%" },
    { key: "low_vol", label: "저변동성 (60d σ)", unit: "%" },
    { key: "volume", label: "거래량 비율 (20d/60d)", unit: "x" },
  ];

  var emptyEl = document.getElementById('sector-stats-empty');
  var tableEl = document.getElementById('sector-stats-table');
  var bodyEl = document.getElementById('sector-stats-body');
  if (!emptyEl || !tableEl || !bodyEl) return;

  emptyEl.style.display = 'block';

  var qs = c.market ? ('?market=' + encodeURIComponent(c.market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/sector-stats' + qs)
    .then(function(r) {
      if (r.status === 404) return null;
      return r.ok ? r.json() : Promise.reject();
    })
    .then(function(d) {
      if (!d) {
        emptyEl.textContent = '섹터 정보 없음';
        return;
      }
      if (!d.sector_size || d.sector_size < 5) {
        emptyEl.textContent = '섹터 표본 부족 (' + (d.sector_size || 0) + '개) — 분위 계산 불가';
        return;
      }
      emptyEl.style.display = 'none';
      tableEl.style.display = 'table';

      FACTORS.forEach(function(f) {
        var rank = (d.ranks || {})[f.key] || {};
        var valKey = f.key === "volume" ? "value_ratio" : "value_pct";
        var rawVal = rank[valKey];
        var pctile = rank.sector_pctile;
        var topPct = rank.sector_top_pct;

        var row = document.createElement('tr');
        row.innerHTML =
          '<td style="padding:8px 12px;border-top:1px solid var(--border);">' + c.escHtml(f.label) + '</td>' +
          '<td style="padding:8px 12px;border-top:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;">' +
            (rawVal != null ? c.fmtNum(rawVal) + (f.unit === '%' ? '%' : 'x') : '-') +
          '</td>' +
          '<td style="padding:8px 12px;border-top:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;">' +
            (pctile != null ? c.fmtNum(pctile * 100) + '%ile' : '-') +
          '</td>' +
          '<td style="padding:8px 12px;border-top:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;">' +
            (topPct != null ? '상위 ' + topPct + '%' : '-') +
          '</td>';
        bodyEl.appendChild(row);
      });
    })
    .catch(function() {
      emptyEl.textContent = '섹터 분위 조회 실패';
    });
})();

// ── § 2-B 정량 팩터 레이더 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof Chart === 'undefined') return;

  var canvas = document.getElementById('factor-radar');
  var emptyEl = document.getElementById('factor-radar-empty');
  if (!canvas) return;

  c.getOverview()
    .then(function(d) {
      var snap = d.factor_snapshot;
      if (!snap) {
        canvas.style.display = 'none';
        emptyEl.style.display = 'block';
        return;
      }

      var labels = ['1m', '3m', '6m', '12m', '저변동', '거래량'];
      var values = [
        snap.r1m_pctile, snap.r3m_pctile, snap.r6m_pctile,
        snap.r12m_pctile, snap.low_vol_pctile, snap.volume_pctile,
      ].map(function(v) { return v != null ? +(v * 100).toFixed(1) : 0; });

      // 시장 중앙선 (0.5) — 점선 데이터셋
      var midline = labels.map(function() { return 50; });

      new Chart(canvas, {
        type: 'radar',
        data: {
          labels: labels,
          datasets: [
            {
              label: c.ticker,
              data: values,
              backgroundColor: 'rgba(78, 163, 255, 0.18)',
              borderColor: '#4ea3ff',
              borderWidth: 2,
              pointBackgroundColor: '#4ea3ff',
            },
            {
              label: '시장 중앙 (50%ile)',
              data: midline,
              borderColor: 'rgba(160, 160, 160, 0.6)',
              borderWidth: 1,
              borderDash: [4, 4],
              pointRadius: 0,
              fill: false,
            },
          ],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { labels: { color: '#a0a0a0', font: { size: 11 } } },
            tooltip: {
              callbacks: {
                label: function(ctx) {
                  return ctx.dataset.label + ': ' + ctx.raw + '%ile';
                },
              },
            },
          },
          scales: {
            r: {
              min: 0, max: 100,
              ticks: { display: false, stepSize: 20 },
              grid: { color: '#2a2a2a' },
              angleLines: { color: '#2a2a2a' },
              pointLabels: { color: '#a0a0a0', font: { size: 12 } },
            },
          },
        },
      });
    })
    .catch(function() {
      canvas.style.display = 'none';
      emptyEl.textContent = '레이더 데이터 조회 실패';
      emptyEl.style.display = 'block';
    });
})();

// ── § 5 시장 특화 수급·공매도·지수 — KRX 는 시계열 차트, US 는 단일 시점 ──
(function() {
  var c = window.__cockpit;
  if (!c) return;

  var section = document.getElementById('sec-krx');
  if (!section) return;
  section.style.display = 'block';

  var contentEl = document.getElementById('krx-content');
  var emptyEl = document.getElementById('krx-empty');

  function labelsFor(marketType) {
    if (marketType === 'KRX') {
      return {
        ownership: '외국인 보유율 추세',
        flow: '외국인·기관 순매수',
        short: '공매도 잔고 추이',
        index: '지수 편입',
      };
    }
    return {
      ownership: '기관 보유',
      flow: 'Insider 순매수 신호',
      short: 'Short interest (Float %)',
      index: '지수 편입',
    };
  }

  // 차트 공통 옵션 (다크 테마)
  function lineChartConfig(label, dataPoints, color) {
    return {
      type: 'line',
      data: {
        labels: dataPoints.map(function(p) { return p.date.slice(5); }),
        datasets: [{
          label: label,
          data: dataPoints.map(function(p) { return p.value; }),
          borderColor: color,
          backgroundColor: color + '33',
          borderWidth: 1.5,
          fill: true,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
        scales: {
          x: { ticks: { color: '#a0a0a0', font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
               grid: { color: 'rgba(255,255,255,0.04)' } },
          y: { ticks: { color: '#a0a0a0', font: { size: 10 } },
               grid: { color: 'rgba(255,255,255,0.04)' } },
        },
      },
    };
  }

  // 외국인 + 기관 일별 순매수 — grouped bar (나란히), 색상 고정 (부호는 0 기준 위/아래 위치로 표현)
  function flowBarConfig(flowPoints) {
    return {
      type: 'bar',
      data: {
        labels: flowPoints.map(function(p) { return p.date.slice(5); }),
        datasets: [
          {
            label: '외국인',
            data: flowPoints.map(function(p) { return p.foreign; }),
            backgroundColor: '#4ea3ff',  // 파랑 — 외국인 정체성 고정
            borderWidth: 0,
          },
          {
            label: '기관',
            data: flowPoints.map(function(p) { return p.institution; }),
            backgroundColor: '#f5a623',  // 주황 — 기관 정체성 고정
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#a0a0a0', font: { size: 10 }, boxWidth: 10 } },
          tooltip: {
            mode: 'index', intersect: false,
            callbacks: {
              label: function(ctx) {
                var v = ctx.raw;
                if (v == null) return ctx.dataset.label + ': -';
                var sign = v > 0 ? '+' : '';
                return ctx.dataset.label + ': ' + sign + v + '억';
              },
            },
          },
        },
        scales: {
          // grouped (stacked: false) — 같은 시점에 외국인·기관 막대 나란히
          x: { ticks: { color: '#a0a0a0', font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
               grid: { display: false } },
          y: { ticks: { color: '#a0a0a0', font: { size: 10 } },
               grid: { color: 'rgba(255,255,255,0.04)',
                       // 0 기준선 강조
                       lineWidth: function(ctx) { return ctx.tick.value === 0 ? 1 : 0.5; },
                       color: function(ctx) { return ctx.tick.value === 0 ? '#a0a0a0' : 'rgba(255,255,255,0.04)'; } } },
        },
      },
    };
  }

  function renderKrx(d) {
    var series = d.series || {};

    // 카드 1: 외국인 보유율 시계열 (라인)
    if (series.ownership && series.ownership.length && typeof Chart !== 'undefined') {
      var canvas = document.getElementById('krx-ownership-canvas');
      new Chart(canvas, lineChartConfig('외국인 보유율 (%)', series.ownership, '#4ea3ff'));
    }
    document.getElementById('krx-foreign-pct').textContent =
      d.ownership_pct != null ? c.fmtNum(d.ownership_pct) + '%' : '-';

    // 카드 2: 외국인+기관 순매수 일별 막대
    if (series.flow && series.flow.length && typeof Chart !== 'undefined') {
      var canvas2 = document.getElementById('krx-flow-canvas');
      new Chart(canvas2, flowBarConfig(series.flow));
    }
    var sigMap = {
      'positive': { text: '▲ 순매수', color: 'var(--green)' },
      'neutral':  { text: '◆ 중립',   color: 'var(--text-muted)' },
      'negative': { text: '▼ 순매도', color: 'var(--red)' },
    };
    var sigInfo = sigMap[d.flow_signal] || { text: '-', color: 'var(--text-muted)' };
    var sigEl = document.getElementById('krx-foreign-signal');
    sigEl.textContent = sigInfo.text; sigEl.style.color = sigInfo.color;
    document.getElementById('krx-flow-summary').textContent = d.flow_summary || '';

    // 카드 3: 공매도 잔고 시계열 (라인) + 현재 % 헤더
    if (series.short && series.short.length && typeof Chart !== 'undefined') {
      var canvas3 = document.getElementById('krx-short-canvas');
      new Chart(canvas3, lineChartConfig('공매도 잔고 비중 (%)', series.short, '#e74c3c'));
    }
    var sqMap = {
      'low':    { color: 'var(--green)', label: '낮음' },
      'medium': { color: '#eab308',      label: '중간' },
      'high':   { color: 'var(--red)',   label: '높음' },
    };
    var sqInfo = sqMap[d.squeeze_risk] || { color: 'var(--text-muted)', label: '-' };
    var sqEl = document.getElementById('krx-squeeze-label');
    sqEl.textContent = sqInfo.label + (d.short_pct != null ? ' ' + c.fmtNum(d.short_pct) + '%' : '');
    sqEl.style.color = sqInfo.color;
  }

  function renderUs(d) {
    // US 는 단일 시점 — 카드 1: 도넛, 카드 2: 신호 텍스트, 카드 3: 게이지 비슷
    var canvas = document.getElementById('krx-ownership-canvas');
    var fp = d.ownership_pct;
    if (fp != null && typeof Chart !== 'undefined') {
      new Chart(canvas, {
        type: 'doughnut',
        data: {
          labels: ['기관', '기타'],
          datasets: [{ data: [fp, Math.max(0, 100 - fp)],
                       backgroundColor: ['#4ea3ff', 'rgba(255,255,255,0.08)'], borderWidth: 0 }],
        },
        options: { responsive: true, maintainAspectRatio: false, cutout: '70%',
                   plugins: { legend: { display: false }, tooltip: { enabled: false } } },
      });
    }
    document.getElementById('krx-foreign-pct').textContent =
      fp != null ? c.fmtNum(fp) + '%' : '-';

    // Insider 신호 — 카드 2 자리에 큰 텍스트
    var flowCanvas = document.getElementById('krx-flow-canvas');
    if (flowCanvas) flowCanvas.style.display = 'none';
    var sigMap = {
      'positive': { text: '▲ Net buy',  color: 'var(--green)' },
      'neutral':  { text: '◆ Neutral',  color: 'var(--text-muted)' },
      'negative': { text: '▼ Net sell', color: 'var(--red)' },
    };
    var sigInfo = sigMap[d.flow_signal] || { text: '-', color: 'var(--text-muted)' };
    var sigEl = document.getElementById('krx-foreign-signal');
    sigEl.textContent = sigInfo.text; sigEl.style.color = sigInfo.color;
    document.getElementById('krx-flow-summary').textContent = d.flow_summary || 'yfinance .info — Insider activity';

    // Short interest — 카드 3 자리에 단일 게이지 텍스트
    var shortCanvas = document.getElementById('krx-short-canvas');
    if (shortCanvas) shortCanvas.style.display = 'none';
    var sqMap = {
      'low':    { color: 'var(--green)', label: '낮음' },
      'medium': { color: '#eab308',      label: '중간' },
      'high':   { color: 'var(--red)',   label: '높음' },
    };
    var sqInfo = sqMap[d.squeeze_risk] || { color: 'var(--text-muted)', label: '-' };
    var sqEl = document.getElementById('krx-squeeze-label');
    sqEl.textContent = sqInfo.label + (d.short_pct != null ? ' ' + c.fmtNum(d.short_pct) + '%' : '');
    sqEl.style.color = sqInfo.color;
  }

  function renderIndices(indices) {
    var idxEl = document.getElementById('krx-index-membership');
    idxEl.innerHTML = '';
    if (!indices || indices.length === 0) {
      idxEl.innerHTML = '<span style="color:var(--text-muted);font-size:13px;">미편입 / 데이터 없음</span>';
      return;
    }
    indices.forEach(function(idx) {
      var span = document.createElement('span');
      span.textContent = idx;
      span.style.cssText = 'display:inline-block;padding:3px 8px;background:rgba(78,163,255,0.15);' +
                           'border:1px solid rgba(78,163,255,0.4);border-radius:4px;font-size:12px;color:var(--accent);';
      idxEl.appendChild(span);
    });
  }

  var qs = c.market ? ('?market=' + encodeURIComponent(c.market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/extended-supply' + qs)
    .then(function(r) {
      if (r.status === 404) return null;
      return r.ok ? r.json() : Promise.reject();
    })
    .then(function(d) {
      if (!d) {
        contentEl.style.display = 'none';
        emptyEl.textContent = '시장 수급 데이터 조회 실패 — pykrx/yfinance 응답 없음';
        emptyEl.style.display = 'block';
        return;
      }
      var labels = labelsFor(d.market_type);
      var t1 = document.getElementById('krx-ownership-title'); if (t1) t1.textContent = labels.ownership;
      var t2 = document.getElementById('krx-flow-title');      if (t2) t2.textContent = labels.flow;
      var t3 = document.getElementById('krx-short-title');     if (t3) t3.textContent = labels.short;
      var t4 = document.getElementById('krx-index-title');     if (t4) t4.textContent = labels.index;

      if (d.market_type === 'KRX') renderKrx(d);
      else                          renderUs(d);
      renderIndices(d.index_membership);
    })
    .catch(function() {
      contentEl.style.display = 'none';
      emptyEl.textContent = '시장 수급 데이터 조회 실패';
      emptyEl.style.display = 'block';
    });
})();
