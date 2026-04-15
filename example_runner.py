import json
from tactical_match_engine.models.player_model import Player
from tactical_match_engine.models.club_model import Club
from tactical_match_engine.services.compatibility_service import CompatibilityService

# Load example data
with open("tactical_match_engine/data/players/player_example.json", "r") as f:
    player_data = json.load(f)
with open("tactical_match_engine/data/clubs/club_example.json", "r") as f:
    club_data = json.load(f)

player = Player(player_data)
club = Club(club_data)
service = CompatibilityService(player, club)

result = service.calculate_full_compatibility()

print(json.dumps(result, indent=2))
