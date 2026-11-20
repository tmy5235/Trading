import { h, onMounted, onBeforeUnmount, ref } from 'vue'

export default {
  setup() {
    const evidence=ref(null), session=ref(null), error=ref('')
    const threshold=ref('0'), interval=ref('5'), maxCycles=ref('3'), callLimit=ref('20')
    let timer
    async function refresh() {
      if (!session.value?.id) return
      try {
        const res=await fetch(`/api/watchlist/${session.value.id}`)
        if (!res.ok) throw new Error('Could not load watchlist session')
        session.value=await res.json()
        if (session.value.running) timer=setTimeout(refresh,700)
      } catch(e) { error.value=e.message }
    }
    async function start(once) {
      error.value=''
      try {
        const res=await fetch(`/api/watchlist/start?once=${once}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({threshold:Number(threshold.value),interval:Number(interval.value),max_cycles:Number(maxCycles.value),call_limit:Number(callLimit.value)})})
        const data=await res.json(); if (!res.ok) throw new Error(typeof data.detail==='string' ? data.detail : 'Check the run settings')
        session.value=data; await refresh()
      } catch(e) { error.value=e.message }
    }
    async function stop() {
      try { const res=await fetch(`/api/watchlist/${session.value.id}/stop`,{method:'POST'}); if (!res.ok) throw new Error('Could not stop'); session.value=await res.json() }
      catch(e) { error.value=e.message }
    }
    onMounted(async()=>{ try { const res=await fetch('/api/watchlist/evidence'); if(!res.ok) throw new Error('Evidence unavailable'); evidence.value=await res.json() } catch(e){error.value=e.message} })
    onBeforeUnmount(()=>clearTimeout(timer))
    const field=(label,value,min,max,step)=>h('label',[`${label} `,h('input',{type:'number',min,max,step,value:value.value,onInput:e=>{value.value=e.target.value}})])
    return ()=>{
      const cycle=session.value?.cycles.at(-1), rows=cycle?.rows || evidence.value?.rows || [], images=cycle?.images || evidence.value?.images || {}
      return h('section',{class:'watchlist-panel'},[
        h('div',{class:'lesson-heading'},[h('div',[h('span',{class:'eyebrow'},'COORDINATED AGENTS'),h('h2','Watchlist review')]),h('span',{class:'simulation-badge'},'SYNTHETIC · NO TRADE')]),
        h('p','Frozen synthetic one-minute candles starting at 14:42 UTC on November 16, 2026. Select ready charts when the second close rises by more than the threshold; rank by rise, then ticker (maximum three).'),
        h('div',{class:'watchlist-controls'},[field('Minimum rise ($)',threshold,0,1000,0.01),field('Interval (seconds)',interval,1,300,1),field('Maximum cycles',maxCycles,1,3,1),field('Session call limit',callLimit,1,100,1)]),
        h('div',[h('button',{disabled:session.value?.running,onClick:()=>start(true)},'Run once'),h('button',{disabled:session.value?.running,onClick:()=>start(false)},'Start'),h('button',{disabled:!session.value?.running || session.value?.stopped,onClick:stop},'Stop')]),
        h('p',{role:'alert'},error.value),
        session.value ? h('p',{role:'status'},`Session ${session.value.running ? 'running' : 'finished'} · ${session.value.calls}/${session.value.settings.call_limit} model/API calls · ${session.value.cycles.length} cycles`) : null,
        h('div',{class:'watchlist-grid'},rows.map(row=>{
          const result=cycle?.assessments[row.snapshot_id]
          return h('article',{class:'watchlist-card'},[
            h('h3',`${row.ticker} · ${row.status}`),h('p',`Cutoff ${row.cutoff} · ${row.source}`),
            h('p',row.snapshot_id || 'No capture'),
            images[row.snapshot_id] ? h('img',{src:images[row.snapshot_id],alt:`${row.ticker} saved synthetic candles`}) : h('p','Chart missing'),
            ...row.bars.map(b=>h('p',`${b.time} · open $${b.open.toFixed(2)} · close $${b.close.toFixed(2)}`)),
            result ? h('p',{class:'lesson-choice'},`${result.answer.choice.toUpperCase()} · probability ${result.answer.probability == null ? 'unavailable' : result.answer.probability.toFixed(2)} · chart ${result.context.last_completed_candle}`) : h('p','No completed assessment')
          ])
        })),
        h('h3','Read-only activity log'),
        session.value ? h('ol',{class:'watchlist-log'},session.value.events.map(e=>h('li',[h('strong',`Cycle ${e.cycle} · ${e.kind}: `),e.message,e.snapshot_id ? ` · ${e.snapshot_id}` : '',e.rows ? h('pre',JSON.stringify(e.rows,null,2)) : null,e.result ? h('pre',JSON.stringify({status:e.result.status,answer:e.result.answer,context:e.result.context},null,2)) : null]))) : h('p','Run a scan to inspect its evidence and results.')
      ])
    }
  }
}
