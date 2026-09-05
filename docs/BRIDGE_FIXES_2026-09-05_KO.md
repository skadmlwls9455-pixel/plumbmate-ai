# 소켓 브리지 신뢰성 수정 기록

기준: `81da97c`에서 재현한 네 결함. 수정 범위는 로컬 저장소이며 GitHub 푸시, 실제 Mods 교체, 게임 실행·저장은 하지 않았다.

## 수정과 회귀 검증

| 결함 | 수정 | 회귀 테스트 |
|---|---|---|
| `functools.wraps`로 복사된 마커 때문에 다른 모드 훅까지 제거 | 클래스에 보관한 실제 wrapper 객체로 소유권 확인. 타 모드가 바깥에 있으면 체인을 변경하지 않고 내부 PlumbMate wrapper만 비활성화 | 복사된 마커·마커 없는 wrapper 보존, 재설치 시 한 번만 pump, 모듈 reload 시 driver 교체 |
| stop 후 남은 내부 훅이 binder를 다시 실행 | driver suspend, binder 해제, client 분리/종료를 독립적으로 수행. 소켓 생성 가능 여부와 무관하게 정지 | 타 모드 wrapper 두 종류 아래에서 stop, 100틱 및 오래된 직접 callback 호출에도 재연결 0, explicit start로만 복귀 |
| 틱당 16개 제한 이후 버퍼에 남은 ACK가 새 recv를 기다리며 정체 | 메모리에 이미 완성된 프레임을 먼저 배출하고 그 후 비차단 recv. 프레임 수·읽기 바이트 상한 유지 | 64개 ACK를 한 배치로 받아 4번 pump로 모두 처리, 부분 프레임, EOF 직전 ACK |
| 매 5초 게임 틱에서 endpoint/token 파일 조회·디렉터리 처리 | 설정은 mod load/명시적 start에서만 읽고 메모리에 보관. CLI는 지속 포트 재사용, 클라이언트는 같은 포트로 backoff 재연결 | 바인딩을 포함한 200틱에서 open/exists/isdir/makedirs 호출 0, 늦은 앱 시작, 같은 포트 재시작, 의도적 포트 변경 시 설정 reload |

같은 수신 경계에서 발견한 문제도 수정했다. 클라이언트·서버 모두 줄바꿈이 포함된 완성 프레임에도 크기 상한을 적용한다. 알 수 없는/중복 ACK는 성공 건수를 늘리지 않는다. 잘못된 포트는 연결 전에 거부한다. binder의 clock 예외와 예상하지 못한 driver 예외가 게임 wrapper 바깥으로 전파되지 않게 한다. 기존 게임 메서드 자체의 예외는 숨기지 않는다.

## 변경된 연결 계약

- 토큰은 게임 시작 전에 준비한다. 최초 포트는 `127.0.0.1:47651`, 이후 `listen`은 저장된 포트를 재사용한다.
- endpoint 파일은 지속 설정이지 생존 신호가 아니다. 앱 종료 시 삭제하지 않는다.
- 포트 점유 시 다른 프로세스를 종료하거나 임의 포트로 이동하지 않고 실패를 표시한다.
- 포트/토큰의 의도적 변경, 실행 중 누락 토큰 생성 뒤에는 `plumbmate.bridge.start` 또는 게임 재시작으로 설정을 다시 읽는다. 일반적인 동반 앱 재시작에는 이 동작이 필요 없다.
- 게임 내부 worker thread, 파일 polling, blocking connect/recv/send, DNS 조회를 추가하지 않았다. 초기 설정 읽기와 수동 명령의 파일 I/O는 별도 경계다.
- ACK는 동반 앱 메모리 큐 수락을 뜻하며 SQLite 영속 저장이나 게임 저장 성공의 증거가 아니다. 전송 후 연결이 끊긴 이벤트는 기존 at-most-once 정책대로 재전송하지 않는다.

## 확인 범위

검증 명령:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=companion/src python3 -m unittest discover -s tests
python3 game_mod/build_game_mod.py
git diff --check
```

- 기존 164개에서 189개로 증가한 테스트 모두 통과. 네트워크 테스트는 임시 로컬 루프백 연결만 사용하며 게임/API 키/외부 모델 호출이 필요 없다.
- 설치본의 `libpython370.dylib`로 세 게임 모듈을 컴파일하고 설치본 bytecode magic과 일치함을 빌드에서 확인했다. 호스트 Python 테스트와 게임 Python 3.7 빌드는 서로 다른 검증이다.
- 결과물: `dist/PlumbMate.ts4script`. 수정본의 실제 게임 로드, 장시간 플레이, 타 모드와의 실제 공존은 아직 재검증 전이다. 테스트의 가짜 Zone/wrapper를 WW/MCCC 호환 완료로 간주하지 않는다.
- 자동 게임 이벤트 관찰, 실제 행동 실행, 세이브 커밋 연동과 장기 기억은 이 수정으로 완성되지 않는다.
