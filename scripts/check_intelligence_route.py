import os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")

from src.api.app import app

target = "/api/entities/{entity_id}/intelligence"
matches = [r.path for r in app.routes if getattr(r, "path", None) == target]

print(f"Total routes: {len(app.routes)}")
print(f"Intelligence route found: {bool(matches)}")
for r in app.routes:
    if "intelligence" in getattr(r, "path", ""):
        print(f"  {r.path}  methods={getattr(r, 'methods', None)}")

assert matches, "Intelligence route not registered!"
print("OK: route registered correctly")
