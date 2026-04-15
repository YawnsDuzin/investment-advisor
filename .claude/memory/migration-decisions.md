---
name: migration-decisions
description: 마이그레이션 중 내린 아키텍처/구현 결정 사항
type: feedback
---

# 마이그레이션 결정 사항

## 아키텍처 결정

### 장비 연동
- **결정**: SDK(DLL) 대신 ISAPI(HTTP)만 사용
- **Why**: 플랫폼 독립성, 비동기 처리 용이, DLL 의존성 제거
- **How to apply**: HikFaceAdapter에서 모든 장비 통신은 HttpClient 사용

### 데이터베이스
- **결정**: MSSQL → PostgreSQL 전환
- **Why**: 라이선스 비용, Linux 호환성
- **How to apply**: EF Core + Npgsql, 스키마는 테이블정리_20260316.md 참조

### 동기화 상태
- **결정**: 문자열 상태코드("0","1","D") → SyncStatus enum
- **Why**: 타입 안전성, 명시적 의미
- **How to apply**: Repository에서 변환 처리

## UI/UX 결정

### 페이지 구조
- **결정**: Form별 탭 → 단일 페이지 + 내부 탭
- **Why**: 네비게이션 단순화, 컨텍스트 유지
- **How to apply**: UserControl + TabControl 조합

### DataGrid
- **결정**: 자동 컬럼 생성 비활성화
- **Why**: 컬럼 순서/포맷 제어
- **How to apply**: AutoGenerateColumns="False" + 명시적 컬럼 정의

## 코드 스타일 결정

### 비동기 처리
- **결정**: Thread.Sleep/전역 플래그 제거 → async/await
- **Why**: UI 응답성, 코드 가독성
- **How to apply**: 모든 I/O 작업은 async 메서드로

### DI 패턴
- **결정**: 전역 변수(g_*) → DI 서비스
- **Why**: 테스트 용이성, 의존성 명확화
- **How to apply**: App.xaml.cs에서 등록, 생성자 주입
