# Migrate Feature Skill

레거시 WinForm 기능을 WPF로 마이그레이션합니다.

## 사용법
- `/migrate-feature <기능명>` - 특정 기능 마이그레이션
- `/migrate-feature <기능명> --analyze-only` - 분석만 수행

## 경로 정보
- **레거시**: `D:\Project\ProSafe.wpf\csharp-solutions\Itlog-HikFace-DllApi`
- **WPF**: `D:\Project\ProSafe_20260311\ProSafeApp`

## 마이그레이션 워크플로우

### Phase 1: 레거시 분석 (Explore Agent)

레거시 프로젝트에서 해당 기능의 코드를 분석합니다:

1. **UI 레이어** (WinForm)
   - Form/UserControl 파일 (`*.cs`, `*.Designer.cs`)
   - 이벤트 핸들러, 컨트롤 바인딩

2. **비즈니스 로직**
   - `FaceIDAgentDw/` - GUI 메인 로직
   - `FaceSync/` - 동기화 로직
   - `ItlogLibUD/`, `ItlogLibUDDw/` - 서버 API 통신

3. **데이터 레이어**
   - `ItlogLibData/`, `ItlogLibDataDw/` - DB 접근
   - SQL 쿼리, 테이블 구조

4. **장비 제어**
   - `ItlogLibHIK/` - HikVision SDK
   - `ItlogLibISAPI/` - ISAPI HTTP

### Phase 2: WPF 아키텍처 매핑

레거시 → WPF 매핑 규칙:

| 레거시 | WPF |
|--------|-----|
| Form/UserControl | Views/Pages/*.xaml |
| 이벤트 핸들러 | ViewModels/*ViewModel.cs ([RelayCommand]) |
| DataGridView | DataGrid + ObservableCollection |
| ItlogLibData | ProSafe.Data.PostgreSql (Repository) |
| ItlogLibHIK/ISAPI | ProSafe.Device.Hikvision (ISAPI only) |
| ItlogLibUD | ProSafe.Network.Prosafe |

### Phase 3: 코드 생성

1. **ViewModel 생성**
   - CommunityToolkit.Mvvm 사용
   - `[ObservableProperty]`, `[RelayCommand]` 어노테이션

2. **View 생성**
   - XAML UserControl
   - DataBinding 설정

3. **Service 통합**
   - 필요시 ProSafe.Services.* 프로젝트에 서비스 추가

### Phase 4: 검증

```bash
dotnet build ProSafe/ProSafe.csproj -c Release -p:Platform=x64
```

## 출력 형식

### 분석 결과
```markdown
## 레거시 분석 결과

### UI 구조
- [파일명](경로) - 설명

### 비즈니스 로직
- [파일명](경로:라인) - 핵심 메서드

### DB 쿼리
- 테이블: `table_name`
- 주요 쿼리: SELECT/INSERT/UPDATE

### WPF 매핑 제안
| 레거시 | WPF 대상 |
|--------|---------|
| ... | ... |
```

## 레거시 프로젝트 구조 참고

```
Itlog-HikFace-DllApi/
├── FaceIDAgentDw/        # GUI 메인 (대우 스마티)
│   ├── Device/           # 장비 제어 클래스
│   ├── Face.cs           # 얼굴 관리 UI
│   ├── Worker.cs         # 근로자 관리 UI
│   └── Sync.cs           # 동기화 UI
├── FaceSync/             # 콘솔 동기화
├── ItlogLibData/         # DB (PMS2)
├── ItlogLibDataDw/       # DB (대우 스마티)
├── ItlogLibHIK/          # HikVision SDK
├── ItlogLibISAPI/        # ISAPI HTTP
├── ItlogLibUD/           # 서버 API
└── ItlogLibUDDw/         # 서버 API (대우)
```

## 주의사항

- 레거시는 MSSQL, WPF는 PostgreSQL - 스키마 변환 필요
- 레거시는 HikVision SDK(DLL), WPF는 ISAPI(HTTP) - 인터페이스 변환
- 헝가리안 표기법(sName, iCount) → PascalCase 변환
- 전역 변수(g_*) → DI 서비스로 변환
