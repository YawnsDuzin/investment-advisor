# Sync Status Skill

동기화 상태를 확인합니다 (PostgreSQL MCP 사용).

## 사용법
- `/sync-status` - 전체 동기화 상태 요약
- `/sync-status workers` - 근로자 동기화 상태
- `/sync-status devices` - 장비별 동기화 상태
- `/sync-status pending` - 대기 중인 동기화 항목

## 쿼리 실행 (@postgres MCP 사용)

### 1. 앱 설정 및 마지막 동기화 시간
```sql
SELECT h_cd, s_cd, last_worker_sync_time, sync_interval_minutes,
       auto_sync_enabled, retry_count, retry_interval_seconds
FROM app_settings WHERE id = 1;
```

### 2. 장비별 동기화 상태
```sql
SELECT d.id, d.name, d.ip, d.is_active,
       COUNT(dw.id) as total_workers,
       SUM(CASE WHEN dw.card_status = 1 THEN 1 ELSE 0 END) as synced,
       SUM(CASE WHEN dw.card_status = 0 THEN 1 ELSE 0 END) as pending,
       SUM(CASE WHEN dw.card_status = 2 THEN 1 ELSE 0 END) as failed
FROM devices d
LEFT JOIN device_workers dw ON d.id = dw.device_id
GROUP BY d.id, d.name, d.ip, d.is_active;
```

### 3. 대기 중인 동기화 항목
```sql
SELECT dw.device_id, d.name as device_name,
       w.w_name, dw.card_status, dw.photo_status, dw.updated_at
FROM device_workers dw
JOIN devices d ON dw.device_id = d.id
JOIN workers w ON dw.worker_id = w.id
WHERE dw.card_status = 0 OR dw.photo_status = 0
ORDER BY dw.updated_at DESC
LIMIT 20;
```

### 4. 최근 출입 로그 (업로드 대기)
```sql
SELECT COUNT(*) as pending_upload_count
FROM access_logs
WHERE upload_fail > 0;
```

## 출력 형식
- 마지막 동기화 시간
- 장비별 동기화 현황 테이블
- 대기/실패 항목 수
- 권장 조치사항