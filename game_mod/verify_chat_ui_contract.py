"""Optional offline probe, run with the installed game's Python 3.7 runner.

Executes ONLY four hash-pinned UI field/message methods against owned fakes.
Does not import a gameplay module, start the game, save, or use networking.
This is not proof of native dialog display, protobuf serialization, or IME.
"""

import hashlib
import importlib.util
import marshal
import os
import sys
import types
import zipfile


GAMEPLAY = "/Applications/EA Games/The Sims 4.app/Contents/Data/Simulation/Gameplay"
EXPECTED = {
    "ui/ui_dialog_generic.pyc": "f528701fc4d2e0bda1d89e1710c84cf8c3c8adc2d10f882b2060d119787b35b4",
    "ui/ui_text_input.pyc": "ed3db5b91e0ab5ecabe46e0fc7a5fa98d5902453377c1074a60e841531508586",
}


def code_at(module, *names):
    for name in names:
        module, = [item for item in module.co_consts if isinstance(item, types.CodeType) and item.co_name == name]
    return module


def cell(value):
    return (lambda: value).__closure__[0]


with zipfile.ZipFile(os.path.join(GAMEPLAY, "simulation.zip")) as archive:
    modules = {}
    for member, expected in EXPECTED.items():
        data = archive.read(member)
        if data[:4].hex() != "420d0d0a" or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Unreviewed native UI bytecode; inspect before updating this probe")
        modules[member] = marshal.loads(data[16:])


class OwnedFields(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class OwnedRepeated(list):
    def add(self):
        row = types.SimpleNamespace()
        self.append(row)
        return row


class BaseDialog(OwnedFields):
    def build_msg(self, **kwargs):
        return types.SimpleNamespace(text_input=OwnedRepeated())

    def _build_localized_string_msg(self, factory, *tokens):
        return factory(*tokens)


class Dialog(BaseDialog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._max_value = self._min_value = None
        self._invalid_max_tooltip = self._invalid_min_tooltip = None
        self.text_input_responses = {}

    @staticmethod
    def TunableFactory(**kwargs):
        return types.SimpleNamespace(default=Dialog)


class TextField(OwnedFields):
    pass


class Length(OwnedFields):
    pass


generic = modules["ui/ui_dialog_generic.pyc"]
field_module = modules["ui/ui_text_input.pyc"]
Dialog.build_msg = types.FunctionType(code_at(generic, "UiDialogTextInput", "build_msg"),
                                     globals(), closure=(cell(Dialog),))
Dialog.on_text_input = types.FunctionType(code_at(generic, "UiDialogTextInput", "on_text_input"), globals())
TextField.build_msg = types.FunctionType(code_at(field_module, "UiTextInput", "build_msg"), globals())
Length.build_msg = types.FunctionType(code_at(field_module, "_TextInputLengthFixed", "build_msg"), globals())

sys.modules["ui"] = types.ModuleType("ui")
generic_stub = types.ModuleType("ui.ui_dialog_generic")
generic_stub.UiDialogTextInputOkCancel = Dialog
sys.modules[generic_stub.__name__] = generic_stub
field_stub = types.ModuleType("ui.ui_text_input")
field_stub.UiTextInput, field_stub._TextInputLengthFixed = TextField, Length
sys.modules[field_stub.__name__] = field_stub
localization = types.ModuleType("plumbmate_role_setup_ui")
localization._text = str
localization.notify = lambda *args: None
sys.modules[localization.__name__] = localization

source = os.path.join(os.path.dirname(os.environ["PLUMBMATE_INSPECT_SCRIPT"]), "src", "plumbmate_mod")
for name in ("chat_input", "chat_ui"):
    spec = importlib.util.spec_from_file_location("plumbmate_" + name, os.path.join(source, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

adapter = sys.modules["plumbmate_chat_ui"]
for draft in ("", "한글  대화\n안녕 🌻", "🌻" * 1000):
    dialog = adapter._make_dialog(object(), "Test Partner", draft)
    message = dialog.build_msg(None, ())
    field, = message.text_input
    assert field.text_input_name == adapter.INPUT_NAME
    assert (field.max_length, field.min_length, field.height) == (2000, 1, 100)
    assert getattr(field, "restricted_characters", None) is None
    assert getattr(field, "initial_value", "") == draft
    assert not dialog.on_text_input("not_the_chat_field", "ignore")
    assert dialog.on_text_input(adapter.INPUT_NAME, draft)
    assert dialog.text_input_responses == {adapter.INPUT_NAME: draft}
print("PASS: pinned native field/message methods, 3 synthetic text cases; UI display and IME UNVERIFIED")
