import { CandlestickSeries, HistogramSeries, LineSeries, createChart, createSeriesMarkers } from 'lightweight-charts'

// Render from frozen server records in a separate chart; live updates cannot change it.
export async function drawFrozenCapture(snapshot, firstTime, instances, types) {
  const toTime = value => Date.parse(value) / 1000
  const endTime = toTime(snapshot.candle_timestamp)
  const items = snapshot.chart_records.filter(r => toTime(r.candle.timestamp) >= firstTime && toTime(r.candle.timestamp) <= endTime)
  if (!items.length || items.at(-1).candle.timestamp !== snapshot.candle_timestamp) throw new Error('Selected candle is outside the chart range; select a visible completed candle and recapture.')
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;left:-10000px;top:0;width:1000px;height:450px;'
  document.body.appendChild(host)
  let chart
  try {
    chart = createChart(host, {width:1000, height:450, timeScale:{timeVisible:true}})
    const candles = chart.addSeries(CandlestickSeries)
    candles.setData(items.map(r => ({time:toTime(r.candle.timestamp), open:r.candle.open, high:r.candle.high, low:r.candle.low, close:r.candle.close})))
    const bars = chart.addSeries(HistogramSeries, {priceFormat:{type:'volume'}, priceScaleId:''})
    bars.setData(items.map(r => r.candle.volume == null ? {time:toTime(r.candle.timestamp)} : {time:toTime(r.candle.timestamp),value:r.candle.volume,color:r.candle.close >= r.candle.open ? '#26a69a80' : '#ef535080'}))
    candles.priceScale().applyOptions({scaleMargins:{top:0.1,bottom:0.3}})
    bars.priceScale().applyOptions({scaleMargins:{top:0.75,bottom:0}})
    for (const [key,color,title] of [['short','#1769aa','Short SMA'],['long','#b85a00','Long SMA']]) {
      const line=chart.addSeries(LineSeries,{color,lineWidth:2,title})
      line.setData(items.map(r => r.sma?.[key] == null ? {time:toTime(r.candle.timestamp)} : {time:toTime(r.candle.timestamp),value:r.sma[key]}))
    }
    const colors=['#8e44ad','#c13d69','#167d7f','#724caa']
    for (const [index, instance] of instances.entries()) {
      const units=types.find(t => t.type === instance.type)?.units
      const line=chart.addSeries(LineSeries,{color:colors[index % colors.length],lineWidth:2,title:instance.name,priceScaleId:units === 'price' ? 'right' : `indicator-${units || instance.type}`})
      line.setData(items.map(r => r.indicators?.[instance.id] == null ? {time:toTime(r.candle.timestamp)} : {time:toTime(r.candle.timestamp),value:r.indicators[instance.id]}))
    }
    const annotations=items.filter(r => r.sma?.signal).map(r => ({time:toTime(r.candle.timestamp),position:r.sma.signal === 'up' ? 'belowBar' : 'aboveBar',color:r.sma.signal === 'up' ? '#008000' : '#c00000',shape:r.sma.signal === 'up' ? 'arrowUp' : 'arrowDown',text:r.sma.signal === 'up' ? 'SMA up' : 'SMA down'}))
    annotations.push({time:endTime,position:'aboveBar',color:'#333333',shape:'circle',text:'Cutoff'})
    createSeriesMarkers(candles,annotations)
    chart.timeScale().fitContent()
    await Promise.race([new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))), new Promise((_, reject) => setTimeout(() => reject(new Error('Chart rendering timed out; keep the dashboard visible and recapture.')), 5000))])
    return {image_url:chart.takeScreenshot().toDataURL('image/png'), capture:{snapshot_id:snapshot.id,symbol:snapshot.symbol,timeframe:snapshot.timeframe,range_start:items[0].candle.timestamp,cutoff:snapshot.candle_timestamp}}
  } finally { chart?.remove(); host.remove() }
}
