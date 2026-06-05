# app2.py — Template Testing Harness

A minimal Flask application for testing all 20 HTML templates **without any backend data operations**.

## Quick Start

```bash
cd webapp
python app2.py
```

Then visit: **http://localhost:5000**

## Features

✅ **Zero Backend Logic**
- No MongoDB connections
- No file system reads
- No data loading or processing
- Pure template rendering

✅ **Dummy Data**
- Every route passes minimal dummy data
- Consistent structure with production app
- Enough data to test layout and styling

✅ **Full Logging**
- Logs all requests/responses to console
- File logging to `logs/app2.log`
- Same logging format as production

✅ **All 20 Templates Covered**

| Template | Route | Purpose |
|----------|-------|---------|
| `home.html` | `/` | Dashboard homepage |
| `index.html` | `/competitions` | League list |
| `competition.html` | `/competition/<country>/<comp>` | Single league |
| `club.html` | `/competition/<c>/<comp>/<club>` | Single club + players |
| `player.html` | `/player/<c>/<comp>/<club>/<file>` | Player profile |
| `player_match_history.html` | `/player_matches/<id>` | Player's match history |
| `match_detail.html` | `/match/<id>` | Single match |
| `matches.html` | `/matches` | All matches list |
| `compare.html` | `/compare` | Player comparison |
| `compatibility.html` | `/compatibility` | Compatibility tool |
| `club_compatibility.html` | `/club_compatibility` | Club fit analysis |
| `scatter.html` | `/scatter` | Scatter plot visualization |
| `player_scatter.html` | `/player_scatter` | Player scatter plot |
| `evolution.html` | `/evolution` | Growth/evolution chart |
| `export.html` | `/export` | Export tools |
| `league_rankings.html` | `/league_rankings` | League rankings |
| `team_playing_style.html` | `/team_playing_style` | Team style analysis |
| `batch_compare.html` | `/batch_compare` | Batch player comparison |
| `base.html` | (base template) | Layout/nav inherited by all |
| `players.html` | (referenced by other templates) | Player table partial |

## What Gets Logged

### Console (INFO level)
```
2026-06-05 10:10:25 [INFO    ] app2:_log_request | [REQ] GET /
2026-06-05 10:10:25 [INFO    ] app2:_log_response | [RES] 200 (0.005s)
2026-06-05 10:10:26 [INFO    ] app2:_log_request | [REQ] GET /competitions
2026-06-05 10:10:26 [INFO    ] app2:_log_response | [RES] 200 (0.003s)
```

### File `logs/app2.log` (DEBUG level)
```
2026-06-05 10:10:25 [DEBUG   ] app2:home | Rendering home.html
2026-06-05 10:10:25 [DEBUG   ] app2:_log_request | [REQ] GET /
2026-06-05 10:10:25 [DEBUG   ] app2:_log_response | [RES] 200 (0.005s)
```

## Testing Workflow

1. **Start app2.py**: `python app2.py`
2. **Visit http://localhost:5000** in your browser
3. **Click through all links** to test each template
4. **Check browser console** for any JavaScript errors
5. **Check logs** (console and `logs/app2.log`) for any Flask errors
6. **Verify styling** — all templates should render with full CSS
7. **Test responsive design** — resize browser to check mobile layout

## Dummy Data Structure

Each route provides minimal-but-valid data:

```python
# Competition
{
    'competition': 'Premier League',
    'country': 'England',
    'clubs': [...]
}

# Club
{
    'team_name': 'Manchester United',
    'season_statistics': {'statistics': {'matches': 32}},
    'profile': {'team': {...}}
}

# Player
{
    'player_name': 'Test Player',
    'position': 'CM',
    'profile': {'player': {...}},
    'statistics': {'statistics': {...}}
}
```

## Differences from app.py

| Aspect | app.py | app2.py |
|--------|--------|---------|
| MongoDB | Tries to connect | Skipped entirely |
| File reads | Scans output/ dir | None |
| Data loading | Real data | Dummy data |
| Backend logic | Full functionality | Stripped |
| Logging | Production logging | Same logging + debug |
| Purpose | Live app | Testing templates |
| Performance | Variable (I/O) | Lightning fast |

## Troubleshooting

**Template not rendering?**
- Check `logs/app2.log` for Jinja2 errors
- Verify template syntax in `templates/`

**CSS/JS not loading?**
- Ensure static files exist in `static/`
- Check Flask's static serving in console

**404 on a route?**
- Add the route to app2.py following the pattern above
- All routes return status 200 with rendered HTML

## When to Use

✅ **Use app2.py when:**
- Testing template HTML/CSS/JS
- Checking responsive design
- Debugging layout issues
- Verifying all templates exist
- No backend data needed

❌ **Use app.py when:**
- Testing actual data loading
- Testing MongoDB/file system
- Testing full application flow
- Testing API endpoints
- Testing with real data

## Development

To add a new route to app2.py:

```python
@app.route('/new_route')
def new_route():
    """Description of what this route does"""
    dummy_data = {'key': 'value'}
    _logger.debug("Rendering new_template.html")
    return render_template('new_template.html', data=dummy_data)
```

No need to change anything else — logging and error handling are built-in.
