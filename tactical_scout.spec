# tactical_scout.spec
# PyInstaller spec for the Tactical Scout standalone exe.
#
# Build command (run from the workspace root):
#   pyinstaller tactical_scout.spec
#
# Output: dist/Tactical Scout.exe  (single file, no console window)

import os
from PyInstaller.building.api import PYZ, EXE, COLLECT
from PyInstaller.building.build_main import Analysis

ROOT = os.path.abspath('.')

# ── Data files bundled inside the exe ────────────────────────────────────────
# Each entry: (source_path, destination_folder_inside_bundle)
datas = [
    # Flask templates + static
    (os.path.join(ROOT, 'Coding', 'webapp', 'templates'),
     'Coding/webapp/templates'),
    (os.path.join(ROOT, 'Coding', 'webapp', 'static'),
     'Coding/webapp/static'),

    # Scraped player / club data
    (os.path.join(ROOT, 'Coding', 'output'),
     'Coding/output'),

    # Match data (heatmaps, formations)
    (os.path.join(ROOT, 'Coding', 'match_output'),
     'Coding/match_output'),

    # League CVS rankings (Opta)
    (os.path.join(ROOT, 'Resources', 'Top_Rankings', 'Opta_League_CVS_2026.json'),
     'Resources/Top_Rankings'),
    (os.path.join(ROOT, 'Resources', 'Top_Rankings', 'IFFHS_League_CVS_Scored_2025.json'),
     'Resources/Top_Rankings'),

    # Role profile JSONs (Position Line)
    (os.path.join(ROOT, 'Resources', 'Position Line'),
     'Resources/Position Line'),
]

# ── Hidden imports PyInstaller may miss ──────────────────────────────────────
hiddenimports = [
    # Flask ecosystem
    'flask',
    'flask.templating',
    'jinja2',
    'jinja2.ext',
    'werkzeug',
    'werkzeug.serving',
    'werkzeug.debug',
    # Data
    'numpy',
    'scipy',
    'scipy.special',
    'scipy.special._ufuncs_cxx',
    'scipy.linalg.cython_blas',
    'scipy.linalg.cython_lapack',
    'scipy.sparse.csgraph._validation',
    # Office exports
    'openpyxl',
    'openpyxl.cell._writer',
    'openpyxl.styles.fills',
    'reportlab',
    'reportlab.pdfgen',
    # Tactical engine modules (non-package imports via dynamic loading)
    'tactical_match_engine',
    'tactical_match_engine.engine',
    'tactical_match_engine.engine.role_encoder',
    'tactical_match_engine.engine.normalization',
    'tactical_match_engine.engine.physical_adaptation',
    'tactical_match_engine.engine.explanation_generator',
    'tactical_match_engine.engine.contender_simulation',
    'tactical_match_engine.services',
    'tactical_match_engine.services.json_loader',
    'tactical_match_engine.services.pdf_generator',
    'tactical_match_engine.models',
    'tactical_match_engine.models.player_model',
    'tactical_match_engine.models.club_model',
    # Webapp helpers
    'data_loader',
    'match_loader',
    # Stdlib used with indirect imports
    'concurrent.futures',
    'csv',
    'io',
    'uuid',
    'threading',
    'glob',
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['launcher.py'],
    pathex=[
        ROOT,
        os.path.join(ROOT, 'Coding', 'webapp'),   # so PyInstaller finds app.py / data_loader.py
        os.path.join(ROOT, 'Coding'),              # so PyInstaller finds scraper_cli.py etc.
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Selenium / scraper stack — not needed in the viewer exe
        'selenium',
        'webdriver_manager',
        # Heavy GUI / plotting not used at runtime
        'matplotlib',
        'customtkinter',
        'tkinter',
        # Test tools
        'pytest',
        'unittest',
    ],
    noarchive=False,
    optimize=0,
)

# ── Package into archive ──────────────────────────────────────────────────────
pyz = PYZ(a.pure)

# ── Single-file exe ───────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Tactical Scout',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # no black CMD window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='Resources/icon.ico',  # uncomment and add an .ico file if desired
)
