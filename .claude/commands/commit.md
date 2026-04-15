---
name: commit
description: Git 커밋 메시지 생성 및 커밋 실행. 변경 파일을 분석하여 Conventional Commits 한글 형식의 커밋 메시지를 자동 생성합니다. "커밋해줘", "변경사항 커밋", "commit", "/commit" 등의 요청 시 사용하세요. 이슈 번호 연동과 Co-Authored-By도 자동 처리됩니다.
---

# Git Commit Skill

변경 파일을 분석하여 Conventional Commits 한글 형식의 커밋 메시지를 생성하고 커밋합니다.

## 커밋 메시지 형식

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type (필수)

| Type | 설명 | 예시 |
|------|------|------|
| `feat` | 새로운 기능 추가 | feat(인증): JWT 토큰 인증 구현 |
| `fix` | 버그 수정 | fix(동기화): 널 참조 예외 수정 |
| `refactor` | 리팩토링 (기능 변경 없음) | refactor(서비스): DI 패턴 적용 |
| `docs` | 문서 수정 | docs(README): 설치 가이드 추가 |
| `style` | 코드 포맷팅, 세미콜론 등 | style: 코드 정렬 |
| `test` | 테스트 코드 | test(장비): 연결 테스트 추가 |
| `chore` | 빌드, 설정 변경 | chore(deps): 패키지 업데이트 |
| `perf` | 성능 개선 | perf(쿼리): 인덱스 최적화 |

### Scope (선택)

변경된 주요 영역을 괄호 안에 표시:
- 페이지명: `장비관리`, `동기화`, `로그기록`
- 레이어: `서비스`, `뷰모델`, `리포지토리`
- 모듈: `인증`, `API`, `DB`

### Subject (필수)

- 한글로 작성
- 50자 이내
- 명령형 어투 사용 ("추가", "수정", "개선" 등)
- 마침표 없음

### Body (선택)

- "무엇을"보다 "왜"에 집중
- 72자마다 줄바꿈

### Footer (자동 추가)

```
Closes #123
Co-Authored-By: duzin park <duzin@anthropic.com>
```

## 워크플로우

### 1단계: 변경 사항 분석

```bash
# 상태 확인 (untracked 포함)
git status

# staged + unstaged 변경 확인
git diff --stat
git diff --staged --stat

# 최근 커밋 스타일 참고
git log --oneline -5
```

### 2단계: 변경 내용 분류

변경된 파일들을 분석하여:
1. **변경 유형** 판단 (feat/fix/refactor 등)
2. **영향 범위(scope)** 식별
3. **핵심 변경 사항** 요약

### 3단계: 커밋 메시지 제안

사용자에게 커밋 메시지 옵션을 제시:

```markdown
## 커밋 메시지 제안

### 옵션 1 (권장)
feat(동기화): 이벤트 수집 서비스 구현

- EventCollectionService 추가
- 실시간 폴링 및 배치 처리 지원
- 장비별 마지막 수집 시간 관리

Closes #45
Co-Authored-By: duzin park <duzin@anthropic.com>

### 옵션 2
feat(서비스): 출입 이벤트 동기화 기능 추가
...

어떤 옵션으로 진행할까요? (1/2/수정)
```

### 4단계: 커밋 실행

사용자 확인 후:

```bash
# 관련 파일만 스테이징 (민감한 파일 제외)
git add <specific-files>

# 커밋 (HEREDOC 사용)
git commit -m "$(cat <<'EOF'
feat(동기화): 이벤트 수집 서비스 구현

- EventCollectionService 추가
- 실시간 폴링 및 배치 처리 지원

Closes #45
Co-Authored-By: duzin park <duzin@anthropic.com>
EOF
)"

# 결과 확인
git status
```

## 이슈 번호 연동

### 자동 감지
- 브랜치명에서 추출: `feature/123-login` → `#123`
- 사용자가 언급: "이슈 45번 관련 커밋" → `#45`

### Footer 형식
- 해결: `Closes #123` 또는 `Fixes #123`
- 참조: `Refs #123`
- 여러 이슈: `Closes #123, #124`

## 주의사항

### 스테이징 규칙
- `.env`, `credentials.json` 등 민감 파일 제외
- 대용량 바이너리 파일 확인
- `git add .` 대신 명시적 파일 지정 권장

### 커밋 분리 기준
여러 종류의 변경이 섞여 있으면 분리 제안:
- feat + fix → 별도 커밋
- 서로 다른 기능 → 별도 커밋
- 단, 관련된 변경은 하나로 묶기

### 실패 시
- pre-commit hook 실패 → 문제 해결 후 **새 커밋** 생성 (amend 아님)
- 충돌 → 해결 방법 안내

## 예시

### 예시 1: 새 기능
```
feat(근로자관리): 검색 필터 기능 추가

- 이름, 소속사, 직종별 필터링 구현
- 검색 조건 저장 기능 추가
- 페이징 처리 개선

Closes #78
Co-Authored-By: duzin park <duzin@anthropic.com>
```

### 예시 2: 버그 수정
```
fix(장비연동): ISAPI 타임아웃 처리 수정

연결 실패 시 무한 대기 문제 해결
- HttpClient 타임아웃 30초로 설정
- 재시도 로직 추가 (최대 3회)

Fixes #92
Co-Authored-By: duzin park <duzin@anthropic.com>
```

### 예시 3: 리팩토링
```
refactor(서비스): DataService를 Facade 패턴으로 전환

Services.* 프로젝트 통합을 위한 구조 변경
- IDataService 인터페이스 도입
- 개별 서비스 의존성 내부로 이동

Co-Authored-By: duzin park <duzin@anthropic.com>
```
