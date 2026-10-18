import httpx
import pytest
from fastapi import FastAPI

from backend.app.pwa import IMMUTABLE, REVALIDATE, mount_pwa


@pytest.fixture
def pwa(tmp_path):
    root = tmp_path / 'dist'
    (root / 'assets').mkdir(parents=True)
    (root / 'index.html').write_text('build one')
    (root / 'manifest.json').write_text('{}')
    (root / 'icon-180.png').write_bytes(b'icon')
    (root / 'assets' / 'index-Ab12_X-y.js').write_text('versioned')
    (root / 'assets' / 'plain.js').write_text('unversioned')
    app = FastAPI()
    mount_pwa(app, root)
    return app, root


@pytest.mark.asyncio
async def test_entry_points_always_fetch_current_build(pwa):
    app, root = pwa
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        for url in ('/', '/index.html', '/?v=20261018', '/session/example'):
            first = await client.get(url)
            assert first.status_code == 200
            assert first.headers['cache-control'] == 'no-store'
            (root / 'index.html').write_text('updated build')
            second = await client.get(url, headers={'If-None-Match': first.headers['etag']})
            assert second.status_code == 200
            assert second.text == 'updated build'
            assert second.headers['cache-control'] == 'no-store'
            (root / 'index.html').write_text('build one')


@pytest.mark.asyncio
async def test_versioned_assets_keep_cache_even_on_not_modified(pwa):
    app, _ = pwa
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        first = await client.get('/assets/index-Ab12_X-y.js')
        assert first.headers['cache-control'] == IMMUTABLE
        second = await client.get('/assets/index-Ab12_X-y.js', headers={'If-None-Match': first.headers['etag']})
        assert second.status_code == 304
        assert second.headers['cache-control'] == IMMUTABLE
        for url in ('/manifest.json', '/icon-180.png', '/assets/plain.js'):
            response = await client.get(url)
            assert response.status_code == 200
            assert response.headers['cache-control'] == REVALIDATE
        for url in ('/assets/missing-Ab12_X-y.js', '/api/missing', '/api'):
            assert (await client.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_outside_files_are_not_served(pwa, tmp_path):
    app, root = pwa
    (tmp_path / 'private.txt').write_text('private')
    (root / 'link.txt').symlink_to(tmp_path / 'private.txt')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        assert (await client.get('/link.txt')).status_code == 404
