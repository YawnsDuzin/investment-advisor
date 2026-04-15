---
name: migration-progress
description: 레거시→WPF 마이그레이션 진행 상황 및 완료 항목
type: project
---

# 마이그레이션 진행 상황

## 완료된 기능

| 기능 | 레거시 파일 | WPF 파일 | 완료일 |
|------|------------|---------|--------|
| 장비 관리 | DeviceManager.cs | DeviceInfoViewModel.cs | 2026-03-20 |
| 로그 기록 | DeviceEventGet.cs | LogRecordViewModel.cs | 2026-03-22 |
| 동기화 관리 | Sync.cs | SyncManagementViewModel.cs | 2026-03-23 |

## 진행 중

| 기능 | 상태 | 담당 | 비고 |
|------|------|------|------|
| - | - | - | - |

## 미착수 (우선순위 순)

| 기능 | 레거시 파일 | 난이도 | 의존성 |
|------|------------|--------|--------|
| 근로자 관리 | Worker.cs | 중 | UserService |
| 얼굴 등록 | Face.cs | 상 | HikFaceAdapter |
| 설정 | Setting.cs | 하 | AppSettingRepository |

## 마이그레이션 결정 사항

- **2026-03-20**: 장비 연동은 SDK 대신 ISAPI만 사용하기로 결정
- **2026-03-22**: 로그는 실시간 + 파일 탭 분리 구조로 변경
