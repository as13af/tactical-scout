"""
Test routes restored in the MongoDB re-enable commit.

Tests run with _USING_MONGO = False to avoid requiring a live MongoDB connection.
Each test monkeypatches the data layer and verifies expected behavior.
"""

import pytest
import sys
import os
from pathlib import Path

# Add webapp root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import app


@pytest.fixture
def client():
    """Flask test client with app.app.testing = True."""
    app.app.config['TESTING'] = True
    with app.app.test_client() as client:
        yield client


@pytest.fixture
def no_mongo(monkeypatch):
    """Monkeypatch _USING_MONGO to False."""
    monkeypatch.setattr(app, '_USING_MONGO', False)


# ── Startup / Initialization Tests ──────────────────────────────────────────

def test_app_imports():
    """App should import without crashing."""
    assert app._loader is not None
    assert hasattr(app, '_USING_MONGO')


# ── POST /api/form_refresh ──────────────────────────────────────────────────

def test_form_refresh_requires_body(client, no_mongo):
    """POST /api/form_refresh with empty body should return 400."""
    resp = client.post('/api/form_refresh', json={})
    assert resp.status_code == 400
    assert 'required' in resp.get_json()['error'].lower()


def test_form_refresh_no_mongo(client, no_mongo):
    """POST /api/form_refresh without MongoDB should return 400."""
    resp = client.post('/api/form_refresh', json={'uniq_tid': '123', 'season_id': '456'})
    assert resp.status_code == 400
    assert 'MongoDB' in resp.get_json()['error']


# ── POST /api/optimize_league ───────────────────────────────────────────────

def test_optimize_league_requires_uniq_tid(client, no_mongo):
    """POST /api/optimize_league with missing uniq_tid should return 400."""
    resp = client.post('/api/optimize_league', json={})
    assert resp.status_code == 400


def test_optimize_league_no_mongo(client, no_mongo):
    """POST /api/optimize_league without MongoDB should return 400."""
    resp = client.post('/api/optimize_league', json={'uniq_tid': '123'})
    assert resp.status_code == 400
    assert 'MongoDB' in resp.get_json()['error']


# ── POST /api/purge_league ─────────────────────────────────────────────────

def test_purge_league_requires_uniq_tid(client, no_mongo):
    """POST /api/purge_league with missing uniq_tid should return 400."""
    resp = client.post('/api/purge_league', json={})
    assert resp.status_code == 400


def test_purge_league_no_mongo(client, no_mongo):
    """POST /api/purge_league without MongoDB should return 400."""
    resp = client.post('/api/purge_league', json={'uniq_tid': '123'})
    assert resp.status_code == 400
    assert 'MongoDB' in resp.get_json()['error']


# ── POST /api/purge_team ───────────────────────────────────────────────────

def test_purge_team_requires_both_ids(client, no_mongo):
    """POST /api/purge_team with missing team_id returns 404 when no MongoDB."""
    resp = client.post('/api/purge_team', json={'uniq_tid': '123'})
    assert resp.status_code == 404


def test_purge_team_no_mongo(client, no_mongo):
    """POST /api/purge_team without MongoDB should return 404."""
    resp = client.post('/api/purge_team', json={'team_id': '123', 'uniq_tid': '456'})
    assert resp.status_code == 404
    assert 'not available' in resp.get_json()['error']


# ── GET /api/playing_style_data ─────────────────────────────────────────────

def test_playing_style_data_requires_params(client, no_mongo):
    """GET /api/playing_style_data without params should return 400."""
    resp = client.get('/api/playing_style_data')
    assert resp.status_code == 400
    assert 'required' in resp.get_json()['error'].lower()


def test_playing_style_data_missing_competition(client, no_mongo):
    """GET /api/playing_style_data with missing competition should return 400."""
    resp = client.get('/api/playing_style_data?country=TestCountry')
    assert resp.status_code == 400


def test_playing_style_data_no_dir(client, no_mongo):
    """GET /api/playing_style_data with non-existent league should return 404."""
    resp = client.get('/api/playing_style_data?country=Nonexistent&competition=Fake_League')
    assert resp.status_code == 404
    assert 'not found' in resp.get_json()['error'].lower()


# ── GET /api/passmap ───────────────────────────────────────────────────────

def test_passmap_no_mongo(client, no_mongo):
    """GET /api/passmap without MongoDB should return 204 (no data)."""
    resp = client.get('/api/passmap/X/Y/Z')
    assert resp.status_code == 204


# ── GET /api/club_avg_heatmap ──────────────────────────────────────────────

def test_club_avg_heatmap_meta_no_mongo(client, no_mongo):
    """GET /api/club_avg_heatmap without MongoDB should return 404."""
    resp = client.get('/api/club_avg_heatmap/A/B/C/D')
    assert resp.status_code == 404


def test_club_avg_heatmap_png_no_mongo(client, no_mongo):
    """GET /api/club_avg_heatmap_png without MongoDB should return 404."""
    resp = client.get('/api/club_avg_heatmap_png/A/B/C/D')
    assert resp.status_code == 404


def test_club_avg_heatmap_zones_png_no_mongo(client, no_mongo):
    """GET /api/club_avg_heatmap_zones_png without MongoDB should return 404."""
    resp = client.get('/api/club_avg_heatmap_zones_png/A/B/C/D')
    assert resp.status_code == 404


# ── GET /heatmap_db ────────────────────────────────────────────────────────

def test_heatmap_db_no_mongo(client, no_mongo):
    """GET /heatmap_db without MongoDB should return 404."""
    resp = client.get('/heatmap_db/A/B/C/stem')
    assert resp.status_code == 404


# ── GET /api/all_competition_ids ────────────────────────────────────────────

def test_all_competition_ids(client, no_mongo):
    """GET /api/all_competition_ids should return 200 with a JSON list."""
    resp = client.get('/api/all_competition_ids')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    # May be empty if no Standings_*.json files exist on disk


# ── GET /api/player_role ───────────────────────────────────────────────────

def test_player_role_requires_player(client, no_mongo):
    """GET /api/player_role without 'player' param should return 400."""
    resp = client.get('/api/player_role')
    assert resp.status_code == 400


def test_player_role_invalid_path(client, no_mongo):
    """GET /api/player_role with invalid path (too few parts) should return 400."""
    resp = client.get('/api/player_role?player=X/Y/Z')
    assert resp.status_code == 400
    assert 'invalid' in resp.get_json()['error'].lower()


def test_player_role_missing_file(client, no_mongo):
    """GET /api/player_role with non-existent file should return 404."""
    resp = client.get('/api/player_role?player=X/Y/Z/nonexistent')
    assert resp.status_code == 404
    assert 'not found' in resp.get_json()['error'].lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
