"""Deep-research stages on the common crawl CAS journal; no body in settings.

A running effect with no durable completion is uncertain after interruption.
Only completed, hash-verified results or never-started work may be used again.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from kazma_core.playwright_loop import finish_owned
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.policy import acquisition_policy
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

pipeline_observer = ContextVar('research_pipeline_observer', default=None)

_MAX_EFFECTS = 128
_MAX_VALUE = 512 * 1024
_MAX_FILE = 24 * 1024**2
_MAX_STORAGE = 512 * 1024**2
_ENV = ('KAZMA_RESEARCH_ALLOW_THIN', 'KAZMA_RESEARCH_GAP_LOOP',
        'KAZMA_RESEARCH_LLM_PLANNER', 'KAZMA_RESEARCH_EXPORT_DOCX',
        'KAZMA_RESEARCH_DIGEST_MAX', 'KAZMA_RESEARCH_SYNTH_MAX_IN',
        'KAZMA_CRAWL_RESPECT_ROBOTS', 'KAZMA_SEARXNG_URL', 'KAZMA_LITELLM',
        'KAZMA_LITELLM_URL', 'KAZMA_LITELLM_KEY', 'LITELLM_MASTER_KEY',
        'KAZMA_LITELLM_LOCAL', 'KAZMA_LITELLM_FALLBACK_DIRECT',
        'KAZMA_BROWSER_NETWORK_MODE', 'KAZMA_JINA_TOKEN_BUDGET',
        'KAZMA_ACQUISITION_MAX_COST_USD')


class UncertainEffect(AcquisitionError):
    def __init__(self, key, effect):
        super().__init__(FetchErrorCode.POLICY,
            f'Research stage {key} has an uncertain outcome; automatic replay refused.')
        self.effect = effect


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False).encode()).hexdigest()


def _scope_signature():
    from kazma_core.model_registry import get_model_registry
    from kazma_core.config_store import get_config_store

    # Persist digests only: endpoints/settings can contain credentials.
    store = get_config_store()
    return _fingerprint(dict(environment={key: os.environ.get(key) for key in _ENV},
        model=get_model_registry().get_active_profile(),
        credentials={key: value for key, value in os.environ.items()
                     if key.endswith('_API_KEY') or key.startswith(('AWS_', 'GOOGLE_', 'AZURE_')) or key in {'KAZMA_MODEL', 'KAZMA_PROVIDER'}},
        configured=dict(providers=[entry for entry in (store.get('providers.list') or []) if isinstance(entry, dict)],
            legacy_key=store.get('llm.api_key'), gateway=store.get('llm.gateway'), nonstop=store.get('agent.nonstop')),
        proxy=store.get('proxy', {}) if acquisition_policy().permits('proxy') else None))


def valid(row):
    policy, effects = row.get('policy', {}), row.get('effects', {})
    if (policy.get('schema') != 1 or not isinstance(policy.get('workspace'), str)
            or not isinstance(policy.get('output'), str)
            or not policy['output'].startswith('research/reports/pipeline-')
            or '..' in policy['output'].replace('\\', '/').split('/')
            or not isinstance(effects, dict) or len(effects) > _MAX_EFFECTS):
        return False
    for key, effect in effects.items():
        if (not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(effect, dict)
                or effect.get('status') not in {'running', 'completed', 'failed', 'ambiguous'}
                or not isinstance(effect.get('input'), str) or len(effect['input']) != 64):
            return False
        if (not isinstance(effect.get('files', []), list)
                or type(effect.get('reserved_bytes')) is not int
                or not 0 <= effect['reserved_bytes'] <= _MAX_STORAGE):
            return False
        if 'outcome_ok' in effect and type(effect['outcome_ok']) is not bool:
            return False
        refs = effect.get('files', []) + ([effect['value']] if effect.get('value') else [])
        if len(refs) > 5:
            return False
        for ref in refs:
            if (not isinstance(ref, dict) or not isinstance(ref.get('path'), str)
                    or len(ref['path']) > 1024 or type(ref.get('size')) is not int
                    or not 0 <= ref['size'] <= _MAX_FILE
                    or not isinstance(ref.get('sha256'), str) or len(ref['sha256']) != 64):
                return False
        if effect.get('status') == 'completed' and not effect.get('value'):
            return False
        if len(json.dumps(effect, ensure_ascii=True)) > 24 * 1024:
            return False
    return sum(effect['reserved_bytes'] for effect in effects.values()) <= _MAX_STORAGE


def settle_unknown(row):
    for effect in row.get('effects', {}).values():
        if effect['status'] == 'running':
            effect.update(status='ambiguous', error='Execution interrupted; outcome uncertain. Automatic replay refused.')


async def gather_owned(*awaitables):
    tasks = [asyncio.create_task(work) for work in awaitables]
    receipt = asyncio.gather(*tasks, return_exceptions=True)
    try:
        results = await asyncio.shield(receipt)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await finish_owned(receipt)
        raise
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


class _PipelineJob:
    def __init__(self, job, root, directory, signature):
        self.job, self.root, self.directory = job, root, directory
        self.signature, self.finished = signature, False
        self.ambiguous_sources = set()

    def checked(self, path):
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        target = target.resolve()
        if not target.is_relative_to(self.directory.resolve()) or not target.is_relative_to(self.root.resolve()):
            raise AcquisitionError(FetchErrorCode.WORKSPACE, 'Research artifact left its frozen output directory; resume refused.')
        return target

    def _read_artifact(self, path, *, value=False):
        target = self.checked(path)
        digest, size = hashlib.sha256(), 0
        limit = _MAX_VALUE if value else _MAX_FILE
        chunks = [] if value else None
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(target, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise AcquisitionError(FetchErrorCode.STORAGE, 'Research artifact is not a regular file; restore refused.')
            stream = os.fdopen(descriptor, 'rb')
            descriptor = None  # stream now owns the descriptor
            with stream:
                while block := stream.read(64 * 1024):
                    size += len(block)
                    if size > limit:
                        raise AcquisitionError(FetchErrorCode.LIMIT, 'Research artifact exceeds its read budget.')
                    digest.update(block)
                    if chunks is not None:
                        chunks.append(block)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        ref = dict(path=str(target.relative_to(self.root)).replace('\\', '/'), size=size, sha256=digest.hexdigest())
        return ref, b''.join(chunks) if chunks is not None else None

    def file_ref(self, path):
        return self._read_artifact(path)[0]

    def restore(self, effect):
        try:
            for ref in effect.get('files', []):
                if self.file_ref(ref['path']) != ref:
                    raise AcquisitionError(FetchErrorCode.STORAGE, 'A completed research artifact changed or is missing; automatic replay refused.')
            ref, payload = self._read_artifact(effect['value']['path'], value=True)
            if ref != effect['value']:
                raise AcquisitionError(FetchErrorCode.STORAGE, 'A completed research artifact changed or is missing; automatic replay refused.')
            # Decode the same bounded bytes that were verified; never reopen.
            return json.loads(payload.decode('utf-8'))
        except (OSError, ValueError) as exc:
            raise AcquisitionError(FetchErrorCode.STORAGE,
                'A completed research artifact changed or is missing; automatic replay refused.') from exc

    async def effect(self, key, inputs, action, *, encode=lambda value: value,
                     decode=lambda value: value, files=lambda value: [], reserve_bytes=1024**2):
        identity = _fingerprint(inputs)
        def begin(row):
            if _scope_signature() != self.signature:
                raise AcquisitionError(FetchErrorCode.POLICY, 'Research configuration changed; further stage dispatch refused.')
            effects = row.setdefault('effects', {})
            current = effects.get(key)
            if current is not None:
                if current['input'] != identity:
                    raise AcquisitionError(FetchErrorCode.POLICY, 'Research stage inputs changed; resume refused.')
                return current
            if len(effects) >= _MAX_EFFECTS:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Research stage journal reached its 128-effect budget.')
            if sum(effect.get('reserved_bytes', 0) for effect in effects.values()) + reserve_bytes > _MAX_STORAGE:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Research job retained-artifact allowance exhausted before dispatch.')
            effects[key] = dict(status='running', input=identity, reserved_bytes=reserve_bytes)
            row['progress'].update(phase=key, message=f'Research stage: {key}')
            return None
        previous = await crawl_jobs.call(self.job.update, begin)
        if previous is not None:
            if previous['status'] == 'ambiguous':
                raise UncertainEffect(key, previous)
            if previous['status'] != 'completed':
                raise AcquisitionError(FetchErrorCode.POLICY,
                    f'Research stage {key} is {previous["status"]}; automatic replay refused. Inspect the durable job before starting a new run.')
            return decode(await crawl_jobs.call(self.restore, previous))
        try:
            result = await action()
            payload = json.dumps(encode(result), ensure_ascii=False, allow_nan=False)
            if len(payload.encode('utf-8')) > _MAX_VALUE:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Research stage result exceeds its 512KiB budget.')
            def commit():
                from kazma_core.tools.research_files import create_extract

                refs = [self.file_ref(path) for path in files(result)]
                destination = self.checked(self.directory / '.effects' / (hashlib.sha256(key.encode()).hexdigest() + '.json'))
                create_extract(destination, payload)
                value_ref = self.file_ref(destination)
                def complete(row):
                    total = sum(ref['size'] for effect in row.get('effects', {}).values()
                                for ref in effect.get('files', []) + ([effect['value']] if effect.get('value') else []))
                    if total + value_ref['size'] + sum(ref['size'] for ref in refs) > _MAX_STORAGE:
                        raise AcquisitionError(FetchErrorCode.LIMIT, 'Research job reached its 512MiB retained-artifact budget.')
                    used = value_ref['size'] + sum(ref['size'] for ref in refs)
                    if used > row['effects'][key]['reserved_bytes']:
                        raise AcquisitionError(FetchErrorCode.LIMIT, 'Research stage exceeded its reserved artifact budget.')
                    row['effects'][key].update(status='completed', value=value_ref, files=refs, reserved_bytes=used)
                    if type(getattr(result, 'ok', None)) is bool:
                        row['effects'][key]['outcome_ok'] = result.ok
                self.job.update(complete)
            await crawl_jobs.call(commit)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # No arbitrary exception text (provider/file errors can hold secrets).
            def failed(row):
                if row['effects'][key]['status'] == 'running':
                    row['effects'][key].update(status='failed', error=type(exc).__name__)
            await crawl_jobs.call(self.job.update, failed)
            if isinstance(exc, (AcquisitionError, TimeoutError)):
                raise
            code = FetchErrorCode.STORAGE if isinstance(exc, OSError) else FetchErrorCode.INTERNAL
            raise AcquisitionError(code,
                f'Research stage {key} failed ({type(exc).__name__}); inspect the durable job. Automatic replay refused.') from exc

    async def receipt(self, key, result):
        data = result.evidence()
        data['attempts'] = data['attempts'][-8:]
        if len(json.dumps(data, ensure_ascii=True)) > 16 * 1024:
            data = dict(ok=result.ok, backend=result.backend, error_code=result.error_code, evidence_clipped=True)
        await crawl_jobs.call(self.job.update, lambda row: row['effects'][key].update(receipt=data))

    async def finish(self, status):
        def finish():
            self.job.finish(status)
            self.finished = True
        from kazma_core.web_acquire.artifact_storage import settle
        await settle(self.job, self.root, self.directory)
        await crawl_jobs.call(finish)


@asynccontextmanager
async def pipeline_job(topic, root, options, *, resume_job_id=None):
    root = Path(root).resolve()
    policy = acquisition_policy()
    signature = await crawl_jobs.call(_scope_signature)
    identity = resume_job_id or uuid.uuid4().hex
    previous = await crawl_jobs.call(crawl_jobs.inspect_jobs, identity) if resume_job_id else None
    output = previous['policy']['output'] if previous and previous.get('kind') == 'research_pipeline' else 'research/reports/pipeline-' + uuid.uuid4().hex
    frozen = dict(options, schema=1, workspace=str(root), output=output,
                  mode=policy.mode, providers=sorted(policy.providers), configuration=signature)
    job = await crawl_jobs.call(crawl_jobs.start,
        'research-pipeline:' + hashlib.sha256(str(root).encode()).hexdigest()[:32],
        topic, frozen, job_id=identity, resume=resume_job_id is not None,
        kind='research_pipeline', on_cancel=lambda owned: owned.finish('interrupted'))
    owned = _PipelineJob(job, root, root / output, signature)
    from kazma_core.web_acquire.research_llm import ResearchRoute, active_route

    route = ResearchRoute(lambda: _scope_signature() == signature)
    route_token = active_route.set(route)
    token = crawl_jobs.current_crawl.set(job)
    try:
        observer = pipeline_observer.get()
        if observer is not None:
            await observer(owned)
        from kazma_core.web_acquire.artifact_storage import reserve
        await reserve(job, root, owned.directory)
        yield owned
    finally:
        crawl_jobs.current_crawl.reset(token)
        active_route.reset(route_token)
        try:
            await finish_owned(route.close())
        finally:
            if not owned.finished:
                await finish_owned(crawl_jobs.call(job.finish, 'interrupted'))


def completed_summary(job_id, root):
    """Read a verified terminal result, with no resumption or provider dispatch."""
    row = crawl_jobs.inspect_jobs(job_id)
    if row is None or row.get('kind') != 'research_pipeline':
        raise AcquisitionError(FetchErrorCode.POLICY, 'Saved research job is no longer retained; automatic replay refused.')
    if Path(row['policy']['workspace']).resolve() != Path(root).resolve():
        raise AcquisitionError(FetchErrorCode.WORKSPACE, 'Switch to the original research workspace before recovery.')
    if row['status'] not in {'completed', 'partial', 'failed'}:
        return None
    effect = row.get('effects', {}).get('terminal-summary')
    if not effect or effect['status'] != 'completed':
        raise AcquisitionError(FetchErrorCode.POLICY, 'Terminal research result was not journaled; inspect the saved report without replaying effects.')
    view = _PipelineJob(None, Path(root), Path(root) / row['policy']['output'], '')
    result = view.restore(effect)
    if not isinstance(result, str):
        raise AcquisitionError(FetchErrorCode.STORAGE, 'Saved research summary is invalid.')
    return result + f'\n**Durable job:** `{job_id}`\n'
