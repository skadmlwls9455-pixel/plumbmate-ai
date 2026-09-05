# RFC-001: 세이브 계보 판별 — companion 구현 부분집합

상태: 오프라인 순수 정책 함수 및 합성 픽스처 테스트 구현. 실제 게임 저장·불러오기 신호 연결과 영구 저장소 연동은 미구현.

상위 계약은 [MASTER_DESIGN_KO.md](MASTER_DESIGN_KO.md)의 5.3 세이브 계보와 9.1 재시작 복원이다. 이 문서는 RFC-001 전체 기억 모델의 완료를 뜻하지 않는다.

## 구현 범위

`companion/src/plumbmate/save_lineage.py`는 현재 관측, 이전 관측, companion의 확정 커밋 기록, 별도로 검증된 불러오기 출처를 받아 판정한다. 파일·게임 API·SQLite에 접근하지 않으며 기존 `CompanionEngine`이나 게임 모드에는 연결하지 않았다. 기존 MVP의 세션 기반 기억 파일에 이 정책이 이미 적용되었다고 볼 수 없다.

`assess_save_lineage()`는 `UNRESOLVED` 또는 `VERIFIED_LOAD`와 진단 힌트를 반환한다. `select_restorable_memory()`는 반환된 확정 경계로 참조를 `eligible`과 `quarantined`로 분리한다. 격리는 조회 후보 제외를 뜻하며 원본 삭제·이동·자동 병합을 수행하지 않는다.

`companion/src/plumbmate/save_slots.py`는 슬롯 입력을 엄격히 정규화하고 확인된 게임 빌드의 저장 슬롯 메타데이터를 분류한다. 파일 이름은 문자열로만 검사하며 파일을 열지 않는다.

## 슬롯·가족·게임 시각 교차 확인

`SaveObservation`은 `session_id`, `load_id`, `slot_id`, `household_id`, `ticks`를 가진다. 게임 ID는 상위 어댑터가 정규화한 불투명 문자열로 받는다. `household_id`는 활성 가족 ID이며 저장 정체성이 아니다. 동일한 프로세스에서도 불러오기가 일어나면 새 `load_id`가 필요하다.

| 관측 차이 | 진단 힌트 | 처리 의미 |
|---|---|---|
| 첫 관측 | `FIRST_OBSERVATION` | 새 세이브라는 결론을 내리지 않음 |
| 일부 메타데이터 없음 | `INCOMPLETE_OBSERVATION` | 이전 값으로 임의 보충하지 않음 |
| 슬롯 변경 | `SLOT_CHANGED` | 새 저장·다른 이름으로 저장·기존 슬롯 불러오기 후보. 서로 다른 슬롯의 시각 순서는 비교하지 않음 |
| 활성 가족 변경 | `HOUSEHOLD_CHANGED` | 회전 플레이 가능. 자동 분기 생성 사유가 아님 |
| 같은 슬롯에서 시각 감소 | `TICKS_REGRESSED` | 롤백·복구 후보. 이전 타임라인의 기억을 자동 복원하지 않음 |
| 같은 슬롯에서 같은 시각 | `TICKS_EQUAL` | 일시정지 중 연속 저장과 같은 시각의 다른 분기를 구별할 수 없음 |
| 같은 슬롯에서 시각 증가 | `TICKS_ADVANCED` | 진행 후보일 뿐, 연속성이나 동일 커밋을 입증하지 못함 |

이 튜플의 해시를 `save_scope_id` 또는 `save_commit_id`로 만들어서는 안 된다. 같은 튜플의 서로 다른 세이브도 가능하고 같은 세이브에서 가족이 달라질 수도 있다. 힌트는 검색·진단 단서이며 어느 힌트도 단독으로 복원 권한을 주지 않는다.

## 자동저장 예약 슬롯과 저장 출처

현재 로컬 빌드 `1.127.41.1230`의 `simulation.zip` 내부 `services/persistence_service.pyc`에서 `AUTO_SAVE_SLOT_ID = 4294967295`, 즉 `0xffffffff`를 확인했다. 확인한 member SHA-256은 `96d4ffd7c4696232306d7aff7ae977c5d9eeaa5599425b71a4df984dcecc58d1`이며 상수 할당은 원본 코드 행 정보 134에 대응한다. 이것은 정적 빌드 근거이며 실제 자동저장을 실행한 결과가 아니다.

`classify_save_slot(value, game_build=...)`은 정확히 이 빌드일 때 해당 숫자를 `ENGINE_AUTOSAVE_RESERVED`로 분류한다. 나머지 숫자는 `OTHER_SLOT`이며 수동저장으로 분류하지 않는다. 빌드가 누락되거나 달라지면 `0xffffffff`도 `UNKNOWN`이다. 게임 빌드 보고 자체의 인증과 다음 패치의 상수 재검증은 호출자·capability 계층 책임이다.

`normalize_slot_id()`는 uint32 정수, 1~10자리 ASCII 십진 문자열, `0x`로 시작하는 1~8자리 16진 문자열과 다음의 정확한 **파일 이름만** 받는다.

- `Slot_XXXXXXXX`
- `Slot_XXXXXXXX.save`
- `Slot_XXXXXXXX.save.verN`

`XXXXXXXX`는 정확히 8자리 16진수이며 대소문자를 구별하지 않는다. `N`은 선행 0 없는 비음수 ASCII 정수이고 `0` 자체는 허용된다. 백업 접미사 지원은 이름 형식 지원이며 게임이 모든 N의 백업을 생성한다는 주장이 아니다. 경로·공백·후행 접미사·음수·bool·실수·uint32 초과 값은 거부한다. 분류 결과의 `canonical_id`는 `Slot_ffffffff` 같은 형식으로 반환되며 실제 어댑터는 이를 `SaveObservation.slot_id`와 `SaveCommit.slot_id`의 정규화된 입력으로 사용한다. 접미사를 제거한 슬롯 값은 백업 파일의 정체성을 보존하지 않으므로 실제 불러온 아티팩트와 커밋의 연결은 별도 provenance에 남겨야 한다.

저장 위치의 종류(`storage_kind`)와 저장을 시작한 주체(`origin`)는 별개다. 이 분류 함수는 모든 경우에 `origin=UNKNOWN`을 반환한다. 예약 슬롯이라는 사실만으로 어떤 호출이 엔진 자동저장에서 시작되었다고 증명할 수 없으며, 일반 번호 슬롯도 MCCC 같은 모드의 자동저장에 사용될 수 있다.

동일 바이트코드의 `save_game_gen`(시작 행 정보 538)에는 `send_save_message` 경로 안에서 성공, `msg.slot_id > 1`, `msg.slot_id != AUTO_SAVE_SLOT_ID` 조건으로 manual-save 콜백을 호출하는 분기가 있다. 따라서 그 콜백만으로 모든 저장의 완료를 관찰한다고 주장할 수 없다. `AutosaveService`와 `REQUEST_AUTOSAVE`의 존재도 실제 저장 완료 신호 또는 load provenance 검증과 구분한다.

자동저장은 저장의 한 종류이며 새 계보라는 뜻이 아니다. 서로 다른 월드가 같은 `Slot_ffffffff` 값을 사용하더라도 기억을 공유하지 않는다. 해당 슬롯에서 같은 ticks의 반복 저장이 일어나도 각 commit/watermark를 유지해야 한다.

### 빈번한 저장의 비용 제어

별개의 저장 시도를 throttle해서 `SAVE_BEGIN`, `SAVE_COMMITTED` 또는 `SAVE_ABORTED` 경계를 생략하지 않는다. 같은 슬롯·시각·자동저장 종류는 중복 제거 키가 아니다. 동일 `attempt_id`에 대한 **동일 단계** 전달 재시도만 멱등 처리하고, 같은 시도의 시작과 완료는 각각 처리한다. 같은 ID에 충돌하는 payload가 오면 단순 중복으로 버리지 않고 미해결 상태로 남겨야 한다.

비싼 요약·색인 갱신은 확정 경계를 기록한 뒤 배치할 수 있지만 매 저장의 commit/watermark 기록과 불러오기 연결 증거는 보존한다. 이 문단은 향후 저장 수명주기 구현 계약이며 현재 모듈에 throttle·이벤트 수신·중복 제거기를 구현했다는 뜻은 아니다.

## 복원 허용 계약

검증된 `LoadProvenance`는 이번 `session_id`와 `load_id`를 특정 `save_scope_id`, `save_commit_id`, `working_epoch`, `watermark_seq`에 연결한다. `evidence_id`는 그 연결을 독립적으로 확인한 근거의 참조다. 단순히 슬롯을 선택했다는 UI 정보나 같은 튜플은 근거가 아니다.

`SaveCommit.verified`와 `LoadProvenance.verified`는 기본값이 `False`다. 플래그는 신뢰 경계 내부의 호출자 계약이며 인증 기능이 아니다. 모델 응답, 일반 채팅 브리지 요청이나 검증되지 않은 JSON의 `verified=true`를 그대로 신뢰해서는 안 된다. 현재 구현에는 이 값을 실제 게임 근거로 채우는 어댑터가 없다.

복원은 다음 검사가 모두 통과할 때만 가능하다.

1. 출처가 검증되었고 현재 세션·불러오기 ID와 정확히 일치한다.
2. 출처가 지목한 scope·commit 조합이 companion 커밋 목록에서 유일하다. 같은 키의 중복 기록도 모호성으로 처리한다.
3. 기록 상태가 검증된 `COMMITTED`다. `PENDING`, `ABORTED`, 미검증 기록은 제외한다.
4. epoch와 watermark가 출처와 커밋 기록에서 정확히 일치한다.
5. 관측 슬롯이 기록과 같고 관측 시각이 저장 시각보다 이르지 않다. 슬롯이나 시각을 얻지 못하면 미해결이다. 가족 ID 누락은 독립적으로 검증된 저장 연결을 무효화하지 않는다.

불러오기 후 게임이 진행되어 관측 시각이 커져도 복원 경계는 저장 당시 `ticks`와 `watermark_seq`에 고정된다. 다른 세션이나 이전 불러오기에 대한 출처를 재사용할 수 없다. 같은 시각의 여러 저장은 출처의 정확한 커밋으로 구별하며 목록 순서로 선택하지 않는다.

## 기억과 IC 경계

`MemoryReference`는 payload 없이 ID, scope, commit, epoch, 사건 순번과 시각을 표현한다. 복원 후보는 다음을 모두 만족해야 한다.

- 검증된 복원 판정이 있다.
- scope·commit·epoch가 복원 경계와 정확히 같다.
- 사건 순번이 확정 watermark 이하이며 사건 시각이 저장 시각 이하이다.

동일 슬롯·동일 시각이어도 다른 scope나 commit의 기억은 제외한다. 이전 저장으로 돌아갈 때 더 나중 커밋, 미래 순번, 미래 시각과 다른 epoch의 기억을 모두 격리한다. 낮은 순번만으로 epoch 간 순서를 추정하지 않는다.

이 구현 부분집합은 조상 커밋 그래프를 탐색하지 않는다. 이전 커밋의 기억을 읽으려면 앞으로 검증된 조상 관계 또는 불러온 커밋에 명시적으로 연결된 불변 스냅샷이 필요하다. 단순히 현재 scope와 같거나 시각이 이르다는 이유만으로 이전 기록을 복원하지 않는다. DB에서 기록을 무조건 재귀속시키는 동작도 없다.

IC 체크포인트에도 같은 경계 검사를 적용해야 하지만 실제 IC 스케줄러 연결은 아직 없다. 향후 복원할 것은 목표와 진행 정보이며 행동 큐 핸들을 재생하지 않는다. 검증된 체크포인트는 `SUSPENDED_RESTORE`에서 현재 세계를 재검사하고, 저장 연결 미확정 시 `NEEDS_REPLAN` 또는 만료 정책을 적용해야 한다.

## 남은 통합 작업

- RFC-008에서 빌드별 저장 시작·완료·실제 불러오기 식별 capability를 검증한다. 이 모듈은 게임 세이브를 읽거나 쓰지 않으며 훅을 설치하지 않는다.
- `SAVE_BEGIN → SAVE_COMMITTED/SAVE_ABORTED`와 pending 체크포인트의 원자적 연결을 구현한다. 현재 `CommitState`는 입력 계약이며 저장 트랜잭션 구현이 아니다.
- 신뢰할 수 있는 실제 load provenance가 없으면 `UNRESOLVED`를 유지하고 미확정 기억을 격리한다. 게임 저장 신호 없는 상태에서 정확한 복원을 지원한다고 표시하지 않는다.
- 새 세이브·다른 이름으로 저장·복구 이후 쓰기·동시 기기 수정은 별도의 계보 조정자가 scope를 배정해야 한다. 이 판별 함수는 분기 후보를 드러내지만 자동 scope 생성이나 병합을 수행하지 않는다.
- companion DB와 engine의 실제 조회 경로에 scope·commit·epoch·watermark 필터를 연결하고 회귀 테스트를 추가한다. 게임 세이브 접근, 임의 DB 삭제나 전체 데이터 초기화로 미해결 상태를 처리하지 않는다.

## 검증

합성 픽스처만 사용하며 게임이나 API 계정이 필요 없다.

```bash
PYTHONPATH=companion/src python3 -m unittest discover -s tests -p 'test_save_lineage.py' -v
PYTHONPATH=companion/src python3 -m unittest discover -s tests -p 'test_save_slots.py' -v
```

테스트는 동일 튜플·동일 시각 분기, 가족 전환, 슬롯 변경, 롤백, 미래 기억 차단, 출처 재사용, 미검증·중단 커밋, 중복 커밋 키, epoch/watermark 불일치, 누락·잘못된 메타데이터와 불러오기 후 진행을 다룬다. 합성 `verified=True` 픽스처의 통과는 실제 게임 저장 신호가 검증되었다는 뜻이 아니다.

슬롯 테스트는 숫자·대소문자·백업 이름 정규화, 잘못된 경로·형식·범위 거부, 확인된 빌드와 미확인 빌드의 분류 차이, 슬롯 종류와 출처의 분리, 예약 슬롯을 공유하는 다른 월드의 기억 격리와 같은 시각의 자동저장 커밋 분리를 검증한다.
