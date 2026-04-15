# EF Core Migration Skill

EF Core 마이그레이션을 관리합니다.

## 사용법
- `/ef-migrate` - 현재 상태 확인
- `/ef-migrate add <이름>` - 새 마이그레이션 생성
- `/ef-migrate update` - 데이터베이스 업데이트

## 작업 절차

1. **현재 마이그레이션 상태 확인**
   ```bash
   cd ProSafe.Data.PostgreSql && dotnet ef migrations list
   ```

2. **모델 변경사항 확인**
   - `ProSafe.Data.Core/Models/` 디렉토리의 최근 변경 확인
   - `ProSafeDbContext` 설정 확인

3. **인자가 "add <이름>"인 경우:**
   ```bash
   cd ProSafe.Data.PostgreSql && dotnet ef migrations add <이름>
   ```

4. **인자가 "update"인 경우:**
   ```bash
   cd ProSafe.Data.PostgreSql && dotnet ef database update
   ```

5. **결과 요약 보고**
   - 생성된 마이그레이션 파일 경로
   - 적용된 변경사항 설명

## 주의사항
- 마이그레이션 전 빌드 성공 여부 확인
- PendingModelChangesWarning 발생 시 새 마이그레이션 필요
