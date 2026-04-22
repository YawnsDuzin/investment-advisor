========================
2026.04.22(수)
========================

_docs\20260422172248_recommendation-engine-redesign.md 의 작업계획을 확인하고, 작업을 진행해줘.

[추가1] "Phase 1a부터 시작 (스키마 v23 + sector_mapping + KRX universe_sync) — 가장 안전한 출발점" 부터 진행해줘.
[추가2] 커밋하고 다음작업 진행해줘.
[추가2-1] "옵션 A (Wikipedia 승인)" 할게 작업 진행해줘.
[추가3] 커밋하고 다음작업 진행해줘.
[추가4] 커밋하고 다음작업 진행해줘.
[추가5] 커밋해줘.

[추가6] "_docs\20260422172248_recommendation-engine-redesign.md" 모든 기능 구현한거야?
이제 분석작업 진행하면 변경된 로직으로 처리되는거야?

[추가7] 변경된 내용과 추가작업을 정리해서 _docs 폴더에 md파일로 생성해줘.

========================

# KRX 전체 메타 sync (KOSPI + KOSDAQ)
python -m analyzer.universe_sync --mode meta --market KRX

python -m analyzer.universe_sync --mode meta --market US

python -m analyzer.main

========================

A로 해주고, 커밋을 먼저 하고 라즈베리파이에서 재분석 명령 알려줘

========================

stock_universe 테이블은 종목별 최신데이터 1개만 관리하는 테이블이야?

========================

종목별 일별 데이터를 1년정도 수집하고 관리하는 테이블을 추가하는 작업을 진행하는 부분으 검토해줘.

[추가1] 결정사항은 비슷한 서비스의 일반적인 방법을 제안 및 결정하고 수동 및 자동 처리 등을 고려하여 작업항목을 정리해서 _docs 폴더에 md 파일로 생성해줘. 저장 후, 활용 방안도 추가해서 작성해줘.
[[생성파일 _docs\20260422235016_ohlcv-history-table-plan.md

========================

git commit & push 해줘 (git status 에서 표시되는 모든 항목)

========================