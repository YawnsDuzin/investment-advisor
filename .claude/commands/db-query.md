# Database Query Skill

PostgreSQL 데이터베이스 조회를 수행합니다 (@postgres MCP 사용).

## 사용법
- `/db-query workers` - 근로자 목록
- `/db-query devices` - 장비 목록
- `/db-query codes <접두사>` - 공통코드 조회 (A=공종, B=직종, H=장비종류, I=장비번호, J=장비위치)
- `/db-query companies` - 협력업체 목록
- `/db-query logs` - 최근 출입 로그
- `/db-query <SQL>` - 직접 SQL 실행

## 주요 테이블

### app_settings (설정)
```sql
SELECT * FROM app_settings WHERE idx = 1;
```

### workers (근로자)
```sql
SELECT idx, w_cd, w_name, sj_cd, w_prejobtype, w_jobtype, w_workstatus
FROM workers
WHERE h_cd = (SELECT h_cd FROM app_settings WHERE idx = 1)
  AND s_cd = (SELECT s_cd FROM app_settings WHERE idx = 1)
LIMIT 50;
```

### devices (장비)
```sql
SELECT idx, name, ip, port, device_kind, location, is_active
FROM devices
WHERE h_cd = (SELECT h_cd FROM app_settings WHERE idx = 1)
  AND s_cd = (SELECT s_cd FROM app_settings WHERE idx = 1);
```

### codes (공통코드)
```sql
-- 공종코드 (A로 시작)
SELECT sub_cd, sub_nm FROM codes WHERE sub_cd LIKE 'A%';
-- 직종코드 (B로 시작)
SELECT sub_cd, sub_nm FROM codes WHERE sub_cd LIKE 'B%';
-- 장비종류 (H로 시작)
SELECT sub_cd, sub_nm FROM codes WHERE sub_cd LIKE 'H%';
```

### companies (협력업체)
```sql
SELECT sj_cd, sj_name, is_active
FROM companies
WHERE h_cd = (SELECT h_cd FROM app_settings WHERE idx = 1)
  AND s_cd = (SELECT s_cd FROM app_settings WHERE idx = 1);
```

### access_logs (출입로그)
```sql
SELECT idx, device_id, worker_id, event_time, event_type, upload_fail
FROM access_logs
ORDER BY event_time DESC
LIMIT 20;
```

## 참고
- 암호화된 컬럼 (w_name, w_hnum 등)은 복호화 없이 조회됨
- h_cd, s_cd 필터링은 app_settings 기준 적용