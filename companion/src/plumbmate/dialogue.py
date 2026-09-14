"""Explicit player/partner roles, separate from selected-Sim transport state."""

from .protocol import ProtocolError, validate_request


class RoleSetupRequired(ProtocolError):
    pass


def dialogue_context(request: dict) -> dict:
    validate_request(request)
    roles = request.get("roles")
    if roles is None:
        raise RoleSetupRequired("Choose separate player and AI partner Sims before chatting")
    player, partner = int(roles["player_sim_id"]), int(roles["partner_sim_id"])
    if not (0 < player < 2**64 and 0 < partner < 2**64) or player == partner:
        raise RoleSetupRequired("Player and partner must be two different valid Sims")
    members = {}
    for member in request.get("household", []):
        ident = int(member["sim_id"])
        if ident in members:
            raise RoleSetupRequired("Household identities are ambiguous")
        members[ident] = member
    if player not in members or partner not in members or int(request["actor"]["sim_id"]) != player:
        raise RoleSetupRequired("Configured roles do not match this household snapshot")
    return {"player": request["actor"], "partner": members[partner],
            "player_message": request["player_message"], "recent_events": request.get("recent_events", [])}
