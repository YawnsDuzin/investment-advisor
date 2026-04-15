# Architecture Review Skill

Clean Architecture 및 SOLID 원칙 준수 여부를 검토합니다.

## 사용법
- `/check-arch` - 전체 아키텍처 검토
- `/check-arch <파일경로>` - 특정 파일/폴더 검토

## 검토 항목

### 1. 레이어 의존성 검토
```
ProSafe (WPF UI) → Services.* → Data.PostgreSql / Device.Hikvision / Network.Prosafe
                              ↓
                   Core 프로젝트 (인터페이스만)
```

- UI 레이어가 Infrastructure를 직접 참조하지 않는지 확인
- Core 프로젝트가 다른 레이어를 참조하지 않는지 확인

### 2. SOLID 원칙 검토
- **S**ingle Responsibility: 클래스당 하나의 책임
- **O**pen/Closed: 확장에 열림, 수정에 닫힘
- **L**iskov Substitution: 인터페이스 대체 가능성
- **I**nterface Segregation: 인터페이스 분리
- **D**ependency Inversion: 추상화에 의존

### 3. MVVM 패턴 검토 (WPF)
- ViewModel이 View를 직접 참조하지 않는지
- `[ObservableProperty]`, `[RelayCommand]` 사용 여부
- DataBinding 적절성

### 4. Repository 패턴 검토
- `IRepository<T>` 인터페이스 구현
- 비동기 메서드 (`*Async`) 사용
- CancellationToken 지원

## 출력 형식
- 위반 사항 목록 (파일:라인)
- 심각도 (높음/중간/낮음)
- 개선 제안