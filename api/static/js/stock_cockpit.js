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

  // ── Hero (overview) ──
  var qs = market ? ('?market=' + encodeURIComponent(market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(ticker) + '/overview' + qs)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
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
  // (window.__cockpit = {ticker, market, qs, fmtPrice, fmtPct, fmtBigNum, fmtNum, escHtml, getProposals} 로 공유)
  var _proposalsPromise = null;
  function getProposals() {
    if (_proposalsPromise === null) {
      _proposalsPromise = fetch('/api/stocks/' + encodeURIComponent(ticker) + '/proposals')
        .then(function(r) { return r.ok ? r.json() : Promise.reject(); });
    }
    return _proposalsPromise;
  }
  window.__cockpit = {
    ticker: ticker, market: market, qs: qs,
    fmtPrice: fmtPrice, fmtPct: fmtPct,
    fmtBigNum: fmtBigNum, fmtNum: fmtNum,
    escHtml: escHtml,
    getProposals: getProposals,
  };
})();

// ── § 1 가격 차트 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof LightweightCharts === 'undefined') return;

  var container = document.getElementById('price-chart');
  container.innerHTML = '';
  var chart = LightweightCharts.createChart(container, {
    height: 380,
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

// ── § 2-A 벤치마크 상대성과 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof LightweightCharts === 'undefined') return;

  // 시장 → 벤치마크 자동 선택
  var BENCH_MAP = {
    'KOSPI':  ['KOSPI', 'KOSDAQ'],
    'KOSDAQ': ['KOSDAQ', 'KOSPI'],
    'NASDAQ': ['NDX100', 'SP500'],
    'NYSE':   ['SP500', 'NDX100'],
  };
  var benches = BENCH_MAP[c.market] || ['SP500'];
  var defaultBench = benches[0];

  // 벤치마크 토글 버튼 동적 생성
  var toggleEl = document.getElementById('benchmark-toggle');
  toggleEl.innerHTML = '';
  benches.forEach(function(code, i) {
    var btn = document.createElement('button');
    btn.dataset.bench = code;
    btn.textContent = code;
    if (i === 0) btn.classList.add('active');
    toggleEl.appendChild(btn);
  });

  var container = document.getElementById('benchmark-chart');
  container.innerHTML = '';
  var chart = LightweightCharts.createChart(container, {
    height: 260,
    layout: { background: { color: 'transparent' }, textColor: '#a0a0a0' },
    grid: { vertLines: { color: '#2a2a2a' }, horzLines: { color: '#2a2a2a' } },
    rightPriceScale: { borderColor: '#3a3a3a' },
    timeScale: { borderColor: '#3a3a3a' },
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

  var stockLine = chart.addLineSeries({ color: '#4ea3ff', lineWidth: 2, title: c.ticker });
  var benchLine = chart.addLineSeries({ color: '#f1c40f', lineWidth: 2, title: defaultBench });

  var stockCache = null;  // CKPT-3: stock OHLCV 응답 캐시 (토글 시 재조회 회피)

  function fetchStock() {
    if (stockCache) return Promise.resolve(stockCache);
    var stockUrl = '/api/stocks/' + encodeURIComponent(c.ticker) + '/ohlcv?days=360' +
                   (c.market ? '&market=' + encodeURIComponent(c.market) : '');
    return fetch(stockUrl).then(function(r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function(d) { stockCache = d; return d; });
  }

  function normalize(series) {
    if (!series.length) return [];
    var base = series[0].close;
    if (!base || base === 0) return [];
    return series.map(function(p) { return { time: p.date, value: +(p.close / base * 100).toFixed(2) }; });
  }

  function loadAndRender(benchCode) {
    var benchUrl = '/api/indices/' + benchCode + '/ohlcv?days=360';
    Promise.all([
      fetchStock(),
      fetch(benchUrl).then(function(r) { return r.ok ? r.json() : Promise.reject(); }),
    ]).then(function(results) {
      var stockData = results[0].series || [];
      var benchData = results[1].series || [];
      if (!stockData.length || !benchData.length) {
        showOverlay('데이터 부족');
        return;
      }
      hideOverlay();

      // CKPT-2: 양쪽 모두 존재하는 첫 거래일을 기준일로 통일
      var benchDates = new Set(benchData.map(function(p) { return p.date; }));
      var commonAlignedStart = null;
      for (var i = 0; i < stockData.length; i++) {
        if (benchDates.has(stockData[i].date)) {
          commonAlignedStart = stockData[i].date;
          break;
        }
      }
      if (!commonAlignedStart) {
        showOverlay('두 시리즈에 공통 거래일이 없음');
        return;
      }
      var s = stockData.filter(function(p) { return p.date >= commonAlignedStart; });
      var b = benchData.filter(function(p) { return p.date >= commonAlignedStart; });
      stockLine.setData(normalize(s));
      benchLine.setData(normalize(b));
      benchLine.applyOptions({ title: benchCode });
      chart.timeScale().fitContent();
    }).catch(function() {
      showOverlay('벤치마크 데이터 조회 실패');
    });
  }

  loadAndRender(defaultBench);

  document.querySelectorAll('#benchmark-toggle button').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('#benchmark-toggle button').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      loadAndRender(btn.dataset.bench);
    });
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
