# 알림 — 종목 구독 알림 회사명·테마 보강 설계

- **작성일**: 2026-05-05 KST
- **상태**: Draft → 사용자 승인 대기
- **관련 코드**: `shared/db/session_repo.py:_generate_notifications()`, `api/templates/notifications.html`, `shared/db/migrations/versions.py`

## 배경

현재 `/pages/notifications` 의 종목 구독 알림은 다음과 같이 표시된다:

```
구독 종목 '112290'이(가) 분석에 등장했습니다
구독 종목 '445180'이(가) 분석에 등장했습니다
```

- 사용자가 본인이 어떤 종목을 구독했는지 티커만으로는 즉시 식별 불가 (특히 KRX 6자리 숫자).
- 어떤 분석 맥락(테마)에서 등장했는지 알 수 없어 클릭 전엔 우선순위 판단 불가.
- 알림 페이지 본연의 가치(빠른 스캔)가 떨어진다.

## 결정

- **포맷 정책**: C 안 — 타이틀 회사명+티커, 다중 테마는 detail 줄에 가운뎃점(`·`) 구분.
- **데이터 보강**: B 안 — `stock_universe.asset_name` join + 세션 themes 컨텍스트 활용.
- **기존 알림 처리**: B 안 — v46 마이그레이션으로 일괄 backfill (`is_read` 보존).
- **테마 구독 알림**: A 안 — 변경 없음 (정보 결손 없음).

## 명세

### Title / Detail 포맷

| 케이스 | title | detail |
|---|---|---|
| 회사명 있음 + 테마 1개 | `구독 종목 '에코프로비엠 (112290)'이(가) 분석에 등장했습니다` | `테마: 2차전지 소재 회복` |
| 회사명 있음 + 테마 N개 (N≥2) | `구독 종목 '에코프로비엠 (112290)'이(가) 분석에 등장했습니다 (N개 테마)` | `테마1 · 테마2 [· …]` |
| 회사명 없음 (US 등) + 테마 1개 | `구독 종목 '112290'이(가) 분석에 등장했습니다` | `테마: 2차전지 소재 회복` |
| 회사명 없음 + 테마 N개 | `구독 종목 '112290'이(가) 분석에 등장했습니다 (N개 테마)` | `테마1 · 테마2 [· …]` |
| backfill — 세션·테마 삭제됨 + 회사명 있음 | `구독 종목 '에코프로비엠 (112290)'이(가) 분석에 등장했습니다` | (NULL) |
| backfill — 세션·테마 삭제됨 + 회사명 없음 | `구독 종목 '112290'이(가) 분석에 등장했습니다` | (NULL) |

- `link` 필드는 변경 없음 (`/pages/stocks/{ticker}`).
- `is_read` 는 backfill 시에도 변경 없음.

### `_generate_notifications()` 변경

신규 단계:
1. themes 순회 시 `ticker_themes: dict[str_upper, list[str]]` 동시 구축 (proposal 의 ticker → 등장한 theme_name).
2. 매칭 직전에 `_fetch_company_names(cur, matched_tickers)` 호출 — `stock_universe` 단일 쿼리:
   ```sql
   SELECT ticker, asset_name FROM stock_universe
   WHERE upper(ticker) = ANY(%s) AND asset_name IS NOT NULL AND asset_name <> ''
   ```
3. 헬퍼 `_format_ticker_notification(sub_key, asset_name, themes_list)` 가 (title, detail) 튜플 반환.
4. INSERT 쿼리에 `detail` 컬럼 추가 (이미 `user_notifications.detail` 컬럼은 존재 — 알림 모델에서 사용 중).

### 마이그레이션 v46

`_migrate_to_v46(cur)` — `user_notifications` ticker 알림 backfill:

```python
def _migrate_to_v46(cur):
    """v46: ticker 구독 알림 회사명·테마 backfill."""
    # 1) ticker 알림 + 컨텍스트 조회 (LEFT JOIN — 세션 삭제된 경우도 처리)
    cur.execute("""
        SELECT n.id,
               s.sub_key,
               u.asset_name,
               COALESCE(
                   (SELECT array_agg(DISTINCT t.theme_name ORDER BY t.theme_name)
                    FROM investment_themes t
                    JOIN investment_proposals p ON p.theme_id = t.id
                    WHERE t.session_id = n.session_id
                      AND upper(p.ticker) = upper(s.sub_key)),
                   ARRAY[]::TEXT[]
               ) AS themes
          FROM user_notifications n
          JOIN user_subscriptions s
            ON s.id = n.sub_id AND s.sub_type = 'ticker'
          LEFT JOIN stock_universe u
            ON upper(u.ticker) = upper(s.sub_key)
    """)
    rows = cur.fetchall()

    updates = []
    for noti_id, sub_key, asset_name, themes in rows:
        title, detail = _format_ticker_notification(sub_key, asset_name, list(themes or []))
        updates.append((title, detail, noti_id))

    if updates:
        cur.executemany(
            "UPDATE user_notifications SET title=%s, detail=%s WHERE id=%s",
            updates,
        )
```

- `_format_ticker_notification` 은 `session_repo` 에 두고 마이그레이션은 import 해서 사용 (단일 진실 소스).
- `SCHEMA_VERSION` → 46. `init_db()` 의 마이그레이션 분기에 `if current < 46: _migrate_to_v46(cur)` 추가.

### 폴백 / 엣지 케이스

| 케이스 | 동작 |
|---|---|
| `stock_universe` 미발견 (US/구버전) | 회사명 생략, 티커만 표시. 로그 출력 없음. |
| `themes` 빈 배열 (세션 CASCADE 삭제 후 backfill) | detail = NULL. 회사명 보강만 수행. |
| 동일 ticker 가 KOSPI/KOSDAQ 양쪽에 존재 | KRX 6자리 ticker 는 시장 간 중복 없음 — 첫 매칭 사용. |
| `asset_name` NULL/공백 | 회사명 생략. SQL `WHERE asset_name IS NOT NULL AND asset_name <> ''` 로 미반환 처리. |
| backfill 후 신규 분석 — 같은 알림 재발생 | 신규 INSERT 이므로 자동으로 새 포맷 적용. |

## 회귀 가드

- `tests/test_db_notifications.py` (신규) — `_format_ticker_notification` 단위 테스트 4 케이스:
  1. 회사명 있음 + 단일 테마
  2. 회사명 있음 + 다중 테마
  3. 회사명 없음 + 단일 테마
  4. 회사명 없음 + 빈 테마 (backfill 폴백)
- 마이그레이션 자체는 mock cursor 로 SQL shape 만 검증 (실제 fetchall 시뮬레이션). 결과 UPDATE 호출 횟수·인자 패턴 확인.

## 비변경 사항

- `api/templates/notifications.html` — title/detail 분기 로직이 이미 존재 ([line 25-27](../api/templates/notifications.html#L25-L27)). HTML 변경 불필요.
- `notifications` API (`api/routes/watchlist.py` 또는 유사) — 컬럼·응답 구조 동일.
- 테마 구독 알림 — 변경 없음.
- `link`, `is_read`, `created_at`, `sub_id` — 모두 동일.

## 후속

- 알림 그룹화 (같은 종목 N일 연속 알림) 는 별도 작업 — 본 spec 범위 외.
- 종목 페이지 페이드 alert / 등장 횟수 트랙은 본 spec 범위 외.

## CLAUDE.md 업데이트

- `## DB Schema` 절: v46 한 줄 추가 — `user_notifications` ticker 알림 회사명·테마 보강 backfill.
- `## Key Conventions` 절 알림 관련 항목에 1 줄 — "ticker 구독 알림은 `stock_universe.asset_name` 으로 회사명 보강 + 등장 테마명을 detail 에 노출".
