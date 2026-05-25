# 2026-05-25 universe-sync-meta.service — KRX 차단 시 sync_meta_krx 전체 사망

- **발생 일시**: 2026-05-24 07:30:01 KST (universe-sync-meta.timer 주간 트리거)
- **대상 스테이지**: Universe 메타 동기화 (`analyzer/universe_sync.py:sync_meta_krx`)
- **상태**: ✅ 해결됨 (커밋 — staged)
- **관련 로그**: systemd journal `universe-sync-meta.service` (rasp-dzp-1)

## 증상

```
2026-05-24T07:30:05 KRX 로그인 실패: KRX_ID 또는 KRX_PW 환경 변수가 설정되지 않았습니다.
2026-05-24T07:30:06 Error occurred in get_market_cap_by_ticker: Expecting value: line 1 column 1 (char 0)
Traceback:
  analyzer/universe_sync.py:1991  main → sync_meta_krx
  analyzer/universe_sync.py:249   sync_meta_krx → _fetch_market_snapshot
  analyzer/universe_sync.py:104   pykrx_stock.get_market_cap(...)
  pykrx/stock/stock_api.py:425    holiday = (df[["종가","시가총액","거래량","거래대금"]] == 0).all(axis=None)
KeyError: "None of [Index(['종가','시가총액','거래량','거래대금'], dtype='object')] are in the [columns]"

2026-05-24T07:30:07 universe-sync-meta.service: Failed with result 'exit-code'.
```

## 근본 원인

1. **직접 원인**: KRX(Akamai) 가 pykrx 익명 요청을 빈/JSON 비호환 응답으로 거부 → pykrx 내부에서 `json.loads` 실패(`Expecting value: line 1 column 1 (char 0)`) → 빈 DataFrame 반환 → pykrx 자체가 `df[["종가","시가총액","거래량","거래대금"]]` 인덱싱하다 `KeyError` 로 죽음.
2. **구조적 원인**: `_fetch_market_snapshot` 의 두 pykrx 호출(`get_market_cap`, `get_market_ohlcv`)이 try/except 가드 밖. 한 시장(또는 KRX 전체) 실패가 함수 → `sync_meta_krx` → `main()` 까지 전파되어 service status=1 로 종료.
3. **근본 원인**: `f2fae1d` (P5c) 가 `sync_prices_krx` 만 try/except 로 격리했고, `sync_meta_krx` 는 같은 패턴이 미적용 상태였음. KRX 차단은 OHLCV/메타 무관하게 발생하는데 한쪽만 보호되어 있었다.

「KRX 로그인 실패」 메시지는 우리 P5d 의 `KRX_ID/KRX_PW` 강제 unset 결과로 pykrx 가 import 시 출력하는 정상 메시지 — 인증 자체가 원인은 아님 (익명 모드는 의도된 동작).

## 수정 사항 (P5e)

- `analyzer/universe_sync.py:_fetch_market_snapshot` — `pykrx_stock.get_market_cap` 호출을 try/except 로 감싸 예외 시 빈 dict 반환. `get_market_ohlcv` 호출도 동일 패턴으로 보호 (실패 시 종가 매핑만 생략, snap 자체는 계속 생성).
- `analyzer/universe_sync.py:sync_meta_krx` — `for market in markets:` 루프 외곽에 try/except 추가. `_fetch_market_snapshot` 내부 가드를 통과한 후 sector_map / sector_norm 가공 단계에서 예외가 나도 한 시장만 건너뛰고 다른 시장은 정상 진행.

## 검증

- `python -c "import ast; ast.parse(open('analyzer/universe_sync.py', encoding='utf-8').read())"` → OK.
- 스모크: pykrx_stock 을 monkey-patch 해 `get_market_cap` 이 `ValueError('Expecting value: line 1 column 1 (char 0)')` throw 하도록 강제 → `_fetch_market_snapshot('20260522','KOSPI')` 가 빈 dict `{}` 반환 (예외 흡수, WARNING 로그만).
- 운영 배포 후 다음 universe-sync-meta.timer 트리거에서 service status=0 / `KRX 메타 동기화 완료: 0건 upsert / 0건 우선주 표시 / X.Xs` 로그 확인.

## 후속 모니터링

- `universe-sync-meta.service` 가 KRX 차단 시 status=0 으로 정상 종료하는지 확인 (status=1 재발 시 가드 누락).
- 동일 패턴 회귀를 막기 위해 향후 pykrx 호출을 새로 추가하는 모듈은 반드시 `_fetch_market_snapshot` / `sync_prices_krx` 의 try/except 패턴을 따른다.
- 장기 — pykrx 1.2.8+ 가 빈 응답 가드를 자체 강화하면 우리 외곽 try/except 의 일부는 단순화 가능.
