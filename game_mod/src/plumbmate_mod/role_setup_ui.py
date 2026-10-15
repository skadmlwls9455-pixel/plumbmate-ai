"""Thin, optional EA UI adapter. Selection never creates Sims or changes saves.

Basic picker/notification display was verified on game 1.127.41.1230.
Portrait overrides use its inspected native icon path; no image files or API.
"""


def _text(value):
    from sims4.localization import LocalizationHelperTuning
    return LocalizationHelperTuning.get_raw_text(str(value))


def notify(owner, title, message, portrait=None):
    from ui.ui_dialog_notification import UiDialogNotification
    dialog = UiDialogNotification.TunableFactory().default(
        owner=owner, title=lambda *args, **kwargs: _text(title),
        text=lambda *args, **kwargs: _text(message))
    display = {}
    if portrait is not None:
        try:
            from distributor.shared_messages import IconInfoData
            # Native build_icon_info_msg expects an icon-capable game object.
            # Never substitute the currently selected Sim for a missing speaker.
            icon_info = portrait.icon_info
            if icon_info is not None and None not in icon_info:
                display["icon_override"] = IconInfoData(obj_instance=portrait)
        except Exception:
            pass  # Text survives unavailable portraits; do not repeat show_dialog.
    dialog.show_dialog(**display)


class RoleSetupUI(object):
    def __init__(self):
        self._generation = 0

    def open(self, environment, binding, output, name_of, on_complete=None):
        zone, household, members, owner = environment()
        if len(members) < 2:
            message = ("현재 가족에 심이 한 명뿐이거나 없어요. 이 버전은 같은 가족의 서로 다른 두 심이 필요해요. "
                       "플레이어 심과 AI가 맡을 심을 준비한 뒤 다시 열어 주세요. "
                       "심을 자동으로 만들거나 API를 호출하지 않았어요.")
            output(message)
            try:
                notify(owner, "PlumbMate · 역할 설정 안내", message)
            except Exception:
                pass
            return
        self._generation += 1
        generation = self._generation

        def valid():
            current_zone, current_household, current_members, _ = environment()
            return (generation == self._generation and current_zone is zone and current_household is household
                    and len(current_members) == len(members)
                    and all(any(member is item for item in current_members) for member in members))

        def choose(options, title, callback):
            from ui.ui_dialog_picker import UiSimPicker, SimPickerRow
            dialog = UiSimPicker.TunableFactory().default(
                owner=owner, title=lambda *args, **kwargs: _text(title),
                text=lambda *args, **kwargs: _text("현재 가족에서 한 명을 골라 주세요. 역할은 이 로트 세션에만 유지돼요."))
            dialog.max_selectable_num = 1
            for member in options:
                dialog.add_row(SimPickerRow(sim_id=member.sim_id, tag=member, name=_text(name_of(member))))

            consumed = False

            def selected(result):
                nonlocal consumed
                try:
                    if consumed or generation != self._generation:
                        return
                    consumed = True
                    if not result.accepted:
                        return
                    if not valid():
                        output("가족이나 장소가 바뀌었어요. 역할 선택창을 다시 열어 주세요.")
                        return
                    picks = result.get_result_tags()
                    if len(picks) != 1 or not any(picks[0] is member for member in options):
                        output("한 명을 선택해 주세요. 역할은 변경하지 않았어요.")
                        return
                    callback(picks[0])
                except Exception:
                    output("역할 선택을 완료하지 못했어요. 역할을 확인한 뒤 다시 설정해 주세요.")
            dialog.show_dialog(on_response=selected)

        def player_chosen(player):
            def partner_chosen(partner):
                binding.bind(zone, household, player, partner, members)
                message = "플레이어: {} → AI 파트너: {}. 장소/가족 전환 후에는 다시 설정해 주세요.".format(name_of(player), name_of(partner))
                output(message)
                if on_complete is not None:
                    on_complete()
                    return
                try:
                    notify(owner, "PlumbMate · 역할 설정", message)
                except Exception:
                    output("알림 표시가 지원되지 않아 결과를 콘솔에 표시했어요.")
            choose([member for member in members if member is not player], "2/2 · AI가 맡을 심", partner_chosen)

        choose(members, "1/2 · 내가 맡을 심", player_chosen)
