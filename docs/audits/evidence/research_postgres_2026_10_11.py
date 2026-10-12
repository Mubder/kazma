"""Disposable local Docker PostgreSQL qualification; never uses an install DSN."""
import os
import re
import time
import uuid
from pathlib import Path

from kazma_core.security.process_budget import run_bounded
from scripts.fast_test import run_pytest

HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'


def main():
    environment = {key: value for key, value in os.environ.items()
                   if key.lower() in {'path', 'systemroot', 'windir', 'temp', 'tmp', 'userprofile', 'appdata', 'localappdata'}}
    def docker(*args, check=True):
        result = run_bounded(['docker', '--host', HOST, *args], env=environment,
            timeout=30, output_bytes=32768, memory_bytes=512 * 1024**2, text=True)
        if check and result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        return result.stdout.strip() if check else result
    container, code, output = None, 1, ''
    try:
        container = docker('run', '--rm', '-d', '--pull=never', '--name', 'kazma-research-audit-' + uuid.uuid4().hex[:12],
            '--label', 'kazma.audit=research-durability', '--cpus', '1', '--memory', '512m', '--pids-limit', '128',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=kazma_fixture',
            '-p', '127.0.0.1::5432', 'postgres:16')
        assert re.fullmatch('[a-f0-9]{64}', container)
        port = docker('port', container, '5432/tcp')
        assert re.fullmatch(r'127\.0\.0\.1:\d+', port), port
        for _ in range(40):
            if docker('exec', container, 'pg_isready', '-U', 'postgres', '-d', 'kazma_fixture', check=False).returncode == 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Owned disposable PostgreSQL did not become ready')
        os.environ.update(KAZMA_TEST_ALLOW_REAL_DB='1', KAZMA_DB_BACKEND='postgres',
                          KAZMA_DATABASE_URL='postgresql://postgres@' + port + '/kazma_fixture')
        code, output = run_pytest(['tests/test_acquisition_postgres.py', '-q', '--tb=short', '--timeout=120'], 240)
    finally:
        if container and re.fullmatch('[a-f0-9]{64}', container):
            docker('rm', '-f', container)
        Path('docs/audits/evidence/research_durability_postgres_2026_10_11.log').write_text(
            output + '\nOwned ephemeral PostgreSQL container removed.\n', encoding='utf-8')
    print(output[-7000:])
    return code


if __name__ == '__main__':
    raise SystemExit(main())
