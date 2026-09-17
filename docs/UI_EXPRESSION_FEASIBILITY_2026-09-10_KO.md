# 감정 기록과 풍선 UI — 구현 전 타당성 검토

상태: 조사/제안만. 기능 추가, 게임 감정 변경, UI 튜닝 덮어쓰기 없음.
설치본 1.127.41.1230 / Python 3.7의 code object를 정적으로 확인했다.
게임 모듈을 import하거나 이 경로로 실행하지 않았다. 심볼 존재는 실게임 성공의 증거가 아니다.

## 서로 다른 네 가지 목표

| 목표 | 구현 부담 추정 | 핵심 경계 |
|---|---|---|
| PlumbMate 최근 대화/감정 기록을 별도 읽기 전용 창으로 보기 | UI 소~중, 영구 저장은 별도 중~대 | 대사/관찰/해석 분리, 세이브 격리 전에는 세션 한정 |
| 기존 관계 프로필의 Sentiment 카드에 통합 | 중~대 | 단순 표시가 아니라 관계/효과/지속 시간 로직에 영향 |
| 기존 아이콘형 말풍선·생각풍선을 선택해 표시 | 소~중 프로토타입 | 리소스 존재·게임 스레드·숨겨진 심·중복·쿨다운 검사 |
| 머리 위 풍선에 자유로운 한국어 문장 표시 | 대 / 미확정 | 확인한 네이티브 경로에 자유 텍스트 인자 없음. 별도 UI 연구 필요 |

사용자가 말한 '감정 노트'가 관계 프로필의 감정, 노트북, 사진 기억 중 어느
화면인지는 확인 전이다. 이들을 같은 기능으로 취급하지 않는다.
작업량은 설계 판단이며, 실측 실행 성능이나 완료 시간 보장이 아니다.

## 로컬 근거

아래는 추정한 식별자가 아니라 설치 파일에서 읽은 이름이다.

- `BalloonRequest.__init__`: sim, icon, icon_object, overlay, balloon_type,
  priority, duration, delay, delay_randomization, category_icon, view_offset,
  rel_track, icon_info 인자. 자유 텍스트 인자는 확인되지 않음.
- `BalloonRequest.distribute`: 숨겨진 심 검사와 `AddBalloon` / distributor 경로.
- `AddBalloon.write`: 아이콘 리소스/객체, 종류, 우선순위, 지속 시간 등의 메시지를 직렬화.
- `BalloonTypeEnum`: THOUGHT / SPEECH / DISTRESS / SENTIMENT / SENTIMENT_INFANT.
- `trigger_passive_balloon`도 존재하지만 수동 요청이 임의 콘텐츠 선택/올바른 RP 표현을
  보장하지는 않음. 확인되지 않은 콘솔 명령 문자열은 제안하지 않는다.
- `NotebookTrackerSimInfo`: `generate_notebook_information`, `save_notebook`,
  `load_notebook`, `save_notebook_notes`가 존재. notebook `_notes`는 기존 사용자
  노트를 덮어쓸 수 있으므로 PlumbMate 저장소로 빌려 쓰지 않는다.
- `SentimentTrack`: 기간·부호·표시 이름 외, 대상 심에 가까이 있을 때 적용하는
  proximity loot과 나이 변화에 따른 전환도 연결됨. 장식용 메모로 임의 추가하지 않는다.
- `buffs.memory`도 별도 체계다. 파일에 'memory'가 있다고 PlumbMate의 영구 RP
  기억 DB와 같은 것으로 간주하지 않는다. `server_commands.memory_commands`는
  Python heap/GC 진단이므로 이번 사용자 기록 UI와 무관하다.

검사한 파일 SHA-256:

```text
balloon/balloon_request.pyc ff2fd57720e39b01a0907a8b06e35c56d2a503b2bb05398b1451392886a63be8
balloon/balloon_ops.pyc 0d44f366fd1d53bf9dec11fec335bbf4e4d6a17c456e0b66a39acca543930012
balloon/balloon_enums.pyc 551cd243c0227c1d046a59bdbf54d8ca64863d9c469e97219c9b05367ac981a2
notebook/notebook_tracker.pyc 0d349f915b2421af7679e1796413ba3b3b49c74374610a23e5d8c02d47fd03a5
relationships/sentiment_track.pyc 900ebd6f989c156b3643f0803f7dcd207e1f3628c4bc696e558419e9e5498e36
```

공개 제작자 사례: [sims4me의 풍선 확장 모드](https://www.curseforge.com/sims4/mods/conversationtweak-more-diverse-speech-and-thought)는
사건·특성에 따른 풍선 다양화를 제공하며, 같은 풍선 튜닝을 바꾸는 모드와의
충돌 및 패치/리소스 차이에 따른 빈 풍선을 경고한다. 이는 가능성의 보조 근거이며
PlumbMate 호환성 검증을 대체하지 않는다. 다운로드/설치는 하지 않았다.

## 권장 분리 구조 — 아직 구현하지 않음

1. **기록 모델:** actor/target 방향, 관찰 근거, 표현인지 사실인지 구분.
   '함께 산책할까?'라는 대사를 '함께 산책함' 사건으로 저장하지 않는다.
2. **표현 정책:** 검증된 출력의 주제/톤에서 허용된 cue를 선택. 모델에 아이콘
   리소스 ID나 실행 명령을 자유롭게 생성시키지 않는다. 매 풍선마다 모델을 부르지 않는다.
3. **풍선 어댑터:** 자기 소유 요청만 표시, 게임 스레드에서 실행, 로트 변경 시 폐기.
   누락 리소스·비활성/숨겨진 심·다른 풍선과 경쟁 시 조용히 생략.
4. **기록 뷰:** 요청 시 제한된 최근 항목만 표시. 텍스트 답변은 검증된 알림 담벼락 유지.
   속마음 원문은 기본으로 공개하지 않고, 원하면 플레이어가 읽을 수 있는 기록만 표시.

초기 제안 한도: 심당 대기 cue 1개, 짧은 표시와 쿨다운, 기록 창은 최근 20개.
정확한 간격/표시 시간은 실제 게임 실험 후 결정한다. 상시 전체 심/기억 스캔은 금지.
이 구성의 **표현 자체**는 추가 API 없이 가능하나, 감정 해석/장기 회고 생성은
별도 모델 호출과 토큰 예산이 필요할 수 있다. 아직 성능 수치는 측정하지 않았다.

## 개선 우선순위

1. 설치된 alpha.6의 입력 안내/만료 알림 실게임 검증.
2. 콘솔 없는 역할 설정·대화 진입 및 현재 수신자/연결 상태의 쉬운 확인.
3. 아이콘 풍선 1개만 표시하는 격리된 capability probe. 화면에 나타나도 실제
   대화/게임 행동 성공으로 기록하지 않을 것.
4. 세션 한정 기록 보기 → 검증된 세이브 격리 후 영구 기록.
5. 인격/관찰 맥락을 포함한 실제 모델 RP 품질 평가. UI 장식만으로 자연스러움이
   해결되지는 않는다. mock을 더 사람답게 꾸며 구현 상태를 숨기지 않는다.
