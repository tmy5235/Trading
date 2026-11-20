import { createApp, h, onMounted, onBeforeUnmount, ref } from 'vue'
import './style.css'
import { drawFrozenCapture } from './chart_capture.js'
import { useVoice } from './voice.js'
import Watchlist from './watchlist.js'
import Backtest from './backtest.js'
import ChartChat, { assessmentQuestion, matchesCapture } from './chart_chat.js'
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, createSeriesMarkers } from 'lightweight-charts'

createApp({
  setup() {
    const chartElement = ref(null), timeframe = ref('1m'), mode = ref('live')
    const showSignals = ref(false), showSmas = ref(true), chatOpen = ref(false), automationOpen = ref(false)
    const records = ref([]), selected = ref(''), hovered = ref(''), feed = ref('unknown'), transport = ref('connecting'), liveSource = ref('Yahoo Finance')
    const sourceTime = ref(null), observationId = ref(null), latestPrice = ref(null), observationSession = ref(null), receiptTime = ref(null), age = ref('unknown'), error = ref('')
    const options = ref([]), symbol = ref('AAPL'), start = ref(''), end = ref(''), replaySource = ref('recorded observations')
    const position = ref(0), count = ref(0), event = ref(null), playing = ref(false)
    const advancedReplay = ref(false), snapshotTime = ref(null), snapshotBusy = ref(false), rangeOpen = ref(false)
    const paper = ref({cash: '0', positions: [], orders: [], pnl_history: []}), paperQty = ref('1'), paperMessage = ref(''), paperBusy = ref(false), stepBusy = ref(false)
    const replayCheckpoint = ref(null), decisionSnapshot = ref(null), decisionResult = ref(null), decisionError = ref(''), decisionBusy = ref(false)
    const chartCapture = ref(null), chartResult = ref(null), chartError = ref(''), chartBusy = ref(false)
    const lessonResult = ref(null), lessonImage = ref(''), lessonError = ref(''), lessonBusy = ref(false), lessonHistory = ref([])
    let lessonSelection = 0
    const automation = ref({state:{enabled:0},events:[]}), automationError = ref(''), automationWorking = ref(false)
    let automationSeen = new Set(), automationQueue = Promise.resolve(), automationTimer, automationInitialized = false, automationOwner = null
    const voice=useVoice()
    let replayScope = null, lastPaperRequest = null, paperTimer
    const shortInput = ref('2'), longInput = ref('3'), shortWindow = ref(2), longWindow = ref(3), windowError = ref('')
    const indicatorTypes = ref([]), instances = ref([]), newType = ref(''), newName = ref(''), settingInputs = ref({}), indicatorError = ref(''), editWindows=ref({})
    const instanceSeries = new Map(), colors = ['#8e44ad', '#c13d69', '#167d7f', '#724caa']
    let chart, prices, volume, shortLine, longLine, markers, resizeObserver, socket, timer, reconnectTimer, playTimer
    let requestVersion = 0, generation = 0, cutoff = null, disposed = false
    const toTime = value => Date.parse(value) / 1000
    function renderRecords(items) {
      if (!items.length) {
        lessonResult.value = null; lessonImage.value = ''; lessonError.value = ''
      }
      const previousLength = records.value.length
      const wasFollowingCandle = selected.value === records.value.at(-1)?.candle.timestamp
      const view = chart.timeScale().getVisibleLogicalRange()
      const follow = view && view.to >= previousLength - 2
      records.value = items
      const candles = items.map(item => item.candle)
      prices.setData(candles.map(c => ({ time: toTime(c.timestamp), open: c.open, high: c.high, low: c.low, close: c.close })))
      volume.setData(candles.map(c => c.volume == null ? { time: toTime(c.timestamp) } : { time: toTime(c.timestamp), value: c.volume, color: c.close >= c.open ? '#19c79a99' : '#f0647499' }))
      shortLine.setData(items.map(r => r.sma?.short == null ? { time: toTime(r.candle.timestamp) } : { time: toTime(r.candle.timestamp), value: r.sma.short }))
      longLine.setData(items.map(r => r.sma?.long == null ? { time: toTime(r.candle.timestamp) } : { time: toTime(r.candle.timestamp), value: r.sma.long }))
      for (const [index, instance] of instances.value.entries()) {
        let series = instanceSeries.get(instance.id)
        if (!series) {
          const units = indicatorTypes.value.find(type => type.type === instance.type)?.units
          series = chart.addSeries(LineSeries, { color: colors[index % colors.length], lineWidth: 2, title: instance.name, priceScaleId: units === 'price' ? 'right' : `indicator-${units || instance.type}` })
          instanceSeries.set(instance.id, series)
        }
        series.setData(items.map(r => r.indicators?.[instance.id] == null ? { time: toTime(r.candle.timestamp) } : { time: toTime(r.candle.timestamp), value: r.indicators[instance.id] }))
      }
      markers.setMarkers(items.filter(r => showSignals.value && r.sma?.signal).map(r => ({ time: toTime(r.candle.timestamp), position: r.sma.signal === 'up' ? 'belowBar' : 'aboveBar', color: r.sma.signal === 'up' ? '#008000' : '#c00000', shape: r.sma.signal === 'up' ? 'arrowUp' : 'arrowDown', text: r.sma.signal === 'up' ? 'SMA up' : 'SMA down' })))
      if (wasFollowingCandle || !candles.some(c => c.timestamp === selected.value)) selected.value = candles.at(-1)?.timestamp || ''
      if (previousLength && items.length > previousLength && follow && mode.value === 'live') {
        if (items.length - previousLength > 20) chart.timeScale().setVisibleLogicalRange({from:Math.max(0,items.length-75),to:items.length+4})
        else chart.timeScale().scrollToRealTime()
      }
    }
    function applyLive(state) {
      if (mode.value !== 'live' || state.symbol !== 'AAPL' || state.timeframe !== timeframe.value) return
      liveSource.value = state.source || 'Yahoo Finance'; feed.value = state.feed_status; sourceTime.value = state.source_time; observationId.value = state.observation_id; latestPrice.value = state.latest_price; observationSession.value = state.observation_session; receiptTime.value = state.receipt_time
      renderRecords(state.records); error.value = state.records.length ? '' : 'No regular-session candles recorded yet.'
      if (!automationInitialized) { automationSeen = new Set(state.records.filter(r => r.ended).map(r => r.candle.timestamp)); automationInitialized = true }
      enqueueCompletedCandles(state.records)
    }
    async function loadLive() {
      const version = ++requestVersion
      clearTimeout(reconnectTimer)
      if (socket) { socket.onclose = null; socket.close(); socket = null }
      feed.value = 'unknown'; transport.value = 'connecting'; sourceTime.value = null; observationId.value = null; latestPrice.value = null; observationSession.value = null; receiptTime.value = null
      renderRecords([])
      try {
        const response = await fetch(`/api/live-history?symbol=AAPL&timeframe=${encodeURIComponent(timeframe.value)}&short=${shortWindow.value}&long=${longWindow.value}`)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const state = await response.json()
        if (version !== requestVersion) return
        applyLive(state); if (state.records.length) chart.timeScale().setVisibleLogicalRange({from:Math.max(0,state.records.length - 75), to:state.records.length + 4})
        const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
        socket = new WebSocket(`${scheme}//${location.host}/api/live?symbol=AAPL&timeframe=${encodeURIComponent(timeframe.value)}&short=${shortWindow.value}&long=${longWindow.value}`)
        socket.onopen = () => { transport.value = 'connected' }
        socket.onmessage = e => { if (version === requestVersion) applyLive(JSON.parse(e.data)) }
        socket.onclose = () => {
          if (version !== requestVersion || disposed) return
          transport.value = 'disconnected'; feed.value = 'unknown'; reconnectTimer = setTimeout(loadLive, 3000)
        }
      } catch (err) {
        if (version !== requestVersion) return
        error.value = `Could not load live history: ${err.message}`; transport.value = 'disconnected'; feed.value = 'unknown'
        reconnectTimer = setTimeout(loadLive, 3000)
      }
    }
    function applyWindows() {
      const a = Number(shortInput.value), b = Number(longInput.value)
      if (!/^\d+$/.test(shortInput.value) || !/^\d+$/.test(longInput.value) || !Number.isSafeInteger(a) || !Number.isSafeInteger(b) || a <= 0 || a >= b) {
        windowError.value = 'Use positive whole numbers with the short window smaller than the long window.'
        return
      }
      windowError.value = ''; shortWindow.value = a; longWindow.value = b
      if (mode.value === 'live') loadLive(); else if (advancedReplay.value) startReplay(); else loadSnapshot()
    }
    function chooseIndicatorType(value) {
      newType.value = value
      const type = indicatorTypes.value.find(item => item.type === value)
      settingInputs.value = Object.fromEntries((type?.settings || []).map(field => [field.key, String(field.default)]))
    }
    async function loadIndicatorConfig() {
      const [typesResponse, instancesResponse] = await Promise.all([fetch('/api/indicator-types'), fetch('/api/indicators')])
      if (!typesResponse.ok || !instancesResponse.ok) throw new Error('Unable to load indicator configuration')
      indicatorTypes.value = await typesResponse.json()
      instances.value = await instancesResponse.json()
      if (!newType.value && indicatorTypes.value.length) chooseIndicatorType(indicatorTypes.value[0].type)
      renderRecords(records.value)
    }
    async function createIndicator() {
      const type = indicatorTypes.value.find(item => item.type === newType.value)
      if (!newName.value.trim()) { indicatorError.value = 'Enter an indicator name.'; return }
      if (!type) { indicatorError.value = 'Choose an indicator type.'; return }
      const settings = {}
      for (const field of type.settings) {
        const raw = settingInputs.value[field.key] || ''
        if (field.type === 'integer' && (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw) < field.minimum)) {
          indicatorError.value = `${field.label} must be a positive whole number.`; return
        }
        settings[field.key] = field.type === 'integer' ? Number(raw) : raw
      }
      try {
        const response = await fetch('/api/indicators', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newName.value.trim(), type: newType.value, settings }) })
        const data = await response.json()
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Invalid settings')
        indicatorError.value = ''; newName.value = ''
        await loadIndicatorConfig()
        if (mode.value === 'live') await loadLive()
        else if (advancedReplay.value && count.value) await requestReplay(position.value, generation)
        else if (!advancedReplay.value) await loadSnapshot()
      } catch (err) { indicatorError.value = err.message }
    }
    async function updateIndicator(instance) {
      const raw=editWindows.value[instance.id] ?? String(instance.settings.window)
      if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw)<1) { indicatorError.value='Window must be a positive whole number.'; return }
      try {
        const response=await fetch(`/api/indicators/${instance.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:{window:Number(raw)}})})
        const data=await response.json(); if (!response.ok) throw new Error(data.detail || 'Unable to update indicator')
        indicatorError.value=''; await loadIndicatorConfig()
        if (mode.value==='live') await loadLive(); else if(advancedReplay.value && count.value) await requestReplay(position.value,generation); else if(!advancedReplay.value) await loadSnapshot()
      } catch (err) { indicatorError.value=err.message }
    }
    async function loadPaper() {
      const response = await fetch('/api/paper/account')
      if (!response.ok) throw new Error('Could not load paper account')
      paper.value = await response.json()
    }
    async function submitPaper(action, retry = false) {
      if (paperBusy.value || stepBusy.value) return
      if (!retry && mode.value === 'replay' && !replayScope) { paperMessage.value = 'Start a replay before submitting an order.'; return }
      if (!retry && (action === 'buy' || action === 'sell') && (!/^[0-9]+$/.test(paperQty.value) || !Number.isSafeInteger(Number(paperQty.value)) || Number(paperQty.value) <= 0)) {
        paperMessage.value = 'Quantity must be a positive whole number.'; return
      }
      if (!retry) lastPaperRequest = { request_id: crypto.randomUUID(), symbol: mode.value === 'replay' ? symbol.value : 'AAPL', action, quantity: ['buy','sell'].includes(action) ? Number(paperQty.value) : null, mode: mode.value, scope: mode.value === 'replay' ? replayScope : null, cursor: mode.value === 'replay' ? (event.value?.receipt_id || 0) : 0 }
      paperBusy.value = true
      try {
        const response = await fetch('/api/paper/action', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(lastPaperRequest)})
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || 'Paper action failed')
        paperMessage.value = `${data.duplicate ? 'Retry: ' : ''}${data.status}${data.reason ? ` — ${data.reason}` : ''}${data.order_id ? ` (order ${data.order_id})` : ''}`
        await loadPaper()
      } catch (err) { paperMessage.value = `Paper action error: ${err.message}` }
      finally { paperBusy.value = false }
    }
    async function processReplayReceipt(receiptId) {
      const response = await fetch('/api/paper/replay-step', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({receipt_id:receiptId, scope:replayScope})})
      if (!response.ok) throw new Error('Could not process replay receipt')
      const step = await response.json()
      replayCheckpoint.value = step.checkpoint_id
      await loadPaper()
    }
    async function freezeReplayDecision() {
      if (!replayCheckpoint.value || !selected.value) return
      decisionBusy.value = true; decisionError.value = ''; decisionResult.value = null
      try {
        const response = await fetch('/api/decision-snapshots', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({symbol:symbol.value, timeframe:timeframe.value, candle_timestamp:selected.value, short:shortWindow.value, long:longWindow.value, mode:'replay', replay_start:start.value, replay_end:end.value, replay_position:position.value, replay_cutoff:cutoff, replay_checkpoint_id:replayCheckpoint.value})})
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || 'Could not freeze snapshot')
        decisionSnapshot.value = data
        renderRecords(data.chart_records)
      } catch (err) { decisionSnapshot.value = null; decisionError.value = err.message }
      finally { decisionBusy.value = false }
    }
    async function requestDecision() {
      if (!decisionSnapshot.value) return
      decisionBusy.value = true; decisionError.value = ''
      try {
        const response = await fetch('/api/decisions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({snapshot_id:decisionSnapshot.value.id})})
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || 'Decision request failed')
        decisionResult.value = data
      } catch (err) { decisionError.value = err.message }
      finally { decisionBusy.value = false }
    }
    async function askAboutChart() {
      const chosen = records.value.find(r => r.candle.timestamp === selected.value)
      if (!chosen?.ended) { chartError.value='Select a completed candle and recapture.'; return }
      if (mode.value === 'replay' && !replayCheckpoint.value) { chartError.value='Advance replay to create an account checkpoint first.'; return }
      const visible = chart.timeScale().getVisibleRange(), selectedTime=toTime(selected.value)
      if (!visible || selectedTime < visible.from || selectedTime > visible.to) { chartError.value='The selected candle is outside the visible chart range. Bring it into view and recapture.'; return }
      const request = {symbol:mode.value === 'replay' ? symbol.value : 'AAPL',timeframe:timeframe.value,candle_timestamp:selected.value,short:shortWindow.value,long:longWindow.value,mode:mode.value}
      if (mode.value === 'replay') Object.assign(request,{replay_start:start.value,replay_end:end.value,replay_position:position.value,replay_cutoff:cutoff,replay_checkpoint_id:replayCheckpoint.value})
      const frozenInstances=instances.value.map(i => ({...i})), frozenTypes=indicatorTypes.value.map(i => ({...i}))
      chartBusy.value=true; chartError.value=''; chartResult.value=null; chartCapture.value=null
      try {
        const response=await fetch('/api/decision-snapshots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)})
        const snapshot=await response.json()
        if (!response.ok) throw new Error(snapshot.detail || 'Snapshot failed; recapture the chart.')
        const captured=await drawFrozenCapture(snapshot,visible.from,snapshot.indicator_instances || frozenInstances,frozenTypes)
        chartCapture.value={...captured,snapshot}
        const reply=await fetch('/api/decisions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot_id:snapshot.id,...captured})})
        const result=await reply.json()
        if (!reply.ok) throw new Error(result.detail || 'Decision failed; recapture the chart.')
        chartResult.value=result
      } catch (err) { chartError.value=err.message }
      finally { chartBusy.value=false }
    }
    async function loadLessonHistory() {
      const response = await fetch('/api/classroom-decisions')
      if (!response.ok) throw new Error('Could not load chart interpretations')
      lessonHistory.value = await response.json()
    }
    function visibleChart() {
      if (!chart || !records.value.length) return null
      const range = chart.timeScale().getVisibleRange()
      if (!range) return null
      const visible = records.value.filter(r => toTime(r.candle.timestamp) >= range.from && toTime(r.candle.timestamp) <= range.to)
      if (!visible.length) return null
      const completed = visible.filter(r => r.ended)
      const forming = visible.filter(r => !r.ended)
      const context = {symbol:mode.value === 'replay' ? symbol.value : 'AAPL', interval:timeframe.value, mode:mode.value,
        source_time:mode.value === 'replay' ? (event.value?.source_time_utc || null) : sourceTime.value,
        replay_receipt_time:mode.value === 'replay' ? (snapshotTime.value || event.value?.received_at || null) : null,
        last_completed_candle:completed.at(-1)?.candle.timestamp || null, forming_candle:forming.at(-1)?.candle.timestamp || null,
        feed_status:mode.value === 'replay' ? 'disconnected' : (['connected','disconnected'].includes(feed.value) ? feed.value : 'unknown')}
      const key = JSON.stringify([generation, requestVersion, lessonSelection, context, position.value,
        chart.timeScale().getVisibleLogicalRange(), chartElement.value.clientWidth, chartElement.value.clientHeight,
        shortWindow.value, longWindow.value, instances.value, showSignals.value,
        visible.map(r => [r.candle, r.ended, r.sma, r.indicators])])
      return {context, key}
    }
    async function requestChartAgent(question, history = [], conversationId = null) {
      if (lessonBusy.value) throw new Error('A chart request is already in progress.')
      lessonBusy.value = true
      try {
        const view = visibleChart()
        const snapshot = view ? {image_url:chart.takeScreenshot().toDataURL('image/png'),
          context:{...view.context, capture_time:new Date().toISOString()}} : null
        const response = await fetch('/api/chart-agent', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({question, snapshot, history, conversation_id:conversationId})})
        const reply = await response.json()
        if (!response.ok) throw new Error(typeof reply.detail === 'string' ? reply.detail : 'Chart agent request failed.')
        const result = reply.tools?.find(call => call.name === 'assess_visible_chart')?.result
        if (result?.id) {
          const sameCapture = await matchesCapture(result, snapshot)
          reply.stale = !sameCapture || disposed || visibleChart()?.key !== view?.key
          if (!reply.stale) {
            lessonResult.value = result
            lessonImage.value = result.image_url
            lessonError.value = ''
          }
          if (!disposed) await loadLessonHistory().catch(() => {})
        }
        return reply
      }
      finally { lessonBusy.value = false }
    }
    async function askClassroomChart() {
      lessonError.value = ''
      try {
        const reply = await requestChartAgent(assessmentQuestion)
        if (reply.stale) lessonError.value = 'The chart changed. The earlier capture is in recent interpretations.'
        else if (!reply.tools?.some(call => call.name === 'assess_visible_chart' && call.result.id))
          lessonError.value = reply.detail || reply.message || 'No chart assessment was returned.'
      } catch (error) { lessonError.value = error.message }
    }
    async function automationCall(path, payload) {
      const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload || {})})
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      return data
    }
    async function refreshAutomation() {
      const response=await fetch('/api/automation')
      if (!response.ok) throw new Error('Could not load paper automation')
      automation.value=await response.json()
    }
    async function startAutomation() {
      if (mode.value !== 'live') { automationError.value='Switch to Live to start paper automation.'; return }
      automationError.value=''
      automationSeen = new Set(records.value.filter(r => r.ended).map(r => r.candle.timestamp))
      try {
        const started=await automationCall('/api/automation/start',{symbol:'AAPL',timeframe:timeframe.value})
        automationOwner=started.session_id
        await refreshAutomation(); enqueueCompletedCandles(records.value)
      } catch (err) { automationError.value=err.message }
    }
    async function stopAutomation() {
      automationError.value=''
      try { await automationCall('/api/automation/stop',{}); automationOwner=null; await refreshAutomation(); await loadPaper() }
      catch (err) { automationError.value=err.message }
    }
    function enqueueCompletedCandles(items) {
      const setting=automation.value.state
      if (!setting.enabled || setting.session_id !== automationOwner || mode.value !== 'live' || setting.symbol !== 'AAPL' || setting.timeframe !== timeframe.value) return
      for (const item of items) {
        if (item.data_source === 'Yahoo history') continue
        const stamp=item.candle.timestamp
        if (!item.ended || automationSeen.has(stamp)) continue
        automationSeen.add(stamp)
        const context={session_id:setting.session_id, timestamp:stamp, timeframe:setting.timeframe,
                       short:shortWindow.value,long:longWindow.value,instances:instances.value.map(i=>({...i})),types:indicatorTypes.value.map(i=>({...i}))}
        automationQueue=automationQueue.then(()=>analyzeAutomaticCandle(context)).catch(err=>{automationError.value=err.message})
      }
    }
    async function analyzeAutomaticCandle(context) {
      let eventId=null, stage='snapshot_unavailable'
      automationWorking.value=true
      try {
        const claim=await automationCall('/api/automation/claim',{session_id:context.session_id,candle_timestamp:context.timestamp})
        if (claim.duplicate) return
        eventId=claim.id
        const snapshot=await automationCall('/api/decision-snapshots',{symbol:'AAPL',timeframe:context.timeframe,candle_timestamp:context.timestamp,short:context.short,long:context.long,mode:'live'})
        await automationCall(`/api/automation/${eventId}/snapshot`,{snapshot_id:snapshot.id})
        stage='chart_unavailable'
        if (!chart || !chartElement.value) throw new Error('Chart is unavailable')
        stage='image_unavailable'
        const available=snapshot.chart_records.filter(r=>toTime(r.candle.timestamp)<=toTime(context.timestamp))
        if (!available.length) throw new Error('Snapshot contains no chart data')
        const first=available[Math.max(0,available.length-100)].candle.timestamp
        const capture=await drawFrozenCapture(snapshot,toTime(first),snapshot.indicator_instances || context.instances,context.types)
        chartCapture.value={...capture,snapshot}
        stage='decision_unavailable'
        const decision=await automationCall('/api/decisions',{snapshot_id:snapshot.id,...capture})
        chartResult.value=decision
        await automationCall(`/api/automation/${eventId}/apply`,{decision_id:decision.decision_id})
      } catch (err) {
        if (eventId) { try { await automationCall(`/api/automation/${eventId}/skip`,{reason:stage}) } catch (_) {} }
        automationError.value=`Automatic analysis: ${err.message}`
      } finally {
        automationWorking.value=false
        try { await refreshAutomation(); await loadPaper() } catch (_) {}
      }
    }
    function stopReplay() { playing.value = false; clearTimeout(playTimer); generation++ }
    function invalidateReplay() {
      stopReplay(); replayScope = null; cutoff = null; position.value = 0; count.value = 0; event.value = null; replayCheckpoint.value = null; snapshotTime.value = null; snapshotBusy.value = false
      hovered.value = ''; selected.value = ''; renderRecords([]); error.value = 'Choose a range and load the snapshot.'
    }
    function setSymbol(value) {
      symbol.value = value
      const item = options.value.find(item => item.symbol === value)
      start.value = item?.start || ''; end.value = item?.end || ''
    }
    async function requestReplay(target, current) {
      const params = new URLSearchParams({ symbol: symbol.value, start: start.value, end: end.value, timeframe: timeframe.value, position: String(target), short: String(shortWindow.value), long: String(longWindow.value) })
      if (cutoff !== null) params.set('cutoff', String(cutoff))
      const response = await fetch(`/api/replay?${params}`)
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
      if (current !== generation || mode.value !== 'replay') return
      replaySource.value = result.source || 'recorded observations'
      cutoff = result.cutoff; position.value = result.position; count.value = result.count; event.value = result.event
      const followLatest = !selected.value || selected.value === records.value.at(-1)?.candle.timestamp
      renderRecords(result.records)
      if (followLatest) selected.value = result.records.at(-1)?.candle.timestamp || ''
      error.value = result.count ? '' : 'This selection has no recorded observations.'

      if (result.position > 0) chart.timeScale().fitContent()
    }
    async function loadSnapshot() {
      stopReplay(); const current = generation
      replayScope = null; cutoff = null; position.value = 0; count.value = 0; event.value = null
      replayCheckpoint.value = null; snapshotTime.value = null; hovered.value = ''; selected.value = ''; renderRecords([])
      if (!start.value || !end.value) { error.value = 'Choose a recorded time range.'; return }
      snapshotBusy.value = true; error.value = ''
      try {
        const params = new URLSearchParams({symbol:symbol.value, start:start.value, end:end.value, timeframe:timeframe.value, short:String(shortWindow.value), long:String(longWindow.value)})
        const response = await fetch(`/api/chart-snapshot?${params}`)
        const result = await response.json()
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
        if (current !== generation || mode.value !== 'replay' || advancedReplay.value) return
        event.value = result.event; snapshotTime.value = result.as_of; replaySource.value = result.source
        renderRecords(result.records)
        error.value = result.records.length ? '' : 'No recorded candles are available for this time range.'
        if (result.records.length) chart.timeScale().setVisibleLogicalRange({from:Math.max(0,result.records.length-75),to:result.records.length+4})
      } catch (err) { if (current === generation) error.value = `Snapshot failed: ${err.message}` }
      finally { if (current === generation) snapshotBusy.value = false }
    }
    async function startReplay() {
      stopReplay(); const current = generation
      replayScope = JSON.stringify([symbol.value, start.value, end.value])
      cutoff = null; position.value = 0; count.value = 0; event.value = null; replayCheckpoint.value = null; selected.value = ''; renderRecords([])
      try { await requestReplay(0, current) } catch (err) { if (current === generation) error.value = `Replay failed: ${err.message}` }
    }
    async function advance(current) {
      if (current !== generation || position.value >= count.value) { playing.value = false; return }
      if (stepBusy.value || paperBusy.value) return
      stepBusy.value = true
      const previousCheckpoint = replayCheckpoint.value
      replayCheckpoint.value = null
      const previousPosition = position.value
      try {
        await requestReplay(position.value + 1, current)
        if (current !== generation) return
        if (event.value) await processReplayReceipt(event.value.receipt_id)
        if (current === generation && playing.value) playTimer = setTimeout(() => advance(current), 200)
      } catch (err) {
        playing.value = false
        if (current === generation) {
          try { await requestReplay(previousPosition, current); replayCheckpoint.value = previousCheckpoint } catch (_) { position.value = previousPosition }
          error.value = `Replay failed: ${err.message}. Retry the observation.`
        }
      } finally { stepBusy.value = false }
    }
    async function loadOptions() {
      try {
        const response = await fetch('/api/replay/options')
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const items = await response.json(); options.value = items
        if (items.length && !start.value) setSymbol(items[0].symbol)
        if (mode.value === 'replay' && !items.length) error.value = 'No recorded observations are available.'
        else if (mode.value === 'replay' && !count.value && advancedReplay.value) error.value = 'Choose a range and start replay.'
      } catch (err) { if (mode.value === 'replay') error.value = `Could not load recordings: ${err.message}` }
    }
    async function changeMode(value) {
      stopReplay(); replayScope = null; mode.value = value
      if (value === 'replay') {
        ++requestVersion; clearTimeout(reconnectTimer)
        if (socket) { socket.onclose = null; socket.close(); socket = null }
        feed.value = 'disconnected'; transport.value = 'disconnected'; selected.value = ''; renderRecords([])
        advancedReplay.value = false; rangeOpen.value = false; snapshotTime.value = null; hovered.value = ''
        error.value = 'Loading recorded chart…'
        await loadOptions()
        if (mode.value === 'replay' && options.value.length) { setSymbol(symbol.value); await loadSnapshot() }
      } else loadLive()
    }
    onMounted(() => {
      chart = createChart(chartElement.value, { width: chartElement.value.clientWidth, height: chartElement.value.clientHeight, layout: { background: { color: '#111c2b' }, textColor: '#91a0b5', attributionLogo: true }, grid: { vertLines: { color: '#1c2a3b' }, horzLines: { color: '#1c2a3b' } }, rightPriceScale: { borderColor: '#2b3a4e' }, timeScale: { timeVisible: true, borderColor: '#2b3a4e' }, crosshair: { vertLine: { color: '#7186a3' }, horzLine: { color: '#7186a3' } } })
      prices = chart.addSeries(CandlestickSeries, { upColor: '#27d0a2', downColor: '#f06b7a', borderVisible: false, wickUpColor: '#27d0a2', wickDownColor: '#f06b7a', priceLineColor:'#70a9da' })
      shortLine = chart.addSeries(LineSeries, { color: '#70a7f5', lineWidth: 2, title: 'Short SMA' })
      longLine = chart.addSeries(LineSeries, { color: '#e2ac62', lineWidth: 2, title: 'Long SMA' })
      markers = createSeriesMarkers(prices)
      volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' })
      prices.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0.3 } })
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } })
      // Hover only inspects; a click explicitly selects a capture cutoff.
      chart.subscribeCrosshairMove(param => { hovered.value = typeof param.time === 'number' ? new Date(param.time * 1000).toISOString().replace('.000Z','Z') : '' })
      chart.subscribeClick(param => { const stamp = typeof param.time === 'number' ? new Date(param.time * 1000).toISOString().replace('.000Z','Z') : ''; if (records.value.some(r => r.candle.timestamp === stamp)) selected.value = stamp })
      resizeObserver = new ResizeObserver(() => chart.applyOptions({ width: chartElement.value.clientWidth, height: chartElement.value.clientHeight }))
      resizeObserver.observe(chartElement.value)
      timer = setInterval(() => { age.value = sourceTime.value ? `${Math.max(0, Math.floor((Date.now() - Date.parse(sourceTime.value)) / 1000))} seconds` : 'unknown' }, 1000)
      loadOptions()
      loadLessonHistory().catch(() => {})
      voice.refresh().catch(err=>{voice.status.value=err.message})
      loadIndicatorConfig().catch(err => { indicatorError.value = err.message })
      refreshAutomation().catch(err => { automationError.value=err.message })
      automationTimer=setInterval(() => refreshAutomation().catch(() => {}), 2000)
      loadPaper().catch(err => { paperMessage.value = err.message })
      paperTimer = setInterval(() => { if (mode.value === 'live' && !paperBusy.value) loadPaper().catch(() => {}) }, 2000)
      loadLive()
    })
    onBeforeUnmount(() => { voice.stop(); disposed = true; stopReplay(); ++requestVersion; clearInterval(timer); clearInterval(paperTimer); clearInterval(automationTimer); clearTimeout(reconnectTimer); socket?.close(); resizeObserver?.disconnect(); chart?.remove() })
    return () => {
      const item = records.value.find(r => r.candle.timestamp === (hovered.value || selected.value)) || records.value.find(r => r.candle.timestamp === selected.value), c = item?.candle
      const formatUtc = value => value ? value.replace('T', ' ').replace(/\.\d+(?:Z|\+00:00)$/, ' UTC').replace(/(?:Z|\+00:00)$/, ' UTC') : 'unavailable'
      const choices = (label, values, current, select, disabled = false) => h('div', { class: 'choice-control' }, [
        h('span', { class: 'control-label' }, label),
        h('div', { class: 'button-group', role: 'group', 'aria-label': label }, values.map(({ value, text }) =>
          h('button', { type: 'button', class: { selected: current === value }, 'aria-pressed': current === value, disabled, onClick: () => select(value) }, text)))
      ])
      return h('main', { class: 'app-shell' }, [
        h('header', { class: 'app-header' }, [
          h('div', { class: 'brand' }, [
            h('div', { class: 'brand-mark', 'aria-hidden': 'true' }, [h('svg', {viewBox:'0 0 24 24', fill:'none'}, [
              h('path', {d:'M5 16v-4m0 2H3m2 0h2M12 19V6m0 10H9V9h3m0 7h3V9h-3M19 12V4m0 5h-2V6h2m0 3h2V6h-2', stroke:'currentColor', 'stroke-width':'1.7', 'stroke-linecap':'round', 'stroke-linejoin':'round'})
            ])]),
            h('div', [h('strong', 'Market Studio'), h('span', 'Trading workspace')])
          ]),
          h('div', {class:'header-actions'}, [h('a', {href:'#chart-chat', onClick:()=>{chatOpen.value=true}}, 'Chart chat'), h('span', { class: 'simulation-badge' }, 'PAPER ACCOUNT')])
        ]),
        h('div', { class: 'page-heading' }, [h('div', [h('span', { class: 'eyebrow' }, 'WORKSPACE'), h('h1', 'Market overview')])]),
        h('div', { class: 'dashboard-grid' }, [
          h('div', { class: 'main-column' }, [
            h('section', { class: 'market-panel' }, [
        choices('Mode', [{value:'live',text:'Live'}, {value:'replay',text:'Snapshot'}], mode.value, changeMode, decisionBusy.value || automation.value.state.enabled),
        mode.value === 'replay' ? h('section', {class:'snapshot-panel'}, [
          h('h2', advancedReplay.value ? 'Step through observations' : 'Saved chart snapshot'),
          h('p', advancedReplay.value ? 'Replay observations in receipt order.' : (snapshotTime.value ? `Snapshot as of ${formatUtc(snapshotTime.value)} · ${records.value.length} candles · ${timeframe.value}` : (snapshotBusy.value ? 'Loading saved chart…' : 'Choose a range to view saved candles.'))),
          !advancedReplay.value ? h('button', {onClick:()=>{rangeOpen.value=!rangeOpen.value}}, rangeOpen.value ? 'Hide time range' : 'Change time range') : null,
          (advancedReplay.value || rangeOpen.value) ? h('div', {class:'snapshot-range'}, [
            choices('Symbol', options.value.map(item => ({value:item.symbol,text:item.symbol})), symbol.value, value => { setSymbol(value); invalidateReplay() }),
            h('label', [' Start UTC: ', h('input', { value: start.value, onInput: e => { start.value = e.target.value; invalidateReplay() } })]),
            h('label', [' End UTC: ', h('input', { value: end.value, onInput: e => { end.value = e.target.value; invalidateReplay() } })]),
          ]) : null,
          advancedReplay.value ? h('div', [
            h('button', { disabled: decisionBusy.value || stepBusy.value || paperBusy.value, onClick: startReplay }, 'Start replay'),
            h('button', { disabled: decisionBusy.value || paperBusy.value || stepBusy.value || playing.value || position.value >= count.value, onClick: () => { playing.value = true; advance(generation) } }, 'Play'),
            h('button', { disabled: !playing.value, onClick: stopReplay }, 'Pause'),
            h('button', { disabled: decisionBusy.value || paperBusy.value || stepBusy.value || playing.value || position.value >= count.value, onClick: () => advance(generation) }, 'Next observation'),
            h('p', { role: 'status' }, `Replay: ${position.value}/${count.value} | Source time: ${event.value?.source_time_utc || 'none'} | Received: ${event.value?.received_at || 'none'} | Observation: ${event.value?.observation_id ?? 'none'}`),
          ]) : (rangeOpen.value ? h('button', {disabled:snapshotBusy.value, onClick:loadSnapshot}, snapshotBusy.value ? 'Loading…' : 'Load snapshot') : null),
          h('button', {disabled:snapshotBusy.value || playing.value || stepBusy.value, onClick:()=>{advancedReplay.value=!advancedReplay.value; rangeOpen.value=advancedReplay.value; if (advancedReplay.value) startReplay(); else loadSnapshot()}}, advancedReplay.value ? 'Back to snapshot' : 'Step through observations'),
        ]) : null,
        h('div', {class:'market-summary'}, [
          h('div', [h('div', {class:'market-identity'}, mode.value === 'replay' ? symbol.value : 'AAPL'), h('p', {class:'market-source'}, mode.value === 'replay' ? replaySource.value : liveSource.value)]),
          mode.value === 'live' ? h('div', {class:'quote-block'}, [h('span', {class:'quote-label'}, 'LATEST OBSERVATION'), h('strong', latestPrice.value == null ? 'Unavailable' : `$${Number(latestPrice.value).toFixed(2)}`), h('span', {class:'quote-session'}, observationSession.value || 'Session unknown')]) : h('div', {class:'quote-block'}, [h('span', {class:'quote-label'}, advancedReplay.value ? 'REPLAY' : 'SNAPSHOT'), h('strong', advancedReplay.value ? `${position.value} / ${count.value}` : `${records.value.length}`), h('span', {class:'quote-session'}, advancedReplay.value ? 'observations' : 'candles')])
        ]),
        mode.value === 'live' ? h('div', {role:'status', class:'feed-line'}, [
          h('span', {class:['status-item', feed.value === 'connected' ? 'is-connected' : 'is-disconnected']}, [h('i', {'aria-hidden':'true'}), `Feed: ${feed.value}`]),
          h('span', {class:['status-item', transport.value === 'connected' ? 'is-connected' : 'is-disconnected']}, [h('i', {'aria-hidden':'true'}), `Dashboard: ${transport.value}`]),
          h('span', {class:'status-age'}, `Source age: ${age.value}`)
        ]) : null,
        mode.value === 'live' ? h('p', {class:'observation-detail'}, `Source ${formatUtc(sourceTime.value)} · Received ${formatUtc(receiptTime.value)} · Observation ${observationId.value ?? 'unavailable'}`) : null,
        choices('Timeframe', ['1m', '5m', '15m', '1h', '1d'].map(v => ({value:v,text:v})), timeframe.value, value => { timeframe.value = value; if (mode.value === 'live') loadLive(); else if (advancedReplay.value) startReplay(); else loadSnapshot() }, decisionBusy.value || automation.value.state.enabled),
        h('div', {class:'chart-meta'}, [h('div', {class:['chart-legend', {'is-hidden':!showSmas.value}]}, [h('span', [h('i', {class:'legend-short'}), `SMA ${shortWindow.value}`]), h('span', [h('i', {class:'legend-long'}), `SMA ${longWindow.value}`])]), h('div', {class:'chart-actions'}, [h('button', {'aria-pressed':showSmas.value, 'aria-label':'Show SMA lines', onClick:()=>{showSmas.value=!showSmas.value;shortLine.applyOptions({visible:showSmas.value});longLine.applyOptions({visible:showSmas.value})}}, showSmas.value ? 'Hide SMAs' : 'Show SMAs'), h('button', {'aria-pressed':showSignals.value,onClick:()=>{showSignals.value=!showSignals.value;renderRecords(records.value)}}, showSignals.value ? 'Hide signals' : 'Show signals'), h('button', {onClick:()=>chart.timeScale().scrollToRealTime()}, 'Go to latest')])]),
        h('div', {class:'quote-strip'}, c ? [h('span', {class:'quote-time'}, hovered.value ? 'CROSSHAIR' : 'SELECTED'), h('span', ['O ', h('b', Number(c.open).toFixed(2))]), h('span', ['H ', h('b', Number(c.high).toFixed(2))]), h('span', ['L ', h('b', Number(c.low).toFixed(2))]), h('span', ['C ', h('b', Number(c.close).toFixed(2))]), h('span', ['VOL ', h('b', c.volume == null ? '—' : Number(c.volume).toLocaleString())])] : [h('span','Waiting for candles')]),
        h('p', {class:'chart-note'}, 'Regular session · UTC. Yahoo history when available; current bars from the stream. Volume may be estimated or unavailable.'),
        h('div', { key:'candlestick-chart', ref: chartElement, class: 'chart-canvas' }), h('p', { role: 'status' }, error.value),
        h('details', {class:'inline-details'}, [h('summary', 'Inspect recent candles'), choices('Inspect candle', records.value.filter((r,i) => i >= records.value.length - 8 || r.candle.timestamp === selected.value).map(r => ({value:r.candle.timestamp,text:r.candle.timestamp.replace('T',' ').replace('Z',' UTC')})), selected.value, value => { selected.value = value })]),
        h('p', {class:'chart-note'}, 'Hover to inspect or click a candle to select it.'),
        c ? h('p', `UTC ${c.timestamp} — Open ${Number(c.open).toFixed(2)}, High ${Number(c.high).toFixed(2)}, Low ${Number(c.low).toFixed(2)}, Close ${Number(c.close).toFixed(2)}, Volume ${c.volume == null ? 'unknown' : Number(c.volume).toLocaleString()} shares — ${item.ended ? 'ended' : 'forming'}; coverage ${item.coverage.minutes_present}/${item.coverage.minutes_expected}${item.data_source ? `; ${item.data_source}` : ''}`) : null,
        item ? h('p', `Short SMA: ${item.sma?.short == null ? 'unavailable' : item.sma.short.toFixed(2)} | Long SMA: ${item.sma?.long == null ? 'unavailable' : item.sma.long.toFixed(2)} | Crossover: ${item.sma?.signal || 'none'}`) : null,
        item && instances.value.length ? h('p', `Indicators: ${instances.value.map(i => `${i.name}: ${item.indicators?.[i.id] == null ? 'unavailable' : item.indicators[i.id].toFixed(2)}`).join(' | ')}`) : null,
        h('details', {class:'inline-details'}, [h('summary', 'SMA settings'), h('div', {class:'sma-controls'}, [
          h('label', ['Short SMA window: ', h('input', { type: 'number', min: 1, step: 1, value: shortInput.value, onInput: e => { shortInput.value = e.target.value } })]),
          h('label', [' Long SMA window: ', h('input', { type: 'number', min: 1, step: 1, value: longInput.value, onInput: e => { longInput.value = e.target.value } })]),
          h('button', { disabled: automation.value.state.enabled, onClick: applyWindows }, 'Apply averages'),
          h('span', { role: 'alert' }, windowError.value),
        ])]),
        h('details', {class:'inline-details'}, [h('summary', 'Additional chart indicators'), h('section', {class:'indicator-panel'}, [
          h('h2', 'Chart indicators'),
          choices('Indicator type', indicatorTypes.value.map(type => ({value:type.type,text:type.label})), newType.value, chooseIndicatorType),
          h('label', [' Indicator name: ', h('input', { value: newName.value, onInput: e => { newName.value = e.target.value } })]),
          ...(indicatorTypes.value.find(type => type.type === newType.value)?.settings || []).map(field => h('label', [` ${field.label}: `, h('input', { type: 'number', min: field.minimum, step: 1, value: settingInputs.value[field.key], onInput: e => { settingInputs.value = { ...settingInputs.value, [field.key]: e.target.value } } })])),
          h('button', { onClick: createIndicator }, 'Add indicator'), h('span', { role: 'alert' }, indicatorError.value),
          h('p', `Configured: ${instances.value.map(i => `${i.name} (${i.type}, window ${i.settings.window})`).join(', ') || 'none'}`),
          ...instances.value.map(i=>h('div',[h('label',[`${i.name} window: `,h('input',{type:'number',min:1,step:1,value:editWindows.value[i.id] ?? String(i.settings.window),onInput:e=>{editWindows.value={...editWindows.value,[i.id]:e.target.value}}})]),h('button',{onClick:()=>updateIndicator(i)},`Update ${i.name}`)])),
        ])]),
            ]),
        h('details', {class:'tool-disclosure'}, [h('summary', 'SMA backtest'), h(Backtest)]),
        h('details', {class:'tool-disclosure'}, [h('summary', 'Watchlist review'), h(Watchlist)]),
        h('details', {class:'tool-disclosure'}, [h('summary', 'Chart signal'), h('section', {class:'lesson-panel'}, [
          h('div', {class:'lesson-heading'}, [h('div', [h('span', {class:'eyebrow'}, 'IMAGE-ONLY EXERCISE'), h('h2', 'Chart signal')]), h('span', {class:'simulation-badge'}, 'NO TRADE')]),
          h('p', 'The agent requests a fresh long, short, or wait assessment. OpenAI receives the chart image and timing context.'),
          h('button', {class:'lesson-action', disabled:lessonBusy.value || playing.value || stepBusy.value || !records.value.length, onClick:askClassroomChart}, lessonBusy.value ? 'Analyzing chart…' : 'Analyze visible chart'),
          h('p', {role:'alert'}, lessonError.value),
          lessonImage.value ? h('div', {class:'lesson-capture'}, [h('h3','Captured chart'), h('img', {src:lessonImage.value, alt:'Chart image sent for analysis'})]) : null,
          lessonResult.value ? h('div', {class:'lesson-result', role:'status'}, [
            h('span', {class:'eyebrow'}, 'MODEL RESPONSE'),
            lessonResult.value.status === 'ok' ? h('p', {class:'lesson-choice'}, `${lessonResult.value.answer.choice.toUpperCase()} (probability: ${lessonResult.value.answer.probability == null ? 'unavailable' : lessonResult.value.answer.probability.toFixed(2)})`) : h('p', {class:'lesson-choice'}, `Analysis ${lessonResult.value.status.replaceAll('_', ' ')}`),
            h('p', lessonResult.value.status === 'missing_key' ? 'Configure OPENAI_API_KEY in the local backend environment.' : lessonResult.value.status === 'ok' ? 'This is the probability of the selected option, not the probability of trading profit. A valid label does not prove the model followed the rules.' : 'No choice was returned. Try again when the provider is available.'),
            lessonResult.value.answer ? h('p', `Options: ${['long','short','wait'].map(v => `${v.toUpperCase()} ${lessonResult.value.answer.probabilities[v] == null ? 'unavailable' : lessonResult.value.answer.probabilities[v].toFixed(2)}`).join(' · ')}`) : null,
            h('p', `${lessonResult.value.context.symbol} · ${lessonResult.value.context.interval} · ${lessonResult.value.context.mode} · ${lessonResult.value.context.data_source || 'source unavailable'} · ${lessonResult.value.context.is_stale ? 'stale' : 'not marked stale'} · Chart time ${formatUtc(lessonResult.value.context.last_completed_candle || lessonResult.value.context.forming_candle)} · Captured ${lessonResult.value.context.capture_time}`),
          ]) : null,
          h('div', {class:'lesson-flow', 'aria-label':'Decision flow'}, [h('span','Visible chart'),h('span','Vue capture'),h('span','FastAPI'),h('span','OpenAI choice'),h('span','Result')]),
          h('p', {class:'chart-note'}, 'The model inspects the last two completed visible candles. Long: both close above open. Short: both close below open. Wait: mixed, equal, unclear, insufficient, or stale.'),
          lessonHistory.value.length ? h('div', {class:'lesson-history'}, [h('h3','Recent chart interpretations'), ...lessonHistory.value.map(x => h('button', {onClick:() => { lessonSelection++; lessonResult.value=x; lessonImage.value=x.image_url; lessonError.value='' }}, `${x.answer?.choice?.toUpperCase() || x.status.replaceAll('_',' ')} · ${x.context.symbol} ${x.context.interval} · ${x.created_at}`))]) : null,
        ])]),
        h('details', {class:'tool-disclosure'}, [h('summary', 'Account-aware chart analysis'), h('section', [
          h('h2', 'Ask about chart'),
          h('p', mode.value === 'replay' && !advancedReplay.value ? 'Use Chart signal to analyze this snapshot. Account-aware analysis is available in step-by-step replay.' : 'Analyze a selected completed candle and paper-account snapshot. No order is placed.'),
          h('button', {disabled:chartBusy.value || decisionBusy.value || stepBusy.value || paperBusy.value || playing.value || !selected.value || (mode.value === 'replay' && !replayCheckpoint.value), onClick:askAboutChart}, chartBusy.value ? 'Analyzing chart…' : 'Ask about chart'),
          h('p', {role:'alert'}, chartError.value),
          chartCapture.value ? h('div', [
            h('p', `Captured ${chartCapture.value.capture.symbol} | ${chartCapture.value.capture.timeframe} | Range ${chartCapture.value.capture.range_start} through ${chartCapture.value.capture.cutoff} | Snapshot ${chartCapture.value.capture.snapshot_id}`),
            h('img', {src:chartCapture.value.image_url, alt:'Frozen chart sent for analysis', style:'width:100%;max-width:1000px'}),
          ]) : null,
          chartResult.value ? h('div', [
            h('p', `Decision ${chartResult.value.decision_id} | Status ${chartResult.value.status} | Paper-order eligible: ${chartResult.value.eligible_for_paper_order ? 'yes' : 'no'}`),
            ...chartResult.value.questions.map(q => h('p', `${q.name}: ${q.instructions} Choices: ${q.choices.map(c => c.value).join(', ')}. Answer: ${chartResult.value.answers[q.name] || 'unavailable'}`)),
            h('p', `Python crossover: ${chartResult.value.calculated_crossover} | Visual comparison: ${chartResult.value.comparison}`),
            h('p', `Reviewer (${chartResult.value.review.reviewer_model}): ${chartResult.value.review.verdict || 'no verdict'} | Review status: ${chartResult.value.review.reviewer_status}`),
            h('p', `Final backend check: ${chartResult.value.review.final_check.status} — ${chartResult.value.review.final_check.explanation}`),
            h('p', `Recorded evidence: ${chartResult.value.explanation}`),
          ]) : null,
        ])]),
        mode.value === 'replay' && advancedReplay.value ? h('section', [
          h('h2', 'Replay decision'),
          h('p', 'Select a completed candle, pause replay, then freeze the visible chart and paper account before requesting a model proposal.'),
          h('button', {disabled: !replayCheckpoint.value || !selected.value || playing.value || stepBusy.value || paperBusy.value || decisionBusy.value, onClick: freezeReplayDecision}, 'Freeze replay snapshot'),
          decisionSnapshot.value ? h('div', [
            h('p', `Frozen snapshot ${decisionSnapshot.value.id} | Replay point ${decisionSnapshot.value.replay.position} | Observation received ${decisionSnapshot.value.decision_time}`),
            h('p', `Candle ${decisionSnapshot.value.candle_timestamp} | Close ${decisionSnapshot.value.candle?.close ?? 'unavailable'} | Short SMA ${decisionSnapshot.value.sma?.short ?? 'unavailable'} | Long SMA ${decisionSnapshot.value.sma?.long ?? 'unavailable'} | Crossover ${decisionSnapshot.value.sma?.signal || 'none'}`),
            h('p', `Frozen account: cash $${decisionSnapshot.value.account?.cash ?? 'unavailable'} | ${decisionSnapshot.value.symbol} shares ${decisionSnapshot.value.account?.shares ?? 'unavailable'}`),
            h('p', `Paper-order eligibility: ${decisionSnapshot.value.eligible_for_paper_order ? 'eligible' : 'ineligible'} — ${decisionSnapshot.value.ineligibility_reasons.join('; ')}`),
            h('button', {disabled: decisionBusy.value, onClick: requestDecision}, 'Request model proposal'),
          ]) : null,
          decisionResult.value ? h('div', [
            h('p', {role:'status'}, `Analyst (${decisionResult.value.model}): ${decisionResult.value.proposal || 'none'} | Status: ${decisionResult.value.status} | ${decisionResult.value.explanation}`),
            h('p', `Reviewer (${decisionResult.value.review.reviewer_model}): ${decisionResult.value.review.verdict || 'no verdict'} | Review status: ${decisionResult.value.review.reviewer_status}`),
            h('p', `Final backend check: ${decisionResult.value.review.final_check.status} — ${decisionResult.value.review.final_check.explanation}`),
          ]) : null,
          h('p', {role:'alert'}, decisionError.value),
        ]) : null,
        h('p', `Crossovers: ${records.value.filter(r => r.sma?.signal).slice(-5).map(r => `${r.candle.timestamp} ${r.sma.signal}`).join(', ') || 'none'} (latest five)`),
        h('p', {class:'attribution'}, ['TradingView Lightweight Charts™ — Copyright © 2025 TradingView, Inc. ', h('a', { href: 'https://www.tradingview.com/', target: '_blank', rel: 'noopener noreferrer' }, 'TradingView')]),
          ]),
          h('aside', { class: 'sidebar' }, [
        h('details', {id:'chart-chat', class:'tool-disclosure', open:chatOpen.value, onToggle:e=>{chatOpen.value=e.target.open}}, [h('summary', 'Chart chat'), h(ChartChat, {busy:lessonBusy.value, request:requestChartAgent})]),
        h('section', {class:'paper-panel'}, [
          h('h2', 'Paper account'),
          h('p', {class:'account-label'}, 'SIMULATED BALANCE'),
          h('p', `Cash: $${paper.value.cash} | Shares: ${paper.value.positions.map(p => `${p.symbol} ${p.shares}`).join(', ') || '0'}`),
          h('details', {class:'inline-details account-details'}, [h('summary', 'Trading controls and history'),
          h('label', ['Shares: ', h('input', {type:'number', min:1, step:1, value:paperQty.value, onInput:e => {paperQty.value=e.target.value}})]),
          ...['buy','sell','close','hold','wait'].map(action => h('button', {disabled:paperBusy.value || stepBusy.value || playing.value || (mode.value === 'replay' && !replayScope), onClick:() => submitPaper(action)}, action[0].toUpperCase()+action.slice(1))),
          h('button', {disabled:paperBusy.value || stepBusy.value || !lastPaperRequest || playing.value || (mode.value === 'replay' && !replayScope), onClick:() => submitPaper(null, true)}, 'Retry last request'),
          h('p', {role:'status'}, paperMessage.value),
          ...paper.value.positions.filter(p => p.shares > 0 && !p.valuation).map(p => h('p', `${p.symbol} valuation: unavailable`)),
          h('h3', 'Profit and loss history'),
          h('table', [
            h('thead', h('tr', ['Symbol','Event','Event time','Price','Price time','Price source','Cash','Shares','Realized','Unrealized','Total P&L'].map(label => h('th', label)))),
            h('tbody', (paper.value.pnl_history || []).map(row => h('tr', [row.symbol, row.event_type, row.event_time, row.price, row.price_time || 'unavailable', row.price_source, row.cash, String(row.shares), row.realized, row.unrealized ?? 'unavailable', row.total ?? 'unavailable'].map(value => h('td', String(value))))))
          ]),
          h('h3', 'Paper order history'),
          h('ul', paper.value.orders.map(o => h('li', `Order ${o.id}: ${o.side} ${o.quantity ?? '?'} ${o.symbol} — ${o.status}${o.reason ? ` (${o.reason})` : ''}${o.fill_price !== null ? ` at $${o.fill_price} (observation ${o.observation_id}, receipt ${o.receipt_id}, executed ${o.executed_at})` : ''}; submitted ${o.submitted_at}`))),
          ]),
        ]),
        h('details', {class:'tool-disclosure', open:!!automation.value.state.enabled || automationOpen.value, onToggle:e=>{automationOpen.value=e.target.open}}, [h('summary', `Paper automation · ${automation.value.state.enabled ? 'running' : 'stopped'}`), h('section', [
          h('h2', 'Paper automation'),
          h('p', `Status: ${automation.value.state.enabled ? 'running' : 'stopped'}${automationWorking.value ? ' | analyzing a completed candle' : ''}`),
          h('p', 'Buys on an upward SMA crossover and closes on a downward crossover. Decisions expire after 60 seconds.'),
          h('button', {disabled: !!automation.value.state.enabled || mode.value !== 'live', onClick:startAutomation}, 'Start paper automation'),
          h('button', {disabled: !automation.value.state.enabled, onClick:stopAutomation}, 'Stop paper automation'),
          h('p', {role:'alert'},automationError.value),
          h('h3','Automation decisions'),
          h('ul', automation.value.events.map(e => h('li', `Candle ${e.candle_timestamp} | ${e.symbol} ${e.timeframe} | Crossover ${e.crossover || 'none'} | Decision ${e.decision_id || 'none'} | Proposal ${e.proposal || 'none'} | Review ${e.reviewer_verdict || 'none'} | ${e.status}${e.reason ? `: ${e.reason}` : ''} | Order ${e.order_id || 'none'} | Fill ${e.fill_id || 'none'}${e.price ? ` at $${e.price}` : ''}${e.cash != null ? ` | Cash $${e.cash} | Shares ${e.shares}` : ''}`))),
        ])]),
        h('details', {class:'tool-disclosure'}, [h('summary', 'Voice conversation'), h('section', [
          h('h2','Discuss a recorded decision by voice'),
          h('p','The selected record and your speech are sent to OpenAI for this conversation. The voice session cannot place orders.'),
          h('button',{disabled:voice.busy.value || voice.active.value,onClick:()=>voice.refresh().catch(err=>{voice.status.value=err.message})},'Refresh decisions'),
          voice.choices.value.length ? choices('Decision', voice.choices.value.map(d => ({value:d.id,text:`${d.symbol || 'unknown'} · ${d.proposal || 'no proposal'} · ${d.id}`})), voice.selected.value, value => voice.select(value), voice.busy.value || voice.active.value) : h('p', 'No recorded decisions'),
          voice.summary.value ? h('p',`Selected ${voice.summary.value.decision_id} | ${voice.summary.value.symbol || 'unknown symbol'} | Analyst: ${voice.summary.value.analyst.proposal || 'none'} | Reviewer: ${voice.summary.value.reviewer?.verdict || 'unavailable'} | Paper outcome: ${voice.summary.value.paper_outcome.status}`) : null,
          h('button',{disabled:!voice.summary.value || voice.busy.value || voice.active.value,onClick:voice.start},'Start voice'),
          h('button',{disabled:!voice.busy.value && !voice.active.value,onClick:()=>voice.stop()},'End voice'),
          h('p',{role:'status'},`${voice.status.value} | Microphone: ${voice.active.value ? 'active' : 'off'}`),
          voice.audioAvailable.value ? h('button',{onClick:voice.play},'Play voice audio') : null,
        ])]),
          ])
        ])
      ])
    }
  },
}).mount('#app')
