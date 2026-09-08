# 2026-09-09 한글 입력창 체크포인트

## 범위

기준 커밋 `7d5b581`의 역할 계약 위에 작은 입력 UI를 추가했다. 사용자는
전원 설정을 끝냈고 코딩에 집중해 달라고 요청했다. 게임 실행/설치/가족
변경/저장/유료 API 호출은 이번 묶음에서 하지 않았다.

## 구현

- `plumbmate.chat`을 인자 없이 실행하면 지정한 AI 파트너 이름이 있는
  native 입력창을 요청한다. 콘솔 `<message>` 방식도 유지한다.
- `chat_input.py`는 순수 입력/초안 정책, `chat_ui.py`는 native 창과 상태
  알림만 담당한다. 실제 전송은 `commands._submit_chat` 한 경로로 모았다.
  UI가 역할 검사·기존 요청 중복 검사·동반 앱의 비용 제한을 우회하지 않는다.
- 창 열기/취소는 요청 파일을 만들지 않는다. 전송 버튼의 수락 응답만 처리하며,
  중복 콜백/새 창으로 대체된 콜백/역할·장소 변경은 보내지 않는다.
- 한글 조합 문자·공백·줄바꿈·이모지를 임의 정규화하거나 접두부로 잘라
  보내지 않는다. 앞뒤 공백만 제거한다. 1000 Unicode code points 초과,
  빈 입력, 잘못된 타입, 단독 surrogate, 허용하지 않은 제어 문자는 거절한다.
- native 기본 최대 길이는 설치본에서 20이었다. 대화창 전용 필드와 길이
  객체를 새로 만들어 기본 튜닝/다른 모드의 singleton을 수정하지 않는다.
  GUI 측 길이는 UTF-16 계산을 고려해 2000, 제출 검사는 1000 code points다.
  native 위젯이 이 한도를 세거나 큰 붙여넣기를 다루는 방식은 실검증 전이다.
- 고칠 수 있는 초안은 최대 2000자 하나만 로컬 메모리에 보관한다. 취소·전송
  성공·다른 역할/씬에서 다시 열면 지운다. 역할 변경 후 자동으로 창을 여는
  감시 루프는 없으며, 저장/장기 기억/텔레메트리에는 넣지 않는다.
- 요청 파일을 남겼다는 상태와 실제 답변 준비를 혼동하지 않는다. 상태 알림
  제목은 `PlumbMate · 대화 상태`. 심의 감정/생각으로 통신 상태를 연기하지 않는다.
  콘솔/알림 오류가 나도 재전송하거나 게임 콜백에 연쇄 예외를 올리지 않는다.

## 검증

최종 전체 회귀 **307개 통과, 12.336초**. 스키마 일치, 게임 Python 3.7 문법,
전이 의존성까지 포함한 빌드 모듈 누락 검사를 통과했다. 입력 최대 길이가
동반 앱의 canonical request schema와 일치하는지도 별도 회귀 검사한다.
schema export 및 `git diff --check`도 통과했다. 원격 CI 결과는 별도다.

실제 설치본 정적 확인:

- `ui/ui_dialog_generic.pyc` SHA-256
  `f528701fc4d2e0bda1d89e1710c84cf8c3c8adc2d10f882b2060d119787b35b4`
- `ui/ui_text_input.pyc` SHA-256
  `ed3db5b91e0ab5ecabe46e0fc7a5fa98d5902453377c1074a60e841531508586`
- `UiDialogTextInputOkCancel`, `UiTextInput`, `_TextInputLengthFixed`,
  `text_input_responses`를 확인했다. 수신에서는 `hasattr(text_inputs, name)`,
  메시지 생성에서는 `text_inputs.items()`가 필요하므로 두 계약을 만족시킨다.
- `game_mod/verify_chat_ui_contract.py`는 해시가 일치하는 메서드 4개만 선택해
  게임 Python 3.7의 별도 프로세스에서 가짜 소유 객체/메시지 컨테이너로 실행한다.
  게임 모듈 전체를 import하지 않는다. 빈 값/한글·조합 문자·줄바꿈/1000 이모지
  3개 합성 입력의 필드 키·초깃값·길이 제한 전달과 응답 수신을 통과했다.
  생성자/실제 protobuf/렌더링/IME는 이 검사로 증명되지 않는다.

게임용 빌드 `dist/PlumbMate-alpha4.ts4script`에는 production 모듈 8개만 넣었다.
개발 검증 스크립트와 EA 바이트코드는 배포 archive에 넣지 않는다. 설치본
magic `420d0d0a` 일치를 검사했다. 산출물 SHA-256:
`b8a05cc6c8132183cd263d2f1bf72ec823f5e560fed2e70b18f86b193d222a78`.
실제 Mods 폴더에는 아직 이 빌드가 없다.

## 재현 및 다음 작업

일반 회귀: `.venv/bin/python -m unittest discover -s tests -q`.
빌드: `python3 game_mod/build_game_mod.py --output dist/PlumbMate-alpha4.ts4script`.
native 메서드 검사는 저장소에 포함한 격리 실행기로 재현할 수 있다. 저장소
루트에서 아래를 실행한다. 다른 게임 빌드 해시는 검토 없이 갱신하지 않는다.

```sh
task_runtime=$(mktemp -d /private/tmp/plumbmate-ui-check.XXXXXX)
unzip -q '/Applications/EA Games/The Sims 4.app/Contents/Data/Simulation/Gameplay/base.zip' 'lib/*' -d "$task_runtime"
cc -std=c11 -O2 game_mod/inspect_with_game_python.c -o "$task_runtime/inspect-runner"
"$task_runtime/inspect-runner" \
  '/Applications/EA Games/The Sims 4.app/Contents/Frameworks/libpython370.dylib' \
  "$task_runtime/lib" "$PWD/game_mod/verify_chat_ui_contract.py"
```

다음 수직 경로는 **자동 답변 전달**이다. 현재는 사용자가 `plumbmate.read`를
실행해야 한다. 구현 전에 다음 경계를 함께 설계한다.

1. 기존 인증된 nonblocking 소켓 위에 별도의 capability/version 협상.
2. 요청 ID + role binding + scene + 로컬 전송 시각으로 수신 권한 검사.
3. 외부 앱이 응답 파일을 읽어 bounded mailbox에 넣고, 게임은 틱의 제한된
   소켓 작업만 수행. game tick 파일 polling과 EA 백그라운드 스레드 호출 금지.
4. 한 번만 표시, 만료/재지정/전환/과대 응답 거절, 끊김 이후 오래된 대사 재생 금지.
   전송 재연결과 모델 재호출을 구분하고, 자동 유료 재시도는 추가하지 않는다.
5. read 수동 경로와 같은 표시 gate를 사용한다. 자동화 기능 부재를 숨기지 않는다.

그 전에 실제 UI 검증에서는 두 심이 있는 허용된 시험 가족으로 역할 선택,
수신자 표시, 직접 IME 입력(조합 중 Enter 포함), 취소, 긴 붙여넣기, 알림 표시,
로트 전환 중 열린 창의 전송 거절을 확인해야 한다. 현재 게임 화면 도구는
상태 조회가 시간 초과되었으므로 실검증 완료라고 보고하지 않는다.
