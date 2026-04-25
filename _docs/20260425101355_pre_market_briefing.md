# 프리마켓 브리핑 시스템 (v34)

**도입일**: 2026-04-25
**관련 마이그레이션**: v34 (`pre_market_briefings` 테이블)
**관련 모듈**: `analyzer/overnight_us.py`, `analyzer/briefing_main.py`, `analyzer/prompts.py` (`BRIEFING_*`), `api/routes/briefing.py`, `api/templates/briefing.html`

## 1. 배경

미국 장 마감(EDT 16:00 → KST 05:00 / EST 16:00 → KST 06:00) 직후 한국 시장 개장(09:00) 전에 다음을 자동으로 정리해 투자자에게 제공:

1. 미국에서 의미 있게 움직인 섹터·종목 (Top movers)
2. 그에 따라 한국 시장에서 갭 상승이 유력한 수혜 종목 (sector_norm 매핑)
3. 짧은 모닝 코멘트 (장 시작 전 한 줄 요약)

기존 03:00 KST 분석 배치는 미국 장이 끝나기 *전* OHLCV 로 동작하여 오버나이트 데이터가 누락되는 구조적 결함이 있었다.

## 2. 데이터 흐름

```
06:30 KST (매일, systemd timer)
  ├─ universe-sync-price.service       (KRX+US OHLCV 일괄 sync)
  ├─ pre-market-briefing.service       (After=universe-sync-price)
  │   ├─ compute_us_overnight_summary()    OHLCV 집계 → top_movers/sectors/indices
  │   ├─ compute_regime()                  B2 레짐 스냅샷 재사용
  │   ├─ fetch_kr_beneficiaries_by_sectors()  sector_norm 공통키로 KR 후보군
  │   ├─ Claude SDK BRIEFING_PROMPT 호출   us_summary.groups + kr_impact + morning_brief
  │   ├─ _validate_kr_picks()               화이트리스트 검증 (AI 환각 차단)
  │   ├─ pre_market_briefings UPSERT       briefing_date PK 멱등 저장
  │   └─ _generate_briefing_notifications() 워치리스트/구독 매칭 → user_notifications
  └─ investment-advisor-analyzer.service (After=pre-market-briefing)
```

## 3. 데이터 모델 (v34)

### `pre_market_briefings`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| `briefing_date` | DATE PK | KST 기준 브리핑 날짜 |
| `source_trade_date` | DATE | 미국 OHLCV 거래일 (KST/EST 시차로 1일 다름) |
| `status` | VARCHAR | success / partial / skipped / failed |
| `us_summary` | JSONB | OHLCV 집계 원본 (top_movers, sector_aggregates, indices) |
| `briefing_data` | JSONB | LLM 출력 (us_summary.groups, kr_impact, morning_brief) |
| `regime_snapshot` | JSONB | B2 레짐 (KOSPI/SP500/NDX100 등) |
| `error_message` | TEXT | 실패/부분 사유 |
| `generated_at`, `updated_at` | TIMESTAMPTZ | 생성·갱신 시각 |

PK가 `briefing_date` 1개라 하루 1건. 같은 날 재실행 시 UPSERT.

## 4. 핵심 모듈

### `analyzer/overnight_us.py`
- `compute_us_overnight_summary(db_cfg)`: 단일 SQL 패스로 NASDAQ/NYSE/AMEX OHLCV 최신일을 sector_norm 별로 집계 + Top movers/losers + 인덱스(SP500/NDX100). 결측 시 빈 dict.
- `fetch_kr_beneficiaries_by_sectors(db_cfg, sectors)`: 주어진 sector_norm 리스트에 매칭되는 KOSPI/KOSDAQ 종목을 시총 desc + 1M 수익률 포함 후보 풀로 반환. **LLM에 화이트리스트로 주입되어 종목 발명 차단**.
- `format_us_summary_text(snap)` / `format_kr_candidates_text(cands)`: 프롬프트 삽입용 한글 텍스트.

### `analyzer/prompts.py`
- `BRIEFING_SYSTEM`: SYSTEM_PROMPT_BASE + 한국 투자자용 프리마켓 작성자 역할 + 수치 추정 금지 + 한국 종목 화이트리스트 강제.
- `BRIEFING_PROMPT`: 입력은 `{regime_section}`/`{us_summary_section}`/`{kr_candidates_section}`/`{date}`/`{trade_date}`. 출력 JSON: `us_summary.groups[].{sector_norm,label,top_movers,catalyst}` + `kr_impact[].{sector_norm,label,strength,korean_picks[],catalysts_kr,related_etfs}` + `morning_brief`.

### `analyzer/briefing_main.py`
- `main()` / `run_briefing_pipeline()`: 엔트리포인트. `python -m analyzer.briefing_main` 으로 단독 실행.
- `_validate_kr_picks(briefing, kr_candidates, log)`: LLM이 후보 풀 밖 종목을 반환하면 제거 + WARNING 로그. asset_name도 후보 풀 값으로 교정.
- `_save_briefing(...)`: pre_market_briefings UPSERT (briefing_date PK).
- `_generate_briefing_notifications(...)`: `user_subscriptions` 의 `sub_type='ticker'`/`'theme'(=sector_norm)` 매칭 → `user_notifications` INSERT (같은 날 같은 sub 중복 방지).

### `api/routes/briefing.py`
- `GET /api/briefing/today`: 최신 1건
- `GET /api/briefing/{date}`: 특정 날짜
- `GET /api/briefing`: 최근 14건 메타 목록
- `GET /pages/briefing` / `GET /pages/briefing/{date}`: HTML 페이지

### `api/templates/briefing.html`
첨부 이미지(KOSPI/코스닥 수혜 시나리오 카드)와 동일 레이아웃:
- 미국 섹터 카드 (이모지 + Top movers 칩 + 카탈리스트 한 줄)
- 한국 수혜 카드 (strength 배지 + 종목 그리드 + expected_open_change_pct)
- 모닝 브리핑 텍스트 박스
- 시장 레짐 디버깅 영역 (200MA/vol/1M)

## 5. systemd 스케줄 (06:30 일괄 정렬)

미국 장 마감 직후 시각으로 모든 일일 배치를 06:30에 정렬. systemd `After=` 의존성으로 직렬 실행 보장.

```
06:30 (매일 트리거)
  → universe-sync-price          ← KRX+US OHLCV 일괄 sync
  → pre-market-briefing          ← After=universe-sync-price
  → investment-advisor-analyzer  ← After=pre-market-briefing
07:30 universe-sync-meta        (일요일만)
07:45 monthly-sector-refresh    (매월 1일)
08:00 ohlcv-cleanup             (일요일만)
```

3개 unit이 모두 06:30 타이머로 트리거되지만 `After=` 체인으로 순서 보장. 하나가 실패해도 다음 unit은 진행 (Wants/Requires 미사용).

## 6. AI 환각 차단 메커니즘

LLM이 한국 시장 종목을 자유롭게 추천하면 존재하지 않는 티커가 나올 수 있다. 다음 3중 차단:

1. **프롬프트 레이어**: `BRIEFING_SYSTEM`에 "후보에 없는 종목 추천 시 즉시 무효 처리" 명시.
2. **데이터 레이어**: `fetch_kr_beneficiaries_by_sectors()` 가 `stock_universe` 화이트리스트만 후보 풀에 주입.
3. **후처리 레이어**: `_validate_kr_picks()` 가 풀 밖 ticker 강제 제거 + asset_name 교정.

## 7. 알림 매핑 규칙

| 구독 타입 | 매칭 키 | 발화 조건 |
|---|---|---|
| `ticker` | `sub_key` = ticker | 해당 ticker 가 어떤 group의 `korean_picks` 에 등장 |
| `theme` | `sub_key` = sector_norm | 해당 섹터 group 에 picks 1개 이상 존재 |

같은 (user_id, sub_id, briefing_date) 조합은 1건만 INSERT. 링크는 `/pages/briefing/{briefing_date}`.

## 8. 운영 체크리스트

### 첫 배포 (라즈베리파이)
```bash
cd /home/pi/investment-advisor/deploy/systemd
# 플레이스홀더 치환 → /etc/systemd/system 복사 (README 참고)
sudo systemctl daemon-reload

# 기존 timer 6:30 적용 (단순 disable→enable로 OnCalendar 갱신 적용)
sudo systemctl restart investment-advisor-analyzer.timer
sudo systemctl restart universe-sync-price.timer
sudo systemctl restart universe-sync-meta.timer
sudo systemctl restart monthly-sector-refresh.timer
sudo systemctl restart ohlcv-cleanup.timer

# 신규 브리핑 활성화
sudo systemctl enable --now pre-market-briefing.timer

# 검증
sudo systemctl list-timers --all | grep -E "advisor|briefing|sync|sector|ohlcv"
sudo systemctl start pre-market-briefing.service        # 즉시 1회 실행
journalctl -u pre-market-briefing.service -n 100 --no-pager
```

### 검증 쿼리
```sql
-- 최신 브리핑 상태
SELECT briefing_date, source_trade_date, status,
       jsonb_array_length(briefing_data->'kr_impact') AS impact_groups,
       generated_at
FROM pre_market_briefings
ORDER BY briefing_date DESC LIMIT 7;

-- 어제 브리핑의 한국 픽 펼치기
SELECT g->>'label' AS sector,
       g->>'strength' AS strength,
       p->>'ticker' AS ticker,
       p->>'asset_name' AS name,
       p->>'expected_open_change_pct' AS expected_gap
FROM pre_market_briefings,
     jsonb_array_elements(briefing_data->'kr_impact') g,
     jsonb_array_elements(g->'korean_picks') p
WHERE briefing_date = CURRENT_DATE
ORDER BY g->>'sector_norm', (p->>'ticker');
```

### 트러블슈팅
- **status='skipped'**: 미국 OHLCV 데이터 없음 → universe-sync-price 실행 여부 확인.
- **status='partial'**: LLM 호출 실패. `error_message='llm_failed'` + `us_summary` JSONB만 채워짐. ai_query_archive에서 raw response 확인.
- **status='failed'**: 파이프라인 예외. `app_logs` 에서 run_id로 추적.
- **kr_impact 빈 배열**: KR 후보 풀에 매칭되는 sector_norm 종목 없음. `stock_universe.sector_norm` 분포 확인 (28버킷 정상화는 monthly-sector-refresh 가 담당).

## 9. 추후 확장

- **status=partial 자동 복구**: LLM 실패 시 다음 트리거에서 us_summary 그대로 사용해 재시도 (현재는 다음 06:30까지 비어있음).
- **티어별 노출 제한**: Free 티어는 섹터 라벨까지만, Pro 이상에 종목 티커 노출 (UI 분기).
- **after_hours 데이터**: 현재는 미국 정규장 종가만 사용. 시간외(after-hours) 4시간 추가 데이터를 받으려면 06:30 → 09:00 KST 로 한 번 더 미루거나 별도 sync 추가 필요.
- **B2b alpha 연동**: post_return_*_pct 와 vendor_chain 분석으로 브리핑 적중률 재측정.
