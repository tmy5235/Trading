import { h, onMounted, ref } from 'vue'

export default {
  setup() {
    const start=ref(''), end=ref(''), short=ref('2'), long=ref('3'), cash=ref('10000'), quantity=ref('1')
    const result=ref(null), error=ref(''), busy=ref(false)
    onMounted(async()=>{
      try {
        const response=await fetch('/api/backtest/range'); if(!response.ok) throw new Error('Recorded ranges unavailable')
        const item=await response.json()
        if(item.start) { start.value=item.start; end.value=item.end }
      } catch(e) { error.value=e.message }
    })
    async function run() {
      busy.value=true; error.value=''; result.value=null
      try {
        const response=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start:start.value,end:end.value,short:Number(short.value),long:Number(long.value),initial_cash:cash.value,quantity:Number(quantity.value)})})
        const data=await response.json(); if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Check your inputs')
        result.value=data
      } catch(e) { error.value=e.message } finally { busy.value=false }
    }
    const field=(label,value,type='text',props={})=>h('label',[`${label} `,h('input',{type,value:value.value,...props,onInput:e=>value.value=e.target.value})])
    return ()=>h('section',{class:'backtest-panel'},[
      h('div',{class:'lesson-heading'},[h('div',[h('span',{class:'eyebrow'},'HISTORICAL STRATEGY'),h('h2','SMA backtest')]),h('span',{class:'simulation-badge'},'SEPARATE SIMULATION')]),
      h('p','Run the long-only crossover strategy on recorded AAPL observations. An order uses a later eligible quote; the paper account is not changed.'),
      h('div',{class:'backtest-controls'},[field('Start UTC',start),field('End UTC',end),field('Short SMA',short,'number',{min:1,max:99}),field('Long SMA',long,'number',{min:2,max:100}),field('Starting cash',cash,'number',{min:0.01}),field('Shares per entry',quantity,'number',{min:1,max:10000})]),
      h('button',{disabled:busy.value,onClick:run},busy.value?'Running…':'Run backtest'), h('p',{role:'alert'},error.value),
      h('p','Maximum 500 recorded receipts per run. Narrow the UTC range if needed. Yahoo history downloaded later is excluded; only recorded stream observations are used.'),
      result.value ? h('div',{role:'status'},[
        h('p',`${result.value.receipts} receipts · ${result.value.source} · frozen receipt cutoff ${result.value.cutoff_receipt_id}`),
        h('p',`Cash $${result.value.cash} · shares ${result.value.shares} · last price ${result.value.last_price == null ? 'unavailable' : '$'+result.value.last_price} · equity $${result.value.equity}`),
        h('p',`Realized $${result.value.realized} · unrealized $${result.value.unrealized} · total P&L $${result.value.total_pnl}`),
        h('h3','Orders and fills'),
        result.value.trades.length ? h('ul',result.value.trades.map(t=>h('li',`${t.action.toUpperCase()} · ${t.status} · signal ${t.candle_time} · decision ${t.decision_time}${t.fill_time ? ` · fill ${t.fill_time} at $${t.price}` : ''}${t.reason ? ` · ${t.reason}` : ''}${t.quantity ? ` · ${t.quantity} shares` : ''}`))) : h('p','No orders in this range.'),
        h('h3','Completed-bar decisions'),
        h('ul',result.value.decisions.map(d=>h('li',`${d.candle_time} · ${d.signal || 'no crossover'} · ${d.action} · ${d.status} · SMA ${d.short_sma ?? 'warming up'} / ${d.long_sma ?? 'warming up'}`))),
        h('p',result.value.assumptions), h('p','Historical performance does not predict future results. One AAPL sample can suffer from selection bias and parameter overfitting.')
      ]) : null
    ])
  }
}
