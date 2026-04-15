# ProSafe Claude Code Skills

ProSafe 프로젝트에서 사용 가능한 슬래시 커맨드 목록입니다.
Claude Code에서 `/` 로 시작하는 명령어로 실행하거나, 자연어로 요청해도 자동으로 트리거됩니다.

---

## 개발 워크플로우

| 커맨드 | 설명 | 사용 예시 |
|--------|------|-----------|
| `/commit` | 변경 파일 분석 → Conventional Commits 한글 메시지 자동 생성 후 커밋 | `커밋해줘`, `변경사항 커밋` |
| `/git-review` | 현재 브랜치 변경사항을 main 대비 코드 리뷰 (품질/버그/성능/보안) | `브랜치 리뷰해줘`, `변경사항 리뷰해줘` |
| `/build-test` | WPF 앱 빌드 및 테스트 실행 | `/build-test`, `/build-test build` |
| `/check-arch` | Clean Architecture / SOLID 원칙 준수 여부 검토 | `/check-arch`, `/check-arch <파일경로>` |

## 레거시 마이그레이션 (WinForm → WPF)

| 커맨드 | 설명 | 사용 예시 |
|--------|------|-----------|
| `/migrate-feature` | 레거시 기능 분석 → WPF 코드 자동 생성 | `/migrate-feature 근로자등록` |
| `/analyze-legacy` | 레거시 코드 분석만 수행 (코드 생성 없음) | `/analyze-legacy <기능명>` |
| `/compare-legacy` | 레거시 구현과 WPF 구현 비교, 누락 로직 탐지 | `/compare-legacy <기능명>` |

## 데이터베이스 / 장비

| 커맨드 | 설명 | 사용 예시 |
|--------|------|-----------|
| `/db-query` | PostgreSQL DB 직접 조회 (MCP 사용) | `/db-query workers`, `/db-query logs` |
| `/ef-migrate` | EF Core 마이그레이션 관리 (상태 확인 / 생성 / 적용) | `/ef-migrate add <이름>` |
| `/device-check` | Hikvision 장비 연결 상태 및 헬스체크 | `/device-check`, `/device-check <장비ID>` |
| `/sync-status` | 동기화 상태 요약 (근로자/장비/대기 건수) | `/sync-status`, `/sync-status pending` |

---

## 상세 설명

### `/commit`
변경 파일을 분석하여 `feat`, `fix`, `refactor` 등 Conventional Commits 형식의 **한글 커밋 메시지**를 자동 생성하고 커밋합니다.
이슈 번호 연동, `Co-Authored-By` 자동 추가 포함.

### `/git-review`
`git diff main...HEAD` 기반으로 변경된 파일을 **4가지 관점**에서 리뷰합니다:
- 코드 품질 / 가독성
- 잠재적 버그 (Null 참조, ConfigureAwait 누락, EF Core 계산 프로퍼티 오용 등)
- 성능 (N+1 쿼리, 불필요한 DB 조회 등)
- 보안 (암호화 누락, 권한 검증 부재 등)

ProSafe 프로젝트 특화 체크리스트(암호화 컬럼, BaseService 패턴, CancellationToken 등) 포함.

### `/build-test`
```
/build-test         → 전체 빌드 + 모든 테스트
/build-test build   → 빌드만
/build-test <프로젝트명>  → 특정 테스트 프로젝트만
```

### `/migrate-feature`
레거시 경로(`D:\Project\ProSafe.wpf\...`)에서 코드를 분석하여 WPF + Clean Architecture 패턴으로 변환합니다.
`--analyze-only` 옵션으로 분석만 수행 가능.

### `/db-query`
```
/db-query workers           → 근로자 목록
/db-query devices           → 장비 목록
/db-query codes <접두사>    → 공통코드 (A=공종, B=직종, H/I/J=장비)
/db-query companies         → 협력업체 목록
/db-query logs              → 최근 출입 로그
```

### `/ef-migrate`
```
/ef-migrate             → 현재 마이그레이션 상태 확인
/ef-migrate add <이름>  → 새 마이그레이션 생성
/ef-migrate update      → DB 업데이트 적용
```

### `/sync-status`
```
/sync-status            → 전체 동기화 상태 요약
/sync-status workers    → 근로자 동기화 상태
/sync-status devices    → 장비별 동기화 상태
/sync-status pending    → 대기 중인 동기화 항목
```

---

## 관련 경로

| 항목 | 경로 |
|------|------|
| 레거시 WinForm | `D:\Project\ProSafe.wpf\csharp-solutions\Itlog-HikFace-DllApi` |
| WPF 현재 | `D:\Project\ProSafe_20260311\ProSafeApp` |
| 스키마 매핑 문서 | `ProSafe/_docs/테이블정리_20260316.md` |
| 런타임 설정 | `Run64/net10.0-windows/Data/settings.json` |
