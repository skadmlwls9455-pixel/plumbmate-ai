"""Offline Python 3.7 native contract check; no game imports or screen access.

Run with inspect_with_game_python.c and the installed game's extracted stdlib.
Executes only a hash-pinned dialog response dispatcher against owned objects.
Does NOT prove the notification button renders or remains clickable client-side.
"""

import hashlib
import marshal
import types
import zipfile

ARCHIVE = "/Applications/EA Games/The Sims 4.app/Contents/Data/Simulation/Gameplay/simulation.zip"
EXPECTED = {
    "ui/ui_dialog.pyc": "c0c98ecebbb9f0240f25ab7ed2efed07cb0b6f75be2e41d69fe44f8ba2280849",
    "ui/ui_dialog_service.pyc": "9c5913af2a8efff913dd5e8b8bd4b471152990363f4a878b2698abfa0f45d93b",
}


def code_at(code, *names):
    for name in names:
        code, = [item for item in code.co_consts if isinstance(item, types.CodeType) and item.co_name == name]
    return code


modules = {}
with zipfile.ZipFile(ARCHIVE) as archive:
    for path, expected in EXPECTED.items():
        data = archive.read(path)
        if data[:4].hex() != "420d0d0a" or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Native UI changed; review bytecode before updating hashes")
        modules[path] = marshal.loads(data[16:])

dialog_module = modules["ui/ui_dialog.pyc"]
buttons = code_at(dialog_module, "ButtonType").co_names
for name in ("DIALOG_RESPONSE_OK", "DIALOG_RESPONSE_CANCEL", "DIALOG_RESPONSE_CUSTOM_1"):
    assert name in buttons, name
assert "_additional_responses" in code_at(dialog_module, "UiDialog", "set_responses").co_names
assert "_additional_responses" in code_at(dialog_module, "UiDialog", "_get_responses_gen").co_names
assert "add_listener" in code_at(dialog_module, "UiDialogBase", "show_dialog").co_names


class OwnedService(object):
    def __init__(self, dialog):
        self._active_dialogs = {17: dialog}
        self.cancelled = []

    def _dialog_cancel_internal(self, dialog):
        self.cancelled.append(dialog)
        self._active_dialogs.pop(17)


OwnedService.dialog_respond = types.FunctionType(
    code_at(modules["ui/ui_dialog_service.pyc"], "UiDialogService", "dialog_respond"), globals())


class OwnedLauncher(object):
    def __init__(self):
        self.opens = 0

    def respond(self, response):
        if response == "open":
            self.opens += 1
            return False
        if response == "error":
            raise ValueError("owned fixture")
        return True


launcher = OwnedLauncher()
service = OwnedService(launcher)
assert service.dialog_respond(17, "open", None) is False
assert service.dialog_respond(17, "open", None) is False
assert launcher.opens == 2 and service._active_dialogs[17] is launcher
assert service.dialog_respond(17, "close", None) is True
assert not service._active_dialogs and service.cancelled == [launcher]
assert service.dialog_respond(17, "open", None) is False
assert launcher.opens == 2
service = OwnedService(OwnedLauncher())
try:
    service.dialog_respond(17, "error", None)
except ValueError:
    assert not service._active_dialogs
else:
    raise AssertionError("Native exception cleanup changed")
print("PASS: native response IDs, additional buttons, listener registration, repeated activation, close and exception cleanup")
print("UNVERIFIED: native subclass factory, client-side button persistence/rendering, focus and in-game navigation")
