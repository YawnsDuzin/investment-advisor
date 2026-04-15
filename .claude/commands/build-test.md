# Build & Test Skill

ProSafe 프로젝트 빌드 및 테스트를 실행합니다.

## 사용법
- `/build-test` - 전체 빌드 + 모든 테스트
- `/build-test build` - 빌드만 실행
- `/build-test <프로젝트명>` - 특정 테스트 프로젝트만 실행

## 작업 절차

1. **Release x64 빌드**
   ```bash
   dotnet build ProSafe/ProSafe.csproj -c Release -p:Platform=x64
   ```

2. **빌드 실패 시:**
   - 오류 메시지 분석
   - 관련 파일 확인 및 수정 제안

3. **빌드 성공 시 테스트 실행:**
   ```bash
   dotnet test ProSafe.Data.Tests/ProSafe.Data.Tests.csproj --no-build -c Release
   dotnet test ProSafe.Device.Tests/ProSafe.Device.Tests.csproj --no-build -c Release
   dotnet test ProSafe.Network.Tests/ProSafe.Network.Tests.csproj --no-build -c Release
   ```

4. **결과 요약**
   - 빌드 시간
   - 테스트 통과/실패 수
   - 실패한 테스트 상세 정보

## 테스트 프로젝트
- `ProSafe.Data.Tests` - Repository 테스트
- `ProSafe.Device.Tests` - Hikvision 장비 테스트
- `ProSafe.Network.Tests` - API 클라이언트 테스트