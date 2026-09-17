"""Read-only game-scene adapter; no UI, bridge files, model calls or state.

EA services are supplied by the bootstrap. Invoke only on the game thread.
RoleBinding owns role identity; this module resolves its live game objects.
"""


def safe_text(value, fallback=""):
    try:
        return str(value)
    except Exception:
        return fallback


def sim_name(sim_info):
    full_name = getattr(sim_info, "full_name", None)
    if full_name:
        return safe_text(full_name)
    first_name = safe_text(getattr(sim_info, "first_name", ""))
    last_name = safe_text(getattr(sim_info, "last_name", ""))
    return (first_name + " " + last_name).strip() or "Unknown Sim"


def active_sim(services, connection):
    try:
        manager = services.client_manager()
        if manager is None:
            return None
        # Native button callbacks have no cheat-console connection argument.
        client = manager.get_first_client() if connection is None else manager.get(connection)
        return getattr(client, "active_sim", None) if client is not None else None
    except Exception:
        # Main menu / household transitions have no usable active client.
        return None


def snapshot(sim):
    sim_info = sim.sim_info
    actor = {
        "sim_id": int(getattr(sim_info, "sim_id", getattr(sim, "id", 0))),
        "name": sim_name(sim_info)[:128],
        "age": safe_text(getattr(sim_info, "age", ""))[:40],
        "gender": safe_text(getattr(sim_info, "gender", ""))[:40],
    }
    try:
        mood = sim.get_mood()
        actor["mood"] = safe_text(getattr(mood, "__name__", mood))[:80]
    except Exception:
        pass

    household = []
    try:
        for member in sim.household.sim_info_gen():
            household.append({
                "sim_id": int(getattr(member, "sim_id", 0)),
                "name": sim_name(member)[:128],
            })
            if len(household) >= 12:
                break
    except Exception:
        household = [{"sim_id": actor["sim_id"], "name": actor["name"]}]
    return actor, household


def role_environment(services, sim):
    if sim is None:
        raise ValueError("가족을 먼저 불러와 주세요.")
    household = getattr(sim, "household", None)
    if household is None:
        raise ValueError("현재 가족 정보를 확인할 수 없어요.")
    members = list(household.sim_info_gen())
    if len(members) > 12:
        raise ValueError("이 시험 버전은 가족 구성원 12명까지 지원해요.")
    return services.current_zone(), household, members, sim.sim_info


def bound_player(binding, environment, selected):
    if binding is None:
        raise ValueError("역할 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    zone_obj, household, members, _ = environment
    player, partner = binding.resolve(zone_obj, household, members)
    sim = selected if selected.sim_info is player else player.get_sim_instance()
    if sim is None:
        raise ValueError("플레이어 심이 현재 로트에 없어요. 돌아온 뒤 대화해 주세요.")
    if partner.get_sim_instance() is None:
        raise ValueError("AI 파트너가 현재 로트에 없어요. 돌아온 뒤 대화해 주세요.")
    return sim, partner
