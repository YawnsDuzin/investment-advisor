# Superpowers 플러그인 가이드

> 최종 갱신: 2026-04-20 | 대상 버전: `superpowers@5.0.7` (Claude Code 공식 마켓플레이스)
> 출처: https://github.com/obra/superpowers

---

## 1. 개요

**Superpowers**는 코딩 에이전트(Claude Code, Codex, Cursor, Copilot CLI, Gemini CLI 등)에 **소프트웨어 개발 방법론 전체**를 주입하는 스킬 묶음 플러그인이다. 언어·프레임워크에 종속되지 않고, "아이디어 탐색 → 설계 → 계획 → TDD 구현 → 리뷰 → 병합"이라는 전 공정을 자동화된 워크플로우로 강제한다.

### 1.1 핵심 철학

| 원칙 | 의미 |
|------|------|
| **Test-Driven Development** | 프로덕션 코드 한 줄을 쓰기 전에 실패하는 테스트부터 작성한다. |
| **Systematic over ad-hoc** | 즉흥 판단 대신 정해진 프로세스를 따른다. |
| **Complexity reduction** | YAGNI/DRY — 복잡도 최소화가 1순위 목표. |
| **Evidence over claims** | "됐어요"가 아니라 실제로 실행한 증거를 요구한다. |

### 1.2 왜 쓰는가

- 에이전트가 **아무 맥락 없이 바로 코딩을 시작하는 것을 차단**한다 (HARD-GATE).
- 설계 → 계획 → 구현을 **여러 서브에이전트로 분리**하여 컨텍스트 오염·오류 전파를 막는다.
- **검증 없이 "완료됨"이라고 선언하는 것을 금지**한다 (verification-before-completion).
- 범용적 — Python/JS/Go/Rust 어느 스택에서도 그대로 적용된다 (테스트 러너만 해당 언어로 치환).

---

## 2. 구성 — 14개 스킬

스킬은 크게 **프로세스 스킬**(어떻게 일할지 결정)과 **메타 스킬**(플러그인 자체 사용)로 나뉜다.

### 2.1 기본 워크플로우 스킬

| 스킬 | 언제 트리거 | 역할 |
|------|------------|------|
| `using-superpowers` | 모든 대화 시작 시 | 스킬 사용법 주입. 1%라도 적용 가능하면 반드시 호출하도록 강제 |
| `brainstorming` | 새 기능·변경 요청 시 | 설계 완료 전 코드 금지. 질문 → 2~3개 접근 제안 → 섹션별 설계 승인 → design doc 저장 |
| `writing-plans` | 설계 승인 후 | 2~5분 단위 태스크로 쪼개진 구현 계획 문서 작성 (파일 경로·완성 코드·검증 절차 포함) |
| `using-git-worktrees` | 계획 승인 후 | 격리된 워크트리·브랜치 생성, 프로젝트 셋업, 테스트 베이스라인 확인 |
| `executing-plans` | 계획 있음 + 대화형 체크포인트 필요 | 사람이 중간중간 확인하며 배치 실행 |
| `subagent-driven-development` | 계획 있음 + 자율 실행 | 태스크마다 새 서브에이전트 디스패치 → 스펙 리뷰 → 코드 품질 리뷰 2단계 검증 |
| `dispatching-parallel-agents` | 독립 태스크 2개 이상 | 병렬 서브에이전트 동시 실행 |
| `test-driven-development` | 구현 중 상시 | RED-GREEN-REFACTOR 강제. 테스트 없이 쓰인 코드는 삭제 |
| `systematic-debugging` | 버그/테스트 실패 시 | 4단계 근본원인 추적. 추측성 패치 금지 |
| `verification-before-completion` | "완료됨" 선언 직전 | 실제 명령 실행 후 출력 확인. 증거 없이 성공 주장 금지 |
| `requesting-code-review` | 태스크 종료 시 | 계획 대비 구현 검토, 심각도별 이슈 리포트 |
| `receiving-code-review` | 리뷰 피드백 수신 시 | 맹목적 수용 대신 기술적 검증 후 적용 |
| `finishing-a-development-branch` | 구현 완료 시 | 테스트 확인 → 병합/PR/폐기 선택지 제시 → 워크트리 정리 |

### 2.2 메타 스킬

| 스킬 | 역할 |
|------|------|
| `writing-skills` | 새 스킬을 작성하거나 기존 스킬을 수정할 때 사용 |

---

## 3. 표준 워크플로우

```
사용자 요청
   │
   ▼
[brainstorming] ── 질문/설계/승인 ── design doc 저장
   │
   ▼
[writing-plans] ── 태스크 단위 계획 ── plan.md 저장
   │
   ▼
[using-git-worktrees] ── 격리 워크트리 + 브랜치
   │
   ▼
[subagent-driven-development | executing-plans]
   │      각 태스크마다:
   │        1) [test-driven-development] — RED → GREEN → REFACTOR
   │        2) [verification-before-completion] — 실행 증거 확인
   │        3) [requesting-code-review] — 스펙 준수 + 코드 품질 검증
   │
   ▼
[finishing-a-development-branch] ── 병합 / PR / 폐기
```

### 3.1 각 단계 특징

- **brainstorming HARD-GATE** — 설계 승인 전에는 어떤 구현 스킬도 호출하지 않는다. "간단해서 설계 불필요"라는 생각 자체가 안티패턴.
- **writing-plans** — "판단력 없고 테스트 싫어하는 주니어라도 따라 할 수 있는 수준"의 상세 계획. 파일 경로·완전한 코드·검증 명령어를 명시.
- **TDD의 쇠법칙(Iron Law)** — `NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST`. 테스트 전에 쓴 코드는 참고용으로도 남기지 말고 **삭제**.
- **2단계 리뷰** — subagent-driven-development에서는 구현 서브에이전트와 별도로 ① 스펙 준수 리뷰어, ② 코드 품질 리뷰어를 각각 디스패치.

---

## 4. 사용법

### 4.1 자동 트리거 (권장)

스킬은 메타 스킬 `using-superpowers`가 세션 시작 시 활성화되어, **작업 유형에 맞는 스킬을 에이전트가 자동으로 호출**한다. 사용자는 보통 아무것도 하지 않아도 된다.

예시:
- "로그인 기능 추가해줘" → 에이전트가 먼저 `brainstorming` 호출 → 질문 → 설계 제시 → 승인받고 `writing-plans`로 진행
- "이 테스트가 실패해" → 에이전트가 `systematic-debugging` 호출 → 4단계 근본원인 추적
- "구현 끝났어, 커밋할게" → 에이전트가 `verification-before-completion` 호출 → 실제 테스트 실행 확인 후 커밋

### 4.2 명시적 호출

특정 스킬을 강제하려면 사용자 메시지에 직접 언급한다:

```
/brainstorming 홈페이지에 통계 카드 섹션을 추가하고 싶어
```

또는 Claude Code의 Skill 도구로 `superpowers:<skill-name>` 형식으로 호출 가능.

### 4.3 사용자 지시와의 우선순위

Superpowers 공식 규칙:

1. **사용자의 명시적 지시** (CLAUDE.md, 직접 요청) — 최우선
2. **Superpowers 스킬** — 기본 시스템 프롬프트를 덮어씀
3. **기본 시스템 프롬프트** — 최하위

예: `CLAUDE.md`에 "TDD 쓰지 말 것"이라고 적혀 있으면 `test-driven-development` 스킬은 따르지 않는다.

---

## 5. 설치

### 5.1 Claude Code (이 프로젝트의 경우 이미 설치됨)

```bash
/plugin install superpowers@claude-plugins-official
```

설치 후 위치: `~/.claude/plugins/cache/claude-plugins-official/superpowers/<version>/`

### 5.2 타 플랫폼

| 플랫폼 | 명령 |
|--------|------|
| Codex CLI | `/plugins` → `superpowers` 검색 → Install |
| Cursor | Agent 채팅에서 `/add-plugin superpowers` |
| Copilot CLI | `copilot plugin marketplace add obra/superpowers-marketplace` → `copilot plugin install superpowers@superpowers-marketplace` |
| Gemini CLI | `gemini extensions install https://github.com/obra/superpowers` |
| OpenCode | `Fetch and follow instructions from https://raw.githubusercontent.com/obra/superpowers/refs/heads/main/.opencode/INSTALL.md` |

---

## 6. 본 프로젝트(Investment Advisor) 적용 가이드

### 6.1 범용성 확인

이 프로젝트 스택(Python 3.10+ / FastAPI / PostgreSQL / Jinja2)에서도 **스킬은 그대로 작동한다**. 예시 스니펫이 `npm test`/`jest`로 되어 있어도 방법론은 동일하며, 명령어만 치환한다:

| Superpowers 예시 | 본 프로젝트 대응 |
|------------------|-----------------|
| `npm test` | `pytest` |
| `jest.fn()` | `unittest.mock.Mock()` / `pytest-mock` |
| `package.json` 감지 | `requirements.txt` / `pyproject.toml` 감지 |

### 6.2 전형적 적용 시나리오

**시나리오 A — 새 기능 추가 (예: 워치리스트에 가격 알림 임계값 설정)**

1. `brainstorming` — 알림 조건 종류(가격/수익률), UI 위치, DB 스키마 질문 → 설계 문서 승인
2. `writing-plans` — v23 마이그레이션 추가, `api/routes/watchlist.py` 엔드포인트, 템플릿 수정 태스크 분할
3. `using-git-worktrees` — 별도 워크트리에서 작업
4. `test-driven-development` — 각 엔드포인트에 대해 pytest 실패 테스트 → 구현 → 통과 확인
5. `requesting-code-review` — 스키마 마이그레이션·보안·티어 제한 검증
6. `finishing-a-development-branch` — dev 브랜치로 병합

**시나리오 B — 버그 수정 (예: 분석 파이프라인 중단점 복구 실패)**

1. `systematic-debugging` — 재현 → 가설 → 증거 수집 → 근본원인 식별 (추측성 try/except 추가 금지)
2. `test-driven-development` — 실패 재현 테스트 작성
3. 수정 → 테스트 통과 확인
4. `verification-before-completion` — 실제 `python -m analyzer.main` 실행으로 체크포인트 복구 확인
5. `commit` 스킬로 커밋

**시나리오 C — 리팩터링 (예: B3 리뷰 반영 — active_page 통일 등)**

1. `writing-plans` — 어떤 파일 어느 라인을 어떻게 바꿀지 명시 (작은 변경은 생략 가능)
2. `test-driven-development` — 기존 페이지 테스트가 여전히 통과하는지 확인
3. 변경 → `requesting-code-review` → 병합

### 6.3 주의 사항

- Superpowers의 `brainstorming`은 `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`에 설계 문서를 저장하려 한다. 본 프로젝트는 `_docs/` 규칙을 쓰므로, 사용자가 필요 시 "설계 문서는 `_docs/`에 저장해줘"라고 지시하거나, 에이전트가 본 가이드를 참고해 경로를 조정한다.
- CLAUDE.md의 프로젝트 규약(한국어 주석, `.env` 관리, Jinja2 매크로 위치 등)은 **Superpowers보다 우선**한다.
- 분석 파이프라인 변경 시에는 TDD 적용이 까다로울 수 있다 (Claude SDK 호출이 비결정적). 이 경우 `_parse_json_response()` 등 **순수 함수 단위로 테스트**를 쪼개거나, 에이전트에게 "이 부분은 통합 테스트 스모크로 대체"를 명시하는 것이 실용적이다.

---

## 7. 주요 파일 경로 (설치본)

```
~/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/
├── README.md                       # 공식 개요
├── RELEASE-NOTES.md
├── CLAUDE.md                       # Claude Code용 글로벌 지시
├── AGENTS.md / GEMINI.md           # 타 플랫폼용
├── skills/                         # 14개 스킬
│   ├── using-superpowers/SKILL.md
│   ├── brainstorming/SKILL.md
│   ├── writing-plans/SKILL.md
│   ├── test-driven-development/SKILL.md
│   ├── systematic-debugging/SKILL.md
│   └── ... (총 14개)
├── agents/                         # 서브에이전트 정의
├── commands/                       # 슬래시 명령
├── hooks/                          # 세션 훅
└── scripts/                        # 유틸 스크립트
```

특정 스킬 내용을 직접 확인하고 싶으면 해당 디렉터리의 `SKILL.md`를 열어보면 된다.

---

## 8. 참고 링크

- GitHub: https://github.com/obra/superpowers
- 최초 공개 블로그: https://blog.fsck.com/2025/10/09/superpowers/
- Discord: https://discord.gg/35wsABTejz
- 이슈: https://github.com/obra/superpowers/issues

---

## 9. 요약

- **Superpowers = 코딩 에이전트용 개발 방법론 강제 프레임워크**
- **14개 스킬**이 설계·계획·TDD·디버깅·리뷰·병합까지 자동 오케스트레이션
- **언어 무관** — 본 프로젝트(Python/FastAPI/PostgreSQL)에도 그대로 적용
- **사용자 CLAUDE.md 지시 > Superpowers > 기본 동작** 순 우선순위
- 일반적으로 **자동 트리거에 맡기면 됨** — 필요 시 `/<skill-name>` 또는 Skill 도구로 명시 호출
