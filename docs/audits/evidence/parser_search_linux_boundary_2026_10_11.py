"""Bounded real Linux parser/search qualification; --wsl reuses the isolated WSL runtime.

No package installs, public network requests, host settings or live stores.
Controlled resolver fixtures run the exact standalone product entry point.
"""
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRATCH = '/home/balfa/.cache/kazma-browser-qualification-494866d8d64245159c10ebab204aec84'


def wsl():
    sys.path.insert(0, str(REPO / 'kazma-core'))
    from kazma_core.security.child_env import tool_child_env
    from kazma_core.security.process_budget import run_bounded
    probe = '/mnt/g/GitHubRepos/kazma/docs/audits/evidence/' + Path(__file__).name
    result = run_bounded(['wsl.exe', '-d', 'Ubuntu', '-u', 'balfa', '--exec',
        '/usr/bin/timeout', '-k', '2', '40', SCRATCH + '/runtime/bin/python',
        '-I', probe, '/mnt/g/GitHubRepos/kazma/kazma-core', SCRATCH],
        env=tool_child_env(), timeout=45, output_bytes=32768, memory_bytes=1024**3)
    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    log = REPO / 'docs/audits/evidence/parser_search_linux_boundary_2026_10_11.log'
    log.write_text(output, encoding='utf-8')
    print(output, flush=True)
    return result.returncode


async def qualify(core, scratch):
    sys.path.insert(0, str(core))
    from kazma_core.security import process_budget as pb
    from kazma_core.web_acquire import parser, search_http, search_runtime, search_sdk

    receipts, children, loop_errors = [], [], []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: loop_errors.append(context))
    original = pb.run_bounded
    with tempfile.TemporaryDirectory(prefix='parser-search-', dir=scratch) as directory:
        root = Path(directory)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root)}
        request = dict(html='<h1>دليل</h1><pre>  x = 1\n\tprint(x)</pre>', url='https://docs.example/a?q=1',
            profile='knowledge', login_only=False, parent_pid=os.getpid(), lifetime=5)
        result = original([sys.executable, '-I', parser.__file__], env=env, input=json.dumps(request),
            timeout=6, memory_bytes=768*1024**2, output_bytes=4*1024**2, text=True)
        data = json.loads(result.stdout)
        assert result.returncode == 0 and 'دليل' in data['text'] and '  x = 1' in data['text']
        receipts.append({'probe': 'actual_arabic_knowledge_parser', 'passed': True})
        fixture = root / 'article.py'
        fixture.write_text('import time\ndef extract_article(*args):\n time.sleep(30)\n')
        program = ('import importlib.util,runpy\noriginal=importlib.util.spec_from_file_location\n'
            'def fixture(name,location,*args,**kwargs):\n'
            f' return original(name,{str(fixture)!r} if name=="_kazma_parser_html_extract" else location,*args,**kwargs)\n'
            'importlib.util.spec_from_file_location=fixture\n'
            f'runpy.run_path({parser.__file__!r},run_name="__main__")')
        request.update(profile='generic', lifetime=.25)
        result = original([sys.executable, '-I', '-c', program], env=env, input=json.dumps(request),
            timeout=3, memory_bytes=768*1024**2, output_bytes=4*1024**2, text=True)
        assert result.returncode == -signal.SIGALRM
        receipts.append({'probe': 'parser_independent_kernel_deadline', 'passed': True})

        request.update(profile='sitemap', lifetime=5,
            html='<urlset><url><loc>https://docs.example/guide.md?x=1&amp;x=2</loc></url></urlset>')
        result = original([sys.executable, '-I', parser.__file__], env=env, input=json.dumps(request),
            timeout=6, memory_bytes=768*1024**2, output_bytes=4*1024**2, text=True)
        data = json.loads(result.stdout)
        assert result.returncode == 0 and data['urls'] == ['https://docs.example/guide.md?x=1&x=2']
        receipts.append({'probe': 'actual_owned_xml_parser', 'passed': True})
        request['html'] = '<!DOCTYPE urlset [<!ENTITY x "bad">]><urlset/>'
        result = original([sys.executable, '-I', parser.__file__], env=env, input=json.dumps(request),
            timeout=6, memory_bytes=768*1024**2, output_bytes=4*1024**2, text=True)
        assert result.returncode == 0 and json.loads(result.stdout)['kind'] == 'invalid'
        receipts.append({'probe': 'xml_entity_refused', 'passed': True})
        xml_fixture = root / 'xml.py'
        xml_fixture.write_text('import time\nclass UnsafeXMLError(ValueError):pass\ndef parse_untrusted_xml(text):time.sleep(30)\n')
        xml_program = ('import runpy\noriginal=runpy.run_path\n'
            'def fixture(path,*args,**kwargs):\n'
            f' return original({str(xml_fixture)!r} if str(path).endswith("/safe_xml.py") else path,*args,**kwargs)\n'
            'runpy.run_path=fixture\n'
            f'runpy.run_path({parser.__file__!r},run_name="__main__")')
        request.update(html='<urlset/>', lifetime=.25)
        result = original([sys.executable, '-I', '-c', xml_program], env=env, input=json.dumps(request),
            timeout=3, memory_bytes=768*1024**2, output_bytes=4*1024**2, text=True)
        assert result.returncode == -signal.SIGALRM
        receipts.append({'probe': 'xml_independent_kernel_deadline', 'passed': True})

        def network_program(body):
            return ('import httpx,runpy,time,socket\nOriginal=httpx.Client\n'
                'def client(**kwargs):\n'
                ' def response(request):\n'+body+'\n'
                ' return Original(**kwargs,transport=httpx.MockTransport(response))\n'
                'httpx.Client=client\n'
                f'runpy.run_path({search_http.__file__!r},run_name="__main__")')
        request = dict(backend='bing',query='fixture',count=1,base=None,proxy=None,parent_pid=os.getpid(),lifetime=5)
        good = network_program('  return httpx.Response(200,stream=httpx.ByteStream(b\'<li class="b_algo"><h2><a href="https://docs.example/">Title</a></h2></li>\'))')
        result = original([sys.executable,'-I','-c',good],env=env,input=json.dumps(request),
            timeout=6,memory_bytes=512*1024**2,output_bytes=512*1024,text=True)
        assert result.returncode == 0 and json.loads(result.stdout)['results'][0]['title'] == 'Title', result.stderr
        receipts.append({'probe':'actual_bing_http_parser','passed':True})
        bad_dns = ('import runpy,socket,time\nsocket.getaddrinfo=lambda *a,**k:time.sleep(30)\n'
            f'runpy.run_path({search_http.__file__!r},run_name="__main__")')
        request['lifetime'] = .5
        result = original([sys.executable,'-I','-c',bad_dns],env=env,input=json.dumps(request),
            timeout=3,memory_bytes=512*1024**2,output_bytes=512*1024,text=True)
        assert result.returncode == -signal.SIGALRM
        receipts.append({'probe':'search_native_dns_kernel_deadline','passed':True})
        request.update(lifetime=5,backend='searxng',base='http://localhost:8088')
        excess = network_program('  return httpx.Response(200,stream=httpx.ByteStream(b"x"*(1024**2+1)))')
        result = original([sys.executable,'-I','-c',excess],env=env,input=json.dumps(request),
            timeout=6,memory_bytes=512*1024**2,output_bytes=512*1024,text=True)
        assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'limit'
        receipts.append({'probe':'search_wire_budget','passed':True})
        sdk_module = root / 'duckduckgo_search.py'
        sdk_module.write_text('import time\nclass DDGS:\n def __init__(self,**kw):time.sleep(30)\n')
        sdk_program = f'import sys,runpy;sys.path.insert(0,{str(root)!r});runpy.run_path({search_sdk.__file__!r},run_name="__main__")'
        result = original([sys.executable,'-I','-c',sdk_program],env=env,
            input=json.dumps(dict(query='fixture',max_results=1,proxy=None,parent_pid=os.getpid(),lifetime=.25)),
            timeout=3,memory_bytes=512*1024**2,output_bytes=256*1024,text=True)
        assert result.returncode == -signal.SIGALRM
        receipts.append({'probe':'sdk_independent_kernel_deadline','passed':True})
        marker = root / 'ready'
        blocked = network_program(f'  open({str(marker)!r},"w").close();time.sleep(30)\n  return httpx.Response(200)')
        def run(command, **kwargs):
            attach = kwargs.get('on_started')
            def started(proc, tree, threads):
                children.append((proc,tree,threads))
                if attach:attach(proc,tree,threads)
            kwargs['on_started'] = started
            return original([sys.executable,'-I','-c',blocked],**kwargs)
        pb.run_bounded = run
        try:
            def search(query,count):
                return search_http.run_http_search('bing',query,count)
            task = asyncio.create_task(search_runtime.run_search(search,'fixture',1))
            try:
                async with asyncio.timeout(5):
                    while not marker.exists():
                        assert not task.done(), repr(task.exception())
                        await asyncio.sleep(.01)
                    task.cancel()
                    asyncio.get_running_loop().call_soon(task.cancel)
                    try:await task
                    except asyncio.CancelledError:pass
                    else:raise AssertionError('Cancellation lost')
                assert all(tree.closed and proc.returncode is not None and
                    not any(thread.is_alive() for thread in threads) for proc,tree,threads in children)
                receipts.append({'probe':'search_repeated_cancel_joins_real_child','passed':True})
            finally:
                if not task.done():
                    task.cancel();await asyncio.gather(task,return_exceptions=True)
        finally:
            pb.run_bounded = original
            assert search_runtime._OWNERS.drain(3)
        await asyncio.sleep(0)
        assert not loop_errors, repr(loop_errors)
        print(json.dumps({'platform':sys.platform,'python':sys.version.split()[0], 'passed':len(receipts),'receipts':receipts},indent=2))


if __name__ == '__main__':
    if '--wsl' in sys.argv:
        raise SystemExit(wsl())
    asyncio.run(qualify(Path(sys.argv[1]), Path(sys.argv[2])))
