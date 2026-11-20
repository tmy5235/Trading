import json
from types import SimpleNamespace
from agents.tool_context import ToolContext
from backend import watchlist

async def fake_run(agent, prompt, *, hooks, **kwargs):
    await hooks.on_llm_start(None,agent,None,[])
    async def call(tool):
        return await tool.on_invoke_tool(ToolContext(context=None,tool_name=tool.name,tool_call_id='fixture',tool_arguments='{}'), '{}')
    if agent.name=='Scanner':
        evidence=json.loads(await call(agent.tools[0]))
        rows=evidence['rows']; threshold=evidence['threshold']
        selected=[r for r in rows if r['status']=='ready' and r['bars'][1]['close']-r['bars'][0]['close']>threshold+1e-9]
        selected.sort(key=lambda r:(-(r['bars'][1]['close']-r['bars'][0]['close']),r['ticker']))
        return SimpleNamespace(final_output=watchlist.Shortlist(candidates=[watchlist.Candidate(ticker=r['ticker'],snapshot_id=r['snapshot_id'],observation=f"{r['bars'][0]['close']} to {r['bars'][1]['close']}") for r in selected[:3]]))
    await call(agent.tools[0]); await hooks.on_llm_start(None,agent,None,[]); await call(agent.tools[1])
    return SimpleNamespace(final_output='done')

