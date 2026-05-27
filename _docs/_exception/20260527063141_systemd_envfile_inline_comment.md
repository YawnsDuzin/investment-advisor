# 2026-05-27 systemd EnvironmentFile 인라인 주석 — analyzer 배치 2일 연속 사망

- **발생 일시**: 2026-05-26 06:31:34 KST · 2026-05-27 06:31:41 KST (2일 연속 동일 시각)
- **대상 스테이지**: AppConfig 초기화 (analyzer/main.py:37) — 어떤 스테이지도 시작 못 함
- **모델**: N/A (Python config 파싱 단계)
- **상태**: ✅ 해결됨 (`.env.example` line 330 인라인 주석 제거)
- **관련 아카이브**: journalctl `investment-advisor-analyzer.service` (운영기)

## 증상

```
Traceback (most recent call last):
  File "/home/dzp/dzp-main/program/investment-advisor/analyzer/main.py", line 37, in main
    cfg = AppConfig()
  File "<string>", line 13, in __init__
  File "<string>", line 7, in __init__
  File "/home/dzp/dzp-main/program/investment-advisor/shared/config.py", line 335, in <lambda>
    default_factory=lambda: int(os.getenv("FOREIGN_FLOW_STALENESS_DAYS", "2"))
ValueError: invalid literal for int() with base 10:
  '2          # health check — 최근 N일 내 row 보유 = 신선'
```

- `investment-advisor-analyzer.service` 가 매일 06:31 KST 시작 직후 exit 1 로 사망
- 5월 26, 27 양일 모두 동일 라인 / 동일 message → 환경 영구 상태 문제 (간헐적 아님)
- API 서비스는 별도 unit 으로 동작 정상

## 근본 원인

1. **직접 원인**: 운영기 `.env` 의 `FOREIGN_FLOW_STALENESS_DAYS` 라인이 인라인 주석을 포함한 채 systemd 가 환경변수로 export. Python `int()` 가 주석까지 포함된 문자열을 파싱 시도해 폭발.

2. **구조적 원인**: `shared/config.py` 의 `.env` 파서는 인라인 주석(`KEY=value  # comment`)을 제거하는 로직이 있지만, **systemd `EnvironmentFile=` 디렉티브가 .env 를 먼저 파싱해 환경변수로 set 한 뒤**, Python 의 `os.environ.setdefault(key, value)` 는 이미 set 된 환경변수를 **덮어쓰지 않는다**. 결국 systemd 가 박아놓은 더러운 값이 그대로 `os.getenv()` 에 잡힌다.

3. **근본 원인**: systemd `EnvironmentFile` 파서 사양 한계 — 라인 단위 `#`/`;` 시작 주석만 인식하고, `KEY=value # comment` 형태의 **인라인 주석은 value 의 일부**로 취급. `man systemd.exec` 의 EnvironmentFile 섹션에 명시되어 있다. `.env.example` 의 line 330 (`FOREIGN_FLOW_STALENESS_DAYS=2          # health check ...`) 가 commit `6304963` (4월 30일) 에서 도입됐고, 운영자가 `.env` 동기화 시 그대로 복사 → systemd → Python 으로 전파.

## 수정 사항

- `.env.example` line 330 의 인라인 주석을 **별도 라인 주석** 으로 분리:
  ```diff
  -FOREIGN_FLOW_STALENESS_DAYS=2          # health check — 최근 N일 내 row 보유 = 신선
  +# health check — 최근 N일 내 row 보유 = "신선"
  +# 주의: systemd EnvironmentFile 는 인라인 주석을 지원하지 않으므로 별도 라인 주석 필수.
  +FOREIGN_FLOW_STALENESS_DAYS=2
  ```
- `shared/config.py` 의 .env 파서는 자체적으로 인라인 주석 제거 가능 (line 18-23) 하므로 개발 환경 (직접 `python -m analyzer.main` 실행) 에서는 회귀 없었음. **systemd 경유 운영 배포에서만 노출되는 버그**.

## 검증

- 로컬: `.env` 에 인라인 주석 박은 라인을 systemd 없이 Python 으로 로드해도 파서가 떼어주므로 통과 (기존 동일).
- 운영기: 운영자가 `.env` 의 `FOREIGN_FLOW_STALENESS_DAYS` 라인에서 인라인 주석을 별도 라인으로 옮긴 뒤 `sudo systemctl start investment-advisor-analyzer.service` 로 재실행 → AppConfig 초기화 통과 확인 필요.
- 회귀 차단: `grep '^[A-Z_][A-Z_0-9]*=[^\s].*#' .env.example` 로 동일 패턴 잔류 여부 점검 — 현재 0건.

## 후속 모니터링

- `.env.example` 신규 라인 추가 시 인라인 주석 금지를 컨벤션화. 향후 동일 회귀 방지 위해 `tools/` 에 `.env.example` 린트 스크립트 추가 검토 (low priority).
- 운영기 `.env` 와 `.env.example` 의 라인-by-라인 동기화는 운영자가 직접 패치해야 함 — 코드 배포로는 운영기 `.env` 가 갱신되지 않는다.
- 동일 패턴: systemd `EnvironmentFile=` 를 쓰는 모든 unit (analyzer / api / briefing / fundamentals / foreign-flow-sync / universe-sync-* / ohlcv-cleanup / macro-observer / sector-refresh) 이 영향권. 단, 환경변수 사용처가 `int()`/`float()` 같은 strict 파서가 아니면 silent 통과될 수 있어 위험.
