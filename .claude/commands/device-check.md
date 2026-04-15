# Device Health Check Skill

Hikvision 장비 연결 상태를 확인합니다.

## 사용법
- `/device-check` - 등록된 모든 장비 상태 확인
- `/device-check <장비ID>` - 특정 장비 상세 확인

## 작업 절차

### 1. 등록된 장비 목록 조회 (@postgres)
```sql
SELECT id, name, ip, port, username, is_active, device_kind, location
FROM devices
WHERE is_active = true
ORDER BY id;
```

### 2. 장비별 ISAPI 연결 테스트
HikFaceAdapter를 통한 연결 확인:
- `GET /ISAPI/System/deviceInfo` - 장비 정보
- `GET /ISAPI/System/time` - 시간 동기화 상태

### 3. 확인 항목
- **네트워크 연결**: IP 접근 가능 여부
- **인증**: Username/Password 유효성
- **시간 동기화**: 서버-장비 시간 차이
- **사용자 수**: 등록된 근로자 수

### 4. 코드 레벨 확인
장비 어댑터 구현 확인:
- [HikFaceAdapter.cs](ProSafe.Device.Hikvision/Adapters/HikFaceAdapter.cs)
- `GetHealthStatusAsync()` 메서드 호출 결과

## 출력 형식
| 장비ID | 이름 | IP | 상태 | 응답시간 | 비고 |
|--------|------|-----|------|----------|------|

## 문제 해결 가이드
- **Connection Refused**: 방화벽/포트 확인
- **401 Unauthorized**: 계정 정보 확인
- **Timeout**: 네트워크 상태/장비 전원 확인