# Analyze Legacy Skill

레거시 WinForm 코드를 분석하여 WPF 마이그레이션을 위한 정보를 추출합니다.

## 사용법
- `/analyze-legacy <기능명>` - 특정 기능 분석
- `/analyze-legacy <파일경로>` - 특정 파일 분석
- `/analyze-legacy --list` - 주요 기능 목록 출력

## 레거시 프로젝트 경로
`D:\Project\ProSafe.wpf\csharp-solutions\Itlog-HikFace-DllApi`

## 분석 항목

### 1. UI 구조 분석
- Form/UserControl 계층 구조
- 컨트롤 목록 (DataGridView, Button, TextBox 등)
- 이벤트 핸들러 매핑

### 2. 비즈니스 로직 분석
- 핵심 메서드 식별
- 호출 흐름 (Call Graph)
- 전역 변수/상태 의존성

### 3. 데이터 레이어 분석
- 사용 테이블 목록
- SQL 쿼리 추출
- 파라미터 매핑

### 4. 외부 연동 분석
- HikVision SDK 호출
- 서버 API 호출
- 파일 I/O

## 주요 기능별 파일 매핑

| 기능 | 레거시 파일 | 설명 |
|------|------------|------|
| 근로자 관리 | `FaceIDAgentDw/Worker.cs` | 근로자 조회/등록/수정 |
| 얼굴 관리 | `FaceIDAgentDw/Face.cs` | 얼굴 사진 관리 |
| 장비 관리 | `FaceIDAgentDw/Device/DeviceManager.cs` | 장비 연결/제어 |
| 동기화 | `FaceSync/DataSync.cs` | 7단계 동기화 |
| 이벤트 수집 | `FaceIDAgentDw/Device/DeviceEventGet.cs` | 출입 이벤트 |
| 서버 통신 | `ItlogLibUDDw/Pms2Ud.cs` | PMS API |
| DB 접근 | `ItlogLibDataDw/Pms2MsSql.cs` | SQL 쿼리 |

## 분석 에이전트 활용

```
Explore agent를 사용하여 레거시 코드 탐색:
- 경로: D:\Project\ProSafe.wpf\csharp-solutions\Itlog-HikFace-DllApi
- 키워드 검색, 파일 패턴 매칭
- 호출 관계 분석
```

## 출력 형식

```markdown
# [기능명] 레거시 분석 결과

## 1. UI 구조
| 컨트롤 | 타입 | 이벤트 | 용도 |
|--------|------|--------|------|
| dgvWorker | DataGridView | CellClick | 근로자 목록 |
| btnSearch | Button | Click | 검색 실행 |

## 2. 핵심 메서드
| 메서드 | 파일:라인 | 설명 |
|--------|----------|------|
| WorkerSearch() | Worker.cs:150 | 근로자 검색 |

## 3. DB 쿼리
```sql
SELECT * FROM pms2_worker WHERE ...
```

## 4. 외부 연동
- SDK: NET_DVR_xxx()
- API: WorkerDataGet()

## 5. WPF 매핑 제안
| 레거시 항목 | WPF 대상 | 비고 |
|------------|---------|------|
| Worker.cs | WorkerManagementViewModel.cs | MVVM 변환 |
```

## 레거시 명명 규칙 참고

| 접두사 | 타입 | 예시 |
|-------|------|------|
| `s` | string | `sName`, `sCode` |
| `i` | int | `iCount`, `iDeviceNo` |
| `b` | bool | `bFlag`, `bSuccess` |
| `ds` | DataSet | `dsWorker` |
| `g_` | 전역 변수 | `g_bInitSDK` |

## 상태 코드 매핑

### 레거시 (pms2_dworker.card/photo)
| 값 | 의미 |
|----|------|
| `0` | 대기 (미등록) |
| `1` | 전송 완료 |
| `2` | 확인 완료 |
| `D` | 삭제 대기 |
| `-1` | 오류 |

### WPF (SyncStatus enum)
| 값 | 의미 |
|----|------|
| `Pending (0)` | 대기 |
| `Processing (1)` | 처리중 |
| `Success (2)` | 성공 |
| `Fail (3)` | 실패 |
| `Ignored (9)` | 무시 |
| `NotRegistered (10)` | 미등록 |
