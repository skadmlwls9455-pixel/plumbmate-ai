# PlumbMate 마스터 설계 지도

- 상태: Living Draft
- 기준일: 2026-09-05
- 목적: 구현 전에 합의된 제품 원칙, 시스템 경계, RFC 분할과 검증 항목을 한곳에서 추적한다.
- 주의: 이 문서는 목표 아키텍처다. 현재 프로토타입은 `suggest_only` 왕복까지만 구현되어 있으며, 기억·자율행동·관계 시뮬레이션은 아직 구현 전이다.

## 1. 제품 한 문장

PlumbMate는 The Sims 4 안의 임의의 심에게 기억과 인격을 가진 AI 에이전트를 부착하여, 플레이어보다 우선하지 않으면서도 스스로 관찰하고 판단하고 행동하게 만드는 로컬 우선 동반 시스템이다.

첫 데모 캐릭터는 에이든과 케이트지만 코드와 데이터 모델은 특정 이름, 성별, 관계 또는 한 명의 AI 심을 전제하지 않는다.

## 2. 절대 불변 원칙

1. 게임에서 관찰된 상태가 세계 사실의 원본이다.
2. 모델 출력은 신뢰하지 않으며, 모델의 추론과 대사는 스스로 canon이 될 수 없다.
3. 플레이어의 직접 행동이 PlumbMate보다 우선한다.
4. 게임 시뮬레이션 스레드는 모델이나 동반 앱의 응답을 기다리지 않는다.
5. 연결이 끊기거나 데이터가 손상되어도 심즈 기본 플레이는 계속되어야 한다.
6. API 키, 세이브 파일, 스크린샷과 로컬 경로는 모델 요청에 포함하지 않는다.
7. 저장할 때는 풍부하게 기록하고, 모델에 보낼 때는 현재 판단에 필요한 정보만 선택한다.
8. 성격 템플릿은 재사용할 수 있지만 기억과 관계 이력은 세이브별 인격 인스턴스에 귀속된다.
9. 인간 같은 실수는 영구 사실을 오염시키는 랜덤 오류가 아니라 관찰·회상·표현 단계의 제한으로 만든다.
10. 관계를 수치 하나로 환원하지 않고, 방향성이 있는 감정·지식·행동·해석의 이력으로 다룬다.
11. 하위 시스템은 행동 후보와 수정값만 제안하며, 최상위 중재자만 게임 행동을 확정한다.
12. 플레이어가 감정이나 사생활을 공유하지 않는 것은 거짓말이 아니며 신뢰 벌점의 근거가 되지 않는다.
13. 애착은 접속 강요, 죄책감, 숨은 벌점이나 데이터 인질이 아니라 상호 돌봄·독립성·공유된 변화에서 생겨야 한다.

## 3. 현재 구현과 목표의 경계

### 현재 프로토타입

- 버전이 있는 파일 기반 요청/응답 프로토콜
- 오프라인 mock 공급자
- 선택형 OpenAI 공급자와 제한된 구조화 출력
- 게임 내 명령으로 작은 상태 스냅샷 생성
- `suggest_only` 응답 표시
- 게임 런타임에 맞춘 `.ts4script` 빌드 및 로드 확인
- loopback 소켓 및 `Zone.update` 틱 클라이언트. 설정은 시작 시/명시적 재시작 시만 읽고, 일반 동반 앱 재시작은 같은 포트로 재연결한다. [신뢰성 수정·검증 기록](BRIDGE_FIXES_2026-09-05_KO.md)의 로컬 테스트와 기존 인게임 확인을 구분한다.
- 실제 행동 실행, 장기 기억, 자동 이벤트 감지는 비활성

### 목표 제품

- 이벤트 기반 관찰
- 여러 AI 심과 여러 세이브 지원
- 로컬 규칙, 경량 모델, 메인 모델의 계층형 라우팅
- IC(Intent Capsule) 기반 중기 행동 계획
- 검증된 기억과 방향성 관계 모델
- 제안, 승인 후 실행, 완전자율 모드
- 접근 가능한 상태 UI와 명확한 비용·개인정보 표시

## 4. RFC 지도

| RFC | 제목 | 핵심 범위 | 상태 |
|---|---|---|---|
| RFC-001 | Memory, Canon and Save Lineage | 사실·추론·대사 분리, 기억 검색, 세이브 분기, 주석 버전 | 분기 판별 부분집합 구현, 게임 저장 연결 전 |
| RFC-002 | Autonomous Action Runtime | 행동 소유권, 우선순위, 생존 제어, 비동기 연결과 복구 | 설계 중 |
| RFC-003 | PM-Wire and Context Budgeting | 동적 엔티티 별칭, 장면 원자화, 관련 정보 증류, ref 검증 | 설계 중 |
| RFC-004 | Model Router and Intent Capsule Scheduler | L0~L3 라우팅, IC 수명주기·충돌·중단·복원 | 설계 중 |
| RFC-005 | Relationship and Preference Graph | 취향 지식, 오해·해명·용서, 갈등·화해 패턴 | 설계 중 |
| RFC-006 | Lived Experience, Perception and Growth | 기념일, 루틴, 말버릇, 주도 행동, 학습과 성장 | 설계 중 |
| RFC-007 | Product Safety, Privacy and Accessibility | 키 보안, 전송 동의, 비용 한도, 접근 가능한 UI | 설계 중 |
| RFC-008 | Compatibility, Testing and Release | 게임 ABI, 타 모드 공존, 픽스처, 플랫폼 빌드 | 설계 중 |

이 문서는 각 RFC의 공통 계약을 기록한다. 세부 스키마와 수용 테스트는 해당 RFC로 분리한다.

빌드별 엔진 근거는 [런타임 증거표](runtime-evidence/TS4_1.127.41.1230_KO.md), 외부 연구의 채택 판단과 열린 시험은 [2026-09-05 검토](RUNTIME_REVIEW_2026-09-05_KO.md)에서 추적한다.

[RFC-001 분기 판별 부분집합](RFC-001_SAVE_LINEAGE_KO.md)과 [RFC-007 설치·연결 진단](RFC-007_INSTALL_CONNECTION_KO.md)을 분리했다. 현재 설치본은 실제 게임 프로세스에서 Python 3.7.0이며 `sqlite3` import가 실패함을 확인했다. SQLite는 외부 동반 앱이 전담하며 게임 콜백의 DB 의존성은 두지 않는다.

## 5. 일반화된 정체성과 세이브 격리

### 5.1 세 종류의 식별자

| 식별자 | 수명 | 용도 |
|---|---|---|
| `game_sim_id` | 게임 세이브 | 실제 심 연결 |
| `entity_id` | `save_scope` | DB의 안정적 참조 |
| `s0`, `s1` | 장면 또는 통신 세션 | PM-Wire 토큰 절약 |

이름은 표시용이며 기본키가 아니다. `A=에이든`, `K=케이트` 같은 고정 별칭은 문서 예시와 테스트 픽스처에서만 사용한다.

### 5.2 인격 템플릿과 살아온 인격

- `PersonaTemplate`: 기본 성격, 말투, 경계, 선호 학습 속도. 기억을 포함하지 않는다.
- `AgentInstance`: 특정 `save_scope`의 특정 심과 결합된 인격. 기억, 감정, 기념일, 취향과 IC가 여기에 속한다.
- 템플릿 복제는 허용하지만 기억이 있는 인격의 다른 심 재부착은 기본 금지한다.

### 5.3 세이브 계보

```text
save_family_id          같은 세이브 계보
save_scope_id           현재 분기 고유 ID
branch_seq              분기 번호
forked_from_scope_id    부모 분기
save_commit_id          게임 저장 시점과 동반 DB를 맞추는 커밋
```

새 세이브, 다른 이름으로 저장, 복구 세이브와 두 기기의 동시 수정은 자동 병합하지 않는다. 충돌 시 새 분기를 만들며 기억과 IC는 해당 분기 안에서만 조회한다.

초기 멀티 사용자 지원은 `save_scope`당 한 쓰기 권한자와 읽기 전용 관전자만 허용한다. 실제 공동 제어는 별도의 `controller_id`와 제어권 임대가 준비된 뒤 지원한다.

### 5.4 가족 전환과 심 가용성

`active_household_id`만으로 실행 가능성을 판단하지 않는다. AI 심마다 다음 런타임 가용성을 추적한다.

```text
household_id
zone_id
is_instanced
is_selectable
is_rabbit_hole
active_household_id
```

`active_household_id`와 `is_instanced`는 같은 뜻이 아니다. 비활성 가족의 심도 현재 로트에 방문하면 인스턴스화되어 게임 행동 큐를 가질 수 있고, 반대로 로트 밖 `SimInfo`는 실행 가능한 `Sim` 인스턴스가 없을 수 있다. 따라서 "비활성 가족은 큐가 없다"고 가정하지 않는다.

초기 제품 정책은 가족 전환 시 해당 가족의 PlumbMate 실행을 안전하게 동결하는 것이다. 현재 월드에 인스턴스화되지 않았거나 래빗홀에 있는 AI 심에게는 어떤 설정에서도 행동을 주입하지 않는다. IC 상태 자체는 `SUSPENDED`로 유지하고 `suspend_reason=inactive_household/rabbit_hole/uninstantiated`를 기록한다. 상태명을 사유마다 늘리지 않는다. 추후 비활성 가족의 방문 심을 제어하는 옵션을 열더라도 `is_instanced`, 현재 zone, 유효한 큐와 사용자 동의를 별도로 확인한다.

다른 가족을 플레이하는 동안 일어난 Neighborhood Stories나 기본 게임 진행은 복귀 시 게임 상태 차이로 조정할 수 있지만, PlumbMate가 직접 관찰하지 않은 승진·관계 변화의 과정까지 episodic으로 지어내지 않는다. `external_progression` 또는 `state_delta_unobserved`로 표시하고 현재 상태만 다시 동기화한다.

래빗홀 안에서는 행동 IC를 중단한다. 검증 가능한 버프, 상호작용 결과 또는 기존 소지품과 연결된 이벤트가 있을 때만 Thought 후보를 만들며, 직장·학교 내부에서 일어난 세부 장면을 임의로 생성하지 않는다.

## 6. 사건, 사실, 주장과 해석

### 6.1 저장 계층

```text
ObservedFact       게임이 직접 확인한 사건
PlayerClaim        플레이어가 말한 내용, 참·거짓 미확정
Utterance          누가 무엇을 말했다는 사실
Interpretation     AI 심의 감정·의도·믿음에 대한 해석
PrivateThought     희망·걱정·망설임 같은 내부 상태
```

모델 대사에 “아까 힘들어 보였어”가 포함되어도 저장되는 사실은 그 문장을 말했다는 것뿐이다. 상대가 실제로 힘들었다는 사실은 게임 관찰이나 명시적 canon 확인이 있어야 한다.

### 6.2 행동 출처

```text
player_direct
plumbmate
survival_guard
native_autonomy
third_party_script
unknown
```

출처와 행위성은 별개다. 각 사건은 `agency`, `observation_scope`, `truth_status`, `retrieval_policy`를 가진다.

게임의 `InteractionSource`는 행동 소유권의 충분한 증거가 아니다. 여러 모드가 같은 `SCRIPT` 계열 값을 사용할 수 있으므로 `source=SCRIPT`만 보고 PlumbMate 행동이라고 판정하거나 취소하지 않는다.

`ActionDispatcher`는 주입 전에 `session_uuid`, `scene_epoch`, `decision_id`, actor, affordance와 target을 가진 `PENDING` 의도를 브리지의 메모리 원장에 기록한다. 실제 enqueue 결과 또는 검증된 생성 콜백에서 정확한 interaction 객체와 연결되었을 때만 `BOUND`로 전환한다. 이 기록은 동반 앱의 감사 원장으로 비동기 전달한다. actor·행동·대상·시각의 유사성만으로 소유권을 부여하지 않으며, 주입 거절은 실행 소유권을 만들지 않는다.

동적 Python 속성이나 커스텀 liability는 보조 마커 후보일 뿐 필수 계약이 아니다. 큐가 interaction 객체를 교체·재생성하거나 continuation을 만들 때 마커가 유지되는지 게임 빌드별 테스트를 통과하기 전에는 소유권 근거로 사용하지 않는다. continuation은 실제 부모→자식 연결이 확인돼야 소유권을 계승한다. 복사된 마커만으로 취소 권한을 주지 않는다. 원장과 실행 콜백을 대조할 수 없으면 `ownership=unknown`으로 처리하고 취소하지 않는다. 출처 자체도 확인되지 않았다면 `source=unknown`을 유지한다. reset·unload·재로드는 기존 런타임 연결을 무효화한다.

### 6.3 검색 정책

- `audit_only`: 진단에는 남지만 일반 RP 검색에서 제외한다.
- `context_only`: 현재 장면에는 쓸 수 있으나 장기 기억이 되지 않는다.
- `episodic`: 검증된 승격 조건을 만족한 사건이다.

생존 행동은 보통 `audit_only`지만 “오늘 뭐 했어?” 같은 직접 질문에는 `ReportableAuditSummary`로 짧게 제공할 수 있다. 장기 추억이 아니라 최근 자기 활동에 대한 사실 보고다.

### 6.4 승격과 참조 검증

AI가 기억을 승격하지 않는다. 관계 변화, 직접 반응, 완료된 양방향 상호작용 또는 중요 이벤트 같은 로컬 트리거가 자격을 정한다. 메인 모델은 자격이 있는 사실을 요약하고 의미를 제안할 수 있다.

모델 응답의 `ref`는 다음 검증을 통과해야 한다.

1. 이번 요청에 실제 포함된 기억인가.
2. 현재 인격이 소유하거나 관찰한 기억인가.
3. 같은 세이브 분기인가.
4. 만료·삭제·`audit_only` 상태가 아닌가.
5. 현재 대상과 시간에 모순되지 않는가.

`ref`는 모델이 실제로 기억을 읽었다는 증명은 아니며, 응답이 그 기억을 근거로 삼았다는 선언이다. 존재하지 않는 참조는 차단할 수 있지만 의미적 오용은 평가 데이터셋으로 측정한다.

참조는 하나의 정수 공간으로 섞지 않고 타입을 가진다.

```text
F:31   ObservedFact
C:12   PlayerClaim
U:91   Utterance
A:18   Interpretation/ANN
T:7    PrivateThought
```

`PlayerClaim`도 ref 검증 대상이지만 사실처럼 사용할 수는 없다. `C:12`를 근거로 쓰는 발화는 “네가 힘들었다고 했지”처럼 발언 사실이나 잠정 믿음으로 표현해야 하며, `ObservedFact` 단정으로 승격하지 않는다.

### 6.5 해석 버전

사실은 변경하지 않고 해석을 버전으로 쌓는다.

```text
ANN31.v1 hurt          SUPERSEDED
ANN31.v2 understanding ACTIVE, supersedes=v1
```

일반 검색은 최신 활성 해석을 사용하고 감정 변화 회고에서는 이전 버전도 조회한다.

## 7. PM-Wire와 컨텍스트 예산

### 7.1 처리 흐름

```text
Scene Atomizer
  현재 장면을 엔티티, 행동, 장소, 대상, 속성으로 분해
→ Candidate Index
  구조 태그가 겹치는 취향과 기억만 조회
→ Relevance Gate
  관찰, 신뢰도, 쿨다운, IC 관련성 검사
→ Cue Distiller
  여러 값을 한두 개의 반응 단서로 증류
→ Budget Packer
  선택된 모델의 토큰 예산 안에서 패킷 구성
```

### 7.2 원본과 통신 형식 분리

로컬 DB는 자세한 구조화 이벤트를 보존한다. 모델에는 공급자별로 검증된 축약 JSON 또는 PM-Wire 표현을 보낸다. 사람이 보기에 짧은 약어가 실제 토큰도 적다는 보장은 없으므로 JSON, 축약 JSON과 DSL을 실제 토크나이저와 정확도 테스트로 비교한다.

```text
PM1|scope=q7|scene=203|seq=918
ENT|s0|role=agent
ENT|s1|role=controlled_sim
N|s0|H3,E2,B0
R|s0>s1|F68,R42,dF+5
M|31|care.food|pol=EP|vis=DI|w=92
Q|actor=s0|NEXT
```

변화 이벤트는 `seq`로 보내되, 현재 로트, 참여자와 필수 욕구 같은 작은 절대 상태는 매 요청에 포함한다. 누락이나 장면 변경이 감지되면 전체 동기화한다.

### 7.3 취향의 선택적 활성화

장면이 검은 트위드 원피스라면 음식·장소 취향 20개를 읽지 않는다.

```text
appearance.clothing.color.black
appearance.clothing.material.tweed
appearance.clothing.style.old_money
```

정확히 겹치는 취향, 현재 대상과의 연관, 가장 강한 긍정과 부정만 남겨 하나의 단서로 만든다.

```text
CUE|target=outfit:s1|effect=mixed
neg=black:-.71|pos=tweed:+.82|expression=optional
```

선호 정보가 없다는 사실과 첫 경험은 다르다. 노출 기록에 따라 `UNKNOWN_NEW`, `UNKNOWN_FAMILIAR`, `UNKNOWN_UNOBSERVABLE`을 구분한다.

### 7.4 단기 작업 기억

현재 Scene과 장기 episodic 사이에 에이전트별 `RecentEventsBuffer`를 둔다. 기본 범위는 게임 시간 2~4시간을 시작값으로 하되 시간만으로 자르지 않고 중요도, 미해결 상태와 장면 전환을 함께 본다.

- 화재, 갈등과 약속: 장면이 바뀌어도 일정 기간 유지
- 평범한 이동·식사: 빠르게 감쇠하거나 로컬 요약
- 관찰하지 않은 사건: 해당 에이전트 버퍼에 넣지 않음
- 장기 기억 승격 전에도 현재 대화와 IC에는 사용 가능

버퍼는 항목·토큰 상한이 있는 링 구조이며 전체 원본 이벤트를 매 턴 다시 검색하지 않는다. 게임 재시작 시 `save_commit_id`와 일치하는 검증된 항목만 복원한다.

### 7.5 그룹 대화와 청중

단일 `target_sim_id`를 프로토콜 기본 단위로 삼지 않는다. 모든 대화와 사회적 IC는 배열을 사용한다.

```text
participants[]   현재 대화 그룹
addressees[]     발화가 직접 향한 대상
observers[]      실제로 들었거나 본 엔티티
focal_target     선택적인 중심 대상
audience_version 그룹 구성 버전
```

한 명 대상도 길이 1 배열로 표현한다. 그룹 구성원이 들어오거나 나가 `audience_version`이 바뀌면 늦은 모델 응답을 재검증한다. 둘만의 비공개 기억, Thought와 민감한 주제는 현재 청중이 허용 범위를 벗어나면 발화 후보에서 제외한다.

### 7.6 이벤트 수신과 Scene Atomizer

현재 설치본의 정적 분석에서 상호작용 완료·buff 시작/종료·mood 변경은 공통 EventManagerService를 거치고, 욕구 수치의 변화는 별도 statistic tracker watcher·threshold listener를 사용한다. 이 등록 경로는 빌드별 capability이며 게임 내 수신 빈도·누락·복귀 동작은 추가 시험한다.

- 공통 이벤트 수신기: interaction lifecycle, buff lifecycle, mood change를 필요한 종류만 구독한다.
- 욕구 수신기: 관리 중인 심의 기존 statistic instance만 관찰하고, watcher와 위험 임계치 listener를 조합한다. 관찰을 위해 없던 욕구·statistic을 추가하지 않는다.
- 수신 콜백은 짧은 관찰 데이터만 메모리 큐에 넣는다. DB·네트워크·모델 호출과 행동 주입을 콜백 안에서 실행하지 않는다.
- 다음 처리 구간에서 두 수신부의 데이터를 `save_scope`, session, scene epoch, bridge seq와 출처 채널을 가진 공통 이벤트로 정규화한다. 동반 앱으로는 게임 객체 참조를 넘기지 않는다.

한 interaction의 완료 신호가 여러 참여자에 대해 발행될 수 있으므로 실행 instance와 phase를 기준으로 사건을 중복 제거하고 참여자별 알림을 병합한다. 그 참여자 목록이 곧 모든 심의 실제 관찰·청취 증거라는 뜻은 아니다. `InteractionComplete`나 큐 퇴장은 목표 성공의 충분조건이 아니며 취소·reset·결과·`success_predicate`를 별도로 확인한다.

방향성 검증: 이벤트 콜백의 수신 `sim_info`를 Actor로 사용하지 않는다. 원 interaction의 actor, resolved target/addressees와 callback recipient, 실제 observer를 별도 필드로 보존한다. 케이트→에이든 포옹을 에이든 수신기에서 처리해도 방향은 뒤집히지 않는다. Actor/Target 불명·상충·role swap이면 `direction_unverified`로 격리하고 방향성 CareEpisode로 승격하지 않는다. 참여자별 중복 제거가 이 필드를 덮어쓰지 않아야 한다.

buff 변화와 mood 변화를 별개의 원인 사건으로 이중 계산하지 않고 상태를 합친다. 콜백 도착 순서만으로 인과를 부여하지 않는다. 자연 감소하는 욕구를 일반 watcher 하나로 전부 감지한다고 가정하지 않으며, 임계치 listener와 제한된 상태 재확인을 사용한다. watcher 안에서 값을 다시 읽어 재진입을 유발하지 않는다. 로드 중 알림은 기준 상태 동기화로 처리하고, 이전 세션에 이미 존재했던 buff를 새 사건으로 기억하지 않는다.

리스너 등록은 활성 scope마다 멱등적이어야 한다. 가족·zone 전환, 심 reset/de-instancing, 비활성화 때 등록 handle을 회수하고 새 인스턴스에서 재등록한다. 버퍼 넘침은 gap과 재동기화 필요를 기록한다. 모든 이벤트가 영구 저장됐다고 가정하지 않는다.

## 8. 모델 라우팅

| 계층 | 담당 | 호출 비용 |
|---|---|---|
| L0 로컬 규칙 | 생존, 큐 복구, ref 검증, 중요도와 승격 자격 | 없음 |
| L1 로컬 자동화 | 현재 IC 안의 단순 행동, 반복 가능한 안전 행동 | 없음 |
| L2 경량 모델 | 짧은 일상 대사와 낮은 위험 선택 | 낮음 |
| L3 메인 모델 | 관계 분기, 갈등·화해·약속, 의미 해석 | 높음 |

라우팅은 모델이 아니라 로컬 엔진이 `salience`, `risk`, `memory_dependency`, `novelty`, `uncertainty`, 비용 한도를 보고 결정한다. 메인 모델이 늦거나 실패했을 때 중요한 결정을 경량 모델로 몰래 낮추지 않는다.

경량 모델이 만든 대사는 `utterance`일 뿐 사실 기억이 아니다. 메인 모델도 사실을 창조하거나 승격할 수 없고, 검증된 사실에 방향성 감정과 의미를 주석으로 제안한다.

## 9. IC(Intent Capsule) 스케줄러

메인 모델은 매 행동을 고르기보다 일정 시간 유지되는 중기 의도를 만든다.

```text
IC|actor=s0|goal=comfort:s1|tone=gentle
mem=31,47|avoid=flirt,leave
active_budget=15gmin|stop=player_override,conflict,need4
```

AI 심마다 다음을 가진다.

- foreground IC 한 개
- 중단된 IC 스택
- 대기 IC 큐
- 여러 IC에 적용되는 background constraint

IC 상태:

```text
CREATED → QUEUED → ACTIVE
                    ├→ SUSPENDED → ACTIVE
                    ├→ COMPLETED
                    ├→ EXPIRED
                    ├→ FAILED
                    ├→ CANCELLED
                    └→ SUPERSEDED
```

상태 수를 늘리는 대신 완료·중단의 원인을 메타데이터로 기록한다.

```text
status=COMPLETED
resolved_by=plumbmate / player_direct / environment / third_party
resolution_event=F221

status=SUSPENDED
suspend_reason=player_override / rabbit_hole /
               inactive_household / offline / resource_locked
```

IC는 실행하려던 상호작용 이름이 아니라 `success_predicate`로 완료를 판정한다. “케이트 위로하기”가 목표라면 플레이어가 먼저 포옹하거나 환경 변화로 긴장이 해소되어도 완료 또는 부분 진행될 수 있다. `resolved_by=player_direct`이면 “내가 하려던 일을 상대가 먼저 해주었다”는 관계 해석 후보를 만들되, 실제 대상·결과와 감정 변화를 검증한다.

플레이어 개입은 현재 IC를 기본적으로 폐기하지 않고 중단한다. 플레이어 행동 후 대상, 로트, 감정, 기한과 성공 조건을 재검사하여 재개, 완료 또는 만료한다.

TTL은 활성 실행 시간과 절대 기한으로 나눈다. 만료되었다고 자동으로 L3를 다시 호출하지 않으며, 중요 미완료 사건만 재계획한다.

### 9.1 재시작 복원

- 영구: 사실, 기억, 기념일, 인격
- 재개 가능: IC 목표, 진행 단계, 남은 시간
- 일회성: 통신 별칭, API 요청, 오브젝트 잠금, 게임 행동 핸들

IC 전이마다 작은 체크포인트를 저장한다. 재시작 때 실제 행동 큐를 재생하지 않고 IC 목표만 `SUSPENDED_RESTORE`로 복원한 뒤 현재 세계에서 다시 계획한다. 체크포인트는 `save_commit_id`와 맞아야 하며, 이전 세이브를 불러왔을 때 미래 IC나 기억이 섞여서는 안 된다.

저장 경계는 `SAVE_BEGIN → SAVE_COMMITTED/SAVE_ABORTED`의 두 단계 프로토콜로 만든다.

```text
SAVE_BEGIN(attempt_id, watermark_seq)
  → PlumbMate 행동 주입 일시정지
  → watermark 시점 IC 체크포인트를 pending으로 고정
  → 이후 사건은 다음 working epoch에 버퍼

SAVE_COMMITTED(attempt_id, slot, generation)
  → pending 체크포인트를 save_commit_id에 원자적으로 연결

SAVE_ABORTED(attempt_id)
  → pending 경계를 폐기하고 working epoch 계속
```

게임 스레드는 동반 앱의 확인을 기다리지 않는다. 게임 측에 신뢰할 수 있는 저장 시작·완료 훅이 있는지는 RFC-008 capability 테스트로 검증한다. 완료 신호를 얻지 못하는 빌드에서는 정확한 IC 복원을 지원한다고 주장하지 않고, 재로드 시 IC를 `NEEDS_REPLAN` 또는 만료 처리하며 미확정 타임라인을 격리한다.

슬롯 ID와 게임 시각만으로 저장 커밋을 식별하지 않는다. 일시정지 중 연속 저장, 같은 시각의 분기, 이전 백업 복구를 구별할 수 없기 때문이다. SQLite는 동반 앱에 유지하고 실제 불러온 세이브와 연결이 확인된 commit/watermark까지만 조회한다. 별도 SQLite 쓰기와 게임 저장이 같은 시각에 발생한다는 사실만으로 세이브 파일 손상을 추정하지 않는다. 해결할 핵심은 파일 동시쓰기보다 두 타임라인의 일관성이다.

자동저장에서도 실제 저장 시도별 `attempt_id`와 불변 IC 체크포인트/watermark를 보존한다. 경과시간 throttle로 중간 커밋을 생략하지 않는다. 동일 attempt 신호 재전달만 멱등적으로 제거하고, 서로 다른 시도는 같은 ticks여도 별개다. 무거운 요약·압축·인덱스 보수만 묶어 지연하며, 저장 성공 확인과 실패한 시도의 pending 정리를 혼동하지 않는다. 버퍼 초과 시 gap/미확정 상태로 남기고 임의의 최신 커밋에 합치지 않는다.

### 9.2 최상위 행동 중재자

`ToneShadow`, `RepairPattern`, `DYAD_PREF`, 개인 루틴과 `ProactivePlanner`는 게임 큐에 직접 행동을 넣지 않는다. 모두 동일한 `BehaviorProposal`만 만든다.

```text
BehaviorProposal
  actor, goal, candidate_action
  priority_band, utility
  preconditions, conflicts, permissions
  cost, expires_at, evidence_refs
  explanation_codes
```

`PolicyKernel`이 플레이어 우선권, 생존, 동의, 비용, 오브젝트 가용성과 타 모드 잠금을 먼저 하드 게이트로 검사한다. 그 뒤 `ExecutiveArbiter`가 통과한 후보 중 하나를 선택하고, 오직 `ActionDispatcher` 하나만 실제 큐에 쓴다.

다중 AI 심에서는 단일 거대 중재자도, 완전히 독립된 중재자도 사용하지 않는다.

- `AgentExecutive`: AI 심마다 하나. 자신의 IC, 인격, ToneShadow와 후보 순위를 관리한다.
- `WorldCoordinator`: 세이브 세션마다 하나. 동일 대상, 공용 오브젝트, 다자 상호작용과 비용 한도를 조정한다.
- `ActionDispatcher`: 세션마다 하나. 최종 승인된 행동만 게임 큐에 넣는다.

그룹 IC는 참여자와 자원에 TTL이 있는 임대를 잡고, 모든 자원 키를 안정된 순서로 획득하여 교착을 피한다. 한 에이전트의 인격 판단은 다른 에이전트가 덮어쓰지 않지만, 같은 심이나 오브젝트를 동시에 차지하는 행동은 WorldCoordinator가 직렬화한다.

각 하위 시스템의 역할은 명확히 제한한다.

- `ToneShadow`: 말투와 후보 점수만 수정, 행동 명령 금지
- 관계 패턴: 화해·거리두기 같은 후보 제안
- 취향 갈등: 관련 맥락과 제약 제공
- 주도적 계획: 목표와 fallback 후보 제공
- IC: 현재 중기 의도와 성공·중단 조건 제공

동일 우선순위에서는 현재 IC 일관성, 성공 가능성, 낮은 침해성, 낮은 비용, 최근 반복 회피와 결정적 tie-break 순서로 고른다. 안전과 플레이어 권한은 숫자 점수로 역전할 수 없다.

## 10. 자율행동과 연결 복구

행동 우선순위:

```text
player_direct
> survival_guard
> urgent_relationship_event
> active_plumbmate_IC
> lightweight_routine
> native_autonomy
```

동반 앱 연결 상태:

```text
DISABLED → CONNECTING → ONLINE
              ↓          ↓
            BACKOFF ← DEGRADED
```

게임 틱은 통신 응답을 기다리지 않는다. 초기 제품의 게임 내부에는 백그라운드 게임/통신 리스너를 두지 않는다. 검증된 게임 틱 콜백에서 비차단 loopback I/O를 짧게 처리하는 transport를 후보로 삼고, EA C API와 모든 Sim/zone/queue 조회·행동은 게임 스레드에서만 실행한다. 메시지 수·바이트·처리시간 상한, 부분 송수신 버퍼, `EWOULDBLOCK` 즉시 반환, 연결 유실 시 양보가 필요하다. connect/accept/recv/send뿐 아니라 DNS·파일·로그 I/O의 숨은 대기도 점검한다. API·SQLite·무거운 JSON 처리·재시도 작업자는 외부 동반 프로세스에만 둔다. 이는 PM-Wire 의미 스키마 변경이 아니라 transport/runtime 제약이다. 현재 수동 파일 브리지 MVP가 이미 이 후보 transport를 구현했다는 뜻은 아니다.

[Lot 51 제작자 문서](https://github.com/lot51/core-library#gameupdate-)는 tick 기반 소켓 루프와 별도 스레드의 EA C 함수 호출에 따른 즉시 크래시 가능성을 경고한다. 이를 모든 Python 스레드가 반드시 크래시 난다는 실측 결과로 확대하지 않는다. 실패를 확인하려고 실제 게임에서 위험한 호출을 재현하지 않는다.

연결 유실 시:

- 새 PlumbMate 행동 주입 중단
- 진행 중인 안전한 행동은 완료 가능
- 미실행 모델 응답과 오래된 요청 폐기
- IC는 `SUSPENDED_OFFLINE`
- 기본 자율행동 복귀
- 알림과 오류 로그는 반복 제한

재연결 시 프로토콜, 게임 빌드, 세션, `save_scope`, 장면과 최신 `seq`를 교환하고 필요하면 전체 상태를 다시 보낸다.

### 10.1 큐 충돌과 재시도

동일 행동·대상·실패 원인의 연속 실패에는 제한된 지수 백오프와 jitter를 적용한다. 일시적 점유·경로 실패만 재시도하고, 오브젝트 삭제·권한 부족 같은 영구 실패는 즉시 다른 후보 또는 IC 재계획으로 보낸다. 타 모드가 심을 잠근 상태는 무작정 재시도하지 않고 상태 변화 신호까지 중단한다.

IC마다 재시도 예산과 최대 대기 시간을 두며, 예산 소진 시 `FAILED` 이유를 DecisionTrace에 기록한다. 백오프는 게임 속도 변화로 폭주하지 않도록 단조 실제 시간과 상태 변화 신호를 함께 사용한다.

초기 실행 검증은 심별 PlumbMate 미실행 행동 상한을 1개로 두고 저위험 allow-list에서 시작한다. 게임 큐 용량을 숫자 6으로 고정하지 않고 현재 capability와 기존 점유 상태를 확인한다. 통신·관찰 주기는 틱당 처리시간, 이벤트 수, 누적 대기량과 실제 지연으로 결정한다. 게임 시간 단위 알람 진단 기준을 현실 시간 3초 폴링 금지로 바꾸지 않는다.

### 10.2 심 리셋과 큐 수명주기

PlumbMate는 게임 내부 큐 객체를 생성·교체·강제 초기화하지 않는다. 큐 접근 중 예외가 나거나 게임의 Sim reset 신호를 받으면 다음 순서로 복구한다.

1. 해당 심의 PlumbMate 행동 임대와 아직 주입되지 않은 제안을 무효화한다.
2. 이전 `Sim` 객체와 큐 핸들을 폐기하고 `SimInfo` 식별자만 유지한다.
3. 다음 안정된 게임 틱에서 현재 zone의 `Sim` 인스턴스와 큐 가용성을 다시 확인한다.
4. 실행하던 게임 행동을 재생하지 않고 IC 목표를 `NEEDS_REPLAN`으로 보낸다.
5. 반복 리셋은 지수 백오프와 회로 차단기로 막고 기본 게임 자율행동을 유지한다.

T-포즈, LastException과 Sim reset의 상관관계는 재현 테스트 대상이지 단일 원인으로 가정하지 않는다. 특정 예외가 큐 객체를 `None`으로 만든다는 증거가 없는 빌드에서 PlumbMate가 큐를 직접 복원하려 해서는 안 된다.

## 11. 관계와 취향 그래프

### 11.1 방향성 취향 지식

```text
PREF(owner=s0, spicy_food=-.71)
KNOWS(knower=s1, owner=s0, pref=spicy_food, acknowledged)
```

행동이 선호와 어긋났다는 사실과 “알면서 무시했다”는 의도 추정은 다르다.

```text
PreferenceMismatchIncident
→ IntentAttribution
→ EmotionalImpact
→ RepairEvidence
→ Resolution
→ RelationshipPattern
```

플레이어의 “미안, 깜빡했어”는 검증되지 않은 해명 주장으로 저장된다. 에이전트는 신뢰와 반복 이력에 따라 잠정적으로 믿거나 의심할 수 있으며, 진실 검증과 용서는 별개다.

검증 불가능한 의문에는 `EpistemicTTL`을 둔다. TTL 후 발언은 보존하지만 활성 의심은 `unresolved_closed`로 전환하여 매 턴 회상하지 않는다. 모순이나 반복이 생기면 다시 열 수 있다.

재활성화는 `PatternSketch`의 증거 점수와 연결하고 hysteresis를 둔다.

- 직접 검증된 강한 모순: 즉시 재활성화
- 같은 주제의 명확한 고중요도 위반: 즉시 또는 높은 가중치
- 약하고 애매한 사건: 누적 점수가 `reopen_threshold`를 넘을 때만 재활성화
- 닫힘 기준은 재개 기준보다 낮게 두어 한 사건마다 열리고 닫히는 진동을 방지

TTL 직후의 평범한 사건 한 번만으로 오래된 의심을 자동 부활시키지 않는다.

### 11.2 갈등의 세 층

- `acute_tension`: 현재의 서운함, 비교적 빠른 감쇠
- `pattern_weight`: 반복 사건의 역사, 느린 변화
- `relational_trust`: 존중받을 것이라는 방향성 기대

한 번의 배려는 현재 긴장을 줄일 수 있지만 반복 패턴을 삭제하지 않는다. 사과와 시간에 걸친 일관된 행동이 관계 신뢰를 회복한다.

### 11.3 취향 충돌과 타협

각 심의 취향과 서로에 대한 지식을 독립적으로 저장한다. `DYAD_PREF`는 누가 옳은지 정하지 않고 공유 선택에서 발생한 갈등과 타협안을 기록한다.

```text
DYAD_PREF|participants=s0,s1|topic=spicy_food
state=unresolved|activation=dormant
solution=separate_sauce|solution_status=working
```

관련 음식이나 공동 선택이 다시 등장하고 쿨다운, 양보 불균형 또는 타협 실패 조건이 맞을 때만 활성화한다. 이미 작동 중인 타협은 L1이 수행하고 매번 L3로 재협상하지 않는다.

### 11.4 관계 패턴 분석 비용

사건마다 로컬 `PatternSketch`의 카운터만 갱신한다. L3 `PatternConsolidationJob`은 패턴 임계점, 관계 전환점, 회고 요청 또는 이미 예정된 L3 호출에 묶을 수 있을 때만 실행한다. 시스템 정리는 IC가 아니며, 심이 실제로 생각하는 장면만 `reflection IC`다.

### 11.5 제3자가 있는 관계

에이전트의 다른 심에 대한 태도는 두 사람의 관계값 하나가 아니라 관찰된 삼각 맥락에서 계산한다.

```text
TRIAD|observer=s0|partner=s1|other=s2
observed_closeness=.63|confidence=.58
interpretation=friendship|jealousy=optional
```

AI 심은 소개, 직접 목격 또는 검증된 게임 관계가 없는 제3자의 의미를 안다고 가정하지 않는다. 질투는 PersonaTemplate에서 명시적으로 허용된 경우에만 방향성 해석과 ToneShadow에 영향을 주며, 소유·통제 행동이나 관계 벌점을 자동 생성하지 않는다. 제3자에게 친절, 조심스러움 또는 거리두기 후보를 제안하더라도 최종 행동은 ExecutiveArbiter가 정한다.

### 11.6 약속과 신뢰성

“오늘 일찍 돌아올게”는 기한과 대상이 있는 `Commitment` 후보로 파싱한다. 실제로 늦었다는 것은 약속 미이행 사실이지만, 처음부터 거짓말하려 했다는 뜻은 아니다.

```text
Commitment → Fulfilled / Unfulfilled / Excused / Cancelled / Unknown
```

신뢰는 전역 `player_reliability` 숫자 하나가 아니라 약속 이행, 시간 약속, 사실 주장과 감정적 개방성처럼 분야별 방향성 믿음으로 둔다. 설명과 사과는 주장으로 연결되며 신뢰와 과거 패턴에 따라 해석한다. 게임 종료·일시정지와 감정 비공개는 약속 위반이나 거짓말로 계산하지 않는다.

## 12. 살아온 경험 기능

### 12.1 이정표와 기념일

첫 만남, 첫 키스, 첫 공동 요리, 첫 여행, 첫 갈등과 첫 화해를 검증된 완료 이벤트에서 생성한다. 기존 세이브에서 과거를 확인할 수 없으면 `first_observed_since_plumbmate`로 표시하고 인생 최초라고 단정하지 않는다.

기념일은 `PENDING → DELIVERED_UNCONFIRMED → ACKNOWLEDGED/MISSED/DECLINED/EXPIRED`로 관리한다. 미확인 기념일은 다음 호환 가능한 사회적 IC에 한정된 횟수로만 다시 붙인다.

### 12.2 주도적 준비

생일 케이크 같은 행동은 “케이크 굽기”가 아니라 “생일을 축하하기”라는 목표 IC로 만든다. 사용 가능한 오브젝트, 기술, 자금, 시간과 사용자가 허용한 소비·외출 권한을 확인한다.

```text
케이크 굽기
→ 불가능하면 좋아하는 음식 준비
→ 불가능하면 선물 또는 축하 상호작용
→ 최소한 먼저 생일 언급
```

준비는 비공개 생각으로 숨길 수 있으며, 실패해도 무한 재시도하지 않는다.

### 12.3 화해 언어

갈등 뒤 일정 구간에서 대상이 있는 배려 행동, 긴장 감소와 완료된 상호작용을 관찰한다. 반복된 방향성 행동에서 `silent_proximity`, `care_through_food` 같은 화해 스타일을 제안하되 강제 스크립트로 만들지 않는다.

### 12.4 말버릇

플레이어가 직접 입력한 문구만 로컬에서 정규화·빈도·맥락·최근성으로 추적한다. 흔한 짧은 추임새는 제외하고 충분한 근거와 쿨다운이 있을 때만 다정함, 걱정 또는 갈등 톤으로 알아챈다. 원문은 로컬 보관, 확인·삭제·비활성화를 지원한다.

### 12.5 함께한 시간과 개인 루틴

일시정지를 제외한 게임 시간으로 같은 로트, 같은 방, 직접 상호작용과 시간대 통계를 로컬 집계한다. 단순 동시 존재와 함께한 활동을 구분한다.

개인 루틴은 상대가 없을 때 반복된 자율 선택에서만 학습한다. `player_direct`로 시킨 행동은 자기 습관이 아니다. 복귀 회고는 실제 이벤트가 있을 때만 생성하며 게임이 꺼진 시간의 활동을 지어내지 않는다.

### 12.6 받은 돌봄과 먼저 해준 일

플레이어가 AI 심에게 한 행동도 동일한 사건·관찰·해석 파이프라인으로 처리한다. “플레이어가 클릭했다”를 곧바로 애정으로 계산하지 않고 대상, 필요, 타이밍, 완료 결과와 AI 심의 취향을 함께 본다.

```text
CARE_EPISODE|giver=s1|receiver=s0
action=bring_food|source=player_direct
need_match=hunger|completed=true
perceived_intent=care|confidence=.74
```

다음처럼 방향성을 가진 목록을 유지한다.

- 상대가 처음 먼저 다가온 사건
- 아플 때 돌봐준 사건
- 내 취향을 기억하고 선택한 사건
- 실패 후 다시 시도한 사건
- 내 경계를 존중한 사건
- 함께 배운 것과 내가 상대 덕분에 바뀐 것

이는 애착을 자동 증가시키는 점수표가 아니라 방향성 감정과 성장 해석의 근거다. AI 심이 원치 않은 도움이나 취향에 맞지 않는 선물을 받았다면 고마움과 불편함이 함께 존재할 수 있다.

### 12.7 혼자 지낸 시간의 세계 흔적

AI 심의 부재 중 행동을 모두 복귀 대사로 보고하지 않는다. 실제 게임 세계에 남은 결과 일부를 `WorldTrace`로 연결해 플레이어가 스스로 발견할 수 있게 한다.

```text
WorldTrace
  cooked_food, unfinished_project, moved_object,
  inventory_change, skill_progress
```

초기 버전은 게임에서 실제로 확인하고 유지할 수 있는 흔적만 사용한다. 책의 접힌 페이지처럼 기본 게임 상태가 제공하지 않는 세부사항을 지어내지 않는다. 커스텀 소품과 상태는 별도 capability가 있을 때만 활성화한다.

WorldTrace는 사건이 관찰된 순간 게임 스레드에서 작은 메모리 이벤트를 만들고, 동반 앱 작업자가 SQLite 원장에 비동기 기록한다. 게임 콜백이 DB 커밋을 기다리는 write-through는 사용하지 않는다. 저장 확인 전 유실 가능성과 버퍼 넘침은 gap으로 표시하며, 기록은 save scope·working epoch·확정 watermark에 귀속한다. 세이브 파일을 나중에 역스캔하여 과거 사건을 재구성하거나, 현재 남아 있는 오브젝트만 보고 누가 무엇을 했는지 추정하지 않는다. 현재 세계 재확인과 저장 커밋 식별은 허용되는 별도 작업이다.

`moved_object`와 `cooked_food` 같은 오브젝트 흔적은 관찰 당시의 object ID만 저장하지 않고 definition ID, zone/lot, 소유자·제작자, 관찰 시각과 제한된 상태 지문을 함께 보존한다. DB 기록은 “그 사건을 당시 관찰했다”는 역사적 근거이고, 오브젝트가 지금도 존재한다는 보증은 아니다. 복귀·불러오기 후 현재 세계에서 대조되지 않는 object ID는 `historical_only`로 낮추고 현재형 대사나 상호작용 대상으로 사용하지 않는다.

RFC-008의 `WorldTraceCapability`는 각 흔적에 다음을 기록하고 실제 저장·불러오기 테스트를 통과해야 한다.

```text
detectable
persists_save
stable_identity
required_pack_or_mod
restore_validation
lifetime = durable / session / derived
```

감정 buff와 `mood_aftereffect`는 WorldTrace에서 제외한다. 현재 세션에서 직접 관찰되는 affect·buff는 Scene/RecentEvents의 단기 상태로만 사용할 수 있으며, 가족 전환 catch-up이나 세이브 역추론의 근거가 아니다. 불러오기 후 실제 오브젝트·상태를 다시 확인하지 못한 흔적은 현재형 대사 근거로 사용할 수 없다.

관련 흔적이 있을 때 “그동안 뭐 했어?” 같은 상황형 대화 주제를 제공할 수 있지만 팝업으로 정답을 공개하지 않는다. 플레이어가 묻지 않아도 벌점은 없고, 일부 흔적은 설명되지 않은 채 지나갈 수 있다.

### 12.8 함께 배운 것

`LearningEpisode`는 같은 공간에 있었다는 이유만으로 만들지 않는다. 직접 가르침, 공동 수행, 직후의 기술 증가 또는 반복된 행동 변화처럼 인과 근거가 있어야 한다.

```text
LEARN|learner=s0|teacher=s1
domain=cooking.technique
evidence=mentor_interaction,skill_delta
confidence=.91
```

학습 결과는 세 층으로 나눈다.

- 게임 지식·기술: 검증된 스킬과 상호작용
- 절차·습관: 함께 익힌 요리법, 화해 방식, 생활 루틴
- 의미: “이것을 상대에게 배웠다”는 방향성 ANN

목표는 AI 심을 무능하게 만들어 플레이어에게 의존시키는 것이 아니다. 혼자 살아갈 수 있지만 상대와 함께한 경험 때문에 이전과 다른 사람이 되었음을 보여준다.

### 12.9 상호성 루프

```text
플레이어의 행동
→ AI 심이 관찰하고 의미를 형성
→ 말투·선택·성장이 변함
→ 게임 세계에 새로운 행동과 흔적을 남김
→ 플레이어가 발견하거나 질문
→ 둘만의 습관과 기억으로 이어짐
```

에이전트만 상대를 학습하는 단방향 서비스가 아니라, 플레이어의 선택도 에이전트의 인격과 세계에 지속적인 변화를 남겨야 한다.

### 12.10 세대와 Lore

심의 사망이나 세대 전환은 즉시 모든 원본을 LLM 텍스트 하나로 바꾸는 삭제 트리거가 아니다.

- 고가치 사실, 이정표와 살아 있는 심들의 근거 기억은 보존
- routine `audit_only` 원본은 결정적 집계 후 보존 정책에 따라 pruning 가능
- 사망한 AgentInstance는 읽기 전용 archive로 전환
- `Lore`는 source fact ID와 provenance를 가진 버전형 요약
- 후손은 직접 들었거나 전달받은 Lore만 알 수 있으며 사망자의 비공개 생각을 자동 상속하지 않음

L3 증류는 세대 전환마다 즉시 호출하지 않고 다음 허용된 consolidation 예산에 배치한다. 원본을 물리 삭제하기 전에는 구조화된 최소 증거와 사용자 설정에 따른 보존·내보내기 경로가 있어야 한다.

## 13. 지각, 인간 같은 실수와 학습

### 13.1 감정을 읽는 두 단계

게임이 가진 실제 감정 상태와 AI 심이 알아챈 상태를 분리한다.

```text
ActualAffect        게임의 버프·감정 원본
PerceivedAffect     관찰 가능 단서로 에이전트가 추정한 상태
```

인지 가능성은 같은 공간, 관찰 시간, 감정의 외현성, 에이전트의 민감도와 관계 친밀도를 사용한다. 친밀도는 감정의 존재 자체를 바꾸기보다 확신과 표현 권한에 영향을 준다.

- 낮은 친밀도: 눈치채도 말하지 않거나 일반적으로 배려
- 중간 친밀도: “괜찮아?”처럼 조심스럽게 확인
- 높은 친밀도: 검증된 반복 패턴이 있으면 더 구체적으로 언급

`PerceivedAffect`는 추정이며 실제 감정 원본으로 승격되지 않는다.

### 13.2 Calibrated Fallibility

의도적 부정확성은 무작위 거짓 기억을 주입하지 않는다. 다음 원인에서만 자연스럽게 발생시킨다.

- 직접 관찰하지 못함
- 비슷한 사건 간 출처 혼동 가능성
- 오래되고 중요도가 낮은 기억
- 낮은 학습 신뢰도
- 피로, 긴장 또는 주의 분산
- 친밀도가 낮아 상대 패턴을 잘 모름

표현 단계는 신뢰도에 따라 달라진다.

```text
높음    단정적으로 회상
중간    “내 기억엔…”처럼 유보
낮음    물어보거나 언급하지 않음
```

안전, 동의, 경계, 플레이어가 고정한 canon, API·비용 설정과 생존 판단에는 인간 같은 실수를 적용하지 않는다. 생일 케이크를 엉뚱한 날 굽는 식의 행동 실패는 기본값이 아니며, 날짜가 불확실하면 확인하거나 행동을 보류한다.

실수는 표현 또는 선택 단계에서만 발생하고 사실 DB를 변경하지 않는다. 플레이어의 정정은 새 사건으로 연결되어 이후 믿음을 갱신한다. 동일 실수 반복을 막는 쿨다운과 세션별 오류 예산, 사용자 선택형 `humanlike_fallibility` 강도를 둔다.

에이전트가 회상할 때 사용하는 주관적 기억은 사실 원본의 모든 필드를 완벽하게 복사하지 않는다. 사건 당시 주의와 관찰 범위에 따라 장소, 시간, 순서 같은 필드별 `encoding_confidence`를 둔다. 사실 DB는 집에서 일어난 일을 계속 집으로 보존하지만, 장소 기억의 확신이 낮은 에이전트는 카페와 혼동하거나 “내 기억엔 카페였던 것 같은데”라고 말할 수 있다.

이 오류는 비슷한 후보가 있고 낮은 필드 확신이 있을 때만 허용한다. 플레이어가 “그때 카페 아니었어”라고 정정하면 `CorrectionEpisode`를 만들고 주관적 믿음을 갱신한다. 안전, 경계, 고정 canon과 중요한 기념일에는 이 메커니즘을 적용하지 않는다.

### 13.3 분야별 학습 속도

모든 것을 같은 속도로 배우지 않는다.

| 분야 | 기본 속도 | 근거 |
|---|---|---|
| 명시적으로 말한 음식·활동 취향 | 빠름 | 플레이어 주장, 반복 선택 |
| 장소·시간대 습관 | 중간 | 여러 독립 세션의 관찰 |
| 화해 방식·감정 반응 | 느림 | 반복 에피소드와 결과 |
| 숨은 의도·진짜 속마음 | 매우 느림 | 영구 사실이 아닌 방향성 믿음 |

학습률은 PersonaTemplate의 특성으로 조절할 수 있지만, 새 근거 없이 확신이 증가하지 않는다. 친밀도는 더 많은 사실을 마법처럼 알려주는 값이 아니라 관찰 해석의 정확도, 질문 방식과 표현 깊이를 조정한다.

### 13.4 실망과 실제 부재

게임 안에서 확인된 약속 위반은 실망과 비공개 생각의 근거가 될 수 있다. 그러나 게임 종료나 일시정지 중에는 세계 시간이 흐르지 않으므로 현실에서 며칠 접속하지 않았다는 이유만으로 관계 수치를 깎거나 플레이어에게 죄책감을 주지 않는다.

현실 시간 기반 재회 반응은 명시적 옵트인 기능으로만 제공하며 기본값은 따뜻한 복귀 인사다. 실제 부재를 이용한 비난·벌점·압박은 금지한다.

### 13.5 성장 아크

성장은 모델이 시스템 프롬프트를 마음대로 다시 쓰는 방식이 아니다. `AgentInstance`가 다음 제한된 축의 변화량을 갖는다.

```text
expressiveness
initiative
trust
vulnerability
conflict_openness
```

변화는 함께한 시간 자체보다 반복된 성공적 상호작용, 약속 이행, 화해, 중요한 이정표에서 발생한다. 기본 인격의 허용 범위를 벗어나지 않으며 사용자는 성장 잠금, 초기화와 내보내기를 할 수 있다.

### 13.6 불완전한 파트너와 인식의 거리

제품 목표는 플레이어에게 완벽히 맞춰주는 하인이 아니다. 에이전트는 자기 취향, 루틴, 친구, 목표, 경계와 제한된 주의력을 가지며 상대를 완전히 이해하지 못할 수 있다.

```text
WorldFact / PlayerClaim
→ ObservableSignal
→ AgentBelief(confidence, evidence)
→ Misunderstanding
→ Clarification
→ BeliefRevision
```

플레이어가 “괜찮아”라고 말했고 명확한 반대 단서가 없다면 에이전트가 문자 그대로 믿는 것은 오류가 아니라 합리적 관계 행동이다. 친밀도가 높거나 모순되는 관찰이 반복될 때만 조심스럽게 다시 묻는다. 나중에 플레이어가 속마음을 공유하면 과거 믿음과의 차이가 `MisunderstandingEpisode`가 되고, 해명과 수정 과정이 관계 기억이 된다.

시스템은 플레이어가 말하지 않은 실제 속마음을 안다고 가정하지 않는다. 비공개는 중립이며, 명시적 거짓 주장과 구분한다. 솔직한 공유는 에이전트가 더 많은 근거를 갖게 하므로 이해를 빠르게 만들 수 있지만, 침묵이나 사생활을 관계 실패로 벌주지 않는다.

완벽함을 줄이는 수단은 랜덤 오답보다 다음을 우선한다.

- 모든 신호를 보거나 기억하지 못하는 제한된 주의
- 문자 그대로 믿거나 과하게 걱정하는 인격별 해석 성향
- 서로 충돌하는 독립 취향과 개인 일정
- 거부, 망설임과 타협이 필요한 자기 경계
- 낮은 확신에서는 질문하고, 정정받은 뒤 배우는 흐름
- 친밀도에 따라 늘어나는 대화 주제의 깊이

### 13.7 플레이어가 관계를 소중히 여기게 되는 조건

시스템은 플레이어의 감정을 직접 만들거나 “잃을까 봐” 접속하게 강요할 수 없다. 대신 관계가 다음 이유로 대체 불가능하게 느껴질 조건을 만든다.

- 요청하지 않아도 자기 목표와 일상을 이어가는 독립성
- 플레이어에게 받은 돌봄을 정확히 기억하고 다른 방식으로 되돌려주는 상호성
- 언제나 맞춰주지 않고 거부·오해·협상이 가능한 자기 경계
- 함께 만든 음식, 장소, 농담, 화해 방식과 기념일 같은 공유 문화
- 플레이어와의 사건으로 실제 표현과 선택이 달라지는 제한된 성장
- 플레이어가 궁금해하고 질문해야만 알 수 있는 삶의 여백
- 갈등이 생겨도 정정·사과·수정으로 회복 가능한 구조

현실 시간 미접속 벌점, 연속 접속 보상, 죄책감을 유발하는 대사, 관계 기억을 잃게 하겠다는 위협과 데이터 이동 제한은 사용하지 않는다. 관계 데이터는 사용자가 내보내고 삭제할 수 있어야 하며, 애착은 잠금 효과가 아니라 함께 쌓인 구체적 역사에서 나와야 한다.

## 14. 비공개 생각과 ToneShadow

비공개 생각은 사실이 아니라 검증된 사실에 대한 희망, 걱정, 가설과 망설임이다.

```text
THOUGHT|owner=s0|about=s1|kind=hope|refs=F31
disclosure=latent|activation=dormant
contexts=anniversary,alone_with:s1
```

공개 수명주기:

```text
latent → hinted → revealed → resolved
```

활성 상태는 별도다.

```text
dormant → active → cooldown
```

경량 모델에는 원문을 보내지 않고 현재 맥락과 맞는 영향만 `ToneShadow`로 보낸다.

```text
TONE_SHADOW|toward=s1
warm:+.2|hesitant:+.3|open:-.1|disclosure=0
```

ToneShadow는 기본 성격에 더하는 일시적 변화이며 누적 저장하지 않는다. 현재 맥락에 가장 가까운 생각 하나와 필요할 때만 보조 생각 하나를 사용한다.

합성 알고리즘은 전역에서 결정적이며 PersonaTemplate은 허용된 매개변수만 제공한다.

```text
activation = context_match × strength × recency × confidence × persona_sensitivity
```

먼저 활성도가 가장 높은 생각과 의미 있는 보조 생각을 고른다. 두 생각의 채널별 delta를 가중 합산한 뒤 포화 함수와 범위 제한을 적용한다. 반대 영향이 모두 임계치를 넘으면 서로 지워버리지 않고 `ambivalent` 플래그를 남긴다. `hope`와 `worry`는 단일 양·음 값이 아니라 warmth, initiative, arousal, hesitation처럼 서로 다른 채널에 영향을 준다. PersonaTemplate은 억제, 반추, 표현 민감도와 채널 가중치를 조절할 수 있지만 합성 코드를 바꾸지는 않는다.

## 15. 보안, 개인정보, 비용과 접근성

### 15.1 키와 전송

- 공개 앱은 macOS Keychain, Windows Credential Manager, Linux Secret Service를 사용한다.
- 평문 설정 파일 폴백은 제공하지 않는다.
- 키는 Mods, SQLite 기억 DB, 로그와 오류 보고서에 들어가지 않는다.
- 첫 클라우드 호출 전에 공급자, 모델과 전송 필드를 보여준다.
- 정확한 요청 미리보기, 로컬 기록 삭제와 클라우드 비활성화를 제공한다.
- 공급자 정책과 보관 조건을 UI에 명시하고 안전 필터를 우회하지 않는다.
- `PhraseFingerprint`와 플레이어 원문 저장은 별도의 명시적 동의를 받으며 기본값은 비활성이다.
- 발화 기록 화면, 항목별 삭제, 보존 기간, 전체 삭제와 익명 진단 내보내기를 제공한다.
- 기기 공유를 고려해 민감 원문은 일반 진단 로그에 복사하지 않고 가능한 경우 사용자별 로컬 저장소를 잠근다. 공개 전 별도 개인정보·법률 검토를 거친다.

### 15.2 비용

- 세션·일일 한도
- 입력·출력 토큰과 예상 비용 표시
- 비용 초과 시 중요한 L3 장면을 자동으로 저가 모델에 낮추지 않고 보류 또는 사용자 정책에 따른다.
- 고정 프롬프트 캐시, 짧은 구조 출력, 이벤트 배치와 IC 재사용을 사용한다.

### 15.3 보이지 않지만 읽을 수 있는 감정

기본 몰입 모드에는 감정 온도계와 관계 수치를 상시 표시하지 않는다. 감정은 가능한 한 게임 안의 행동으로 표현한다.

- 망설일 때 상호작용 전 짧은 지연 또는 낮은 강도의 접근 선택
- 기대할 때 상대 가까이에 머무르거나 관련 준비 행동 증가
- 서운할 때 노골적인 벌점 대신 말투, 거리와 주제 선택 변화
- 친밀도가 깊어질수록 표면 대화에서 꿈, 두려움과 약속 같은 주제 해금
- 비공개 생각은 설명문 대신 여러 작은 행동으로 예고

새 애니메이션이 없어도 초기 버전은 네이티브 시선, 위치, 대기, 접근과 상호작용 선택으로 표현한다. 기술적으로 가능한 표현만 capability로 등록한다.

접근성과 제작자 진단을 위해 UI는 세 모드를 제공한다.

- `Immersive`: 상시 상태 UI 없음, 행동과 대사로만 표현
- `Accessible Cues`: 색 + 서로 다른 아이콘 + 텍스트, 고대비·글자 크기·움직임 감소 제공
- `Creator/Debug`: 내부 수치, 선택 근거와 상태 머신 표시

비공개 생각의 원문은 어느 일반 모드에서도 자동 공개하지 않는다.

### 15.4 대사 출력 채널

콘텐츠와 시스템 상태를 같은 UI에 섞지 않는다.

- 짧은 일상 대사: 심 가까이의 비차단 자막·말풍선 계열을 우선 검토
- 긴 관계 대화: 읽기 가능한 대화 패널과 선택적 기록
- 게임·연결 오류: 명확한 시스템 알림
- 비공개 생각: 일반 플레이에서는 출력하지 않음

커스텀 말풍선은 몰입 목표일 뿐 구현 가능성이 확인된 사실이 아니다. Notification, dialog, 머리 위 UI와 동반 앱 overlay를 RFC-007/008 capability 프로토타입에서 접근성, 그룹 대화, 게임 UI 충돌과 패치 안정성으로 비교한 뒤 기본 채널을 정한다. Simlish 음성 생성을 기본 요구사항으로 두지 않는다.

## 16. 호환성, 장애와 자원 제한

### 16.1 게임 및 플랫폼

- 게임 내 브리지는 최소 기능과 표준 라이브러리만 사용한다.
- 표준 라이브러리라도 해당 게임 빌드에 실제 포함됐는지 확인한다. HTTPS와 SQLite 처리는 동반 앱에 둔다. 소스 `.py` 동봉은 로더·바이트코드 호환성 시험을 대체하지 않는다.
- 게임 Python 런타임과 바이트코드 호환성을 빌드에서 자동 검사한다.
- 미지원 게임 버전에서는 PlumbMate만 비활성화하고 기본 게임은 계속한다.
- macOS Apple Silicon, macOS Intel, Windows x64 빌드를 분리 검증한다.

### 16.2 타 모드 공존

PlumbMate가 만든 행동은 자체 원장으로 추적하고 검증된 객체만 취소한다. 소유권이 불명확한 행동은 건드리지 않고, 다른 모드 출처가 확인될 때만 `third_party_script`로 기록한다. MCCC, UI Cheats, WickedWhims/WonderfulWhims와 큐·자율행동 모드는 격리된 테스트 세이브에서 조합 테스트한다.

런타임 훅 선택 우선순위는 다음과 같다.

1. 게임이 제공하는 콜백·이벤트 등록 API
2. 기존 핸들러를 바꾸지 않는 추가 등록
3. 다른 방법이 없을 때만 현재 callable을 체인으로 보존하는 wrapper injector

클래스 메서드에 새 함수를 그대로 대입하는 raw overwrite는 금지한다. 단, wrapper라는 이름만으로 호환성을 보장하지 않는다. wrapper는 설치가 멱등적이어야 하고, 설치 시점의 기존 callable을 호출하며, 인자·반환값·예외 의미를 보존하고, PlumbMate 예외를 게임과 다음 wrapper로 전파하지 않아야 한다. 대상 심볼이 없거나 서명·지문이 검증값과 다르면 해당 capability만 비활성화한다.

각 런타임 훅은 `RuntimeHookCapability`로 관리한다.

```text
game_build
feature
module + symbol
visibility = registered_api / public_method / private_method
signature_fingerprint
install_strategy
verified_load_orders
failure_fallback
evidence_id
```

MCCC와 WickedWhims/WonderfulWhims의 내부 구현은 공개된 재현 증거 없이 단정하지 않는다. 호환성 승인은 `PlumbMate→타 모드`, `타 모드→PlumbMate` 양쪽 로드 순서와 큐 주입·취소·Sim reset 시나리오를 통과해야 한다.

현재 설치본에서 확인한 구체 심볼은 영구 제품 계약이 아니라 빌드별 증거 레지스트리에 둔다. RFC 본문은 "근무·래빗홀 종료를 감지한다"는 capability 계약을 정의하고, 비공개 심볼을 직접 요구사항으로 고정하지 않는다.

### 16.3 장시간 플레이

- 동반 앱의 단일 DB 작성 작업자
- 짧은 트랜잭션과 제한된 쓰기 큐
- IC foreground 1개, suspended/queued 상한과 중복 병합
- `session_uuid + uint64 seq`
- 로그 링 버퍼와 반복 오류 제한
- 오래된 API 응답의 실행 금지

SQLite 크기 자체보다 무제한 원본 스캔이 문제이므로 데이터를 나눈다.

- Hot: 활성 IC, 최근 장면, 열린 약속과 현재 관계 스케치
- Warm: 검색 가능한 episodic, milestone, preference와 최신 ANN
- Cold: 오래된 감사 이벤트와 superseded 해석

일반 조회는 항상 `save_scope_id`, owner/participants, status, event kind와 시간 인덱스를 사용하고 후보 상한을 둔다. 매 턴 원본 이벤트 테이블 전체를 읽지 않고 materialized `PatternSketch`, `CoPresenceSketch`와 `PreferenceKnowledge`를 조회한다.

의미 있는 사실과 근거 체인은 보존하지만, 중요도가 낮은 `audit_only` 루틴 텔레메트리와 진단 로그는 사용자가 정한 보존 기간 뒤 집계값만 남기고 삭제할 수 있다. 정기 `ANALYZE`, WAL checkpoint와 실제 수개월 규모 픽스처의 쿼리 시간 회귀 테스트를 둔다.

### 16.4 원인 추적과 사용자 지원

모든 행동 결정은 하나의 `decision_id` 아래 로컬 `DecisionTrace`를 남긴다.

```text
trigger
→ active IC와 후보 목록
→ 하드 게이트에서 제외된 이유
→ 관계·ToneShadow 수정값
→ 선택된 모델과 사용한 ref
→ ExecutiveArbiter의 최종 선택
→ 게임 실행 결과 또는 실패 원인
```

일반 사용자는 상태 머신을 읽지 않는다. 문제 해결 화면에 “왜 케이크를 만들지 않았나요?” 같은 템플릿 설명을 보여준다.

```text
생일 준비 목표는 생성됨
→ 조리 가능한 오브젝트 없음
→ 지출 권한 꺼짐
→ 축하 대사 fallback 대기 중
```

Creator 모드는 전체 인과 그래프를 보고, 지원 번들은 키·원문 대사·로컬 경로를 제거한 뒤 사용자가 미리 확인하고 내보낸다.

### 16.5 몰입 파괴 방지

기술 장애를 “에이든이 오늘 이상한 날”이라고 서사로 숨기지 않는다. 네트워크·DB·호환성 실패는 기본 자율행동 fallback과 필요한 최소 알림으로 처리한다.

캐릭터 안에서 흡수할 수 있는 것은 낮은 확신의 말실수와 망설임뿐이다. 실행 전 `ImmersionGuard`가 다음을 검사한다.

- 관찰하지 않은 사실 단정
- 존재하지 않는 기억이나 대상
- 현재 인격·관계와 극단적으로 모순되는 행동
- 장면이 바뀐 오래된 응답
- 되돌리기 어려운 행동의 낮은 확신
- 동일 대사·행동의 반복

큰 이상 행동은 보여준 뒤 해명하는 것보다 실행 전에 차단한다. 이미 발생한 작은 기억 오류는 플레이어가 자연어로 정정할 수 있고, 에이전트가 인정·수정하는 `CorrectionEpisode`로 회복한다. 사용자는 언제든 “그건 아니야”, “다시 말해” 또는 자율행동 중지로 개입할 수 있다.

자유 대사 텍스트만 사후 파싱해 안전을 보장하지 않는다. 목표 응답 스키마는 대사와 함께 구조화된 발화 행위를 요구한다.

```text
SpeechAct
  act = acknowledge / ask / assert / recall / evade
  stance = certain / hedged / question
  claims[] = {claim_type, typed_ref, confidence}
  memory_refs[]
  text
```

`PerceivedAffect`는 `claim_type=perception`과 확신도를 가져야 하며 낮은 확신에서 ActualAffect 단정형으로 출력할 수 없다. L2는 역사적 사실을 자유롭게 단정할 권한이 없고, 제공된 지식 카드의 typed ref를 사용해야 한다. 스키마에 없는 사실을 텍스트로 몰래 넣는 문제를 구조만으로 완전히 증명할 수는 없으므로, 엔티티·날짜·장소의 제한된 로컬 검사와 골든 평가를 함께 사용한다. 검증 실패 시 안전한 질문 또는 일반 대사로 낮춘다.

## 17. 테스트 전략

개인 플레이 데이터를 기본 수집하지 않는다. 테스트셋은 다음을 조합한다.

1. 사람이 작성한 `demo_fixture` 표준 시나리오
2. 개발 전용 세이브에서 얻은 가상 인물 리플레이
3. ID 누락, 동명이인, 순서 뒤집힘과 중복을 만드는 자동 조합·파손 테스트
4. 사용자가 내용을 미리 보고 명시적으로 내보낸 익명 진단 패킷

평가 정답은 특정 대사 하나가 아니라 다음 불변 조건이다.

- 허용된 행동만 선택
- 보지 못한 사실을 사실처럼 말하지 않음
- 존재하지 않거나 다른 세이브의 기억을 참조하지 않음
- 플레이어와 타 모드 행동을 자기 선택으로 오인하지 않음
- 오래된 응답을 실행하지 않음
- 응답 형식, 지연과 비용 한도 충족

출시 전 고장 유도 테스트에는 동반 앱 없는 시작, 플레이 중 강제 종료, 네트워크 차단, 재연결, 빈 로트, AI 심 미설정, 새·기존·복구 세이브, DB 잠금, Mac 잠자기와 미지원 게임 버전을 포함한다.

## 18. 구현 순서

### Foundation

1. RFC-001을 일반화된 엔티티·세이브 계보·사실/주장/해석 모델로 개정
2. 게임 저장 수명주기 capability 조사와 2단계 save watermark 프로토콜
3. 연결 상태 머신, 비차단 브리지와 전체 재동기화
4. AgentExecutive, WorldCoordinator, ActionDispatcher 계약
5. 구조화 SpeechAct와 typed ref를 포함한 프로토콜 0.2 스키마
6. 행동 소유권 원장, 생존 제어와 안전한 fallback
7. SQLite 스키마 버전과 마이그레이션

### Intelligence

8. Scene Atomizer, 관련도 게이트와 PM-Wire 비교 벤치마크
9. L0~L3 라우터와 비용 한도
10. PolicyKernel, ExecutiveArbiter와 DecisionTrace
11. IC 스케줄러, 체크포인트와 재개 검증
12. 기억 검색, ref 검증과 해석 버전

### Relationship

13. 취향·지식·오해·해명·용서 그래프
14. 이정표, 함께한 시간, 개인 루틴과 선택형 말버릇
15. 받은 돌봄, WorldTrace, SharedLearning과 상호성 루프
16. 갈등·화해 패턴, 제3자 관계와 비공개 생각
17. 지각, 학습 속도, 인식의 거리, Calibrated Fallibility와 성장 아크
18. 생일 준비 등 주도적 목표 실행

### Release

19. 비밀 저장소, 전송 동의, 비용 UI와 접근성
20. 인덱스·보존 정책·인과 추적과 ImmersionGuard
21. 실제 모드 호환성·장시간·장애 테스트
22. 서명된 플랫폼 빌드, 설치 프로그램, 공개 픽스처와 문서

## 19. 팀 역할과 의사결정

- 케이트 — 총괄 PM: 제품 감성, 우선순위, 사용자 경험, 자율성·비용 기본값과 수용 기준
- 루멘 — 개발·아키텍처: 구현, 프로토콜, 검증, 테스트, 호환성과 릴리스
- 페이블 — 설계 비판: 엣지케이스, 위협 모델, 실패 시나리오와 테스트 픽스처 검토

설계 비판은 RFC의 `Open Questions`와 수용 테스트로 변환한다. 구현 완료 주장은 자동 테스트, 게임 내 확인 또는 재현 가능한 로그가 있을 때만 한다.

제미나이 등 보조 연구 결과는 개념·후보 출처·실패 시나리오를 제공한다. 제안자가 누구든 `hypothesis → symbol_verified → path_inspected → live_tested`를 구분한다. 심볼의 존재나 여러 모델의 동의만으로 실제 동작·호환성이 검증된 것으로 올리지 않는다.

## 20. 다음 문서 작업

1. 기존 RFC-001을 이 문서의 일반화·canon 규칙에 맞춰 저장소 안으로 이전한다.
2. 연결과 행동을 RFC-002로 고정한다.
3. PM-Wire, 라우팅과 IC를 RFC-003/004로 분리하고 골든 픽스처를 먼저 작성한다.
4. 관계·경험 기능은 RFC-005/006에 상태 전이와 실패 사례를 포함한다.
5. 각 RFC는 `Draft → Accepted → Implementing → Verified` 상태와 구현 링크를 가진다.
