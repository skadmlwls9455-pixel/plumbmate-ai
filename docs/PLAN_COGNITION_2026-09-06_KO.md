# 인지 기능(기억·감정·욕구·목표) 구현 계획

- 기준: `lumen/bridge-reliability` @ `dd2977d` (228 테스트), `MASTER_DESIGN_KO.md` §6·§7.4·§9·§11·§13, `aiden` RFC-001(기억 시스템), NaviBrain 논문 매니페스트(83편, 2026-08-10).
- 제약: 게임 쪽은 Python 3.7·엔진 스레드 비차단·파일/DB 접근 금지. 모든 인지 처리와 SQLite는 동반 앱(3.11+). 저장은 풍부하게, 모델 입력은 12KB packer 안에서만.
- 원칙: "RFC 이름의 빈 클래스부터 늘리지 않는다"(UX 검토 §다음 순서 4). 각 단계는 **관찰 → 저장 → 회상 → 대사**가 끝까지 이어지는 수직 기능 하나로 닫는다.

## 0. 현실 점검 — 코드에 있는 것과 없는 것

| 항목 | 상태 | 근거 |
|---|---|---|
| 턴 계약(request/response 스키마, 길이·허용 필드) | 있음 | `companion/contracts.py`, `schemas/*.json` |
| 12KB 컨텍스트 packer, 세션당 20회 시도 예산 | 있음 | `budget.py` `clamp_request`, `SessionBudget` |
| 요청 격리·턴 영수증 | 있음 | `engine.py`, `turn_status.py`, `turn_bridge.py` |
| 이벤트 소켓(allow-list 6종, HMAC, at-most-once) | 있음 | `event_socket.py`, `bridge_client.py` |
| 세이브 계보 판정 헬퍼 | 있음(연결 전) | `save_lineage.py`, `save_slots.py` |
| 기억 DB·검색·회상 | **없음** | `engine.py`가 `memory_summary=""`로 고정 |
| 관찰 이벤트 원자화(Scene Atomizer) | **없음** | 게임 쪽은 `heartbeat`만 실제 발생 |
| `BehaviorProposal`·`PolicyKernel`·`DecisionTrace`·IC 스케줄러 | **설계 문서에만 존재** | `MASTER_DESIGN_KO.md` §9.2, §18 |
| 행동 실행 | 없음(`suggest_only`) | ROADMAP Milestone 2 |

다른 클로드의 논문 정리는 "BehaviorProposal에 감정 가중치", "DecisionTrace 기록을 Reflexion에" 처럼 위 표의 **설계 전용 개념을 이미 있는 코드로 전제**했다. 그 방향 자체는 맞지만, 순서는 "그 구조를 먼저 만드는 것"이 아니라 "지금 있는 턴 파이프라인에 가장 작은 기억 하나를 끼우는 것"이어야 한다.

## 1. 논문 → 구조 매핑 (정정 포함)

| 논문 | 쓰는 곳 | 판단 |
|---|---|---|
| **#38 Generative Agents** (Park 2023) | 기억 스트림 + 검색 점수(최근성·중요도·관련성) + 성찰 + 계획 | **가장 직접적인 청사진.** RFC-001의 점수 공식(`kw/ent/imp/rec/open`)은 이 논문의 3항 점수를 규칙 기반으로 확장한 것. 정리에서 빠져 있어 최우선으로 추가 |
| #11 MemGPT | 계층(작업 기억 ↔ 장기 저장) + 예산 내 page-in | 12KB packer가 이미 "메인 컨텍스트 창"이다. MemGPT의 self-directed paging은 LLM 도구 호출이 필요해 비용이 큼 → **규칙 기반 선택기(3~6개)가 page-in을 대신**한다 |
| #3 CoALA | 기억 4분류(작업/일화/의미/절차) | RFC-001 유형(canon/episodic/sentiment/promise/summary)의 상위 어휘로 채택. 절차 기억은 행동 실행 전까지 보류 |
| #16 Complementary Learning Systems | 빠른 일화 저장 → 느린 의미 통합 | RFC-001 §8 병합·요약 잡의 이론적 근거. 야간 배치 = "느린 시스템" |
| #8 EMA (Marsella & Gratch) | 사건 평가(appraisal) → 감정 → 대처 | 게임 감정은 `ActualAffect` 원본(§13.1)이고, EMA는 **에이든이 사건을 어떻게 받아들이는가**(directed sentiment 변화)의 규칙 엔진에 쓴다. 게임 mood를 덮어쓰지 않는다 |
| #9 Homeostatic RL | 욕구 편차가 동기를 만든다 | 심즈 motive(배고픔·에너지·사회·재미…)를 스냅샷에 실어 **욕구 긴급도**로 변환. 행동 실행 전에는 제안 편향과 대사 톤에만 사용 |
| #5 Cohen & Levesque, #28 BDI | 의도 = 커밋을 포함한 선택 | IC 스케줄러(§9)의 이론 근거. 구현은 Phase 4 |
| #48 Reflexion | 언어적 자기 수정 | 세션 종료 시 성찰 잡. 산출물은 반드시 `ai_inferred`(승격 관문 통과 전 canon 불가) |
| #36/#37 Autotelic, #40 Proactive Agent | 내생적 목표·선제 행동 | 목표 생성기 → IC 후보. **행동 실행 런타임(RFC-002) 없이는 제안까지만** |
| #44 Goal Drift, #42 Left Alone | 장기 일관성 | 장기 플레이 회귀 테스트의 설계 기준 (200턴 mock 검사를 목표 일관성 검사로 확장) |
| #71~#80 Bayesian ToM 계열, #21 MToM | 상대 의도·감정 추정 | `PerceivedAffect`·`TRIAD`(§11.5·§13.1). Phase 5 이후 |
| #59 DGM, #60~#64 자기진화 | 코드 자기 수정 | **채택 안 함.** 파라미터(감쇠 반감기·가중치) 튜닝을 데이터로 하는 수준까지만 |
| #39 Voyager | 스킬 라이브러리 | 행동 카탈로그(허용 상호작용 목록)가 생긴 뒤 검토 |

## 2. 경계 배치 — 무엇이 게임 쪽에 가고 무엇이 동반 앱에 가는가

게임 쪽(3.7)에 추가하는 것은 딱 두 가지, 둘 다 **기존 비차단 경로** 안에서만:

1. **스냅샷 확장**: `_snapshot`에 motive 수준(예: `commodity_tracker`의 hunger/energy/social/fun/hygiene/bladder)과 현재 buff 이름 몇 개. 요청 스키마에 `actor.motives{}`·`actor.buffs[]` 추가(길이 상한 명시). 파일 쓰기는 이미 명시적 명령 경로에서만 일어난다.
2. **관찰 이벤트 전달**: `EventManagerService`에 `InteractionComplete`·`MoodChange` 핸들러를 등록하고, 콜백 안에서는 `TICK_DRIVER.client.queue_event()`만 호출한다(§7.6 경로는 런타임 증거표에서 확인됨). 정규화·중요도 판정은 하지 않는다. allow-list에 `interaction_completed`, `mood_changed`를 양쪽 동시에 추가하고 기존 동일성 테스트가 지킨다.

동반 앱(3.11)이 나머지 전부: SQLite 기억 저장, 평가(appraisal), 검색·선택, 요약·성찰 잡, 목표 생성. 게임 콜백은 여전히 DB·모델·파일을 모른다.

## 3. 단계별 계획

### Phase 0 — 기반 정리 (즉시)

- 기반 브랜치를 `lumen/bridge-reliability`로 확정. PR #1은 대체.
- 루멘이 "다음 단계"로 남긴 keepalive(120초)·미전송 outbox 이관·ACK 기한을 작은 후속 PR로 이식.
- 완료 조건: CI(ubuntu/macOS × 3.11/3.13) green, 인게임 스모크(다단어 채팅·stop/start).

### Phase 1 — 기억 수직 슬라이스 (첫 번째 진짜 기능)

목표: **관찰된 사건 하나 → 출처 있는 기억 한 줄 → 다음 턴의 적절한 회상 한 문장.**

- `companion/memory/store.py`: RFC-001 §10 스키마의 **부분집합**만 — `entities`, `entity_aliases`, `memories`(type: episodic/canon/promise), `memory_entities`, `retrieval_log`, FTS5. sentiment·relationship_summary 테이블은 스키마엔 두되 쓰지 않는다.
- 쓰기 경로: 소켓 이벤트(`zone_loaded`, `interaction_started/completed`)와 턴 요청의 `recent_events` → `provenance=game_observed`, importance는 §7 규칙표. `memory_patch`는 계속 버린다(루멘 정책 유지).
- 읽기 경로: RFC-001 §4 점수 공식 그대로, 임베딩 없음. 3~6개 선택 → `memory_summary`에 **한 줄씩** 채워 12KB packer로 넘긴다. packer가 잘라내면 잘린 사실은 "모른다"로 둔다(UX 검토 원칙).
- 세이브 범위: `save_lineage`가 `VERIFIED_LOAD`가 아니면 회상을 끄고 저장만 한다. 루멘의 "미검증 세이브 기억 복원 금지" 정책과 정합.
- 테스트: RFC-001 §11의 T1(약속 트리거)·T3(별칭)·T5(중요도 vs 최근성)·T9(빈 DB)·T10(상한)을 골든 테스트로. 200턴 mock 시나리오에 "회상이 12KB 안에서 3~6개를 넘지 않는다"를 추가.
- 완료 조건: mock 프로바이더로 `plumbmate.chat 아까 그 불난 거 기억나?` → 응답에 해당 사건 회상 포함, `retrieval_log`에 점수 분해 기록.

### Phase 2 — 감정·욕구 (EMA + Homeostatic)

- `companion/affect.py`: 사건 → **평가 규칙표** → 방향성 sentiment 델타(`source→target`, valence/intensity). 게임 mood는 `ActualAffect`로 그대로 보관하고 에이든의 해석은 `ai_inferred` sentiment 행으로만 저장(confidence ≤ 0.9, RFC-001 §9).
- `companion/drives.py`: motive 편차 → 긴급도(0~1). 출력은 `state` 블록 한 줄("배고픔 높음, 사회 욕구 중간")로 프롬프트에 들어가고, `action.arguments.idea` 제안에 편향을 준다.
- 스키마: request에 `actor.motives`·`actor.buffs`(Phase 2 게임 쪽 변경 1번), 응답은 변경 없음.
- 테스트: 결정론적 평가 규칙(같은 사건 → 같은 델타), sentiment 비대칭(T2), 욕구 긴급도 단조성.
- 완료 조건: 같은 메시지라도 배고픈 에이든과 배부른 에이든의 mock/실 응답 제안이 달라진다는 것을 골든 테스트로 고정.

### Phase 3 — 계층 기억 + 성찰 (MemGPT/CLS + Reflexion)

- `RecentEventsBuffer`(§7.4)를 링 버퍼로 구현하고, 야간 배치 = RFC-001 §8 병합·아카이브 잡, 주 1회 `relationship_summary` 재생성(관계당 LLM 1콜, 세션 예산에서 차감).
- Reflexion: 세션 종료 시 `retrieval_log`와 턴 이력을 보고 "다음에 더 잘할 것" 1~3줄을 `ai_inferred`로 저장. **승격 관문(`promote_to_canon`) 없이는 절대 canon이 되지 않는다.**
- 테스트: T6(병합), T7(고정 보호), T11(모순 격리), T12(승격 관문).

### Phase 4 — 목표와 의도 (BDI/Autotelic/Proactive)

- 목표 생성기: 관계 지표·욕구·미완료 약속에서 IC 후보(`goal`, `success_predicate`, `expires_at`) 생성. §9의 상태기계는 `CREATED/QUEUED/ACTIVE/SUSPENDED/COMPLETED/EXPIRED`만 먼저.
- 실행은 없다. 후보는 `suggest_only` 응답의 `idea`와 대사에만 반영. 실행은 RFC-002(행동 소유권·PolicyKernel 하드 게이트)가 먼저다.
- 테스트: 목표 드리프트 검사(#44) — 200턴 동안 활성 목표가 근거 없이 바뀌지 않는다.

### Phase 5+ — 마음 이론, 파라미터 자기조정

- `PerceivedAffect`·`TRIAD`(§11.5·§13.1)를 #71~#80의 역계획 관점으로. 
- 감쇠 반감기·가중치는 `retrieval_config` 한 행으로 두고, 회수 실패율 데이터로 조정(DGM은 여기까지만).

## 4. 우선순위와 추천

1. **Phase 0 → Phase 1**이 전부다. 기억 한 줄이 실제로 회상되는 것을 보기 전에는 감정·목표를 붙이지 않는다.
2. 다른 클로드의 "1단계 = #8+#9"는 **2단계로 미룬다.** 감정·욕구는 기억 위에서만 의미가 있고, 지금은 기억이 없다.
3. Generative Agents(#38)를 정리에 추가하고 RFC-001의 근거로 명시한다.

## 5. 가장 위험한 결정 세 가지

- **기억 회상을 세이브 검증(`VERIFIED_LOAD`)에 묶는 것** — 안전하지만, 게임 쪽 저장 훅이 확인될 때까지 회상이 "저장만 하고 꺼진" 상태로 보일 수 있다. 기본값: 묶는다. 대신 `plumbmate.status`에 "기억: 저장 중 / 회상 꺼짐(세이브 미검증)"을 표시해 오해를 막는다.
- **임베딩 없이 시작** — RFC-001 위험 1과 동일. 기본값: FTS5+별칭, `retrieval_log`로 회수 실패를 계측한 뒤 결정.
- **AI 추론의 canon 오염** — Reflexion·appraisal 산출물은 전부 `ai_inferred`. 기본값: 자동 승격 옵션을 만들지 않는다.
