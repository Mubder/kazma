"""Debug script: import the dashboard module and check globals."""
import asyncio
import sys

sys.path.insert(0, "/home/balfaris/kazma/kazma-ui")
sys.path.insert(0, "/home/balfaris/kazma/kazma-core")
sys.path.insert(0, "/home/balfaris/kazma/kazma-gateway")

from kazma_ui import dashboard

print(f"_checkpoint_manager: {dashboard._checkpoint_manager}")
print(f"_checkpoint_manager type: {type(dashboard._checkpoint_manager).__name__}")
print(f"_session_store: {dashboard._session_store}")
print(f"_tracer: {dashboard._tracer}")


async def test_list():
    if dashboard._checkpoint_manager is None:
        print("FAIL: _checkpoint_manager is None!")
        return
    cm = dashboard._checkpoint_manager
    saver = await cm._get_saver()
    print(f"saver type: {type(saver).__name__}")
    print(f"saver.conn: {hasattr(saver, 'conn')}")
    if hasattr(saver, "conn") and saver.conn:
        cur = await saver.conn.execute("SELECT COUNT(*) FROM checkpoints")
        row = await cur.fetchone()
        print(f"Direct checkpoint count: {row[0]}")
    results = await cm.list_checkpoints(limit=5)
    print(f"list_checkpoints returned: {len(results)}")


asyncio.run(test_list())
