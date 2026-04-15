---
name: migration-patterns
description: 레거시 WinForm → WPF 마이그레이션 시 검증된 변환 패턴
type: feedback
---

# 검증된 마이그레이션 패턴

## 1. UI 변환 패턴

### DataGridView → DataGrid
```xml
<!-- 레거시: dgvWorker.DataSource = ds.Tables[0] -->
<DataGrid ItemsSource="{Binding Workers}" AutoGenerateColumns="False">
    <DataGrid.Columns>
        <DataGridTextColumn Header="이름" Binding="{Binding Name}"/>
    </DataGrid.Columns>
</DataGrid>
```

### 이벤트 핸들러 → RelayCommand
```csharp
// 레거시: btnSearch_Click(object sender, EventArgs e)
// WPF:
[RelayCommand]
private async Task SearchAsync()
{
    Workers = new ObservableCollection<Worker>(await _service.GetWorkersAsync());
}
```

### MessageBox → IDialogService
```csharp
// 레거시: MessageBox.Show("완료");
// WPF: await _dialogService.ShowMessageAsync("완료");
```

### 필수 입력 라벨 → RequiredLabelStyle
```xml
<!-- 레거시: Label.ForeColor = Color.Red 또는 수동으로 * 추가 -->
<!-- WPF: RequiredLabelStyle + AttachedProperty -->
<ContentControl Content="IP 주소" Style="{StaticResource RequiredLabelStyle}"/>

<!-- 필수 표시 비활성화 (선택적) -->
<ContentControl Content="비고"
                Style="{StaticResource RequiredLabelStyle}"
                helpers:RequiredLabelHelper.IsRequired="False"/>
```
- 정의: `Styles/Common.xaml`
- Helper: `Helpers/RequiredLabelHelper.cs`

## 2. 데이터 레이어 패턴

### ADO.NET → EF Core Repository
```csharp
// 레거시: SqlCommand + DataSet
// WPF:
public async Task<List<Worker>> GetAllAsync()
{
    return await _context.Workers.ToListAsync();
}
```

### 헝가리안 → PascalCase
| 레거시 | WPF |
|--------|-----|
| `sName` | `Name` |
| `iCount` | `Count` |
| `bFlag` | `IsEnabled` |
| `dsWorker` | `workers` (List) |

## 3. 장비 연동 패턴

### SDK → ISAPI
```csharp
// 레거시: NET_DVR_StartRemoteConfig() + 콜백 대기
// WPF:
public async Task<DeviceCommandResult> SendFaceDataAsync(...)
{
    var response = await _httpClient.PostAsync(url, content);
    // 직접 응답 처리
}
```

### 전역 플래그 → async/await
```csharp
// 레거시:
// g_bWorkerDataSetCheck = true;
// while (g_bWorkerDataSetCheck) Thread.Sleep(100);

// WPF:
await SendFaceDataAsync(...);  // 비동기 완료 대기
```

## 4. 서비스 패턴

### 전역 변수 → DI
```csharp
// 레거시: Program.g_sHcd, Program.g_sScd
// WPF:
public class SettingsProvider : ISettingsProvider
{
    public AppSetting Settings { get; }
}
// ViewModel에서 주입받아 사용
```

### 정적 메서드 → 인스턴스 서비스
```csharp
// 레거시: MsSql.WorkerGet(sHcd, sScd)
// WPF:
private readonly IWorkerRepository _workerRepo;
await _workerRepo.GetAllAsync();
```

## 5. 상태 코드 매핑

| 레거시 (card/photo) | WPF (SyncStatus) |
|---------------------|------------------|
| `"0"` | `Pending (0)` |
| `"1"` | `Processing (1)` |
| `"2"` | `Success (2)` |
| `"D"` | 별도 처리 (삭제 플래그) |
| `"-1"` | `Fail (3)` |

## 6. 로깅 패턴

```csharp
// 레거시: Log.Write(...)
// WPF: Logger.Write(LogLevel.Info, "Category", "Message", "Details");
```

## 주의사항

- 레거시 `Thread.Sleep()` 제거 → `await Task.Delay()` 또는 제거
- 레거시 `lock` → `SemaphoreSlim` 또는 Channel 기반 큐
- 레거시 `Invoke()` → WPF `Dispatcher.Invoke()` 또는 `IProgress<T>`
