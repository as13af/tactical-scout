"""
app2.py — Template Testing Harness

Minimal Flask app that renders all templates without data operations.
No MongoDB, no file reads, no backend logic — just pure template testing.

Run with: python app2.py
Access: http://localhost:5000/
"""

import sys
import os
import logging

# ── Logging Setup ──────────────────────────────────────────────────────────────
_LOG_FORMAT = '%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s | %(message)s'
_LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_root_logger.addHandler(_console_handler)

_file_handler = logging.FileHandler(os.path.join(_LOG_DIR, 'app2.log'), encoding='utf-8')
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_root_logger.addHandler(_file_handler)

_logger = logging.getLogger(__name__)
_logger.info("app2.py — Template Testing Harness initialized")

# ── Flask Setup ────────────────────────────────────────────────────────────────
from flask import Flask, render_template, request, jsonify

_WEBAPP_DIR = os.path.dirname(__file__)

app = Flask(
    __name__,
    template_folder=os.path.join(_WEBAPP_DIR, 'templates'),
    static_folder=os.path.join(_WEBAPP_DIR, 'static'),
)

# ── HTTP Request/Response Logging ──────────────────────────────────────────────
import time as _timing

@app.before_request
def _log_request():
    request.start_time = _timing.time()
    _logger.info(f"[REQ] {request.method} {request.path}")

@app.after_request
def _log_response(response):
    elapsed = _timing.time() - getattr(request, 'start_time', _timing.time())
    _logger.info(f"[RES] {response.status_code} ({elapsed:.3f}s)")
    return response

# ── Dummy Data Generators ──────────────────────────────────────────────────────

def _dummy_competition_data():
    """Minimal competition data for rendering"""
    return {
        'competition': 'Premier League',
        'country': 'England',
        'clubs': [
            {'slug': 'manchester_united', 'name': 'Manchester United', 'player_count': 25},
            {'slug': 'liverpool', 'name': 'Liverpool', 'player_count': 23},
            {'slug': 'manchester_city', 'name': 'Manchester City', 'player_count': 24},
        ]
    }

def _dummy_club_data():
    """Minimal club data for rendering"""
    return {
        'team_name': 'Manchester United',
        '_club': 'manchester_united',
        'season_statistics': {'statistics': {'matches': 32, 'wins': 20}},
        'profile': {'team': {'teamColors': {'primary': '#DA291C', 'secondary': '#FFF'}}}
    }

def _dummy_player_data():
    """Minimal player data for rendering"""
    return {
        'player_name': 'Test Player',
        'player_id': '123456',
        'position': 'CM',
        'profile': {
            'player': {
                'positionsDetailed': ['CM', 'CAM'],
                'name': 'Test Player',
                'height': 183,
                'weight': 78,
                'preferredFoot': 'Right',
                'age': 26
            }
        },
        'statistics': {
            'statistics': {
                'appearances': 28,
                'goals': 3,
                'assists': 5,
                'minutesPlayed': 2340,
                'rating': 7.2,
                'keyPasses': 42
            }
        }
    }

def _dummy_players_list():
    """List of players for table rendering"""
    return [
        {
            'name': 'Player One',
            'player_id': '1',
            'team': 'Team A',
            'position': 'ST',
            'age': 28,
            'stats': {'appearances': 25, 'goals': 12, 'assists': 3}
        },
        {
            'name': 'Player Two',
            'player_id': '2',
            'team': 'Team B',
            'position': 'CM',
            'age': 25,
            'stats': {'appearances': 22, 'goals': 2, 'assists': 8}
        },
    ]

def _dummy_match_data():
    """Match data for rendering"""
    return {
        'event_id': 123456,
        'homeTeam': {'name': 'Team A', 'score': 2},
        'awayTeam': {'name': 'Team B', 'score': 1},
        'status': 'finished',
        'startTimestamp': 1717530000,
        'events': [
            {'id': 1, 'type': 'goal', 'minute': 15, 'player': 'Player One'},
            {'id': 2, 'type': 'goal', 'minute': 45, 'player': 'Player Two'},
        ]
    }

# ── Routes: All Templates ──────────────────────────────────────────────────────

@app.route('/')
def home():
    """Home page"""
    stats = {
        'competitions': 6,
        'clubs': 47,
        'players': 815,
        'top_players': []
    }
    _logger.debug("Rendering home.html")
    return render_template('home.html', stats=stats)

@app.route('/competitions')
def competitions():
    """All competitions"""
    competitions_list = [
        {'country': 'England', 'competition': 'Premier League', 'club_count': 20},
        {'country': 'Spain', 'competition': 'La Liga', 'club_count': 20},
        {'country': 'Italy', 'competition': 'Serie A', 'club_count': 20},
    ]
    _logger.debug("Rendering index.html")
    return render_template('index.html', competitions=competitions_list)

@app.route('/competition/<country>/<competition>')
def competition(country, competition):
    """Single competition"""
    data = _dummy_competition_data()
    _logger.debug(f"Rendering competition.html for {country}/{competition}")
    return render_template('competition.html', data=data, country=country,
                         competition=competition, cvs_score=85.5)

@app.route('/competition/<country>/<competition>/<club>')
def club(country, competition, club):
    """Single club"""
    club_data = _dummy_club_data()
    players = _dummy_players_list()
    _logger.debug(f"Rendering club.html for {country}/{competition}/{club}")
    return render_template('club.html', data=club_data, players=players,
                         country=country, competition=competition, club=club)

@app.route('/player/<country>/<competition>/<club>/<player_file>')
def player(country, competition, club, player_file):
    """Single player profile"""
    player_data = _dummy_player_data()
    _logger.debug(f"Rendering player.html")
    return render_template('player.html', player=player_data,
                         country=country, competition=competition, club=club)

@app.route('/player_matches/<int:player_id>')
def player_matches(player_id):
    """Player match history"""
    appearances = [
        {'date': '2026-06-01', 'opponent': 'Team X', 'minutes': 90, 'rating': 7.5},
        {'date': '2026-05-25', 'opponent': 'Team Y', 'minutes': 75, 'rating': 7.2},
    ]
    per90_data = {}
    _logger.debug(f"Rendering player_match_history.html")
    return render_template('player_match_history.html',
                         player_id=player_id, player_name='Test Player',
                         appearances=appearances, per90=per90_data,
                         profile_path='England/Premier League/manchester_united/player_123')

@app.route('/match/<int:event_id>')
def match_detail(event_id):
    """Match detail"""
    data = _dummy_match_data()
    _logger.debug(f"Rendering match_detail.html")
    return render_template('match_detail.html', match=data, stat_meta={})

@app.route('/matches')
def matches():
    """All matches"""
    matches_list = [
        {'event_id': 1, 'homeTeam': {'name': 'Team A'}, 'awayTeam': {'name': 'Team B'}, 'score': '2-1'},
        {'event_id': 2, 'homeTeam': {'name': 'Team C'}, 'awayTeam': {'name': 'Team D'}, 'score': '1-0'},
    ]
    _logger.debug("Rendering matches.html")
    return render_template('matches.html', matches=matches_list)

@app.route('/compare')
def compare():
    """Player comparison"""
    _logger.debug("Rendering compare.html")
    return render_template('compare.html', players=[], scatter_data=[])

@app.route('/compatibility')
def compatibility():
    """Compatibility scoring"""
    _logger.debug("Rendering compatibility.html")
    return render_template('compatibility.html')

@app.route('/club_compatibility')
def club_compatibility():
    """Club compatibility"""
    _logger.debug("Rendering club_compatibility.html")
    return render_template('club_compatibility.html', result={})

@app.route('/scatter')
def scatter():
    """Scatter plot visualization"""
    _logger.debug("Rendering scatter.html")
    return render_template('scatter.html', scatter_data=[], labels={})

@app.route('/player_scatter')
def player_scatter():
    """Player scatter plot"""
    _logger.debug("Rendering player_scatter.html")
    return render_template('player_scatter.html', scatter_data=[], labels={})

@app.route('/evolution')
def evolution():
    """Evolution/growth visualization"""
    _logger.debug("Rendering evolution.html")
    return render_template('evolution.html', evolution_data={})

@app.route('/export')
def export():
    """Export tools"""
    _logger.debug("Rendering export.html")
    return render_template('export.html')

@app.route('/league_rankings')
def league_rankings():
    """League rankings"""
    _logger.debug("Rendering league_rankings.html")
    return render_template('league_rankings.html')

@app.route('/team_playing_style')
def team_playing_style():
    """Team playing style analysis"""
    _logger.debug("Rendering team_playing_style.html")
    return render_template('team_playing_style.html', result={})

@app.route('/batch_compare')
def batch_compare():
    """Batch player comparison"""
    _logger.debug("Rendering batch_compare.html")
    return render_template('batch_compare.html', results=[])

# ── API Stubs (return empty data) ──────────────────────────────────────────────

@app.route('/api/compatibility')
def api_compatibility():
    """Stub: compatibility API"""
    return jsonify({'score': 0.75, 'details': {}})

@app.route('/api/club_compatibility')
def api_club_compatibility():
    """Stub: club compatibility API"""
    return jsonify({'overall_score': 0.80, 'scores': {}})

@app.route('/api/scatter_data')
def api_scatter_data():
    """Stub: scatter data API"""
    return jsonify([])

@app.route('/api/player_scatter_data')
def api_player_scatter_data():
    """Stub: player scatter data API"""
    return jsonify([])

# ── Error Handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    _logger.warning(f"404 Not Found: {request.path}")
    return jsonify({'error': 'Template not found'}), 404

@app.errorhandler(500)
def server_error(error):
    _logger.error(f"500 Server Error: {error}", exc_info=True)
    return jsonify({'error': 'Server error'}), 500

# ── Main ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    _logger.info("Starting app2.py template tester on http://localhost:5000")
    _logger.info("Navigate to / or any route above to test templates")
    app.run(debug=True, host='localhost', port=5000)
