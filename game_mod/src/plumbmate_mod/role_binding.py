"""Session-only role binding. No game imports, files, networking or saved IDs.

Object identity is a conservative runtime fence, NOT a verified save identity.
Any zone/household replacement requires explicit setup again in this alpha.
"""

import uuid


class RoleBinding(object):
    def __init__(self):
        self.clear()

    def clear(self):
        self._zone = None
        self._household = None
        self._player = None
        self._partner = None
        self.binding_id = None
        self.scene_id = None
        self._player_id = None
        self._partner_id = None

    def bind(self, zone, household, player, partner, members):
        if zone is None or household is None or player is partner:
            raise ValueError("두 역할에 서로 다른 심을 선택해 주세요.")
        ids = [getattr(member, "sim_id", None) for member in members]
        player_id, partner_id = getattr(player, "sim_id", 0), getattr(partner, "sim_id", 0)
        if (any(type(ident) is not int or not 0 < ident < 2**64 for ident in ids)
                or type(player_id) is not int or type(partner_id) is not int
                or not 0 < player_id < 2**64 or not 0 < partner_id < 2**64
                or player_id == partner_id or len(set(ids)) != len(ids)
                or not any(player is member for member in members)
                or not any(partner is member for member in members)):
            raise ValueError("현재 가족에 있는 서로 다른 두 심을 선택해 주세요.")
        self._zone, self._household = zone, household
        self._player, self._partner = player, partner
        self._player_id, self._partner_id = player_id, partner_id
        self.binding_id, self.scene_id = uuid.uuid4().hex, uuid.uuid4().hex

    def resolve(self, zone, household, members):
        if (self.binding_id is None or zone is not self._zone or household is not self._household
                or getattr(self._player, "sim_id", None) != self._player_id
                or getattr(self._partner, "sim_id", None) != self._partner_id
                or not any(self._player is member for member in members)
                or not any(self._partner is member for member in members)):
            self.clear()
            raise ValueError("플레이어 심과 AI 파트너를 먼저 지정해 주세요. plumbmate.setup")
        return self._player, self._partner

    def wire(self):
        if self.binding_id is None:
            raise ValueError("역할이 지정되지 않았어요.")
        return {"binding_id": self.binding_id, "scene_id": self.scene_id,
                "player_sim_id": int(self._player.sim_id), "partner_sim_id": int(self._partner.sim_id)}
