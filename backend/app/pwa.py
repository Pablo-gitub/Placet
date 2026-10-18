"""Cache policy for the PWA shell and Vite's content-versioned assets."""
from pathlib import Path
import re

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

REVALIDATE = 'no-cache, max-age=0, must-revalidate'
IMMUTABLE = 'public, max-age=31536000, immutable'
# Vite's generated names use an eight-character content hash before the suffix.
HASHED_ASSET = re.compile(r'-[A-Za-z0-9_-]{8}\.[^/]+$')


class VersionedAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers['Cache-Control'] = IMMUTABLE if HASHED_ASSET.search(path) else REVALIDATE
        return response


def mount_pwa(app, directory):
    root = Path(directory).resolve()
    if not root.exists():
        return
    if (root / 'assets').exists():
        app.mount('/assets', VersionedAssets(directory=root / 'assets'), name='assets')

    @app.get('/{full_path:path}')
    async def serve_pwa(full_path: str):
        if full_path == 'api' or full_path.startswith('api/'):
            raise HTTPException(status_code=404, detail='API route not found')
        candidate = (root / full_path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=404, detail='File not found')
        target = candidate if candidate.is_file() else root / 'index.html'
        # The entry point must never be reused without fetching the current build.
        policy = 'no-store' if target.suffix.lower() == '.html' else REVALIDATE
        return FileResponse(target, headers={'Cache-Control': policy})
